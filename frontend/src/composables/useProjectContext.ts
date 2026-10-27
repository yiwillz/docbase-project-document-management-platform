// 项目内页面共用：当前项目 id、项目信息、权限
// readonly：已归档，或者没有这个页面主要操作的权限（默认是存文件 file.write；配置类页面传 project.config）
// readonlyNotice：页面顶部的只读提示（已归档 / 没有权限），不只读时为空
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'

import { useProjectStore } from '@/stores/projects'
import { useUserStore } from '@/stores/user'

export function useProjectContext(permission = 'file.write') {
  const { t } = useI18n()
  const route = useRoute()
  const store = useProjectStore()
  const user = useUserStore()
  const projectId = computed(() => Number(route.params.projectId))
  const project = computed(() => store.projects.find((p) => p.id === projectId.value) ?? null)
  const archived = computed(() => !!project.value?.archived_at)
  const can = (code: string) => !archived.value && user.can(projectId.value, code)
  const readonly = computed(() => !can(permission))
  const readonlyNotice = computed(() => {
    if (archived.value) return t('common.readonlyArchived')
    return readonly.value ? t('common.readonlyNoPermission') : ''
  })
  return { projectId, project, readonly, readonlyNotice, archived, can }
}
