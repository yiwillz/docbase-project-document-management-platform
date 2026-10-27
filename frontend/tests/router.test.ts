// 路由：项目内页面能匹配上（第 9 步曾因路由写法不被新版 vue-router 支持，进入项目显示"页面不存在"）
import { describe, expect, it, vi } from 'vitest'

import { PROJECT_PAGES, router } from '@/router'

// 只测路由匹配，不需要真正的布局和页面
vi.mock('@/layouts/AppLayout.vue', () => ({ default: { template: '<router-view />' } }))
vi.mock('@/views/Placeholder.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/NotFound.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/ProjectList.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/StructurePage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/EquipmentPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/SettingsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/MatrixPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/MdlPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/ChangesPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/TransmittalsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/CloseoutPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/DropPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/FilesPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/ImportsPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/DeliveryPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/LoginPage.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/views/UsersPage.vue', () => ({ default: { template: '<div />' } }))
// 当作已经登录的用户（默认系统管理员）；项目只有 152
const mock = vi.hoisted(() => ({ perms: null as string[] | null }))
vi.mock('@/stores/user', () => ({
  useUserStore: () => ({
    loaded: true,
    me: { id: 1 },
    isSysadmin: mock.perms === null,
    load: async () => ({ id: 1 }),
    can: (_pid: number, perm: string) => mock.perms === null || mock.perms.includes(perm),
  }),
}))
vi.mock('@/stores/projects', () => ({
  useProjectStore: () => ({ loaded: true, projects: [{ id: 152 }], load: async () => undefined }),
}))

describe('router', () => {
  it('matches every project page', () => {
    for (const page of PROJECT_PAGES) {
      const route = router.resolve(`/projects/152/${page.name}`)
      expect(route.name).toBe(page.name)
      expect(route.params.projectId).toBe('152')
    }
  })

  it('redirects project root to the matrix', async () => {
    await router.push('/projects/152')
    expect(router.currentRoute.value.fullPath).toBe('/projects/152/matrix')
  })

  it('sends unknown projects back to the list and pages without permission to the matrix', async () => {
    await router.push('/projects/999/files')
    expect(router.currentRoute.value.name).toBe('projects')
    mock.perms = ['file.read', 'history.read'] // 只读成员
    try {
      await router.push('/projects/152/imports')
      expect(router.currentRoute.value.fullPath).toBe('/projects/152/matrix')
      await router.push('/projects/152/files')
      expect(router.currentRoute.value.name).toBe('files')
    } finally {
      mock.perms = null
    }
  })

  it('treats non-numeric project ids and unknown paths as not found', async () => {
    await router.push('/projects/abc/matrix')
    expect(router.currentRoute.value.name).toBe('not-found')
    expect(router.resolve('/nothing/here').name).toBe('not-found')
  })
})
