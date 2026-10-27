// 第 15 步主流程：交付包（估算、作为提交生成、下载）→ 阶段基线（冻结、查看、和现在比、按基线打包）→ 共享盘镜像
// 测试自己启动一个后台 worker；镜像目录是 playwright.config 里的 MIRROR_DIR（临时目录）
import type { ChildProcess } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { type APIRequestContext, expect, test } from '@playwright/test'

import { API as DEFAULT_API } from '../playwright.config'
import { USER, api, asUser, newProject, startWorker, stopWorker } from './helpers'

const API = process.env.E2E_API ?? DEFAULT_API
let worker: ChildProcess | null = null
test.afterAll(() => stopWorker(worker))

interface Item {
  id: number
  equipment_tag: string | null
  submittal_no: string
  status: string
}

async function store(request: APIRequestContext, base: string, ids: number[], name: string, body: string) {
  const resp = await request.post(`${API}${base}/files/preview`, {
    headers: { 'X-User': USER },
    multipart: { deliverable_ids: ids.join(','), file: { name, mimeType: 'application/octet-stream', buffer: Buffer.from(body) } },
  })
  expect(resp.ok(), await resp.text()).toBeTruthy()
  const { token } = (await resp.json()) as { token: string }
  await api(request, 'POST', `${base}/files/uploads/${token}/confirm`, {})
}

test('delivery: package as submission, baseline compare, package from baseline, mirror', async ({ page, request }) => {
  test.setTimeout(180_000)
  const p = await newProject(request, 'E2E15')
  const base = `/api/projects/${p.id}`
  const mirrorStatus = await api<{ enabled: boolean; dir: string | null }>(request, 'GET', `${base}/mirror`)
  test.skip(!mirrorStatus.enabled || !mirrorStatus.dir?.toLowerCase().endsWith('docbase-e2e-mirror'), '后端没有配置 e2e 用的镜像目录')

  const alpha = await api<{ id: number }>(request, 'POST', `${base}/equipment-types`, { name: 'Alpha' })
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [, ds] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Datasheet' }],
  })
  await api(request, 'POST', `${base}/equipment`, { tag: '72', name: 'Mixer', equipment_type_id: alpha.id })
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, { document_type_ids: [ds.id], all_equipment: true, mode: 'add' })
  const items = async () => (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items
  const d72 = (await items()).find((i) => i.equipment_tag === '72')!
  const spec = (await items()).find((i) => i.submittal_no === '1.1')!
  await store(request, base, [d72.id], 'Datasheet 20260226.pdf', 'ds v1')
  await store(request, base, [spec.id], 'Area A 20260226.pdf', 'spec')

  worker = startWorker()
  await asUser(page)
  await page.goto(`/projects/${p.id}/delivery`)

  // ---------------- 交付包：估算 → 作为提交生成 → 下载
  await expect(page.locator('[data-test="pkg-estimate"]')).toContainText('2 个文件')
  await page.locator('[data-test="pkg-submit"]').click()
  await page.locator('[data-test="pkg-create"]').click()
  const first = page.locator('[data-test="pkg-list"] .el-table__body tr').first()
  await expect(first.locator('[data-test="pkg-status"]')).toHaveText('已完成', { timeout: 60_000 })
  await expect(first).toContainText(`${p.code}-TR-`)
  const downloading = page.waitForEvent('download')
  await first.locator('[data-test="pkg-download"]').click()
  expect((await downloading).suggestedFilename()).toMatch(new RegExp(`^${p.code}_package_\\d{8}_\\d+\\.zip$`))
  await expect.poll(async () => (await items()).find((i) => i.id === d72.id)?.status).toBe('submitted')

  // ---------------- 阶段基线：冻结 → 之后 72 升版 → 和现在比看到"新版本"
  await page.locator('[data-test="tab-baselines"]').click()
  await page.locator('input[data-test="baseline-name"]').fill('Design freeze')
  await page.locator('[data-test="baseline-freeze"]').click()
  const row = page.locator('[data-test="baseline-list"] .el-table__body tr').first()
  await expect(row).toContainText('Design freeze')
  await expect(row).toContainText('2 个应交项')
  await store(request, base, [d72.id], 'Datasheet V2 20260301.pdf', 'ds v2')
  await row.locator('[data-test="baseline-compare"]').click()
  const cmp = page.locator('[data-test="compare-dialog"]')
  await expect(cmp.locator('[data-test="compare-change"]')).toContainText('新版本')
  await expect(cmp).toContainText('V1 Datasheet 20260226.pdf → V2 Datasheet V2 20260301.pdf')
  await page.keyboard.press('Escape')
  await row.locator('[data-test="baseline-view"]').click()
  await expect(page.locator('[data-test="baseline-detail"]')).toContainText('V1 1.2.72_Datasheet-Mixer-')
  await page.keyboard.press('Escape')

  // ---------------- 按基线打包：回到交付包，带着基线
  await row.locator('[data-test="baseline-pack"]').click()
  await expect(page.locator('[data-test="pkg-estimate"]')).toContainText('2 个文件')
  await page.locator('[data-test="pkg-create"]').click()
  const second = page.locator('[data-test="pkg-list"] .el-table__body tr').first()
  await expect(second).toContainText('按基线：Design freeze')
  await expect(second.locator('[data-test="pkg-status"]')).toHaveText('已完成', { timeout: 60_000 })

  // ---------------- 共享盘镜像：1 分钟内同步，镜像里有最新版
  await page.locator('[data-test="tab-mirror"]').click()
  await expect(page.locator('[data-test="mirror-status"]')).toContainText('docbase-e2e-mirror')
  const folder = join(mirrorStatus.dir!, p.code, 'Alpha', '72 Mixer')
  await expect.poll(() => existsSync(folder) && readdirSync(folder).some((f) => f.includes('-V2.')), { timeout: 60_000 }).toBe(true)
})
