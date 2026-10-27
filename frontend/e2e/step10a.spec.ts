// 第 10A 步主流程：建里程碑 → 粘贴文件种类 → 设模块 → 粘贴设备（含没编号、台数几千的条目）
// → 模块对应设备种类 → 勾选对应设备 → 应交项按条目生成 → 设置页改客户审核期限
import { expect, test } from '@playwright/test'

import { api, asUser, newProject } from './helpers'

test('set up a project: structure, equipment, settings', async ({ page, request }) => {
  const project = await newProject(request, 'E2E10A')
  await asUser(page)

  // ---------------- 项目结构：建里程碑
  await page.goto(`/projects/${project.id}/structure`)
  await page.locator('.milestones').getByRole('button', { name: '新建' }).click()
  await page.locator('input[data-test="ms-name"]').fill('Design')
  await page.getByRole('dialog').getByRole('button', { name: '保存' }).click()
  await expect(page.locator('[data-test="milestone"]')).toContainText('M1')

  // 粘贴两行文件种类（第二行不填类别等，第一行按设备）
  await page.getByRole('button', { name: '从 Excel 粘贴多行' }).click()
  await page.getByPlaceholder('粘贴到这里').fill('Datasheet\tDesign\t合同规定\t是\nLayout\tDesign\n')
  await page.getByRole('button', { name: '确定（2 行）' }).click()
  const table = page.locator('[data-test="doc-table"]')
  await expect(table.locator('.el-table__body tr')).toHaveCount(2)
  await expect(table).toContainText('1.1')
  await expect(table).toContainText('1.2')

  // 一行一行加：回车新建
  await page.locator('input[data-test="new-doc"]').fill('Spare parts list')
  await page.locator('input[data-test="new-doc"]').press('Enter')
  await expect(table.locator('.el-table__body tr')).toHaveCount(3)

  // 在表格里直接改名（第 10A 步试用时发现文字框改不动，这里防止再出现）
  const nameBox = table.locator('.el-table__body tr').nth(2).locator('.el-input input').first()
  await nameBox.fill('Spare parts list (rev)')
  await nameBox.press('Enter')
  await expect
    .poll(async () => {
      const tree = await api<{ milestones: { document_types: { name: string }[] }[] }>(
        request,
        'GET',
        `/api/projects/${project.id}/structure`,
      )
      return tree.milestones[0].document_types.map((d) => d.name)
    })
    .toEqual(['Datasheet', 'Layout', 'Spare parts list (rev)'])

  // 模块管理里建模块 A、B
  await page.getByRole('button', { name: '模块管理' }).click()
  const moduleMgr = page.getByRole('dialog', { name: '模块管理' })
  for (const [i, name] of ['A', 'B'].entries()) {
    await moduleMgr.getByPlaceholder('新模块名称').fill(name)
    await moduleMgr.getByPlaceholder('新模块名称').press('Enter')
    await expect(moduleMgr.locator('.module-row')).toHaveCount(i + 2) // 已建的 + 新建那一行
  }
  await moduleMgr.getByRole('button', { name: 'Close' }).or(moduleMgr.locator('.el-dialog__headerbtn')).first().click()

  // 第 2 行（Layout）设模块分区 A、B
  await table.locator('.el-table__body tr').nth(1).locator('.el-checkbox').click()
  await page.getByRole('button', { name: '设模块分区' }).click()
  const modulesDialog = page.getByRole('dialog', { name: '设模块分区' })
  await modulesDialog.locator('.el-select').click()
  await page.getByRole('option', { name: 'A', exact: true }).click()
  await page.getByRole('option', { name: 'B', exact: true }).click()
  await modulesDialog.locator('.el-dialog__title').click() // 收起下拉框
  await modulesDialog.getByRole('button', { name: '确定' }).click()
  await expect(table.locator('.el-table__body tr').nth(1)).toContainText('A')

  // ---------------- 设备：粘贴两行，其中一行没编号、3000 台
  await page.goto(`/projects/${project.id}/equipment`)
  await page.getByRole('button', { name: '从 Excel 粘贴多行' }).click()
  const rows = [
    ['71', 'Alpha tool 1', 'Alpha', 'Vendor A', '1'],
    ['', 'Beta part', 'Part', '', '3000'],
  ]
  await page.getByPlaceholder('粘贴到这里').fill(rows.map((r) => r.join('\t')).join('\n'))
  await page.getByRole('button', { name: '确定（2 行）' }).click()
  const preview = page.getByRole('dialog', { name: '导入预览' })
  await expect(preview).toContainText('会新建设备种类')
  await preview.locator('[data-test="import-confirm"]').click()
  const equipmentTable = page.locator('[data-test="equipment-table"]')
  await expect(equipmentTable).toContainText('Beta part')
  await expect(equipmentTable).toContainText('无编号')
  await expect(equipmentTable).toContainText('3,000')

  // 模块 A 对应设备种类 Alpha
  await page.getByText('设备种类与模块').click()
  await page.locator('[data-test="module-types-A"]').click()
  await page.getByRole('option', { name: 'Alpha' }).click()
  await page.keyboard.press('Escape')
  await expect.poll(async () => {
    const tree = await api<{ modules: { id: number; name: string }[] }>(request, 'GET', `/api/projects/${project.id}/structure`)
    const moduleA = tree.modules.find((m) => m.name === 'A')!
    const types = await api<{ name: string }[]>(request, 'GET', `/api/projects/${project.id}/modules/${moduleA.id}/equipment-types`)
    return types.map((t) => t.name)
  }).toEqual(['Alpha'])

  // ---------------- 回到结构：Datasheet 对应全部设备
  await page.goto(`/projects/${project.id}/structure`)
  await page.getByRole('button', { name: '0 台条目' }).click()
  await page.locator('[data-test="equip-by"]').getByText('全部设备').click()
  await page.locator('[data-test="equip-ok"]').click()
  await expect(page.getByRole('button', { name: '2 台条目' })).toBeVisible()

  // 应交项：按设备 2 个（按条目，台数 3000 的条目只算 1 个）、按模块 2 个、整个种类 1 个
  const summary = await api<{ by_kind: Record<string, number> }>(
    request,
    'GET',
    `/api/projects/${project.id}/deliverables/summary`,
  )
  expect(summary.by_kind).toEqual({ per_equipment: 2, per_module: 2, whole: 1 })

  // ---------------- 设置：客户审核期限
  await page.goto(`/projects/${project.id}/settings`)
  await page.locator('.el-input-number input').first().fill('14')
  await page.locator('[data-test="save-info"]').click()
  await expect(page.getByText('已保存')).toBeVisible()
  const saved = await api<{ review_days: number }>(request, 'GET', `/api/projects/${project.id}`)
  expect(saved.review_days).toBe(14)
})

test('archived project pages are read-only', async ({ page, request }) => {
  const project = await newProject(request, 'E2E10A-RO')
  await api(request, 'POST', `/api/projects/${project.id}/archive`, {})
  await asUser(page)
  await page.goto(`/projects/${project.id}/structure`)
  await expect(page.getByText('这个项目已归档，只能查看')).toBeVisible()
  await expect(page.getByRole('button', { name: '从 Excel 粘贴多行' })).toHaveCount(0)
  await page.goto(`/projects/${project.id}/equipment`)
  await expect(page.getByRole('button', { name: '新建设备' })).toHaveCount(0)
})
