// 当前登录的用户：登录、退出、在各项目里的权限
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { type Me, authApi } from '@/api/auth'
import { ApiError } from '@/api/http'

export const useUserStore = defineStore('user', () => {
  const me = ref<Me | null>(null)
  const loaded = ref(false)

  const name = computed(() => me.value?.display_name || me.value?.username || '')
  const isSysadmin = computed(() => !!me.value?.is_sysadmin)

  /** 取当前用户；没登录返回 null（不报错）。 */
  async function load(): Promise<Me | null> {
    try {
      me.value = await authApi.me()
    } catch (err) {
      if (!(err instanceof ApiError) || err.status !== 401) throw err
      me.value = null
    } finally {
      loaded.value = true
    }
    return me.value
  }

  async function login(username: string, password: string): Promise<void> {
    me.value = await authApi.login(username, password)
    loaded.value = true
  }

  async function logout(): Promise<void> {
    try {
      await authApi.logout()
    } finally {
      me.value = null
    }
  }

  /** 在这个项目里有没有这个权限（系统管理员全部都有）。 */
  function can(projectId: number, permission: string): boolean {
    if (!me.value) return false
    if (me.value.is_sysadmin) return true
    return (me.value.projects[String(projectId)] ?? []).includes(permission)
  }

  return { me, loaded, name, isSysadmin, load, login, logout, can }
})
