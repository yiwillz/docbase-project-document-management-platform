// 第 14B 步主流程：存量导入——填目录扫描 → 预演报告 → 改报告导回 → 执行 → 看明细 → 整批撤销
// 测试自己启动一个连测试库的后台 worker，跑完关掉；源文件造在临时目录（playwright.config 里的 IMPORT_ROOT）
import { type ChildProcess, spawnSync } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { expect, test } from '@playwright/test'

import { PYTHON } from '../playwright.config'
import { api, asUser, newProject, startWorker, stopWorker } from './helpers'

interface Item {
  submittal_no: string
  equipment_tag: string | null
  file_count: number
}

let worker: ChildProcess | null = null

test.afterAll(() => stopWorker(worker))

test('bulk import: scan, report round trip, execute, details, undo', async ({ page, request }, testInfo) => {
  test.setTimeout(180_000)
  const p = await newProject(request, 'E2E14')
  const base = `/api/projects/${p.id}`
  const roots = await api<string[]>(request, 'GET', `${base}/imports/roots`)
  // Windows 的临时目录可能是短路径（YIZHAN~1），按目录名认，源文件建在后端说的那个路径下
  const root = roots.find((r) => r.toLowerCase().endsWith('docbase-e2e-import'))
  test.skip(!root, '后端没有配置 e2e 用的导入目录（连的是已经开着的后端时跳过）')

  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [, ds] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Datasheet', keywords: ['DS'] }],
  })
  await api(request, 'POST', `${base}/equipment`, { tag: '72', name: 'Mixer', vendor: 'Vendor A' })
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, { document_type_ids: [ds.id], all_equipment: true, mode: 'add' })
  const items = async () => (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items

  // ---------------- 造一个虚构的存量目录
  const source = join(root!, p.code)
  rmSync(source, { recursive: true, force: true })
  const files: Record<string, string> = {
    '72 Mixer/Mixer DS V1.pdf': 'ds v1',
    '72 Mixer/Mixer DS V2 20260301.pdf': 'ds v2',
    'Spec/Spec rev 2.pdf': 'spec',
    'Tool 205/notes.pdf': 'notes',
  }
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(join(source, rel, '..'), { recursive: true })
    writeFileSync(join(source, rel), text)
  }

  worker = startWorker()

  await asUser(page)
  await page.goto(`/projects/${p.id}/imports`)
  await page.locator('input[data-test="import-dir"]').fill(source)
  await page.locator('[data-test="import-create"]').click()
  const row = page.locator('[data-test="import-batches"] .el-table__body tr').first()
  await expect(row.locator('[data-test="import-status"]')).toHaveText('预演完成', { timeout: 60_000 })
  const summary = row.locator('[data-test="import-summary"]')
  await expect(summary).toContainText('共 4 个')
  await expect(summary).toContainText('旧版 1')
  await expect(summary).toContainText('待归类 1')

  // ---------------- 下载预演报告，把 notes 改到 1.1 Spec，导回
  const downloading = page.waitForEvent('download')
  await row.locator('[data-test="import-report"]').click()
  const report = testInfo.outputPath('report.xlsx')
  await (await downloading).saveAs(report)
  const edited = testInfo.outputPath('report-edited.xlsx')
  const py = [
    'import sys',
    'from openpyxl import load_workbook',
    'wb = load_workbook(sys.argv[1]); ws = wb["items"]',
    'for r in range(2, ws.max_row + 1):',
    '    if ws.cell(r, 2).value == "Tool 205/notes.pdf": ws.cell(r, 14).value = "1.1"; ws.cell(r, 18).value = "Site notes"',
    'wb.save(sys.argv[2])',
  ].join('\n')
  const out = spawnSync(PYTHON, ['-c', py, report, edited], { encoding: 'utf-8' })
  expect(out.status, out.stderr).toBe(0)
  const chooser = page.waitForEvent('filechooser')
  await row.getByRole('button', { name: '导回改过的报告' }).click()
  await (await chooser).setFiles(edited)
  const preview = page.locator('[data-test="report-preview"]')
  await expect(preview).toContainText('Tool 205/notes.pdf')
  await preview.locator('[data-test="report-confirm"]').click()
  await expect(summary).not.toContainText('待归类')

  // ---------------- 执行
  await row.locator('[data-test="import-execute"]').click()
  await page.locator('.el-message-box').getByRole('button', { name: '执行导入' }).click()
  await expect(row.locator('[data-test="import-status"]')).toHaveText('已执行', { timeout: 60_000 })
  await expect(summary).toContainText('已导入 4')
  await expect.poll(async () => (await items()).find((i) => i.equipment_tag === '72')?.file_count).toBe(1)

  // ---------------- 明细：旧版那一行
  await row.locator('[data-test="import-detail"]').click()
  const drawer = page.locator('[data-test="import-items"]')
  // 点看得见的按钮（会等抽屉滑入停稳）；强行点隐藏的单选框可能在动画中点到旁边的"平台已有"
  await drawer.locator('.el-radio-button', { hasText: '旧版' }).click()
  await expect(drawer.getByRole('radio', { name: '旧版' })).toBeChecked()
  await expect(drawer.locator('.el-table__body')).toContainText('72 Mixer/Mixer DS V1.pdf')
  await expect(drawer.locator('.el-table__body')).toContainText('1.2.72_Datasheet-Mixer-')
  await expect(drawer.locator('.el-table__body')).toContainText('1.2 Datasheet / 72 Mixer')
  await page.keyboard.press('Escape')

  // ---------------- 整批撤销
  await row.locator('[data-test="import-undo"]').click()
  await page.locator('.el-message-box').getByRole('button', { name: '整批撤销' }).click()
  await expect(row.locator('[data-test="import-status"]')).toHaveText('已撤销', { timeout: 60_000 })
  await expect.poll(async () => (await items()).every((i) => i.file_count === 0)).toBe(true)
  rmSync(source, { recursive: true, force: true })
})
