<script setup lang="ts">
// 智能投递（方式二）：一句描述 + 拖入文件或文件夹 → 逐个识别位置、预览文件名 → 可改 → 一起存入；
// 识别不出的存进待归类（保持原名），在"待归类"里再归类
import { FolderOpened, Refresh, UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { type Item, deliverablesApi } from '@/api/deliverables'
import { equipmentApi } from '@/api/equipment'
import { showError } from '@/api/errors'
import { CHUNK, type Overrides, SOURCES, type StoredFile, type StoredResult, type UploadPlan, filesApi } from '@/api/files'
import DeliverablePicker from '@/components/DeliverablePicker.vue'
import { useProjectContext } from '@/composables/useProjectContext'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly, readonlyNotice } = useProjectContext()

const tab = ref<'drop' | 'unclassified'>(route.query.tab === 'unclassified' ? 'unclassified' : 'drop')
watch(tab, (value) => router.replace({ query: { ...route.query, tab: value === 'drop' ? undefined : value } }))

const items = ref<Item[]>([])
const vendors = ref<string[]>([])

async function loadOptions(): Promise<void> {
  try {
    const [list, equipment] = await Promise.all([deliverablesApi.list(projectId.value), equipmentApi.list(projectId.value)])
    items.value = list.items
    vendors.value = [...new Set(equipment.map((e) => e.vendor).filter((v): v is string => !!v))].sort()
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 投递
const ctx = reactive({ description: '', vendor: '' })

type Status = 'queued' | 'uploading' | 'recognizing' | 'ready' | 'stored' | 'error'
interface Row {
  key: number
  file: File
  folder: string
  status: Status
  progress: number | null
  plan: UploadPlan | null
  overrides: Overrides
  target: string
  recordReply: boolean
  error: string | null
  stored: StoredResult | null
}

const rows = ref<Row[]>([])
const dragging = ref(false)
const fileInput = ref<HTMLInputElement>()
const folderInput = ref<HTMLInputElement>()
let seq = 0
const MAX_PARALLEL = 2

function add(file: File, folder: string): void {
  rows.value.push({
    key: ++seq, file, folder, status: 'queued', progress: null, plan: null, overrides: {}, target: '',
    recordReply: false, error: null, stored: null,
  })
}

function folderOf(path: string): string {
  const i = path.lastIndexOf('/')
  return i > 0 ? path.slice(0, i) : ''
}

function onPick(event: Event): void {
  const el = event.target as HTMLInputElement
  for (const f of Array.from(el.files ?? [])) add(f, folderOf(f.webkitRelativePath || ''))
  el.value = ''
  pump()
}

// 拖进来的文件夹：逐层读出里面的文件，记下相对路径
async function readEntry(entry: FileSystemEntry, path: string): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject))
    add(file, path)
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader()
    const sub = path ? `${path}/${entry.name}` : entry.name
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) break
      for (const e of batch) await readEntry(e, sub)
    }
  }
}

async function onDrop(event: DragEvent): Promise<void> {
  dragging.value = false
  const dt = event.dataTransfer
  if (!dt) return
  const entries = Array.from(dt.items ?? [])
    .map((i) => i.webkitGetAsEntry?.())
    .filter((e): e is FileSystemEntry => !!e)
  if (entries.length) {
    for (const e of entries) await readEntry(e, '')
  } else {
    for (const f of Array.from(dt.files)) add(f, '')
  }
  pump()
}

function targetOf(plan: UploadPlan): string {
  return plan.deliverable_ids.join(',')
}

async function process(row: Row): Promise<void> {
  row.status = row.file.size > CHUNK ? 'uploading' : 'recognizing'
  try {
    row.plan = await filesApi.dropPreview(
      projectId.value,
      row.file,
      { description: ctx.description.trim(), folder: row.folder, vendor: ctx.vendor },
      (sent, total) => {
        row.progress = Math.floor((sent * 100) / total)
        if (sent >= total) row.status = 'recognizing'
      },
    )
    row.target = targetOf(row.plan)
    row.recordReply = !!row.plan.recognition?.client_reply?.item_ids.length
    row.status = 'ready'
  } catch (err) {
    row.status = 'error'
    row.error = err instanceof Error ? err.message : String(err)
  }
}

