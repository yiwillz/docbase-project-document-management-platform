<script setup lang="ts">
// 整体布局：顶栏（产品名、项目切换、语言、当前用户）+ 左侧菜单（进入项目后才有）+ 内容区
import {
  Box,
  CircleCheck,
  Clock,
  Cpu,
  Document,
  Folder,
  Grid,
  Operation,
  Promotion,
  Setting,
  Suitcase,
  Upload,
  User,
} from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { authApi } from '@/api/auth'
import { showError } from '@/api/errors'
import { type SystemStatus, systemApi } from '@/api/system'
import { LOCALES, type Locale, setLocale } from '@/i18n'
import { PROJECT_PAGES } from '@/router'
import { useProjectStore } from '@/stores/projects'
import { useUserStore } from '@/stores/user'

const { t, d, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const projects = useProjectStore()
const user = useUserStore()

const ICONS = { Grid, Folder, Upload, Box, Suitcase, Operation, Document, Promotion, CircleCheck, Cpu, Clock, Setting }

const projectId = computed(() => (route.params.projectId ? Number(route.params.projectId) : null))
// 没有权限的菜单不显示（如存量导入只给管理员）
const menuPages = computed(() => PROJECT_PAGES.filter((p) => projectId.value !== null && user.can(projectId.value, p.permission)))
const activeProjects = computed(() => projects.projects.filter((p) => !p.archived_at || p.id === projectId.value))

watch(projectId, (id) => (projects.currentId = id), { immediate: true })

function switchProject(id: number): void {
  // 换项目时停留在同一个功能页
  const page = PROJECT_PAGES.some((p) => p.name === route.name) ? String(route.name) : 'matrix'
  router.push(`/projects/${id}/${page}`)
}

function changeLocale(value: Locale): void {
  setLocale(value)
}

// 当前用户：修改密码、退出登录；系统管理员多一个"用户管理"
const pwd = reactive({ open: false, old: '', next: '', again: '', saving: false })

function onUserCommand(command: string): void {
  if (command === 'users') router.push({ name: 'users' })
  else if (command === 'password') Object.assign(pwd, { open: true, old: '', next: '', again: '' })
  else if (command === 'logout') logout()
}

async function savePassword(): Promise<void> {
  if (pwd.next !== pwd.again) {
    ElMessage.error(t('user.passwordMismatch'))
    return
  }
  pwd.saving = true
  try {
    await authApi.changePassword(pwd.old, pwd.next)
    ElMessage.success(t('user.passwordChanged'))
    pwd.open = false
  } catch (err) {
    showError(err)
  } finally {
    pwd.saving = false
  }
}

async function logout(): Promise<void> {
  await user.logout()
  projects.projects = []
  projects.loaded = false
  router.push({ name: 'login' })
}

async function loadProjects(): Promise<void> {
  try {
    await projects.load()
  } catch (err) {
    showError(err)
  }
}

// 系统状态：文件存储断开（所有人看到）、备份异常（只有系统管理员看到）；每分钟查一次
const sys = ref<SystemStatus | null>(null)
let statusTimer: ReturnType<typeof setInterval> | undefined

async function loadStatus(): Promise<void> {
  try {
    sys.value = await systemApi.status()
  } catch {
    // 查不到就不提醒（后端断开时页面别处会报错）
  }
}

const backupWarning = computed(() => {
  const b = sys.value?.backup
  if (!b || b.ok) return ''
  if (!b.configured) return t('system.backupOff')
  const last = b.last_ok ? d(new Date(b.last_ok), 'long') : t('system.never')
  return b.error ? t('system.backupFailed', { last, error: b.error }) : t('system.backupStale', { last })
})

onMounted(() => {
  document.documentElement.lang = locale.value
  if (user.me) loadProjects()
  loadStatus()
  statusTimer = setInterval(loadStatus, 60_000)
})
onUnmounted(() => clearInterval(statusTimer))
</script>

<template>
  <el-container class="layout">
    <el-header class="header">
      <router-link to="/projects" class="brand">
        <img src="/favicon.svg" alt="" class="logo" />
        <span class="name">{{ t('app.name') }}</span>
        <span class="title">{{ t('app.title') }}</span>
      </router-link>

      <el-select
        v-if="projects.loaded"
        :model-value="projectId ?? undefined"
        :placeholder="t('header.selectProject')"
        filterable
        class="project-select"
        @change="switchProject"
      >
        <el-option
          v-for="p in activeProjects"
          :key="p.id"
          :value="p.id"
          :label="`${p.code}  ${p.name}`"
        />
      </el-select>

      <div class="spacer" />

      <el-select :model-value="locale" class="lang-select" @change="changeLocale">
        <el-option v-for="l in LOCALES" :key="l" :value="l" :label="t(`lang.${l}`)" />
      </el-select>
      <el-dropdown trigger="click" @command="onUserCommand">
        <el-button text class="user" data-test="user-menu">
          <el-icon><User /></el-icon>
          <span>{{ user.name || '—' }}</span>
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-if="user.isSysadmin" command="users" data-test="menu-users">{{ t('user.manage') }}</el-dropdown-item>
            <el-dropdown-item command="password">{{ t('user.changePassword') }}</el-dropdown-item>
            <el-dropdown-item command="logout" divided data-test="menu-logout">{{ t('user.logout') }}</el-dropdown-item>
            <el-dropdown-item v-if="sys" disabled divided>{{ t('system.version', { v: sys.version }) }}</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </el-header>

    <el-container class="body">
      <el-aside v-if="projectId" width="200px" class="aside">
        <el-menu :default-active="String(route.name)" router class="menu">
          <el-menu-item
            v-for="page in menuPages"
            :key="page.name"
            :index="page.name"
            :route="`/projects/${projectId}/${page.name}`"
          >
            <el-icon><component :is="ICONS[page.icon]" /></el-icon>
            <span>{{ t(`menu.${page.name}`) }}</span>
          </el-menu-item>
        </el-menu>
      </el-aside>
      <el-main class="main">
        <el-alert
          v-if="sys && !sys.storage.ok"
          type="error"
          :closable="false"
          show-icon
          class="sys-alert"
          :title="t('system.storageDown')"
          :description="sys.storage.error ?? ''"
          data-test="storage-alert"
        />
        <el-alert v-if="backupWarning" type="warning" show-icon class="sys-alert" :title="backupWarning" data-test="backup-alert" />
        <router-view />
      </el-main>
    </el-container>

    <el-dialog v-model="pwd.open" :title="t('user.changePassword')" width="420px">
      <el-form label-width="100px" @submit.prevent="savePassword">
        <el-form-item :label="t('user.oldPassword')"><el-input v-model="pwd.old" type="password" show-password /></el-form-item>
        <el-form-item :label="t('user.newPassword')"><el-input v-model="pwd.next" type="password" show-password /></el-form-item>
        <el-form-item :label="t('user.againPassword')"><el-input v-model="pwd.again" type="password" show-password /></el-form-item>
      </el-form>
      <p class="hint">{{ t('user.passwordHint') }}</p>
      <template #footer>
        <el-button @click="pwd.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="pwd.saving" :disabled="!pwd.old || !pwd.next" @click="savePassword">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<style scoped>
.sys-alert {
  margin-bottom: 12px;
}
.layout {
  height: 100vh;
}
.header {
  display: flex;
  align-items: center;
  gap: 16px;
  background: var(--brand-navy);
  color: #fff;
  padding: 0 16px;
}
.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
  color: #fff;
  text-decoration: none;
  white-space: nowrap;
}
.logo {
  width: 22px;
  height: 22px;
  align-self: center;
}
.name {
  font-weight: 700;
  font-size: 18px;
}
.title {
  font-size: 13px;
  opacity: 0.8;
}
.project-select {
  width: 280px;
}
.lang-select {
  width: 110px;
}
.spacer {
  flex: 1;
}
.user {
  color: #fff;
}
.user span {
  margin-left: 4px;
}
.body {
  overflow: hidden;
}
.aside {
  border-right: 1px solid var(--el-border-color-light);
  background: #fff;
}
.menu {
  border-right: none;
}
.main {
  background: var(--el-bg-color-page);
}
.hint {
  color: var(--el-text-color-secondary);
  margin-top: 0;
}
</style>
