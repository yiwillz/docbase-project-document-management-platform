// 第 12B 步主流程：项目结构里加识别关键词 → 智能投递几个文件（识别、待归类）→ 全部存入 →
// 项目列表的待归类数 → 归类 → 客户退回的带章文件同时记客户回复
import { expect, test } from '@playwright/test'

import { api, asUser, newProject } from './helpers'

interface Item {
  id: number
  submittal_no: string
  equipment_tag: string | null
  status: string
  file_count: number
}

const pdf = (name: string, text: string) => ({ name, mimeType: 'application/pdf', buffer: Buffer.from(text) })

test('smart drop, unclassified, classify, client return', async ({ page, request }) => {
  const p = await newProject(request, 'E2E12')
  const base = `/api/projects/${p.id}`
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [, datasheet] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec', keywords: ['specification'] }, { name: 'Datasheet' }],
  })
  for (const tag of ['72', '73']) {
    await api(request, 'POST', `${base}/equipment`, { tag, name: 'Mixer', vendor: 'Vendor A' })
  }
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, {
    document_type_ids: [datasheet.id],
    all_equipment: true,
    mode: 'add',
  })
  const items = async () => (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items

  await asUser(page)

  // ---------------- 项目结构：给 Datasheet 加识别关键词 "cut sheet"
  await page.goto(`/projects/${p.id}/structure`)
  await page.getByRole('button', { name: '详情' }).nth(1).click() // 第二行是 1.2 Datasheet（名称在输入框里）
  const detail = page.locator('[data-test="doc-detail"]')
  await detail.locator('[data-test="doc-keywords"]').click()
  await page.keyboard.type('cut sheet')
  await page.keyboard.press('Enter')
  await detail.getByRole('button', { name: '保存' }).click()
  await expect
    .poll(async () => (await api<{ id: number; keywords: string[] }[]>(request, 'GET', `${base}/document-types`)).find((d) => d.id === datasheet.id)?.keywords)
    .toEqual(['cut sheet'])

  // ---------------- 智能投递：三个文件，一个按关键词 + 设备编号，一个按种类关键词，一个识别不出
  await page.goto(`/projects/${p.id}/drop`)
  await page.locator('input[data-test="drop-description"]').fill('Vendor A documents')
  await page.locator('input[data-test="drop-input"]').setInputFiles([
    pdf('Mixer cut sheet 72 20260226.pdf', 'ds72'),
    pdf('Project specification.pdf', 'spec'),
    pdf('random notes.pdf', 'notes'),
  ])
  const rows = page.locator('[data-test="drop-row"]')
  await expect(rows).toHaveCount(3)
  const row72 = rows.filter({ hasText: 'Mixer cut sheet 72' })
  await expect(row72.locator('[data-test="drop-name"]')).toContainText('1.2.72_Datasheet-Mixer-20260226-V1.pdf')
  await expect(row72.locator('[data-test="drop-level"]')).toHaveText('高')
  await expect(rows.filter({ hasText: 'Project specification' }).locator('[data-test="drop-name"]')).toContainText('1.1_Spec-Project specification-')
  const notes = rows.filter({ hasText: 'random notes' })
  await expect(notes.locator('[data-test="drop-name"]')).toContainText('待归类')
  await expect(notes.locator('[data-test="drop-name"]')).toContainText('random notes.pdf')

  await page.locator('[data-test="drop-store"]').click()
  await expect(page.locator('[data-test="drop-summary"]')).toContainText('已存入 3 个，其中待归类 1 个')
  await expect.poll(async () => (await items()).find((i) => i.equipment_tag === '72')?.file_count).toBe(1)

  // ---------------- 项目列表显示待归类数，点进去是待归类页
  await page.goto('/projects')
  const count = page.locator('tr').filter({ hasText: p.code }).locator('[data-test="unclassified-count"]')
  await expect(count).toHaveText('1')
  await count.click()
  await expect(page).toHaveURL(/drop\?tab=unclassified/)

  // ---------------- 归类到 73
  const list = page.locator('[data-test="unclassified-list"]')
  await expect(list).toContainText('random notes.pdf')
  await list.locator('[data-test="classify"]').click()
  const dialog = page.locator('[data-test="classify-dialog"]')
  await dialog.locator('[data-test="classify-target"]').click()
  await page.getByRole('option', { name: '1.2 Datasheet / 73 Mixer' }).click()
  await dialog.locator('[data-test="classify-save"]').click()
  await expect(page.getByText('没有待归类的文件')).toBeVisible()
  await expect.poll(async () => (await items()).find((i) => i.equipment_tag === '73')?.file_count).toBe(1)

  // ---------------- 客户退回的带章文件：认出传送单和审核代码，同时记客户回复
  const ds72 = (await items()).find((i) => i.equipment_tag === '72')!
  const tr = await api<{ number: string }>(request, 'POST', `${base}/transmittals`, { deliverable_ids: [ds72.id] })
  await page.locator('.el-radio-button').filter({ hasText: '投递' }).click()
  await page.locator('input[data-test="drop-input"]').setInputFiles([pdf(`${tr.number} Mixer cut sheet 72 Code 1.pdf`, 'stamped')])
  const back = page.locator('[data-test="drop-row"]').filter({ hasText: tr.number })
  await expect(back.locator('[data-test="drop-name"]')).toContainText(`${tr.number} Mixer cut sheet 72 Code 1.pdf`) // 客户文件保持原名
  await expect(back.locator('[data-test="drop-record-reply"]')).toContainText(tr.number)
  await expect(back.locator('[data-test="drop-record-reply"] input')).toBeChecked()
  await page.locator('[data-test="drop-store"]').click()
  await expect(page.locator('[data-test="drop-summary"]')).toContainText('记客户回复 1 个')
  await expect.poll(async () => (await items()).find((i) => i.id === ds72.id)?.status).toBe('approved')
})