// 同时最多处理 2 个文件
function pump(): void {
  const running = rows.value.filter((r) => r.status === 'uploading' || r.status === 'recognizing').length
  const next = rows.value.filter((r) => r.status === 'queued').slice(0, Math.max(0, MAX_PARALLEL - running))
  for (const row of next) {
    const live = rows.value.find((r) => r.key === row.key)!
    process(live).then(pump)
  }
}

async function change(row: Row, patch: Overrides): Promise<void> {
  if (!row.plan) return
  row.overrides = { ...row.overrides, ...patch }
  try {
    row.plan = await filesApi.replan(projectId.value, row.plan.token, row.overrides)
    if (!row.plan.recognition?.client_reply || row.plan.unclassified) row.recordReply = false
  } catch (err) {
    showError(err)
  }
}

function setTarget(row: Row, value: string): void {
  row.target = value
  const ids = value ? value.split(',').map(Number) : []
  const next: Overrides = { ...row.overrides, deliverable_ids: ids }
  delete next.file_id
  row.overrides = next
  change(row, {})
}

function remove(row: Row): void {
  rows.value = rows.value.filter((r) => r !== row)
}

function clearDone(): void {
  rows.value = rows.value.filter((r) => r.status !== 'stored')
}

const ready = computed(() => rows.value.filter((r) => r.status === 'ready' && r.plan?.ok))
const busy = computed(() => rows.value.some((r) => ['queued', 'uploading', 'recognizing'].includes(r.status)))
const saving = ref(false)
const summary = ref<{ stored: number; unclassified: number; replies: number } | null>(null)

async function storeAll(): Promise<void> {
  saving.value = true
  const result = { stored: 0, unclassified: 0, replies: 0 }
  for (const row of ready.value) {
    try {
      const body: Overrides = { ...row.overrides }
      if (row.recordReply) body.record_reply = true
      row.stored = await filesApi.confirm(projectId.value, row.plan!.token, body)
      row.status = 'stored'
      result.stored++
      if (row.stored.unclassified) result.unclassified++
      if (row.stored.reply) result.replies++
    } catch (err) {
      showError(err)
    }
  }
  saving.value = false
  summary.value = result
  if (result.stored) {
    ElMessage.success(t('drop.storedSummary', result))
    loadUnclassified()
  }
}

function levelOf(row: Row): 'high' | 'medium' | 'low' | 'unclassified' {
  if (!row.plan) return 'low'
  if (row.plan.unclassified) return 'unclassified'
  const c = row.plan.recognition?.candidates.find((x) => x.deliverable_ids.join(',') === row.target)
  return c?.level ?? 'high' // 手动选的位置算确定
}

// 几个文件放到同一处（按设备的同一台、或同标题）：存入时会依次升版，提醒一下
function sameTarget(row: Row): number {
  if (!row.target || row.status !== 'ready') return 0
  return rows.value.filter((r) => r !== row && r.status === 'ready' && r.target === row.target).length
}

const LEVEL_TAG = { high: 'success', medium: 'warning', low: 'info', unclassified: 'info' } as const

function reasons(row: Row): string[] {
  const c = row.plan?.recognition?.candidates.find((x) => x.deliverable_ids.join(',') === row.target)
  return (c?.reasons ?? [])
    .filter((r) => r.kind !== 'not_delivered')
    .map((r) => t(`drop.reason.${r.kind}`, { text: r.text }) + (r.where !== '-' ? t(`drop.where.${r.where}`) : ''))
}

// ---------------------------------------------------------------- 待归类
const unclassified = ref<StoredFile[]>([])
const loadingUnclassified = ref(false)
const classifyDialog = reactive({ open: false, file: null as StoredFile | null, target: '', reason: '', saving: false })

