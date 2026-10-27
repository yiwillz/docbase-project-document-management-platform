<script setup lang="ts">
// 文件详情：所在位置、版本历史（下载 / 预览）、操作记录；上传新版本、重新归类；右边在线预览（PDF、图片、纯文本）
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type DetailVersion, type FileDetail, browseApi } from '@/api/browse'
import type { Item } from '@/api/deliverables'
import { showError } from '@/api/errors'
import { filesApi } from '@/api/files'
import DeliverablePicker from '@/components/DeliverablePicker.vue'
import StatusTag from '@/components/StatusTag.vue'
import UploadDialog from '@/components/UploadDialog.vue'

const props = defineProps<{ projectId: number; fileId: number | null; items: Item[]; readonly?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

const detail = ref<FileDetail | null>(null)
const loading = ref(false)
const previewing = ref<DetailVersion | null>(null)
const tab = ref<'versions' | 'logs'>('versions')
const uploadOpen = ref(false)

async function load(): Promise<void> {
  if (!props.fileId) return
  loading.value = true
  try {
    detail.value = await browseApi.detail(props.projectId, props.fileId)
    const first = detail.value.versions.find((v) => v.preview)
    previewing.value = first ?? null
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

watch(open, (value) => {
  if (value) {
    tab.value = 'versions'
    load()
  }
})

const uploadItems = computed(() => props.items.filter((i) => detail.value?.deliverable_ids.includes(i.id)))
const isImage = computed(() => /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(previewing.value?.original_name ?? ''))

function where(loc: FileDetail['locations'][number]): string {
  return [loc.milestone_code, `${loc.submittal_no ?? ''} ${loc.document_type_name ?? ''}`.trim(), loc.where]
    .filter(Boolean)
    .join(' / ')
}

function size(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return Array.isArray(value) ? value.join(', ') : String(value)
}

function onStored(): void {
  load()
  emit('changed')
}

// ---------------------------------------------------------------- 重新归类
const moving = reactive({ open: false, target: '', reason: '', saving: false })

function openMove(): void {
  Object.assign(moving, { open: true, target: detail.value?.deliverable_ids.join(',') ?? '', reason: '', saving: false })
}

async function saveMove(): Promise<void> {
  const d = detail.value
  if (!d) return
  moving.saving = true
  try {
    const ids = moving.target ? moving.target.split(',').map(Number) : []
    await browseApi.move(props.projectId, d.id, ids, moving.reason.trim() || undefined)
    ElMessage.success(ids.length ? t('browse.moved') : t('browse.movedOut'))
    moving.open = false
    await load()
    emit('changed')
  } catch (err) {
    showError(err)
  } finally {
    moving.saving = false
  }
}
</script>

<template>
  <el-drawer v-model="open" :title="detail?.filename ?? ''" size="92%" data-test="file-detail">
    <div v-loading="loading" class="layout">
      <div v-if="detail" class="left">
        <el-descriptions :column="1" border size="small" class="mb">
          <el-descriptions-item :label="t('browse.location')">
            <template v-if="detail.locations.length">
              <div v-for="loc in detail.locations" :key="loc.deliverable_id" class="loc">
                {{ where(loc) }}
                <StatusTag v-if="loc.status" :status="loc.status" :overdue="false" />
              </div>
            </template>
            <el-tag v-else type="info" size="small">{{ t('drop.unclassified') }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item :label="t('upload.source')">{{ t(`upload.sources.${detail.source}`) }}</el-descriptions-item>
          <el-descriptions-item v-if="detail.title" :label="t('upload.fileTitle')">{{ detail.title }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="!readonly" class="toolbar mb">
          <el-button v-if="uploadItems.length" type="primary" size="small" data-test="detail-upload" @click="uploadOpen = true">
            {{ t('upload.uploadNew') }}
          </el-button>
          <el-tooltip :disabled="!detail.submitted" :content="t('browse.submittedHint')">
            <span>
              <el-button size="small" :disabled="detail.submitted" data-test="detail-move" @click="openMove">
                {{ detail.locations.length ? t('browse.move') : t('drop.classify') }}
              </el-button>
            </span>
          </el-tooltip>
        </div>

        <el-radio-group v-model="tab" size="small" class="mb">
          <el-radio-button value="versions">{{ t('browse.versions', { n: detail.versions.length }) }}</el-radio-button>
          <el-radio-button value="logs" data-test="detail-logs-tab">{{ t('browse.logs', { n: detail.logs.length }) }}</el-radio-button>
        </el-radio-group>

        <el-table
          v-if="tab === 'versions'"
          :data="detail.versions"
          size="small"
          highlight-current-row
          data-test="detail-versions"
          :row-class-name="({ row }: { row: DetailVersion }) => (row.recycled_at ? 'recycled' : '')"
        >
          <el-table-column :label="t('upload.col.version')" width="60">
            <template #default="{ row }">V{{ row.version_no }}</template>
          </el-table-column>
          <el-table-column :label="t('upload.col.filename')" min-width="260">
            <template #default="{ row }">
              <div>{{ row.filename }}</div>
              <div class="muted">{{ t('upload.originalName', { name: row.original_name }) }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('upload.col.uploaded')" width="140">
            <template #default="{ row }">
              {{ row.uploaded_by ?? '' }}
              <div class="muted">{{ String(row.uploaded_at).slice(0, 16).replace('T', ' ') }} · {{ size(row.size) }}</div>
            </template>
          </el-table-column>
          <el-table-column width="150">
            <template #default="{ row }">
              <el-tag v-if="row.purged_at" type="info" size="small">{{ t('upload.state.purged') }}</el-tag>
              <template v-else>
                <el-button v-if="row.preview" link type="primary" size="small" data-test="version-preview" @click="previewing = row as DetailVersion">
                  {{ t('browse.preview') }}
                </el-button>
                <a :href="filesApi.downloadUrl(projectId, row.id)" class="link">{{ t('common.download') }}</a>
                <el-tag v-if="row.recycled_at" type="warning" size="small">{{ t('upload.state.recycled') }}</el-tag>
              </template>
              <el-tag v-if="row.protected" size="small" effect="plain">{{ t('upload.state.protected') }}</el-tag>
            </template>
          </el-table-column>
        </el-table>

        <el-table v-else :data="detail.logs" size="small" data-test="detail-logs">
          <el-table-column :label="t('changes.time')" width="140">
            <template #default="{ row }">{{ String(row.created_at).slice(0, 16).replace('T', ' ') }}</template>
          </el-table-column>
          <el-table-column prop="actor" :label="t('changes.actor')" width="90" />
          <el-table-column :label="t('changes.action')" width="70">
            <template #default="{ row }">{{ t(`history.action.${row.action}`) }}</template>
          </el-table-column>
          <el-table-column :label="t('changes.fields')" min-width="220">
            <template #default="{ row }">
              <div v-for="(v, f) in row.changes" :key="f" class="muted">{{ f }}: {{ fmt(v[0]) }} → {{ fmt(v[1]) }}</div>
              <div v-if="row.reason">{{ t('browse.reasonLine', { reason: row.reason }) }}</div>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <div class="right" data-test="detail-preview">
        <template v-if="previewing">
          <div class="muted mb">{{ t('browse.previewing', { v: previewing.version_no, name: previewing.original_name }) }}</div>
          <img v-if="isImage" :src="browseApi.previewUrl(projectId, previewing.id)" class="img" alt="" />
          <iframe v-else :src="browseApi.previewUrl(projectId, previewing.id)" class="frame" :title="previewing.original_name" />
        </template>
        <el-empty v-else :description="t('browse.noPreview')" :image-size="70" />
      </div>
    </div>

    <UploadDialog v-if="uploadItems.length" v-model="uploadOpen" :project-id="projectId" :items="uploadItems" @stored="onStored" />

    <el-dialog v-model="moving.open" :title="t('browse.moveTitle')" width="620px" append-to-body data-test="move-dialog">
      <el-form label-width="80px">
        <el-form-item :label="t('drop.target')">
          <DeliverablePicker v-model="moving.target" :items="items" allow-unclassified data-test="move-target" />
        </el-form-item>
        <el-form-item :label="t('common.reason')"><el-input v-model="moving.reason" /></el-form-item>
      </el-form>
      <p class="muted">{{ t('browse.moveNote') }}</p>
      <template #footer>
        <el-button @click="moving.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="moving.saving" data-test="move-save" @click="saveMove">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<style scoped>
.layout {
  display: flex;
  gap: 16px;
  height: 100%;
}
.left {
  flex: 0 0 52%;
  overflow: auto;
}
.right {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.frame {
  flex: 1;
  width: 100%;
  min-height: 70vh;
  border: 1px solid var(--el-border-color-lighter);
}
.img {
  max-width: 100%;
  max-height: 75vh;
  object-fit: contain;
  border: 1px solid var(--el-border-color-lighter);
}
.toolbar {
  display: flex;
  gap: 8px;
}
.mb {
  margin-bottom: 10px;
}
.loc {
  display: flex;
  align-items: center;
  gap: 6px;
}
.link {
  margin: 0 6px;
  font-size: 12px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
:deep(.recycled) {
  color: var(--el-text-color-placeholder);
}
</style>
