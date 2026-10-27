// 第 17C 步：项目列表的完成率 / 缺件 / 逾期（点数字跳到应交矩阵并带好筛选）、变更记录导出 Excel
import { expect, test } from '@playwright/test'

import { api, asUser, newProject } from './helpers'

test('project list stats and change log export', async ({ page, request }) => {
  const p = await newProject(request, 'E2E17C')
  const base = `/api/projects/${p.id}`
  const m = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design', due_date: '2020-01-01' })
  await api(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m.id,
    rows: [{ name: 'Spec' }, { name: 'Layout' }, { name: 'Manual' }, { name: 'Report' }],
  })
  const { items } = await api<{ items: { id: number; submittal_no: string }[] }>(request, 'GET', `${base}/deliverables`)
  const id = (no: string) => items.find((i) => i.submittal_no === no)!.id
  await api(request, 'PATCH', `${base}/deliverables/${id('1.1')}`, { status: 'approved', status_date: '2026-01-05', status_basis: 'Letter 12' })
  await api(request, 'PATCH', `${base}/deliverables/${id('1.2')}`, { status: 'not_applicable', status_basis: 'Deleted by client' })

  await asUser(page)
  await page.goto('/projects')
  const row = page.locator('.el-table__row').filter({ hasText: p.code })
  // 应交 3（不含不适用），已批准 1 → 33.3%；缺件、逾期 = 1.3、1.4 两项未提交且过了截止日
  await expect(row.locator('[data-test="completion"]')).toHaveText('33.3%')
  await expect(row.locator('[data-test="outstanding-count"]')).toHaveText('2')
  await row.locator('[data-test="overdue-count"]').click()
  await expect(page).toHaveURL(new RegExp(`/projects/${p.id}/matrix\\?overdue=true`))

  // 变更记录：按当前筛选导出
  await page.goto(`/projects/${p.id}/changes`)
  const download = page.waitForEvent('download')
  await page.locator('[data-test="export-changes"]').click()
  expect((await download).suggestedFilename()).toMatch(new RegExp(`^${p.code}_ChangeLog_\\d{8}\\.xlsx$`))
})

// 第 17D 步：顶部数字点卡片 / 按钮后不变，再点一次取消；收集情况
test('matrix cards toggle without changing the numbers; received column', async ({ page, request }) => {
  const p = await newProject(request, 'E2E17D')
  const base = `/api/projects/${p.id}`
  const m = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  await api(request, 'POST', `${base}/document-types/bulk`, { milestone_id: m.id, rows: [{ name: 'Spec' }, { name: 'Layout' }] })
  const { items } = await api<{ items: { id: number; submittal_no: string }[] }>(request, 'GET', `${base}/deliverables`)
  await api(request, 'PATCH', `${base}/deliverables/${items[0].id}`, { responsible: 'vendor' })

  await asUser(page)
  await page.goto(`/projects/${p.id}/matrix`)
  const unassigned = page.locator('[data-test="chip-unassigned"]')
  await expect(unassigned).toContainText('1')
  await expect(page.locator('[data-test="kpi-received"]')).toContainText('已收到 0 / 应交 2')
  await expect(page.locator('[data-test="not-received"]')).toHaveCount(2)

  // 点"供应商"：只剩供应商的那一项，顶部数字不变；再点一次取消
  await page.locator('[data-test="chip-vendor"]').click()
  await expect(page).toHaveURL(/responsible=vendor/)
  await expect(page.locator('[data-test="chip-vendor"]')).toHaveClass(/active/)
  await expect(unassigned).toContainText('1')
  await expect(page.locator('[data-test="not-received"]')).toHaveCount(1)
  await page.locator('[data-test="chip-vendor"]').click()
  await expect(page).not.toHaveURL(/responsible=/)
  await expect(page.locator('[data-test="not-received"]')).toHaveCount(2)

  // 缺件卡片也是点一下筛、再点取消
  await page.locator('[data-test="kpi-outstanding"]').click()
  await expect(page).toHaveURL(/outstanding=true/)
  await expect(page.locator('[data-test="kpi-outstanding"]')).toContainText('2')
  await page.locator('[data-test="kpi-outstanding"]').click()
  await expect(page).not.toHaveURL(/outstanding=/)
})

// 第 17E 步：系统管理员删除空项目（输入项目编号确认）；有文件的只能归档
test('sysadmin deletes an empty project', async ({ page, request }) => {
  const p = await newProject(request, 'E2E17E')
  await asUser(page)
  await page.goto('/projects')
  const row = page.locator('.el-table__row').filter({ hasText: p.code })
  await row.locator('[data-test="project-delete"]').click()
  const dialog = page.locator('[data-test="delete-dialog"]')
  await expect(dialog.locator('[data-test="delete-confirm"]')).toBeDisabled()
  await dialog.locator('[data-test="delete-code"]').fill(p.code)
  await dialog.locator('[data-test="delete-confirm"]').click()
  await expect(page.getByText(`已删除项目 ${p.code}`)).toBeVisible()
  await expect(page.locator('.el-table__row').filter({ hasText: p.code })).toHaveCount(0)
})

// 第 17F 步：没用过的设备能删除；用过的提示原因并改为停用
test('delete unused equipment; used equipment is deactivated instead', async ({ page, request }) => {
  const p = await newProject(request, 'E2E17F')
  const base = `/api/projects/${p.id}`
  const m = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [ds] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, { milestone_id: m.id, rows: [{ name: 'Datasheet' }] })
  await api(request, 'POST', `${base}/equipment`, { tag: '71', name: 'Etcher' })
  await api(request, 'POST', `${base}/equipment`, { tag: '72', name: 'Mixer' })
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, { document_type_ids: [ds.id], all_equipment: true, mode: 'add' })
  const { items } = await api<{ items: { id: number; equipment_tag: string }[] }>(request, 'GET', `${base}/deliverables`)
  await api(request, 'POST', `${base}/transmittals`, { deliverable_ids: [items.find((i) => i.equipment_tag === '72')!.id] })

  await asUser(page)
  await page.goto(`/projects/${p.id}/equipment`)
  const table = page.locator('[data-test="equipment-table"]')
  // 71 没用过：删除
  await table.locator('.el-table__row').filter({ hasText: 'Etcher' }).locator('[data-test="equipment-delete"]').click()
  await page.locator('.el-message-box').getByRole('button', { name: '删除' }).click()
  await expect(table.locator('.el-table__row').filter({ hasText: 'Etcher' })).toHaveCount(0)
  // 72 进过传送单：说明原因，改为停用
  await table.locator('.el-table__row').filter({ hasText: 'Mixer' }).locator('[data-test="equipment-delete"]').click()
  await expect(page.locator('.el-message-box')).toContainText('传送单 1 张')
  await page.locator('.el-message-box').getByRole('button', { name: '改为停用' }).click()
  await expect(page.getByText('已停用').first()).toBeVisible()
  await expect(table.locator('.el-table__row').filter({ hasText: 'Mixer' })).toHaveCount(0) // 默认不显示停用的
})
