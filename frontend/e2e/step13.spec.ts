// 第 13 步主流程：文件浏览三种视图 → 点节点 → 关键字 → 文件详情（预览、操作记录）→ 重新归类 → 上传到此处 → 回收区
import { type APIRequestContext, expect, test } from '@playwright/test'

import { API as DEFAULT_API } from '../playwright.config'
import { USER, api, asUser, newProject } from './helpers'

const API = process.env.E2E_API ?? DEFAULT_API
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64')

interface Item {
  id: number
  submittal_no: string
  equipment_tag: string | null
  module_name: string | null
}

async function store(request: APIRequestContext, base: string, ids: number[], name: string, body: Buffer | string) {
  const resp = await request.post(`${API}${base}/files/preview`, {
    headers: { 'X-User': USER },
    multipart: { deliverable_ids: ids.join(','), file: { name, mimeType: 'application/octet-stream', buffer: Buffer.from(body) } },
  })
  expect(resp.ok(), await resp.text()).toBeTruthy()
  const { token } = (await resp.json()) as { token: string }
  return api<{ file_id: number }>(request, 'POST', `${base}/files/uploads/${token}/confirm`, {})
}

test('browse files: views, search, detail, preview, move, upload here, recycle', async ({ page, request }) => {
  const p = await newProject(request, 'E2E13')
  const base = `/api/projects/${p.id}`
  const alpha = await api<{ id: number }>(request, 'POST', `${base}/equipment-types`, { name: 'Alpha' })
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [, ds, layout] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Datasheet' }, { name: 'Layout' }],
  })
  await api(request, 'POST', `${base}/equipment`, { tag: '72', name: 'Mixer', vendor: 'Vendor A', equipment_type_id: alpha.id })
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, { document_type_ids: [ds.id], all_equipment: true, mode: 'add' })
  await api(request, 'PUT', `${base}/document-types/${layout.id}/modules`, { module_names: ['A', 'B'] })
  const items = (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items
  const id = (pred: (i: Item) => boolean) => items.find(pred)!.id
  const spec = id((i) => i.submittal_no === '1.1')
  const d72 = id((i) => i.equipment_tag === '72')
  const layoutA = id((i) => i.module_name === 'A')
  const layoutB = id((i) => i.module_name === 'B')
  await store(request, base, [spec], 'Area A.png', PNG)
  await store(request, base, [d72], 'mixer datasheet.pdf', '%PDF-1.4 fake')
  const dwg = await store(request, base, [layoutA], 'Alpha bench.dwg', 'dwg')

  await asUser(page)
  await page.goto(`/projects/${p.id}/files`)
  const tree = page.locator('[data-test="file-tree"]')
  const list = page.locator('[data-test="files-list"]')
  await expect(list.locator('[data-test="open-file"]')).toHaveCount(3)

  // ---------------- 里程碑视图：展开 M1，点 1.1 Spec
  await tree.locator('.el-tree-node__content').filter({ hasText: 'M1 Design' }).locator('.el-tree-node__expand-icon').click()
  await tree.locator('.el-tree-node__content').filter({ hasText: '1.1 Spec' }).click()
  await expect(page).toHaveURL(new RegExp(`node=x(%3A|:)${spec}`))
  await expect(list.locator('[data-test="open-file"]')).toHaveCount(1)
  await expect(list).toContainText('1.1_Spec-Area A-')

  // ---------------- 设备视图：设备种类 → 设备 → 种类
  await page.locator('[data-test="view-equipment"]').click()
  await expect(tree).toContainText('Alpha')
  await expect(tree).not.toContainText('1.1 Spec') // 不对应设备的不在这个视图里
  await page.goto(`/projects/${p.id}/files?view=equipment&node=x:${d72}`)
  await expect(list).toContainText('1.2.72_Datasheet-Mixer-')

  // ---------------- 关键字（原文件名）
  await page.goto(`/projects/${p.id}/files`)
  await page.locator('input[data-test="files-search"]').fill('alpha bench')
  await page.locator('input[data-test="files-search"]').press('Enter')
  await expect(page).toHaveURL(/q=alpha/)
  await expect(list.locator('[data-test="open-file"]')).toHaveCount(1)

  // ---------------- 文件详情：图片预览、操作记录
  await page.goto(`/projects/${p.id}/files?node=x:${spec}`)
  await list.locator('[data-test="open-file"]').click()
  const detail = page.locator('[data-test="file-detail"]')
  await expect(detail.locator('[data-test="detail-preview"] img')).toHaveAttribute('src', /\/preview$/)
  await detail.locator('[data-test="detail-logs-tab"]').click()
  await expect(detail.locator('[data-test="detail-logs"]')).toContainText('新建')
  await page.keyboard.press('Escape')

  // ---------------- 重新归类：Layout A 的图纸移到 B
  await page.goto(`/projects/${p.id}/files?node=x:${layoutA}`)
  await list.locator('[data-test="open-file"]').click()
  await expect(detail.locator('[data-test="detail-preview"]')).toContainText('不能在线预览')
  await detail.locator('[data-test="detail-move"]').click()
  const move = page.locator('[data-test="move-dialog"]')
  await move.locator('[data-test="move-target"]').click()
  await page.getByRole('option', { name: '1.3 Layout / B', exact: true }).click()
  await move.locator('[data-test="move-save"]').click()
  await expect(page.getByText('已移到新位置')).toBeVisible()
  const moved = await api<{ deliverable_ids: number[] }>(request, 'GET', `${base}/files/${dwg.file_id}`)
  expect(moved.deliverable_ids).toEqual([layoutB])
  await page.keyboard.press('Escape')

  // ---------------- 上传到此处：Layout A 现在空了
  await page.goto(`/projects/${p.id}/files?node=x:${layoutA}`)
  await expect(page.getByText('这里没有文件')).toBeVisible()
  await page.locator('[data-test="upload-here"]').click()
  const upload = page.locator('[data-test="upload-dialog"]')
  await upload.locator('input[data-test="upload-input"]').setInputFiles({ name: 'Alpha area plan.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF plan') })
  await expect(upload.locator('[data-test="upload-name"]')).toContainText('1.3_Layout-Alpha area plan-')
  await upload.locator('[data-test="upload-confirm"]').click()
  await expect(list.locator('[data-test="open-file"]')).toHaveCount(1)

  // ---------------- 回收区：72 传到第 4 版，V1 进回收区
  for (const n of [2, 3, 4]) await store(request, base, [d72], 'mixer datasheet.pdf', `%PDF-1.4 v${n}`)
  await page.goto(`/projects/${p.id}/files`)
  await tree.locator('.el-tree-node__content').filter({ hasText: '回收区' }).click()
  const recycle = page.locator('[data-test="recycle-list"]')
  await expect(recycle).toContainText('1.2.72_Datasheet-Mixer-')
  await expect(recycle).toContainText('还剩 90 天')
})
