// 第 10C 步主流程：应交矩阵勾选 → 生成传送单 → 录入客户回复 → 导出 / 原样导回 → 作废 → 闭环检查、强制关闭、重新打开、导出
import { writeFile } from 'node:fs/promises'

import { expect, test } from '@playwright/test'

import { API as DEFAULT_API } from '../playwright.config'
import { USER, api, asUser, newProject } from './helpers'

const API = process.env.E2E_API ?? DEFAULT_API

interface Item {
  id: number
  submittal_no: string
  status: string
  transmittal_no: string | null
  review_code: string | null
}

interface Code {
  code: string
  label_zh: string | null
  label_en: string
  status: string
  final: boolean
}

test('transmittals, client reply and milestone closeout', async ({ page, request }, testInfo) => {
  // ---------------- 造数据：M1 两个种类（Spec 整个种类、Datasheet 按两台设备）
  const p = await newProject(request, 'E2E10C')
  const base = `/api/projects/${p.id}`
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design', due_date: '2020-01-01' })
  const [, datasheet] = await api<{ id: number }[]>(request, 'POST', `${base}/document-types/bulk`, {
    milestone_id: m1.id,
    rows: [{ name: 'Spec' }, { name: 'Datasheet' }],
  })
  for (const e of [{ tag: '71', name: 'Alpha tool' }, { tag: '72', name: 'Dryer' }]) {
    await api(request, 'POST', `${base}/equipment`, e)
  }
  await api(request, 'POST', `${base}/document-types/bulk-equipment`, {
    document_type_ids: [datasheet.id],
    all_equipment: true,
    mode: 'add',
  })
  const codes = await api<Code[]>(request, 'GET', `${base}/review-codes`)
  const approved = codes.find((c) => c.status === 'approved' && c.final)!
  const items = async () => (await api<{ items: Item[] }>(request, 'GET', `${base}/deliverables`)).items

  await asUser(page)
  await page.goto(`/projects/${p.id}/matrix`)

  // ---------------- 应交矩阵里勾选 1.1 和 Alpha tool → 生成传送单
  const list = page.locator('[data-test="matrix-list"]')
  await list.locator('tr').filter({ hasText: '整个种类' }).locator('.el-checkbox').click()
  await list.locator('tr').filter({ hasText: 'Alpha tool' }).locator('.el-checkbox').click()
  await page.locator('[data-test="make-transmittal"]').click()
  const create = page.locator('[data-test="transmittal-create"]')
  await expect(create).toContainText('应交项（2）')
  await create.locator('input[data-test="tr-subject"]').fill('Design batch 1')
  await create.locator('[data-test="tr-create"]').click()
  await expect.poll(async () => (await items()).filter((i) => i.status === 'submitted').length).toBe(2)
  const trNo = (await items()).find((i) => i.transmittal_no)!.transmittal_no!
  // 列表里的传送单号可以点进传送单详情
  await list.locator('tr').filter({ hasText: 'Alpha tool' }).getByRole('link', { name: trNo }).click()
  await expect(page).toHaveURL(/transmittals\?open=\d+/)
  const drawer = page.locator('[data-test="tr-detail"]')
  await expect(drawer).toContainText('Design batch 1')
  await expect(drawer.locator('[data-test="tr-items"]')).toContainText('Alpha tool')

  // ---------------- 导出传送单
  const exported = page.waitForEvent('download')
  await drawer.locator('[data-test="tr-export"]').click()
  expect((await exported).suggestedFilename()).toBe(`${trNo}.xlsx`)

  // ---------------- 录入客户回复：整张传送单批准
  await drawer.locator('[data-test="tr-reply"]').click()
  const reply = page.locator('[data-test="tr-reply-dialog"]')
  await reply.locator('[data-test="tr-code"]').click()
  await page.getByRole('option', { name: new RegExp(`^${approved.code} `) }).click()
  await reply.locator('.el-form-item').filter({ hasText: '回复编号' }).locator('input').fill('CL-100')
  await reply.locator('[data-test="tr-reply-save"]').click()
  await expect
    .poll(async () => (await items()).filter((i) => i.review_code === approved.code).map((i) => i.status))
    .toEqual(['approved', 'approved'])
  await expect(drawer.locator('[data-test="tr-items"]')).toContainText('CL-100')
  await page.keyboard.press('Escape')
  await expect(page).not.toHaveURL(/open=/)
  await expect(page.locator('[data-test="tr-list"] .el-tag')).toHaveText('已回复')

  // ---------------- 原样导回刚导出的传送单：预览里没有改动
  const resp = await request.get(`${API}${base}/transmittals`, { headers: { 'X-User': USER } })
  const [tr] = (await resp.json()) as { id: number }[]
  const file = testInfo.outputPath('transmittal.xlsx')
  const xlsx = await request.get(`${API}${base}/transmittals/${tr.id}/export`, { headers: { 'X-User': USER } })
  await writeFile(file, await xlsx.body())
  await page.locator('input[data-test="tr-return-file"]').setInputFiles(file)
  await expect(page.getByText('和平台里的一样，没有改动')).toBeVisible()
  await expect(page.locator('[data-test="tr-return-confirm"]')).toBeDisabled()
  await page.keyboard.press('Escape')

  // ---------------- 在传送单页自己选 Dryer 再建一张，然后作废
  await page.locator('[data-test="tr-new"]').click()
  await create.locator('[data-test="tr-picker"] tr').filter({ hasText: 'Dryer' }).locator('.el-checkbox').click()
  await create.locator('[data-test="tr-create"]').click()
  await expect(drawer).toContainText('Dryer')
  await expect.poll(async () => (await items()).find((i) => i.status === 'submitted')?.submittal_no).toBe('1.2')
  await drawer.getByRole('button', { name: '作废' }).click()
  await page.locator('.el-message-box input').fill('Sent by mistake')
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(drawer).toContainText('已作废')
  await expect.poll(async () => (await items()).filter((i) => i.status === 'submitted').length).toBe(0)

  // ---------------- 闭环检查：Dryer 还没交，不能关；强制关闭 → 重新打开
  await page.goto(`/projects/${p.id}/closeout`)
  const verdict = page.locator('[data-test="co-verdict"]')
  await expect(verdict).toContainText('还不能关闭：1 项应交项卡住')
  await expect(page.locator('[data-test="co-items"]')).toContainText('Dryer')
  await expect(page.locator('[data-test="co-items"]')).not.toContainText('Alpha tool')

  const listFile = page.waitForEvent('download')
  await page.locator('[data-test="co-export"]').click()
  await listFile
  const reqFile = page.waitForEvent('download')
  await page.locator('[data-test="co-requirements"]').click()
  await reqFile

  await page.locator('[data-test="co-close"]').click()
  const closeDialog = page.locator('[data-test="co-close-dialog"]')
  await closeDialog.locator('[data-test="co-close-save"]').click()
  await expect(page.getByText('强制关闭要写原因')).toBeVisible()
  await closeDialog.locator('input[data-test="co-reason"]').fill('Client agreed to defer Dryer datasheet')
  await closeDialog.locator('input[data-test="co-basis"]').fill('MOM-07')
  await closeDialog.locator('[data-test="co-close-save"]').click()
  await expect(verdict).toContainText('已关闭')
  await expect(verdict).toContainText('MOM-07')

  // 关闭后又改了一项 → 提醒"关闭之后又有改动"
  const dryer = (await items()).find((i) => i.status !== 'approved')!
  await api(request, 'PATCH', `${base}/deliverables/${dryer.id}`, { status: 'not_applicable', status_basis: 'Deleted by client' })
  await page.reload()
  await expect(page.locator('[data-test="co-changed"]')).toBeVisible()

  await page.locator('[data-test="co-reopen"]').click()
  await page.locator('.el-message-box input').fill('Recheck')
  await page.locator('.el-message-box').getByRole('button', { name: '确定' }).click()
  await expect(verdict).toContainText('可以关闭')
})
