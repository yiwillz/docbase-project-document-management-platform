<script setup lang="ts">
// 上传到应交项（方式一）：选文件 → 逐个预览（文件名、新文件还是升版、是否重复）→ 可改来源 / 标题 / 日期 / 升版对象 → 一起存入
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Item } from '@/api/deliverables'
import { showError } from '@/api/errors'
import { type Overrides, SOURCES, type UploadPlan, filesApi } from '@/api/files'

const props = defineProps<{ projectId: number; items: Item[] }>()
const emit = defineEmits<{ stored: [] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

interface Row {
  key: number
  file: File
  plan: UploadPlan | null
  overrides: Overrides
  busy: boolean
  error: string | null
  stored: string | null // 存入后的文件名
  progress: number | null // 大文件分块上传的进度（%）
}

const rows = ref<Row[]>([])
const saving = ref(false)
const dragging = ref(false)
const input = ref<HTMLInputElement>()
let seq = 0

// 按设备的应交项（或一个文件挂几台设备）一次只传一个文件：再传就是升版
const single = computed(() => props.items.length > 1 || props.items[0]?.kind === 'per_equipment')
const first = computed(() => props.items[0])
const target = computed(() => {
  const i = first.value
  if (!i) return ''
  const where = props.items
    .map((x) => [x.equipment_tag, x.equipment_name].filter(Boolean).join(' ') || x.module_name)
    .filter(Boolean)
    .join(' & ')
  return `${i.submittal_no} ${i.document_type_name}${where ? ' / ' + where : ''}`
})

watch(open, (value) => {
  if (value) rows.value = []
})

async function addFiles(list: FileList | File[] | null | undefined): Promise<void> {
  const picked = Array.from(list ?? [])
  if (!picked.length) return
  if (single.value && (picked.length > 1 || rows.value.some((r) => !r.stored))) {
    ElMessage.warning(t('upload.oneAtATime'))
    picked.splice(1)
    rows.value = rows.value.filter((r) => r.stored)
  }
  for (const file of picked) {
    const row: Row = { key: ++seq, file, plan: null, overrides: {}, busy: true, error: null, stored: null, progress: null }
    rows.value.push(row)
    const live = rows.value[rows.value.length - 1]!
    try {
      live.plan = await filesApi.preview(props.projectId, props.items.map((i) => i.id), file, (sent, total) => {
        live.progress = Math.floor((sent * 100) / total)
      })
    } catch (err) {
      live.error = err instanceof Error ? err.message : String(err)
      showError(err)
    } finally {
      live.busy = false
    }
  }
}

function onPick(event: Event): void {
  const el = event.target as HTMLInputElement
  addFiles(el.files)
  el.value = ''
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  addFiles(event.dataTransfer?.files)
}

async function change(row: Row, patch: Overrides): Promise<void> {
  if (!row.plan) return
  row.overrides = { ...row.overrides, ...patch }
  row.busy = true
  try {
    row.plan = await filesApi.replan(props.projectId, row.plan.token, row.overrides)
  } catch (err) {
    showError(err)
  } finally {
    row.busy = false
  }
}

function asTarget(row: Row): number | 'new' | 'auto' {
  return row.overrides.file_id ?? 'auto'
}

function setTarget(row: Row, value: number | 'new' | 'auto'): void {
  const next = { ...row.overrides }
  if (value === 'auto') delete next.file_id
  else next.file_id = value
  if (value !== 'new' && value !== 'auto') delete next.source // 升版时来源跟原文件
  row.overrides = next
  change(row, {})
}

function remove(row: Row): void {
  rows.value = rows.value.filter((r) => r !== row)
}

const ready = computed(() => rows.value.filter((r) => r.plan && r.plan.ok && !r.stored && !r.busy))

async function storeAll(): Promise<void> {
  saving.value = true
  let stored = 0
  for (const row of ready.value) {
    try {
      const result = await filesApi.confirm(props.projectId, row.plan!.token, row.overrides)
      row.stored = result.filename
      stored++
      if (result.recycled.length) ElMessage.info(t('upload.recycled', { v: result.recycled.map((n) => `V${n}`).join(', ') }))
    } catch (err) {
      showError(err)
    }
  }
  saving.value = false
  if (stored) {
    ElMessage.success(t('upload.stored', { n: stored }))
    emit('stored')
    if (rows.value.every((r) => r.stored)) open.value = false
  }
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}
</script>

<template>
  <el-dialog v-model="open" :title="t('upload.title')" width="900px" top="5vh" data-test="upload-dialog">
    <p class="target">
      {{ t('upload.target') }}<strong>{{ target }}</strong>
    </p>
    <div
      class="drop"
      :class="{ dragging }"
      data-test="upload-drop"
      @click="input?.click()"
      @dragover.prevent="dragging = true"
      @dragleave="dragging = false"
      @drop.prevent="onDrop"
    >
      <el-icon :size="28"><UploadFilled /></el-icon>
      <div>{{ single ? t('upload.dropOne') : t('upload.dropMany') }}</div>
      <input ref="input" type="file" hidden :multiple="!single" data-test="upload-input" @change="onPick" />
    </div>

    <div v-for="row in rows" :key="row.key" v-loading="row.busy && row.progress === null" class="row" data-test="upload-row">
      <div class="head">
        <span class="original">{{ row.file.name }}</span>
        <span class="muted">{{ size(row.file.size) }}</span>
        <div class="spacer" />
        <el-tag v-if="row.stored" type="success" size="small">{{ t('upload.done') }}</el-tag>
        <el-button v-else link type="danger" size="small" @click="remove(row)">{{ t('common.delete') }}</el-button>
      </div>
      <el-progress v-if="row.busy && row.progress !== null" :percentage="row.progress" />
      <el-alert v-if="row.error" type="error" :closable="false" :title="row.error" />
      <template v-if="row.plan">
        <div class="name" data-test="upload-name">
          <el-tag :type="row.plan.mode === 'new' ? 'success' : 'warning'" size="small">
            {{ row.plan.mode === 'new' ? t('upload.newFile') : t('upload.newVersion', { v: row.plan.version_no }) }}
          </el-tag>
          <span>{{ row.stored ?? row.plan.filename }}</span>
        </div>
        <el-alert v-if="row.plan.duplicate" type="error" :closable="false" show-icon data-test="upload-duplicate">
          {{ t('upload.duplicate', { name: row.plan.duplicate.filename, v: row.plan.duplicate.version_no }) }}
          <template v-if="row.plan.duplicate.recycled">{{ t('upload.duplicateRecycled') }}</template>
        </el-alert>
        <el-form v-if="!row.stored && !row.plan.duplicate" inline size="small" class="form">
          <el-form-item :label="t('upload.source')">
            <el-select
              :model-value="row.plan.source"
              :disabled="row.plan.mode === 'version'"
              class="w120"
              @change="(v: Overrides['source']) => change(row, { source: v })"
            >
              <el-option v-for="s in SOURCES" :key="s" :value="s" :label="t(`upload.sources.${s}`)" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="!row.plan.per_equipment && row.plan.source !== 'client'" :label="t('upload.fileTitle')">
            <el-input
              :model-value="row.overrides.title ?? row.plan.title"
              class="w200"
              data-test="upload-title"
              @update:model-value="(v: string) => (row.overrides = { ...row.overrides, title: v })"
              @change="() => change(row, {})"
            />
          </el-form-item>
          <el-form-item :label="t('upload.fileDate')">
            <el-date-picker
              :model-value="row.plan.file_date"
              type="date"
              value-format="YYYY-MM-DD"
              :clearable="false"
              class="w140"
              @update:model-value="(v: string) => change(row, { file_date: v })"
            />
            <span class="muted ml">{{ t(`upload.dateSource.${row.plan.date_source}`) }}</span>
          </el-form-item>
          <el-form-item v-if="row.plan.candidates.length" :label="t('upload.as')">
            <el-select :model-value="asTarget(row)" class="w220" @change="(v: number | 'new' | 'auto') => setTarget(row, v)">
              <el-option value="auto" :label="t('upload.asAuto')" />
              <el-option value="new" :label="t('upload.asNew')" />
              <el-option
                v-for="c in row.plan.candidates"
                :key="c.file_id"
                :value="c.file_id"
                :label="t('upload.asVersionOf', { title: c.title || '#' + c.file_id, v: c.current_version + 1 })"
              />
            </el-select>
          </el-form-item>
        </el-form>
        <p v-if="row.plan.original_version && !row.stored" class="muted">
          {{ t('upload.originalVersion', { v: row.plan.original_version }) }}
        </p>
      </template>
    </div>
    <p class="muted">{{ t('upload.note') }}</p>
    <template #footer>
      <el-button @click="open = false">{{ t('common.close') }}</el-button>
      <el-button type="primary" :disabled="!ready.length" :loading="saving" data-test="upload-confirm" @click="storeAll">
        {{ t('upload.confirm', { n: ready.length }) }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.target {
  margin: 0 0 10px;
}
.drop {
  border: 1px dashed var(--el-border-color);
  border-radius: 6px;
  padding: 18px;
  text-align: center;
  color: var(--el-text-color-secondary);
  cursor: pointer;
}
.drop.dragging,
.drop:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
.row {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 10px 12px;
  margin-top: 10px;
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
  font-family: var(--el-font-family);
  word-break: break-all;
}
.form {
  margin-top: 4px;
}
.form :deep(.el-form-item) {
  margin-bottom: 6px;
}
.spacer {
  flex: 1;
}
.w120 {
  width: 120px;
}
.w140 {
  width: 140px;
}
.w200 {
  width: 200px;
}
.w220 {
  width: 220px;
}
.ml {
  margin-left: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
