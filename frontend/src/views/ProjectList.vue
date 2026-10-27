<script setup lang="ts">
// 项目列表：搜索、显示已归档、新建、进入、归档 / 取消归档、删除空项目（系统管理员）
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'

import { showError } from '@/api/errors'
import { percent } from '@/utils/status'
import { type Project, projectsApi } from '@/api/projects'
import { useProjectStore } from '@/stores/projects'
import { useUserStore } from '@/stores/user'

const { t, d } = useI18n()
const router = useRouter()
const store = useProjectStore()
const user = useUserStore()

const keyword = ref('')
const showArchived = ref(false)
const loading = ref(false)

const rows = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return store.projects
    .filter((p) => showArchived.value || !p.archived_at)
    .filter((p) => !k || [p.code, p.name, p.client_name ?? ''].some((v) => v.toLowerCase().includes(k)))
    .sort((a, b) => a.code.localeCompare(b.code))
})

async function reload(): Promise<void> {
  loading.value = true
  try {
    await store.load()
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

function open(p: Project): void {
  router.push(`/projects/${p.id}/matrix`)
}

async function toggleArchive(p: Project): Promise<void> {
  try {
    if (p.archived_at) {
      store.upsert(await projectsApi.unarchive(p.id))
      ElMessage.success(t('project.unarchivedDone'))
    } else {
      await ElMessageBox.confirm(t('project.archiveConfirm', { code: p.code }), t('project.archive'), {
        type: 'warning',
        confirmButtonText: t('common.ok'),
        cancelButtonText: t('common.cancel'),
      })
      store.upsert(await projectsApi.archive(p.id))
      ElMessage.success(t('project.archivedDone'))
    }
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') showError(err)
  }
}

// ---------------------------------------------------------------- 删除空项目（系统管理员）
// 有文件、传送单、发过 MDL、基线、存量导入的不能删，只能归档；要输入项目编号确认
const del = reactive({
  open: false,
  project: null as Project | null,
  checking: false,
  blockers: {} as Record<string, number>,
  code: '',
  reason: '',
  saving: false,
})

async function openDelete(p: Project): Promise<void> {
  Object.assign(del, { open: true, project: p, checking: true, blockers: {}, code: '', reason: '' })
  try {
    del.blockers = (await projectsApi.deleteCheck(p.id)).blockers
  } catch (err) {
    showError(err)
    del.open = false
  } finally {
    del.checking = false
  }
}

async function confirmDelete(): Promise<void> {
  const p = del.project
  if (!p) return
  del.saving = true
  try {
    await projectsApi.remove(p.id, del.code.trim(), del.reason.trim() || undefined)
    store.projects = store.projects.filter((x) => x.id !== p.id)
    del.open = false
    ElMessage.success(t('project.deleted', { code: p.code }))
  } catch (err) {
    showError(err)
  } finally {
    del.saving = false
  }
}

// ---------------------------------------------------------------- 新建项目
const dialog = ref(false)
const saving = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ code: '', name: '', client_name: '', contractor_name: '' })
const rules = computed<FormRules>(() => ({
  code: [{ required: true, whitespace: true, message: t('project.codeRequired'), trigger: 'blur' }],
  name: [{ required: true, whitespace: true, message: t('project.nameRequired'), trigger: 'blur' }],
}))

function openCreate(): void {
  Object.assign(form, { code: '', name: '', client_name: '', contractor_name: '' })
  dialog.value = true
  formRef.value?.clearValidate()
}

