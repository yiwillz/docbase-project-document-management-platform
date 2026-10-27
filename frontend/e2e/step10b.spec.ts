// 第 10B 步主流程：应交矩阵（汇总筛选、改状态、批量改、矩阵）→ MDL（草稿、发给客户、对比、导回）→ 变更记录
import { writeFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

import { API as DEFAULT_API } from '../playwright.config'
import { USER, api, asUser, newProject } from './helpers'

const API = process.env.E2E_API ?? DEFAULT_API

interface Item {
  id: number
  submittal_no: string
  status: string
  status_date: string | null
  status_basis: string | null
  equipment_tag: string | null
  module_name: string | null
}

test('deliverables matrix, MDL and change log', async ({ page, request }, testInfo) => {
  // ---------------- 造数据：M1 已过截止日（未提交的都逾期）
  const p = await newProject(request, 'E2E10B')
  const base = `/api/projects/${p.id}`
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design', due_date: '2020-01-01' })
  const [, layout, datasheet] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Layout' }, { name: 'Datasheet' }],
  })
  await api(request, 'PUT', `${base}/document-types/${layout.id}/modules`, { module_names: ['A', 'B'] })
  for (const e of [{ tag: '71', name: 'Alpha tool' }, { name: 'Beta part', quantity: 3000 }]) {
    await api(request, 'POST', `${base}/equipment`, e)
  }
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, {
    document_type_ids: [datasheet.id],
    all_equipment: true,
    mode: 'add',
  })
  const items = async () => (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items

  await asUser(page)
  await page.goto(`/projects/${p.id}/matrix`)

  // ---------------- 汇总：5 项全部逾期；点"逾期"即筛选，条件进地址栏
  const summary = page.locator('[data-test="summary"]')
  await expect(summary).toContainText('已批准 0 / 应交 5')
  await expect(page.locator('[data-test="kpi-overdue"]')).toContainText('5')
  await page.locator('[data-test="kpi-overdue"]').click()
  await expect(page).toHaveURL(/overdue=true/)
  const list = page.locator('[data-test="matrix-list"]')
  await expect(list).toContainText('1.3 Datasheet')
  await expect(list).toContainText('Beta part')

  // ---------------- 改一项：1.1 已批准，写状态日期和依据
  await list.locator('tr').filter({ hasText: '整个种类' }).first().getByRole('button', { name: '编辑' }).click()
  const dialog = page.locator('[data-test="edit-dialog"]')
  await dialog.locator('[data-test="edit-status"]').click()
  await page.getByRole('option', { name: '已批准' }).click()
  await dialog.locator('.el-form-item').filter({ hasText: '状态日期' }).locator('input').fill('2026-10-20')
  await dialog.locator('.el-form-item').filter({ hasText: '状态日期' }).locator('input').press('Enter')
  await dialog.locator('.el-form-item').filter({ hasText: '依据' }).locator('input').fill('CL-021')
  await dialog.locator('[data-test="edit-save"]').click()
  await expect
    .poll(async () => (await items()).find((i) => i.submittal_no === '1.1'))
    .toMatchObject({ status: 'approved', status_date: '2026-10-20', status_basis: 'CL-021' })
  // 筛选还在（逾期），1.1 不再出现
  await expect(list).not.toContainText('1.1 Spec')

  // ---------------- 批量改：清除筛选，选 1.3 下面两台设备，改成已提交
  await page.locator('[data-test="clear-filters"]').click()
  await expect(page).not.toHaveURL(/overdue/)
  for (const name of ['Alpha tool', 'Beta part']) {
    await list.locator('tr').filter({ hasText: name }).locator('.el-checkbox').click()
  }
  await page.locator('[data-test="bulk-edit"]').click()
  await dialog.locator('[data-test="edit-status"]').click()
  await page.getByRole('option', { name: '已提交' }).click()
  await dialog.locator('.el-form-item').filter({ hasText: '状态日期' }).locator('input').fill('2026-10-22')
  await dialog.locator('.el-form-item').filter({ hasText: '状态日期' }).locator('input').press('Enter')
  await dialog.locator('[data-test="edit-save"]').click()
  await expect
    .poll(async () => (await items()).filter((i) => i.submittal_no === '1.3').map((i) => i.status))
    .toEqual(['submitted', 'submitted'])

  // ---------------- 矩阵：格子颜色、点格子打开修改窗口
  await page.locator('[data-test="view-matrix"]').click()
  await expect(page).toHaveURL(/view=matrix/)
  const grid = page.locator('[data-test="matrix-grid"]')
  await expect(grid).toContainText('1.2 Layout')
  await expect(grid.locator('td.cell').filter({ hasText: 'A' })).toHaveCount(1) // 1.1 已批准
  await expect(grid.locator('td.cell').filter({ hasText: 'S' })).toHaveCount(2) // 两台已提交
  await grid.locator('td.cell').filter({ hasText: 'O' }).first().click()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: '取消' }).click()

  // ---------------- MDL：草稿下载、发给客户（Rev.0）、加种类后再发（Rev.1）、对比
  await page.goto(`/projects/${p.id}/mdl`)
  const draft = page.waitForEvent('download')
  await page.locator('[data-test="export-draft"]').click()
  expect((await draft).suggestedFilename()).toContain('MDL_draft')

  await page.locator('[data-test="issue"]').click()
  await page.locator('input[data-test="issue-description"]').fill('First issue')
  const issued = page.waitForEvent('download')
  await page.locator('[data-test="issue-confirm"]').click()
  await issued
  await expect(page.getByText('已发出新版本 Rev.0')).toBeVisible()
  await expect(page.locator('[data-test="versions"]')).toContainText('First issue')

  await api(request, 'POST', `${base}/document-types/bulk`, { milestone_id: m1.id, rows: [{ name: 'Noise study' }] })
  await page.locator('[data-test="issue"]').click()
  await page.locator('input[data-test="issue-description"]').fill('Noise study added')
  const issued2 = page.waitForEvent('download')
  await page.locator('[data-test="issue-confirm"]').click()
  await issued2
  await expect(page.getByText('已发出新版本 Rev.1')).toBeVisible()
  await expect(page.locator('[data-test="versions"] .el-table__body tr')).toHaveCount(2) // 等版本列表刷新完
  await page.locator('[data-test="versions"] tr').filter({ hasText: 'Rev.0' }).getByRole('button', { name: '和当前比' }).click()
  await expect(page.locator('[data-test="diff-document_types-added"]')).toContainText('Noise study')
  await page.keyboard.press('Escape')

  // ---------------- MDL 导回：原样上传平台导出的文件 → 预览没有改动
  const resp = await request.get(`${API}${base}/mdl/export`, { headers: { 'X-User': USER } })
  const file = testInfo.outputPath('mdl.xlsx')
  await writeFile(file, await resp.body())
  await page.locator('input[data-test="return-file"]').setInputFiles(file)
  await expect(page.getByText('和平台里的一样，没有改动')).toBeVisible()
  await expect(page.locator('[data-test="return-confirm"]')).toBeDisabled()
  await page.keyboard.press('Escape')

  // ---------------- 变更记录：能看到刚才的修改，对象有名称
  await page.goto(`/projects/${p.id}/changes`)
  const logs = page.locator('[data-test="change-logs"]')
  await expect(logs).toContainText('1.1 Spec')
  await expect(logs).toContainText('1.3 Datasheet / 71')
  await expect(logs).toContainText('应交项')
})