async function loadUnclassified(): Promise<void> {
  loadingUnclassified.value = true
  try {
    unclassified.value = await filesApi.unclassified(projectId.value)
  } catch (err) {
    showError(err)
  } finally {
    loadingUnclassified.value = false
  }
}

function openClassify(file: StoredFile): void {
  Object.assign(classifyDialog, { open: true, file, target: '', reason: '', saving: false })
}

async function saveClassify(): Promise<void> {
  const f = classifyDialog.file
  if (!f || !classifyDialog.target) return
  classifyDialog.saving = true
  try {
    const r = await filesApi.classify(
      projectId.value,
      f.id,
      classifyDialog.target.split(',').map(Number),
      classifyDialog.reason.trim() || undefined,
    )
    ElMessage.success(t('drop.classified', { name: r.filename ?? '' }))
    classifyDialog.open = false
    await loadUnclassified()
  } catch (err) {
    showError(err)
  } finally {
    classifyDialog.saving = false
  }
}

function size(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

watch(projectId, () => {
  rows.value = []
  loadOptions()
  loadUnclassified()
})
onMounted(() => {
  loadOptions()
  loadUnclassified()
})
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.drop') }}</h2>
      <el-radio-group v-model="tab" size="small">
        <el-radio-button value="drop">{{ t('drop.tabDrop') }}</el-radio-button>
        <el-radio-button value="unclassified" data-test="tab-unclassified">
          {{ t('drop.tabUnclassified', { n: unclassified.length }) }}
        </el-radio-button>
      </el-radio-group>
    </div>

    <!-- 投递 -->
    <template v-if="tab === 'drop'">
      <el-card v-if="!readonly" shadow="never">
        <el-form inline>
          <el-form-item :label="t('drop.description')">
            <el-input v-model="ctx.description" :placeholder="t('drop.descriptionHint')" class="w360" data-test="drop-description" />
          </el-form-item>
          <el-form-item :label="t('drop.vendor')">
            <el-select v-model="ctx.vendor" clearable filterable :placeholder="t('drop.vendorHint')" class="w200">
              <el-option v-for="v in vendors" :key="v" :value="v" :label="v" />
            </el-select>
          </el-form-item>
        </el-form>
        <div
          class="drop"
          :class="{ dragging }"
          data-test="drop-zone"
          @dragover.prevent="dragging = true"
          @dragleave="dragging = false"
          @drop.prevent="onDrop"
        >
          <el-icon :size="32"><UploadFilled /></el-icon>
          <div>{{ t('drop.dropHere') }}</div>
          <div class="pick">
            <el-button size="small" @click="fileInput?.click()">{{ t('drop.pickFiles') }}</el-button>
            <el-button size="small" :icon="FolderOpened" @click="folderInput?.click()">{{ t('drop.pickFolder') }}</el-button>
          </div>
          <input ref="fileInput" type="file" multiple hidden data-test="drop-input" @change="onPick" />
          <input ref="folderInput" type="file" webkitdirectory hidden @change="onPick" />
        </div>
        <p class="muted">{{ t('drop.note') }}</p>
      </el-card>

      <el-card v-if="rows.length" shadow="never">
        <div class="toolbar mb">
          <span>{{ t('drop.count', { n: rows.length, ready: ready.length }) }}</span>
          <div class="spacer" />
          <el-button size="small" @click="clearDone">{{ t('drop.clearDone') }}</el-button>
          <el-button type="primary" :disabled="!ready.length || busy" :loading="saving" data-test="drop-store" @click="storeAll">
            {{ t('drop.storeAll', { n: ready.length }) }}
          </el-button>
        </div>
        <el-alert v-if="summary" type="success" :closable="false" show-icon class="mb" data-test="drop-summary">
          {{ t('drop.storedSummary', summary) }}
          <router-link :to="{ name: 'matrix', params: { projectId } }">{{ t('drop.toMatrix') }}</router-link>
        </el-alert>

        <div v-for="row in rows" :key="row.key" class="row" :class="row.status" data-test="drop-row">
          <div class="head">
            <span class="original">{{ row.file.name }}</span>
            <span v-if="row.folder" class="muted">{{ row.folder }}</span>
            <span class="muted">{{ size(row.file.size) }}</span>
            <div class="spacer" />
            <template v-if="row.plan && row.status !== 'stored'">
              <el-tag :type="LEVEL_TAG[levelOf(row)] || undefined" size="small" data-test="drop-level">{{ t(`drop.level.${levelOf(row)}`) }}</el-tag>
            </template>
            <el-tag v-if="row.status === 'stored'" type="success" size="small">{{ t('upload.done') }}</el-tag>
            <el-button v-else-if="row.status !== 'uploading' && row.status !== 'recognizing'" link type="danger" size="small" @click="remove(row)">
              {{ t('drop.skip') }}
            </el-button>
          </div>
          <el-progress v-if="row.status === 'uploading' && row.progress !== null" :percentage="row.progress" />
          <div v-else-if="row.status === 'queued' || row.status === 'recognizing'" class="muted">{{ t(`drop.status.${row.status}`) }}</div>
          <el-alert v-if="row.error" type="error" :closable="false" :title="row.error" />

          <template v-if="row.plan">
            <div class="name" data-test="drop-name">
              <el-tag :type="row.plan.unclassified ? 'info' : row.plan.mode === 'new' ? 'success' : 'warning'" size="small">
                {{ row.plan.unclassified ? t('drop.unclassified') : row.plan.mode === 'new' ? t('upload.newFile') : t('upload.newVersion', { v: row.plan.version_no }) }}
              </el-tag>
              <span>{{ row.stored?.filename ?? row.plan.filename }}</span>
            </div>
            <el-alert v-if="row.plan.duplicate" type="error" :closable="false" show-icon data-test="drop-duplicate">
              {{ t('upload.duplicate', { name: row.plan.duplicate.filename, v: row.plan.duplicate.version_no }) }}
            </el-alert>
            <template v-if="row.status === 'ready' && !row.plan.duplicate">
              <el-form label-width="80px" size="small" class="form">
                <el-form-item :label="t('drop.target')">
                  <DeliverablePicker
                    :model-value="row.target"
                    :items="items"
                    :candidates="row.plan.recognition?.candidates"
                    allow-unclassified
                    data-test="drop-target"
                    @update:model-value="(v: string) => setTarget(row, v)"
                  />
                </el-form-item>
                <el-form-item v-if="sameTarget(row)" label=" ">
                  <span class="warn" data-test="drop-same-target">{{ t('drop.sameTarget', { n: sameTarget(row) }) }}</span>
                </el-form-item>
                <el-form-item v-if="reasons(row).length" :label="t('drop.why')">
                  <span class="muted">{{ reasons(row).join(t('common.listSep')) }}</span>
                </el-form-item>
                <el-form-item :label="t('upload.source')">
                  <el-select
                    :model-value="row.plan.source"
                    :disabled="row.plan.mode === 'version'"
                    class="w120"
                    @change="(v: Overrides['source']) => change(row, { source: v })"
                  >
                    <el-option v-for="s in SOURCES" :key="s" :value="s" :label="t(`upload.sources.${s}`)" />
                  </el-select>
                  <span class="muted ml">{{ t('drop.dateLine', { date: row.plan.file_date, from: t(`upload.dateSource.${row.plan.date_source}`) }) }}</span>
                </el-form-item>
                <el-form-item v-if="row.plan.recognition?.client_reply && !row.plan.unclassified" :label="t('drop.reply')">
                  <el-checkbox v-model="row.recordReply" :disabled="!row.plan.recognition.client_reply.item_ids.length" data-test="drop-record-reply">
                    {{
                      t('drop.replyHint', {
                        code: row.plan.recognition.client_reply.code_text,
                        no: row.plan.recognition.client_reply.transmittal_no ?? '—',
                      })
                    }}
                  </el-checkbox>
                </el-form-item>
              </el-form>
            </template>
            <p v-if="row.stored?.reply" class="muted">
              {{ t('drop.replyDone', { no: row.stored.reply.transmittal_no ?? '', code: row.stored.reply.code }) }}
            </p>
          </template>
        </div>
      </el-card>
    </template>

    <!-- 待归类 -->
    <el-card v-else shadow="never">
      <div class="toolbar mb">
        <span class="muted">{{ t('drop.unclassifiedNote') }}</span>
        <div class="spacer" />
        <el-button :icon="Refresh" size="small" @click="loadUnclassified">{{ t('common.refresh') }}</el-button>
      </div>
      <el-empty v-if="!unclassified.length && !loadingUnclassified" :description="t('drop.noUnclassified')" :image-size="60" />
      <el-table v-else v-loading="loadingUnclassified" :data="unclassified" size="small" data-test="unclassified-list">
        <el-table-column :label="t('upload.col.filename')" min-width="320">
          <template #default="{ row }">
            <a v-if="row.versions[0]" :href="filesApi.downloadUrl(projectId, row.versions[0].id)">{{ row.versions[0].filename }}</a>
          </template>
        </el-table-column>
        <el-table-column :label="t('upload.source')" width="90">
          <template #default="{ row }">{{ t(`upload.sources.${row.source}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('upload.col.version')" width="70">
          <template #default="{ row }">V{{ row.current_version }}</template>
        </el-table-column>
        <el-table-column :label="t('upload.col.size')" width="90">
          <template #default="{ row }">{{ size(row.versions[0]?.size ?? null) }}</template>
        </el-table-column>
        <el-table-column :label="t('upload.col.uploaded')" width="170">
          <template #default="{ row }">
            {{ row.versions[0]?.uploaded_by ?? '' }}
            <div class="muted">{{ (row.versions[0]?.uploaded_at ?? '').slice(0, 16).replace('T', ' ') }}</div>
          </template>
        </el-table-column>
        <el-table-column v-if="!readonly" width="90" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" data-test="classify" @click="openClassify(row as StoredFile)">{{ t('drop.classify') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog v-model="classifyDialog.open" :title="t('drop.classifyTitle')" width="620px" data-test="classify-dialog">
      <p v-if="classifyDialog.file">{{ classifyDialog.file.versions[0]?.filename }}</p>
      <el-form label-width="80px">
        <el-form-item :label="t('drop.target')">
          <DeliverablePicker v-model="classifyDialog.target" :items="items" data-test="classify-target" />
        </el-form-item>
        <el-form-item :label="t('common.reason')"><el-input v-model="classifyDialog.reason" /></el-form-item>
      </el-form>
      <p class="muted">{{ t('drop.classifyNote') }}</p>
      <template #footer>
        <el-button @click="classifyDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!classifyDialog.target" :loading="classifyDialog.saving" data-test="classify-save" @click="saveClassify">
          {{ t('drop.classify') }}
        </el-button>
      </template>
    </el-dialog>
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
.mb {
  margin-bottom: 10px;
}
.ml {
  margin-left: 8px;
}
.w120 {
  width: 120px;
}
.w200 {
  width: 200px;
}
.w360 {
  width: 360px;
}
.drop {
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
  padding: 22px;
  text-align: center;
  color: var(--el-text-color-secondary);
}
.drop.dragging {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.pick {
  margin-top: 8px;
}
.row {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-top: 10px;
}
.row.stored {
  background: var(--el-fill-color-lighter);
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.original {
  font-weight: 500;
}
.name {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0;
  word-break: break-all;
}
.form :deep(.el-form-item) {
  margin-bottom: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.warn {
  color: var(--el-color-warning);
  font-size: 12px;
}
</style>
