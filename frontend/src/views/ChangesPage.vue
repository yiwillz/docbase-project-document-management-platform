<script setup lang="ts">
// 变更记录页：按对象类型、操作人、时间筛选，展开看字段前→后；按当前筛选导出 Excel
import { Download, Refresh } from '@element-plus/icons-vue'
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type ChangeLog, changeLogsApi } from '@/api/changeLogs'
import { showError } from '@/api/errors'
import { download } from '@/api/http'
import { useProjectContext } from '@/composables/useProjectContext'

const { t, d, locale } = useI18n()
const { projectId, can } = useProjectContext()

const ENTITY_TYPES = [
  'projects', 'milestones', 'modules', 'document_types', 'document_type_modules', 'document_type_equipment',
  'equipment', 'equipment_types', 'equipment_units', 'deliverables', 'transmittals', 'transmittal_items',
  'review_codes', 'status_mappings', 'mdl_versions', 'module_equipment_types', 'files', 'versions', 'baselines',
  'import_batches', 'project_members',
]
const PAGE_SIZE = 50

const query = reactive({ entity_type: '', actor: '', range: null as [string, string] | null, page: 1 })
const rows = ref<ChangeLog[]>([])
const total = ref(0)
const loading = ref(false)

// 按当前筛选导出 Excel（界面是英文时导出英文表头）
function exportExcel(): void {
  const [since, until] = query.range ?? [undefined, undefined]
  download(`/api/projects/${projectId.value}/change-logs/export.xlsx`, {
    entity_type: query.entity_type || undefined,
    actor: query.actor.trim() || undefined,
    since: since ? `${since}T00:00:00` : undefined,
    until: until ? `${until}T23:59:59` : undefined,
    lang: locale.value === 'en' ? 'en' : 'zh',
  }).catch(showError)
}

async function load(): Promise<void> {
  loading.value = true
  try {
    const [since, until] = query.range ?? [undefined, undefined]
    const result = await changeLogsApi.list({
      project_id: projectId.value,
      entity_type: query.entity_type || undefined,
      actor: query.actor.trim() || undefined,
      since: since ? `${since}T00:00:00` : undefined,
      until: until ? `${until}T23:59:59` : undefined,
      limit: PAGE_SIZE,
      offset: (query.page - 1) * PAGE_SIZE,
    })
    rows.value = result.items
    total.value = result.total
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

function search(): void {
  query.page = 1
  load()
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

watch(projectId, search)
onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <h2>{{ t('menu.changes') }}</h2>
      <el-select v-model="query.entity_type" clearable :placeholder="t('changes.allTypes')" class="w200" @change="search">
        <el-option v-for="e in ENTITY_TYPES" :key="e" :value="e" :label="t(`changes.entity.${e}`)" />
      </el-select>
      <el-input v-model="query.actor" clearable :placeholder="t('changes.actor')" class="w160" @change="search" />
      <el-date-picker
        v-model="query.range"
        type="daterange"
        value-format="YYYY-MM-DD"
        :start-placeholder="t('changes.from')"
        :end-placeholder="t('changes.to')"
        @change="search"
      />
      <div class="spacer" />
      <el-button v-if="can('export')" :icon="Download" data-test="export-changes" @click="exportExcel">{{ t('changes.export') }}</el-button>
      <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
    </div>

    <el-card shadow="never">
      <el-table v-loading="loading" :data="rows" row-key="id" size="small" data-test="change-logs">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="changes">
              <div v-for="(v, f) in row.changes" :key="f">
                {{ t('equipment.change', { field: String(f), from: fmt(v[0]), to: fmt(v[1]) }) }}
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('changes.time')" width="160">
          <template #default="{ row }">{{ d(new Date(row.created_at), 'long') }}</template>
        </el-table-column>
        <el-table-column prop="actor_name" :label="t('changes.actor')" width="120" />
        <el-table-column :label="t('changes.action')" width="80">
          <template #default="{ row }">{{ t(`history.action.${row.action}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('changes.object')" min-width="300">
          <template #default="{ row }">
            <span class="muted">{{ t(`changes.entity.${row.entity_type}`, row.entity_type) }}</span>
            <template v-if="row.summary">{{ t(`changes.summary.${row.summary}`) }} {{ row.label ?? '' }}</template>
            <template v-else>{{ row.label ?? `#${row.entity_id}` }}</template>
          </template>
        </el-table-column>
        <el-table-column :label="t('changes.fields')" min-width="200">
          <template #default="{ row }">{{ Object.keys(row.changes).join(', ') }}</template>
        </el-table-column>
        <el-table-column prop="reason" :label="t('common.reason')" min-width="160" />
      </el-table>
      <el-pagination
        v-if="total > PAGE_SIZE"
        v-model:current-page="query.page"
        class="pager"
        layout="total, prev, pager, next"
        :total="total"
        :page-size="PAGE_SIZE"
        @current-change="load"
      />
    </el-card>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.spacer {
  flex: 1;
}
.w160 {
  width: 160px;
}
.w200 {
  width: 200px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-right: 6px;
}
.changes {
  padding: 4px 48px;
  font-size: 13px;
  line-height: 1.7;
}
.pager {
  margin-top: 10px;
  justify-content: flex-end;
}
</style>
