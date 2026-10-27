// 第 16 步主流程：没登录跳到登录页 → 密码错误提示 → 只读成员登录后看不到修改按钮和管理员菜单 → 退出；
// 系统管理员在"用户管理"建用户，再在项目"设置 → 成员"里把他加成成员
import { expect, test } from '@playwright/test'

import { api, asUser, ensureUser, newProject } from './helpers'

const stamp = Date.now().toString().slice(-6)

test('login, read-only member, logout', async ({ page, request }) => {
  const p = await newProject(request, 'E2E16')
  const base = `/api/projects/${p.id}`
  const m1 = await api<{ id: number }>(request, 'POST', `${base}/milestones`, { name: 'Design' })
  await api(request, 'POST', `${base}/document-types/bulk`, { milestone_id: m1.id, rows: [{ name: 'Spec' }] })
  const viewer = `e2e.viewer${stamp}`
  const viewerId = await ensureUser(request, viewer, 'viewer-pass-1')
  await api(request, 'PUT', `${base}/members/${viewerId}`, { role: 'readonly' })

  // ---------------- 没登录：跳到登录页，登录后回到原来要去的页面
  await page.goto(`/projects/${p.id}/matrix`)
  await expect(page).toHaveURL(/\/login\?next=/)
  await page.locator('[data-test="login-username"]').fill(viewer)
  await page.locator('[data-test="login-password"]').fill('wrong-password')
  await page.locator('[data-test="login-submit"]').click()
  await expect(page.locator('[data-test="login-error"]')).toContainText('用户名或密码不对')
  await page.locator('[data-test="login-password"]').fill('viewer-pass-1')
  await page.locator('[data-test="login-submit"]').click()
  await expect(page).toHaveURL(new RegExp(`/projects/${p.id}/matrix`))

  // ---------------- 只读：顶部提示；没有存量导入菜单、没有编辑和导出按钮
  await expect(page.getByText('没有修改这一页的权限')).toBeVisible()
  await expect(page.locator('.aside')).not.toContainText('存量导入')
  await expect(page.locator('.aside')).toContainText('文件浏览')
  await expect(page.locator('[data-test="edit-item"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '缺件清单' })).toHaveCount(0)
  await page.goto(`/projects/${p.id}/settings`)
  await page.locator('[data-test="tab-members"]').click()
  await expect(page.locator('[data-test="member-table"]')).toContainText(viewer)
  await expect(page.locator('[data-test="member-add"]')).toHaveCount(0)
  // 用户管理只给系统管理员：直接输地址也回到项目列表
  await page.goto('/admin/users')
  await expect(page).toHaveURL(/\/projects$/)

  // ---------------- 退出：回到登录页，再打开页面又要登录
  await page.locator('[data-test="user-menu"]').click()
  await expect(page.locator('[data-test="menu-users"]')).toHaveCount(0)
  await page.locator('[data-test="menu-logout"]').click()
  await expect(page).toHaveURL(/\/login/)
  await page.goto('/projects')
  await expect(page).toHaveURL(/\/login/)
})

test('sysadmin creates a user and adds them as a member', async ({ page, request }) => {
  const p = await newProject(request, 'E2E16B')
  await asUser(page)
  await page.goto('/projects')
  await page.locator('[data-test="user-menu"]').click()
  await page.locator('[data-test="menu-users"]').click()
  await expect(page).toHaveURL(/\/admin\/users/)

  // ---------------- 建用户
  const name = `e2e.new${stamp}`
  await page.locator('[data-test="user-create"]').click()
  await page.locator('[data-test="user-username"]').fill(name)
  await page.locator('[data-test="user-password"]').fill('short')
  await page.locator('[data-test="user-display"]').fill('New Engineer')
  await page.locator('[data-test="user-save"]').click()
  await expect(page.locator('.el-message--error')).toContainText('密码至少 8 位') // 后端拒绝太短的密码
  await page.locator('[data-test="user-password"]').fill('long-enough-1')
  await page.locator('[data-test="user-save"]').click()
  await expect(page.locator('[data-test="user-table"]')).toContainText(name)

  // ---------------- 加成员：选人 + 角色
  await page.goto(`/projects/${p.id}/settings`)
  await page.locator('[data-test="tab-members"]').click()
  await page.locator('[data-test="member-add"]').click()
  await page.locator('[data-test="member-users"]').click()
  await page.locator('.el-select-dropdown__item:visible').filter({ hasText: name }).click()
  await page.keyboard.press('Escape')
  await page.locator('[data-test="member-save"]').click()
  await expect(page.locator('[data-test="member-table"]')).toContainText(name)
  const members = await api<{ username: string; role: string }[]>(request, 'GET', `/api/projects/${p.id}/members`)
  expect(members.find((m) => m.username === name)?.role).toBe('member')
})
