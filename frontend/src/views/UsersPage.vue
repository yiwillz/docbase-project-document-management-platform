<script setup lang="ts">
// 用户管理（只给系统管理员）：新建用户、改姓名 / 邮箱、设为系统管理员、停用 / 启用、重设密码
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'

import { type UserRow, authApi } from '@/api/auth'
import { showError } from '@/api/errors'
import { useUserStore } from '@/stores/user'

const { t, d } = useI18n()
const me = useUserStore()

const rows = ref<UserRow[]>([])
const loading = ref(false)
const showInactive = ref(false)
const visible = computed(() => rows.value.filter((u) => showInactive.value || u.active))

async function load(): Promise<void> {
  loading.value = true
  try {
    rows.value = await authApi.users()
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

function replace(row: UserRow): void {
  const i = rows.value.findIndex((u) => u.id === row.id)
  if (i >= 0) rows.value.splice(i, 1, row)
  else rows.value.push(row)
}

// ---------------------------------------------------------------- 新建 / 编辑
const edit = reactive({
  open: false,
  id: null as number | null,
  username: '',
  display_name: '',
  email: '',
  password: '',
  is_sysadmin: false,
  active: true,
  saving: false,
})

function openCreate(): void {
  Object.assign(edit, { open: true, id: null, username: '', display_name: '', email: '', password: '', is_sysadmin: false, active: true })
}

function openEdit(row: UserRow): void {
  Object.assign(edit, {
    open: true,
    id: row.id,
    username: row.username,
    display_name: row.display_name ?? '',
    email: row.email ?? '',
    password: '',
    is_sysadmin: row.is_sysadmin,
    active: row.active,
  })
}

async function save(): Promise<void> {
  edit.saving = true
  const text = (v: string) => v.trim() || undefined
  try {
    if (edit.id === null) {
      replace(
        await authApi.createUser({
          username: edit.username.trim(),
          password: edit.password,
          display_name: text(edit.display_name),
          email: text(edit.email),
          is_sysadmin: edit.is_sysadmin,
        }),
      )
      ElMessage.success(t('users.created'))
    } else {
      replace(
        await authApi.updateUser(edit.id, {
          display_name: edit.display_name.trim() || null,
          email: edit.email.trim() || null,
          is_sysadmin: edit.is_sysadmin,
          active: edit.active,
        }),
      )
      ElMessage.success(t('common.saved'))
    }
    edit.open = false
  } catch (err) {
    showError(err)
  } finally {
    edit.saving = false
  }
}

// ---------------------------------------------------------------- 重设密码
const reset = reactive({ open: false, user: null as UserRow | null, password: '', saving: false })

function openReset(row: UserRow): void {
  Object.assign(reset, { open: true, user: row, password: '' })
}

async function saveReset(): Promise<void> {
  if (!reset.user) return
  reset.saving = true
  try {
    replace(await authApi.resetPassword(reset.user.id, reset.password))
    ElMessage.success(t('users.passwordReset'))
    reset.open = false
  } catch (err) {
    showError(err)
  } finally {
    reset.saving = false
  }
}

onMounted(load)
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <h2>{{ t('users.title') }}</h2>
        <el-checkbox v-model="showInactive">{{ t('users.showInactive') }}</el-checkbox>
        <div class="spacer" />
        <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
        <el-button type="primary" :icon="Plus" data-test="user-create" @click="openCreate">{{ t('users.create') }}</el-button>
      </div>
    </template>

    <el-table v-loading="loading" :data="visible" row-key="id" data-test="user-table">
      <el-table-column prop="username" :label="t('users.username')" min-width="140" />
      <el-table-column :label="t('users.displayName')" min-width="140">
        <template #default="{ row }">{{ row.display_name ?? '' }}</template>
      </el-table-column>
      <el-table-column :label="t('users.email')" min-width="180">
        <template #default="{ row }">{{ row.email ?? '' }}</template>
      </el-table-column>
      <el-table-column :label="t('users.state')" width="190">
        <template #default="{ row }">
          <el-tag v-if="row.is_sysadmin" size="small" type="warning" class="mr">{{ t('users.sysadmin') }}</el-tag>
          <el-tag v-if="!row.active" size="small" type="info" class="mr">{{ t('users.inactive') }}</el-tag>
          <el-tag v-if="row.locked" size="small" type="danger">{{ t('users.locked') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('users.lastLogin')" width="200">
        <template #default="{ row }">{{ row.last_login_at ? d(new Date(row.last_login_at), 'long') : '—' }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="220" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" data-test="user-edit" @click="openEdit(row as UserRow)">{{ t('common.edit') }}</el-button>
          <el-button link type="primary" data-test="user-reset" @click="openReset(row as UserRow)">{{ t('users.resetPassword') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="muted">{{ t('users.hint') }}</p>

    <el-dialog v-model="edit.open" :title="edit.id === null ? t('users.create') : t('users.edit')" width="460px">
      <el-form label-width="110px" @submit.prevent="save">
        <el-form-item :label="t('users.username')">
          <el-input v-model="edit.username" :disabled="edit.id !== null" data-test="user-username" />
        </el-form-item>
        <el-form-item v-if="edit.id === null" :label="t('users.password')">
          <el-input v-model="edit.password" type="password" show-password data-test="user-password" />
        </el-form-item>
        <el-form-item :label="t('users.displayName')"><el-input v-model="edit.display_name" data-test="user-display" /></el-form-item>
        <el-form-item :label="t('users.email')"><el-input v-model="edit.email" /></el-form-item>
        <el-form-item :label="t('users.sysadmin')">
          <el-switch v-model="edit.is_sysadmin" :disabled="edit.id === me.me?.id" />
          <span class="muted ml">{{ t('users.sysadminHint') }}</span>
        </el-form-item>
        <el-form-item v-if="edit.id !== null" :label="t('users.active')">
          <el-switch v-model="edit.active" :disabled="edit.id === me.me?.id" />
          <span class="muted ml">{{ t('users.activeHint') }}</span>
        </el-form-item>
      </el-form>
      <p v-if="edit.id === null" class="muted">{{ t('user.passwordHint') }}</p>
      <template #footer>
        <el-button @click="edit.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :loading="edit.saving"
          :disabled="!edit.username.trim() || (edit.id === null && !edit.password)"
          data-test="user-save"
          @click="save"
        >
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="reset.open" :title="t('users.resetPassword')" width="420px">
      <p>{{ t('users.resetFor', { name: reset.user?.username ?? '' }) }}</p>
      <el-input v-model="reset.password" type="password" show-password :placeholder="t('users.newPassword')" />
      <p class="muted">{{ t('users.resetHint') }}</p>
      <template #footer>
        <el-button @click="reset.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="reset.saving" :disabled="!reset.password" @click="saveReset">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.spacer {
  flex: 1;
}
.mr {
  margin-right: 4px;
}
.ml {
  margin-left: 8px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
