<script setup lang="ts">
// 存量导入：填目录 → 后台扫描 → 下载预演报告 →（改好导回）→ 执行（可暂停 / 继续）→（需要时）整批撤销。
// 扫描、执行、撤销都在后台 worker 里做，页面每 2 秒刷新一次进度。原目录只读不改。
import { Download, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { showError } from '@/api/errors'
import { type Batch, type ImportRow, type ItemQuery, type ReportPreview, importsApi } from '@/api/imports'
import type { Lang } from '@/api/mdl'
import { useProjectContext } from '@/composables/useProjectContext'

const { t } = useI18n()
const { projectId, readonly, readonlyNotice } = useProjectContext('file.import')

const roots = ref<string[]>([])
const batches = ref<Batch[]>([])
const loading = ref(false)
const form = reactive({ source_dir: '', description: '', saving: false })
const lang = ref<Lang>('zh')

async function load(): Promise<void> {
  try {
    batches.value = await importsApi.list(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function loadRoots(): Promise<void> {
  try {
    roots.value = await importsApi.roots(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function create(): Promise<void> {
  if (!form.source_dir.trim()) return
  form.saving = true
  try {
    await importsApi.create(projectId.value, form.source_dir.trim(), form.description.trim())
    ElMessage.success(t('imports.created'))
    form.source_dir = ''
    await load()
  } catch (err) {
    showError(err)
  } finally {
    form.saving = false
  }
}

// ---------------------------------------------------------------- 后台任务：进度、暂停 / 继续
const BUSY = ['scanning', 'executing', 'undoing']

function jobActive(b: Batch): boolean {
  return !!b.job && ['queued', 'running'].includes(b.job.status)
}

function percent(b: Batch): number {
  const j = b.job
  if (!j || !j.total) return 0
  return Math.min(100, Math.floor((j.progress * 100) / j.total))
}

// 任务排队 15 秒还没人领：多半是 worker 没开
const firstSeenQueued = new Map<number, number>()
const workerMissing = computed(() =>
  batches.value.some((b) => {
    if (b.job?.status !== 'queued') return false
    const since = firstSeenQueued.get(b.job.id)
    return since !== undefined && Date.now() - since > 15000
  }),
)

let timer: ReturnType<typeof setInterval> | undefined
function poll(): void {
  for (const b of batches.value) {
    if (b.job?.status === 'queued' && !firstSeenQueued.has(b.job.id)) firstSeenQueued.set(b.job.id, Date.now())
  }
  if (batches.value.some((b) => BUSY.includes(b.status) && jobActive(b))) load()
}

async function act(b: Batch, fn: (pid: number, id: number) => Promise<Batch>, message?: string): Promise<void> {
  try {
    await fn(projectId.value, b.id)
    if (message) ElMessage.success(message)
    await load()
  } catch (err) {
    showError(err)
  }
}

async function execute(b: Batch): Promise<void> {
  try {
    await ElMessageBox.confirm(t('imports.executeConfirm', { n: b.counts.action_import ?? 0 }), t('imports.execute'), {
      confirmButtonText: t('imports.execute'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
  } catch {
    return
  }
  await act(b, importsApi.execute, t('imports.executeStarted'))
}

async function undo(b: Batch): Promise<void> {
  try {
    await ElMessageBox.confirm(t('imports.undoConfirm'), t('imports.undo'), {
      confirmButtonText: t('imports.undo'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
  } catch {
    return
  }
  await act(b, importsApi.undo, t('imports.undoStarted'))
}

function summary(b: Batch): string[] {
  const c = b.counts
  const parts: string[] = []
  const add = (key: string, n: number | undefined) => {
    if (n) parts.push(t(`imports.count.${key}`, { n }))
  }
  add('total', c.total)
  add('new', c.state_new)
  add('old_version', c.state_old_version)
  add('duplicate', c.state_duplicate)
  add('existing', c.state_existing)
  add('unclassified', c.unclassified)
  add('suspected', c.suspected)
  add('unreadable', c.state_unreadable)
  if (['executing', 'done', 'undoing', 'undone'].includes(b.status)) {
    add('done', c.result_done)
    add('failed', c.result_failed)
    add('skipped', c.result_skipped)
  }
  if (['undoing', 'undone'].includes(b.status)) {
    add('undone', c.result_undone)
    add('kept', c.result_kept)
  }
  return parts
}

const STATUS_TAG: Record<string, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  scanning: 'warning',
  ready: '',
  executing: 'warning',
  done: 'success',
  undoing: 'warning',
  undone: 'info',
  failed: 'danger',
}

// ---------------------------------------------------------------- 改过的报告导回
const fileInput = ref<HTMLInputElement>()
const reportFor = ref<Batch | null>(null)
const preview = reactive({ open: false, data: null as ReportPreview | null, busy: false })

function pickReport(b: Batch): void {
  reportFor.value = b
  fileInput.value?.click()
}

async function onReport(event: Event): Promise<void> {
  const el = event.target as HTMLInputElement
  const file = el.files?.[0]
  el.value = ''
  const b = reportFor.value
  if (!file || !b) return
  preview.busy = true
  try {
    preview.data = await importsApi.reportPreview(projectId.value, b.id, file)
    preview.open = true
  } catch (err) {
    showError(err)
  } finally {
    preview.busy = false
  }
}

async function confirmReport(): Promise<void> {
  const b = reportFor.value
  if (!b || !preview.data) return
  preview.busy = true
  try {
    const r = await importsApi.reportConfirm(projectId.value, b.id, preview.data.token)
    ElMessage.success(t('imports.reportApplied', { n: r.updated }))
    preview.open = false
    await load()
  } catch (err) {
    showError(err)
  } finally {
    preview.busy = false
  }
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return Array.isArray(value) ? value.join(', ') || '—' : String(value)
}

// ---------------------------------------------------------------- 明细
const FILTERS = ['all', 'new', 'old_version', 'duplicate', 'existing', 'unclassified', 'suspected', 'failed', 'kept'] as const
type Filter = (typeof FILTERS)[number]
const detail = reactive({
  open: false,
  batch: null as Batch | null,
  filter: 'all' as Filter,
  page: 1,
  rows: [] as ImportRow[],
  total: 0,
  loading: false,
})
const PAGE = 100

function queryOf(f: Filter): ItemQuery {
  if (f === 'all') return {}
  if (f === 'unclassified') return { unclassified: true }
  if (f === 'suspected') return { suspected: true }
  if (f === 'failed' || f === 'kept') return { result: f }
  return { state: f }
}

let itemsSeq = 0

async function loadItems(): Promise<void> {
  const b = detail.batch
  if (!b) return
  const seq = ++itemsSeq // 连着切类别时，只用最后一次的结果（先发的请求可能后回来）
  detail.loading = true
  try {
    const r = await importsApi.items(projectId.value, b.id, {
      ...queryOf(detail.filter),
      offset: (detail.page - 1) * PAGE,
      limit: PAGE,
    })
    if (seq !== itemsSeq) return
    detail.rows = r.items
    detail.total = r.total
  } catch (err) {
    if (seq === itemsSeq) showError(err)
  } finally {
    if (seq === itemsSeq) detail.loading = false
  }
}

function openDetail(b: Batch): void {
  Object.assign(detail, { open: true, batch: b, filter: 'all', page: 1 })
  loadItems()
}

watch(() => detail.filter, () => {
  detail.page = 1
  loadItems()
})

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

watch(projectId, () => {
  load()
  loadRoots()
})
onMounted(() => {
  loading.value = true
  Promise.all([load(), loadRoots()]).finally(() => (loading.value = false))
  timer = setInterval(poll, 2000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.imports') }}</h2>
      <div class="spacer" />
      <span class="muted">{{ t('imports.reportLang') }}</span>
      <el-radio-group v-model="lang" size="small">
        <el-radio-button value="zh">{{ t('lang.zh-CN') }}</el-radio-button>
        <el-radio-button value="en">EN</el-radio-button>
      </el-radio-group>
      <el-button :icon="Refresh" size="small" @click="load">{{ t('common.refresh') }}</el-button>
    </div>

    <el-card v-if="!readonly" shadow="never">
      <el-alert v-if="!roots.length" type="warning" :closable="false" show-icon :title="t('imports.noRoots')" class="mb" />
      <el-form v-else label-width="120px">
        <el-form-item :label="t('imports.sourceDir')">
          <el-input v-model="form.source_dir" :placeholder="roots[0]" data-test="import-dir" />
          <div class="muted">{{ t('imports.roots', { roots: roots.join(t('common.listSep')) }) }}</div>
        </el-form-item>
        <el-form-item :label="t('drop.description')">
          <el-input v-model="form.description" :placeholder="t('drop.descriptionHint')" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="form.saving" :disabled="!form.source_dir.trim()" data-test="import-create" @click="create">
            {{ t('imports.scan') }}
          </el-button>
          <span class="muted ml">{{ t('imports.scanNote') }}</span>
        </el-form-item>
      </el-form>
    </el-card>

    <el-alert v-if="workerMissing" type="warning" :closable="false" show-icon :title="t('imports.workerMissing')" />

    <el-card shadow="never">
      <el-empty v-if="!batches.length && !loading" :description="t('imports.none')" />
      <el-table v-else v-loading="loading" :data="batches" size="small" data-test="import-batches">
        <el-table-column prop="id" label="#" width="50" />
        <el-table-column :label="t('imports.sourceDir')" min-width="240">
          <template #default="{ row }">
            <div class="path">{{ row.source_dir }}</div>
            <div class="muted">{{ row.created_by }} · {{ String(row.created_at).slice(0, 16).replace('T', ' ') }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('imports.status')" width="190">
          <template #default="{ row }">
            <el-tag :type="STATUS_TAG[row.status] || undefined" size="small" data-test="import-status">{{ t(`imports.state.${row.status}`) }}</el-tag>
            <el-tag v-if="row.job && row.job.status === 'paused'" type="info" size="small" class="ml">{{ t('imports.paused') }}</el-tag>
            <el-progress
              v-if="['scanning', 'executing', 'undoing'].includes(row.status) && row.job?.total"
              :percentage="percent(row as Batch)"
              :format="() => `${row.job.progress}/${row.job.total}`"
              class="progress"
            />
            <div v-if="row.error || row.job?.error" class="err">{{ row.error || row.job.error }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('imports.summary')" min-width="260">
          <template #default="{ row }">
            <span class="muted" data-test="import-summary">{{ summary(row as Batch).join(t('common.listSep')) }}</span>
          </template>
        </el-table-column>
        <el-table-column v-if="!readonly" :label="t('common.actions')" width="300" fixed="right">
          <template #default="{ row }">
            <div class="actions">
              <template v-if="['scanning', 'executing', 'undoing'].includes(row.status)">
                <el-button v-if="jobActive(row as Batch)" size="small" data-test="import-pause" @click="act(row as Batch, importsApi.pause)">
                  {{ t('imports.pause') }}
                </el-button>
                <el-button v-else size="small" data-test="import-resume" @click="act(row as Batch, importsApi.resume)">
                  {{ t('imports.resume') }}
                </el-button>
              </template>
              <template v-if="['ready', 'done'].includes(row.status)">
                <el-button size="small" :icon="Download" data-test="import-report" @click="importsApi.report(projectId, row.id, lang).catch(showError)">
                  {{ t('imports.report') }}
                </el-button>
              </template>
              <template v-if="row.status === 'ready'">
                <el-button size="small" :icon="Upload" :loading="preview.busy && reportFor?.id === row.id" @click="pickReport(row as Batch)">
                  {{ t('imports.reportBack') }}
                </el-button>
                <el-button size="small" type="primary" data-test="import-execute" @click="execute(row as Batch)">{{ t('imports.execute') }}</el-button>
              </template>
              <el-button v-if="row.status === 'done'" size="small" type="danger" plain data-test="import-undo" @click="undo(row as Batch)">
                {{ t('imports.undo') }}
              </el-button>
              <el-button size="small" link type="primary" data-test="import-detail" @click="openDetail(row as Batch)">{{ t('imports.detail') }}</el-button>
            </div>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted">{{ t('imports.flowNote') }}</p>
      <input ref="fileInput" type="file" accept=".xlsx" hidden data-test="import-report-file" @change="onReport" />
    </el-card>

    <!-- 报告导回预览 -->
    <el-dialog v-model="preview.open" :title="t('imports.reportBack')" width="760px" data-test="report-preview">
      <template v-if="preview.data">
        <el-alert v-for="e in preview.data.errors" :key="e.row" type="error" :closable="false" :title="e.message" class="mb" />
        <el-empty v-if="preview.data.ok && !preview.data.changes.length" :description="t('imports.noChanges')" :image-size="50" />
        <el-table v-else-if="preview.data.changes.length" :data="preview.data.changes" size="small" max-height="400">
          <el-table-column prop="row" :label="t('mdl.row')" width="60" />
          <el-table-column prop="path" :label="t('imports.path')" min-width="240" />
          <el-table-column :label="t('mdl.changes')" min-width="260">
            <template #default="{ row }">
              <div v-for="(v, f) in row.changes" :key="f">
                {{ t('equipment.change', { field: t(`imports.field.${f}`), from: fmt(v[0]), to: fmt(v[1]) }) }}
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="preview.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :disabled="!preview.data?.ok || !preview.data.changes.length"
          :loading="preview.busy"
          data-test="report-confirm"
          @click="confirmReport"
        >
          {{ t('import.confirm') }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 明细 -->
    <el-drawer v-model="detail.open" :title="detail.batch ? `#${detail.batch.id} ${detail.batch.source_dir}` : ''" size="85%" data-test="import-items">
      <el-radio-group v-model="detail.filter" size="small" class="mb">
        <el-radio-button v-for="f in FILTERS" :key="f" :value="f">{{ t(`imports.filter.${f}`) }}</el-radio-button>
      </el-radio-group>
      <el-table v-loading="detail.loading" :data="detail.rows" size="small">
        <el-table-column :label="t('imports.path')" min-width="260">
          <template #default="{ row }">
            <div class="path">{{ row.path }}</div>
            <div class="muted">{{ size(row.size) }} · {{ row.modified }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('imports.status')" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="row.state === 'new' ? 'success' : row.state === 'old_version' ? 'warning' : 'info'">
              {{ t(`imports.itemState.${row.state}`) }}
            </el-tag>
            <div v-if="row.action === 'skip'" class="muted">{{ t('imports.skip') }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('drop.target')" min-width="200">
          <template #default="{ row }">
            <span v-if="row.location">{{ row.location }}</span>
            <el-tag v-else-if="row.action === 'import'" type="info" size="small">{{ t('drop.unclassified') }}</el-tag>
            <el-tag v-if="row.manual && row.location" size="small" effect="plain" type="info" class="ml">{{ t('imports.manual') }}</el-tag>
            <el-tag v-else-if="row.level && row.location" size="small" effect="plain" class="ml">{{ t(`drop.level.${row.level}`) }}</el-tag>
            <div v-if="row.suspected_equipment" class="warn">{{ t('imports.suspected', { tag: row.suspected_equipment }) }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('imports.rank')" width="70">
          <template #default="{ row }">{{ row.version_rank ? `V${row.version_rank}` : '' }}</template>
        </el-table-column>
        <el-table-column prop="planned_name" :label="t('imports.newName')" min-width="280" />
        <el-table-column :label="t('imports.result')" width="130">
          <template #default="{ row }">
            <el-tag v-if="row.result !== 'pending'" size="small" :type="row.result === 'done' ? 'success' : row.result === 'failed' ? 'danger' : 'info'">
              {{ t(`imports.itemResult.${row.result}`) }}
            </el-tag>
            <div v-if="row.error" class="err">{{ row.error }}</div>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        v-if="detail.total > PAGE"
        layout="prev, pager, next, total"
        :total="detail.total"
        :page-size="PAGE"
        :current-page="detail.page"
        class="pager"
        @current-change="(p: number) => { detail.page = p; loadItems() }"
      />
    </el-drawer>
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
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.spacer {
  flex: 1;
}
.mb {
  margin-bottom: 10px;
}
.ml {
  margin-left: 6px;
}
.path {
  word-break: break-all;
}
.progress {
  margin-top: 4px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.actions .el-button + .el-button {
  margin-left: 0;
}
.pager {
  margin-top: 10px;
  justify-content: flex-end;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.warn {
  color: var(--el-color-warning);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
}
</style>