async function create(): Promise<void> {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    const project = await projectsApi.create({
      code: form.code.trim(),
      name: form.name.trim(),
      client_name: form.client_name.trim() || null,
      contractor_name: form.contractor_name.trim() || null,
    })
    store.upsert(project)
    dialog.value = false
    ElMessage.success(t('project.created'))
    open(project)
  } catch (err) {
    showError(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-card shadow="never">
    <template #header>
      <div class="toolbar">
        <h2>{{ t('project.title') }}</h2>
        <el-input
          v-model="keyword"
          :placeholder="t('project.searchPlaceholder')"
          :prefix-icon="Search"
          clearable
          class="search"
        />
        <el-checkbox v-model="showArchived">{{ t('project.showArchived') }}</el-checkbox>
        <div class="spacer" />
        <el-button :icon="Refresh" @click="reload">{{ t('common.refresh') }}</el-button>
        <el-button v-if="user.isSysadmin" type="primary" :icon="Plus" @click="openCreate">{{ t('project.create') }}</el-button>
      </div>
    </template>

    <el-table
      v-loading="loading"
      :data="rows"
      :empty-text="store.loaded && !store.projects.length ? t('project.none') : t('common.empty')"
      row-class-name="row"
      @row-dblclick="open"
    >
      <el-table-column prop="code" :label="t('project.code')" width="160" sortable />
      <el-table-column prop="name" :label="t('project.name')" min-width="220" />
      <el-table-column prop="client_name" :label="t('project.client')" min-width="180" />
      <el-table-column prop="contractor_name" :label="t('project.contractor')" min-width="180" />
      <el-table-column :label="t('project.completion')" width="100" data-test="col-completion">
        <template #default="{ row }">
          <span v-if="row.completion !== null && row.completion !== undefined" data-test="completion">{{ percent(row.completion) }}</span>
          <span v-else class="muted">—</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('project.outstanding')" width="90">
        <template #default="{ row }">
          <router-link
            v-if="row.outstanding_count"
            :to="{ name: 'matrix', params: { projectId: row.id }, query: { outstanding: 'true' } }"
            data-test="outstanding-count"
            @click.stop
          >
            {{ row.outstanding_count }}
          </router-link>
          <span v-else class="muted">0</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('project.overdue')" width="90">
        <template #default="{ row }">
          <router-link
            v-if="row.overdue_count"
            :to="{ name: 'matrix', params: { projectId: row.id }, query: { overdue: 'true' } }"
            class="danger"
            data-test="overdue-count"
            @click.stop
          >
            {{ row.overdue_count }}
          </router-link>
          <span v-else class="muted">0</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('project.unclassified')" width="100">
        <template #default="{ row }">
          <router-link
            v-if="row.unclassified_count"
            :to="{ name: 'drop', params: { projectId: row.id }, query: { tab: 'unclassified' } }"
            data-test="unclassified-count"
            @click.stop
          >
            {{ row.unclassified_count }}
          </router-link>
          <span v-else class="muted">0</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('project.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="row.archived_at ? 'info' : 'success'" disable-transitions>
            {{ row.archived_at ? t('project.archived') : t('project.active') }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('project.createdAt')" width="130">
        <template #default="{ row }">{{ d(new Date(row.created_at), 'short') }}</template>
      </el-table-column>
      <el-table-column :label="t('common.actions')" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="open(row as Project)">{{ t('project.open') }}</el-button>
          <el-button v-if="user.isSysadmin" link type="danger" data-test="project-delete" @click.stop="openDelete(row as Project)">
            {{ t('common.delete') }}
          </el-button>
          <el-button v-if="user.can(row.id, 'project.manage')" link @click="toggleArchive(row as Project)">
            {{ row.archived_at ? t('project.unarchive') : t('project.archive') }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog v-model="del.open" :title="t('project.deleteTitle', { code: del.project?.code ?? '' })" width="480px" data-test="delete-dialog">
    <div v-loading="del.checking">
      <template v-if="Object.keys(del.blockers).length">
        <el-alert type="warning" :closable="false" show-icon :title="t('project.cannotDelete')" />
        <ul class="blockers">
          <li v-for="(n, key) in del.blockers" :key="key">{{ t(`project.blocker.${key}`, { n }) }}</li>
        </ul>
        <p class="muted">{{ t('project.archiveInstead') }}</p>
      </template>
      <template v-else-if="!del.checking">
        <p>{{ t('project.deleteHint', { code: del.project?.code ?? '' }) }}</p>
        <el-input v-model="del.code" :placeholder="del.project?.code" data-test="delete-code" class="mb" />
        <el-input v-model="del.reason" :placeholder="t('common.reason')" />
      </template>
    </div>
    <template #footer>
      <el-button @click="del.open = false">{{ t('common.cancel') }}</el-button>
      <el-button
        v-if="!Object.keys(del.blockers).length"
        type="danger"
        :loading="del.saving"
        :disabled="del.checking || del.code.trim() !== del.project?.code"
        data-test="delete-confirm"
        @click="confirmDelete"
      >
        {{ t('common.delete') }}
      </el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="dialog" :title="t('project.create')" width="520px">
    <el-form ref="formRef" :model="form" :rules="rules" label-width="100px" @submit.prevent="create">
      <el-form-item :label="t('project.code')" prop="code">
        <el-input v-model="form.code" maxlength="100" :placeholder="t('project.codeHint')" />
      </el-form-item>
      <el-form-item :label="t('project.name')" prop="name">
        <el-input v-model="form.name" maxlength="200" />
      </el-form-item>
      <el-form-item :label="t('project.client')">
        <el-input v-model="form.client_name" maxlength="200" />
      </el-form-item>
      <el-form-item :label="t('project.contractor')">
        <el-input v-model="form.contractor_name" maxlength="200" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" @click="create">{{ t('common.create') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.search {
  width: 260px;
}
.spacer {
  flex: 1;
}
.blockers {
  margin: 10px 0;
  padding-left: 20px;
  line-height: 1.8;
}
.mb {
  margin-bottom: 8px;
}
.danger {
  color: var(--el-color-danger);
}
:deep(.row) {
  cursor: pointer;
}
</style>
