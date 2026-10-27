<script setup lang="ts">
// 设置页"成员"：谁能进这个项目、是什么角色（项目管理员 / 成员 / 只读）；加成员、改角色、移出
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type Member, type Role, authApi } from '@/api/auth'
import { showError } from '@/api/errors'
import { useUserStore } from '@/stores/user'

const props = defineProps<{ projectId: number; readonly: boolean }>()
const { t, te } = useI18n()
const me = useUserStore()

const rows = ref<Member[]>([])
const roles = ref<Role[]>([])
const loading = ref(false)

// 内置角色用界面语言显示，自定义角色显示它的名称
function roleLabel(code: string, name: string): string {
  return te(`members.role.${code}`) ? t(`members.role.${code}`) : name
}

async function load(): Promise<void> {
  loading.value = true
  try {
    ;[rows.value, roles.value] = await Promise.all([authApi.members(props.projectId), authApi.roles()])
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

async function changeRole(row: Member, role: string): Promise<void> {
  try {
    rows.value = await authApi.setMember(props.projectId, row.user_id, role)
    ElMessage.success(t('common.saved'))
  } catch (err) {
    showError(err)
    await load()
  }
}

async function remove(row: Member): Promise<void> {
  try {
    await ElMessageBox.confirm(t('members.removeConfirm', { name: row.display_name || row.username }), t('members.remove'), {
      type: 'warning',
      confirmButtonText: t('common.ok'),
      cancelButtonText: t('common.cancel'),
    })
    rows.value = await authApi.removeMember(props.projectId, row.user_id)
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') showError(err)
  }
}

// ---------------------------------------------------------------- 加成员
const add = reactive({
  open: false,
  userIds: [] as number[],
  role: 'member',
  saving: false,
  candidates: [] as { id: number; username: string; display_name: string | null }[],
})

async function openAdd(): Promise<void> {
  try {
    add.candidates = await authApi.memberCandidates(props.projectId)
    Object.assign(add, { open: true, userIds: [], role: 'member' })
  } catch (err) {
    showError(err)
  }
}

async function saveAdd(): Promise<void> {
  add.saving = true
  try {
    for (const id of add.userIds) rows.value = await authApi.setMember(props.projectId, id, add.role)
    ElMessage.success(t('members.added', { n: add.userIds.length }))
    add.open = false
  } catch (err) {
    showError(err)
  } finally {
    add.saving = false
  }
}

watch(() => props.projectId, load)
onMounted(load)
</script>

<template>
  <div>
    <p class="muted">{{ t('members.hint') }}</p>
    <div v-if="!readonly" class="toolbar mb">
      <el-button type="primary" :icon="Plus" size="small" data-test="member-add" @click="openAdd">{{ t('members.add') }}</el-button>
    </div>
    <el-table v-loading="loading" :data="rows" row-key="user_id" data-test="member-table">
      <el-table-column prop="username" :label="t('users.username')" min-width="140" />
      <el-table-column :label="t('users.displayName')" min-width="140">
        <template #default="{ row }">
          {{ row.display_name ?? '' }}
          <el-tag v-if="!row.active" size="small" type="info">{{ t('users.inactive') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('members.roleLabel')" width="200">
        <template #default="{ row }">
          <el-select
            v-if="!readonly && row.user_id !== me.me?.id"
            :model-value="row.role"
            size="small"
            data-test="member-role"
            @change="(v: string) => changeRole(row as Member, v)"
          >
            <el-option v-for="r in roles" :key="r.code" :value="r.code" :label="roleLabel(r.code, r.name)" />
          </el-select>
          <span v-else>{{ roleLabel(row.role, row.role_name) }}</span>
        </template>
      </el-table-column>
      <el-table-column v-if="!readonly" width="90">
        <template #default="{ row }">
          <el-button v-if="row.user_id !== me.me?.id" link type="danger" @click="remove(row as Member)">{{ t('members.remove') }}</el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-descriptions :column="1" border size="small" class="mt">
      <el-descriptions-item v-for="r in roles" :key="r.code" :label="roleLabel(r.code, r.name)">
        {{ te(`members.roleHint.${r.code}`) ? t(`members.roleHint.${r.code}`) : '' }}
      </el-descriptions-item>
    </el-descriptions>

    <el-dialog v-model="add.open" :title="t('members.add')" width="460px">
      <el-form label-width="90px">
        <el-form-item :label="t('members.users')">
          <el-select v-model="add.userIds" multiple filterable style="width: 100%" data-test="member-users">
            <el-option
              v-for="u in add.candidates"
              :key="u.id"
              :value="u.id"
              :label="u.display_name ? `${u.display_name} (${u.username})` : u.username"
            />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('members.roleLabel')">
          <el-select v-model="add.role" data-test="member-add-role">
            <el-option v-for="r in roles" :key="r.code" :value="r.code" :label="roleLabel(r.code, r.name)" />
          </el-select>
        </el-form-item>
      </el-form>
      <p v-if="!add.candidates.length" class="muted">{{ t('members.noCandidates') }}</p>
      <template #footer>
        <el-button @click="add.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="add.saving" :disabled="!add.userIds.length" data-test="member-save" @click="saveAdd">
          {{ t('common.save') }}
        </el-button>
      </template>
    </el-dialog>
  </div>
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
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.mb {
  margin-bottom: 8px;
}
.mt {
  margin-top: 16px;
}
</style>
