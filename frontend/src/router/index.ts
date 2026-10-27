// 路由：项目内的页面都在 /projects/:projectId/ 下，刷新页面不会丢失当前项目
import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

import { ElMessage } from 'element-plus'

import { setUnauthorizedHandler } from '@/api/http'
import { i18n } from '@/i18n'
import AppLayout from '@/layouts/AppLayout.vue'
import { useProjectStore } from '@/stores/projects'
import { useUserStore } from '@/stores/user'

// 项目内菜单：路由名、图标、在哪一步做、看得到这个菜单要的权限
export const PROJECT_PAGES = [
  { name: 'matrix', icon: 'Grid', step: 10, permission: 'file.read' },
  { name: 'files', icon: 'Folder', step: 13, permission: 'file.read' },
  { name: 'drop', icon: 'Upload', step: 12, permission: 'file.write' },
  { name: 'imports', icon: 'Box', step: 14, permission: 'file.import' },
  { name: 'delivery', icon: 'Suitcase', step: 15, permission: 'file.read' },
  { name: 'structure', icon: 'Operation', step: 10, permission: 'file.read' },
  { name: 'mdl', icon: 'Document', step: 10, permission: 'file.read' },
  { name: 'transmittals', icon: 'Promotion', step: 10, permission: 'file.read' },
  { name: 'closeout', icon: 'CircleCheck', step: 10, permission: 'file.read' },
  { name: 'equipment', icon: 'Cpu', step: 10, permission: 'file.read' },
  { name: 'changes', icon: 'Clock', step: 10, permission: 'history.read' },
  { name: 'settings', icon: 'Setting', step: 10, permission: 'file.read' },
] as const

// 已经做好的功能页；其他的先显示"建设中"
const PAGE_COMPONENTS: Partial<Record<(typeof PROJECT_PAGES)[number]['name'], () => Promise<unknown>>> = {
  matrix: () => import('@/views/MatrixPage.vue'),
  mdl: () => import('@/views/MdlPage.vue'),
  changes: () => import('@/views/ChangesPage.vue'),
  transmittals: () => import('@/views/TransmittalsPage.vue'),
  closeout: () => import('@/views/CloseoutPage.vue'),
  drop: () => import('@/views/DropPage.vue'),
  files: () => import('@/views/FilesPage.vue'),
  imports: () => import('@/views/ImportsPage.vue'),
  delivery: () => import('@/views/DeliveryPage.vue'),
  structure: () => import('@/views/StructurePage.vue'),
  equipment: () => import('@/views/EquipmentPage.vue'),
  settings: () => import('@/views/SettingsPage.vue'),
}

const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/projects' },
  { path: '/login', name: 'login', component: () => import('@/views/LoginPage.vue'), meta: { public: true } },
  {
    path: '/',
    component: AppLayout,
    children: [
      { path: 'projects', name: 'projects', component: () => import('@/views/ProjectList.vue') },
      { path: 'admin/users', name: 'users', component: () => import('@/views/UsersPage.vue'), meta: { sysadmin: true } },
      ...PROJECT_PAGES.map((page) => ({
        path: `projects/:projectId/${page.name}`,
        name: page.name,
        component: PAGE_COMPONENTS[page.name] ?? (() => import('@/views/Placeholder.vue')),
        props: { step: page.step },
      })),
      { path: 'projects/:projectId', redirect: (to) => `/projects/${to.params.projectId}/matrix` },
      { path: ':pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFound.vue') },
    ],
  },
]

export const router = createRouter({ history: createWebHistory(), routes })

// 全局检查：项目编号必须是数字（不是就当作页面不存在）；没登录跳到登录页；用户管理只给系统管理员；
// 项目不存在或看不到 → 回项目列表；没有这一页要的权限（如存量导入）→ 回应交矩阵
router.beforeEach(async (to) => {
  const id = to.params.projectId
  if (id !== undefined && !/^\d+$/.test(String(id))) {
    return { name: 'not-found', params: { pathMatch: to.path.slice(1).split('/') } }
  }
  if (to.meta.public) return true
  const user = useUserStore()
  if (!user.loaded) {
    try {
      await user.load()
    } catch {
      return true // 后端连不上：照常进页面，页面上会提示
    }
  }
  if (!user.me) return { name: 'login', query: to.fullPath === '/projects' ? {} : { next: to.fullPath } }
  if (to.meta.sysadmin && !user.isSysadmin) return { name: 'projects' }
  if (id !== undefined) {
    const pid = Number(id)
    const store = useProjectStore()
    try {
      if (!store.loaded || !store.projects.some((p) => p.id === pid)) await store.load()
    } catch {
      return true // 后端连不上：照常进页面，页面上会提示
    }
    if (!store.projects.some((p) => p.id === pid)) {
      ElMessage.warning(i18n.global.t('project.unavailable', { id: pid }))
      return { name: 'projects' }
    }
    const page = PROJECT_PAGES.find((p) => p.name === to.name)
    if (page && page.name !== 'matrix' && !user.can(pid, page.permission)) {
      return { name: 'matrix', params: { projectId: String(pid) } }
    }
  }
  return true
})

// 接口返回"没登录 / 登录过期"：回到登录页，登录后回到这里
setUnauthorizedHandler(() => {
  const user = useUserStore()
  user.me = null
  const current = router.currentRoute.value
  if (current.name !== 'login') router.push({ name: 'login', query: { next: current.fullPath } })
})

// 页面代码加载失败（升级后浏览器还留着旧版本，或开发服务器刚重排了依赖）：自动刷新一次到目标页面
router.onError((err, to) => {
  const message = err instanceof Error ? err.message : String(err)
  const key = `docbase.reloaded:${to.fullPath}`
  if (/dynamically imported module|Importing a module script failed/.test(message) && !sessionStorage.getItem(key)) {
    sessionStorage.setItem(key, '1')
    window.location.assign(to.fullPath)
  }
})
