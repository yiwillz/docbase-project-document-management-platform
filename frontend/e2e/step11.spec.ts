// 第 11 步主流程：应交矩阵上传 → 预览 → 存入 → 再传升版 → 重复拦截 → 几台设备一个文件 → 版本历史与下载 → 命名模板
import { expect, test } from '@playwright/test'

import { api, asUser, newProject } from './helpers'

interface Item {
  id: number
  submittal_no: string
  equipment_tag: string | null
  file_count: number
  current_version: number | null
}

const pdf = (name: string, text: string) => ({ name, mimeType: 'application/pdf', buffer: Buffer.from(text) })

test('upload, version, duplicate, multi-equipment, download, naming templates', async ({ page, request }) => {
  const p = await newProject(request, 'E2E11')
  const base = `/api/projects/${p.id}`
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  const [, datasheet] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Datasheet' }],
  })
  for (const e of [{ tag: '72', name: 'Mixer', vendor: 'Vendor A' }, { tag: '73', name: 'Mixer' }]) {
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
  const list = page.locator('[data-test="matrix-list"]')
  const dialog = page.locator('[data-test="upload-dialog"]')
  const specRow = list.locator('tr').filter({ hasText: '整个种类' })

  // ---------------- 整个种类：上传 → 预览文件名 → 存入
  await specRow.locator('[data-test="upload-item"]').click()
  await dialog.locator('input[data-test="upload-input"]').setInputFiles(pdf('Area A 20260226.pdf', 'a1'))
  await expect(dialog.locator('[data-test="upload-name"]')).toContainText('1.1_Spec-Area A-20260226-V1.pdf')
  await expect(dialog.locator('[data-test="upload-name"]')).toContainText('新文件 V1')
  await dialog.locator('[data-test="upload-confirm"]').click()
  await expect(dialog).toBeHidden()
  await expect(specRow.locator('[data-test="item-files"]')).toHaveText('已收到 V1')

  // ---------------- 同标题再传：升版到 V2
  await specRow.locator('[data-test="upload-item"]').click()
  await dialog.locator('input[data-test="upload-input"]').setInputFiles(pdf('area_a V2 20260301.pdf', 'a2'))
  await expect(dialog.locator('[data-test="upload-name"]')).toContainText('升版到 V2')
  await expect(dialog.locator('[data-test="upload-name"]')).toContainText('1.1_Spec-Area A-20260301-V2.pdf')
  await dialog.locator('[data-test="upload-confirm"]').click()
  await expect(specRow.locator('[data-test="item-files"]')).toHaveText('已收到 V2')

  // ---------------- 内容完全相同：拦截，不能存
  const alphaRow = list.locator('tr').filter({ hasText: '72 Mixer' })
  await alphaRow.locator('[data-test="upload-item"]').click()
  await dialog.locator('input[data-test="upload-input"]').setInputFiles(pdf('copy.pdf', 'a2'))
  await expect(dialog.locator('[data-test="upload-duplicate"]')).toContainText('1.1_Spec-Area A-20260301-V2.pdf')
  await expect(dialog.locator('[data-test="upload-confirm"]')).toBeDisabled()
  await dialog.getByRole('button', { name: '关闭', exact: true }).click()

  // ---------------- 勾选 72、73 → 一个文件挂两台设备
  for (const tag of ['72', '73']) {
    await list.locator('tr').filter({ hasText: `${tag} Mixer` }).locator('.el-checkbox').click()
  }
  await page.locator('[data-test="upload-selected"]').click()
  await dialog.locator('input[data-test="upload-input"]').setInputFiles(pdf('mixer datasheet 2026-02-26.pdf', 'm1'))
  await expect(dialog.locator('[data-test="upload-name"]')).toContainText('1.2.72&73_Datasheet-Mixer-20260226-V1.pdf')
  await dialog.locator('[data-test="upload-confirm"]').click()
  await expect
    .poll(async () => (await items()).filter((i) => i.submittal_no === '1.2').map((i) => i.file_count))
    .toEqual([1, 1])

  // ---------------- 版本历史与下载
  await specRow.locator('[data-test="item-files"]').click()
  const drawer = page.locator('[data-test="files-drawer"]')
  await expect(drawer.locator('[data-test="file-card"]')).toContainText('V2')
  await expect(drawer.locator('[data-test="file-card"]')).toContainText('原文件名：area_a V2 20260301.pdf')
  const downloaded = page.waitForEvent('download')
  await drawer.locator('[data-test="version-download"]').first().click()
  expect((await downloaded).suggestedFilename()).toBe('1.1_Spec-Area A-20260301-V2.pdf')
  await page.keyboard.press('Escape')

  // ---------------- 命名模板：写错（少了 {version}）马上提示；改对后保存，显示的文件名也变
  await page.goto(`/projects/${p.id}/settings`)
  await page.locator('[data-test="tab-naming"]').click()
  await expect(page.locator('[data-test="naming-help"]')).toContainText('{version}')
  await page.locator('input[data-test="naming-general"]').fill('{no}-{title}')
  await expect(page.locator('[data-test="naming-error"]')).toBeVisible()
  await expect(page.locator('[data-test="naming-save"]')).toBeDisabled()
  await page.locator('input[data-test="naming-general"]').fill('{no}-{title}-V{version}')
  await expect(page.locator('[data-test="naming-error"]')).toBeHidden()
  await page.locator('[data-test="naming-save"]').click()
  await expect(page.getByText('已保存')).toBeVisible()
  const spec = (await items()).find((i) => i.submittal_no === '1.1')!
  const files = await api<{ versions: { filename: string }[] }[]>(request, 'GET', `${base}/deliverables/${spec.id}/files`)
  expect(files[0]!.versions[0]!.filename).toBe('1.1-Area A-V2.pdf')
})
