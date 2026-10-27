<script setup lang="ts">
// 应交项的文件：每个文件的版本历史、下载；进回收区的版本标灰（清理前仍可下载）
import { Upload } from '@element-plus/icons-vue'
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { Item } from '@/api/deliverables'
import { showError } from '@/api/errors'
import { type FileVersion, type StoredFile, filesApi } from '@/api/files'
import UploadDialog from '@/components/UploadDialog.vue'

const props = defineProps<{ projectId: number; item: Item | null; readonly?: boolean }>()
const emit = defineEmits<{ changed: [] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

const files = ref<StoredFile[]>([])
const loading = ref(false)
const uploadOpen = ref(false)

async function load(): Promise<void> {
  if (!props.item) return
  loading.value = true
  try {
    files.value = await filesApi.ofDeliverable(props.projectId, props.item.id)
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

watch(open, (value) => {
  if (value) load()
})

function onStored(): void {
  load()
  emit('changed')
}

function size(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function state(v: FileVersion): 'purged' | 'recycled' | 'current' | 'old' {
  if (v.purged_at) return 'purged'
  if (v.recycled_at) return 'recycled'
  return 'current'
}

function title(): string {
  const i = props.item
  if (!i) return ''
  const where = [i.equipment_tag, i.equipment_name].filter(Boolean).join(' ') || i.module_name
  return `${i.submittal_no} ${i.document_type_name}${where ? ' / ' + where : ''}`
}
</script>

<template>
  <el-drawer v-model="open" :title="title()" size="70%" data-test="files-drawer">
    <div class="toolbar">
      <el-button v-if="!readonly && item" type="primary" :icon="Upload" data-test="files-upload" @click="uploadOpen = true">
        {{ files.length ? t('upload.uploadNew') : t('upload.upload') }}
      </el-button>
    </div>
    <el-empty v-if="!files.length && !loading" :description="t('upload.noFiles')" :image-size="60" />
    <div v-for="f in files" :key="f.id" v-loading="loading" class="file" data-test="file-card">
      <div class="head">
        <strong>{{ f.title || '#' + f.id }}</strong>
        <el-tag size="small" effect="plain">{{ t(`upload.sources.${f.source}`) }}</el-tag>
        <span class="muted">{{ t('upload.versionsCount', { n: f.versions.length }) }}</span>
        <span v-if="f.deliverable_ids.length > 1" class="muted">{{ t('upload.sharedBy', { n: f.deliverable_ids.length }) }}</span>
      </div>
      <el-table :data="f.versions" size="small" :row-class-name="({ row }) => (row.recycled_at ? 'recycled' : '')">
        <el-table-column :label="t('upload.col.version')" width="70">
          <template #default="{ row }">V{{ row.version_no }}</template>
        </el-table-column>
        <el-table-column :label="t('upload.col.filename')" min-width="320">
          <template #default="{ row }">
            <a v-if="!row.purged_at" :href="filesApi.downloadUrl(projectId, row.id)" data-test="version-download">{{ row.filename }}</a>
            <span v-else>{{ row.filename }}</span>
            <div class="muted">{{ t('upload.originalName', { name: row.original_name }) }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="file_date" :label="t('upload.fileDate')" width="100" />
        <el-table-column :label="t('upload.col.size')" width="80">
          <template #default="{ row }">{{ size(row.size) }}</template>
        </el-table-column>
        <el-table-column :label="t('upload.col.uploaded')" width="150">
          <template #default="{ row }">
            {{ row.uploaded_by ?? '' }}
            <div class="muted">{{ row.uploaded_at.slice(0, 16).replace('T', ' ') }}</div>
          </template>
        </el-table-column>
        <el-table-column :label="t('upload.col.state')" width="130">
          <template #default="{ row }">
            <el-tag v-if="state(row as FileVersion) === 'purged'" type="info" size="small">{{ t('upload.state.purged') }}</el-tag>
            <el-tag v-else-if="state(row as FileVersion) === 'recycled'" type="warning" size="small">{{ t('upload.state.recycled') }}</el-tag>
            <el-tag v-if="row.protected" size="small" effect="plain">{{ t('upload.state.protected') }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <p class="muted">{{ t('upload.retentionNote') }}</p>
    <UploadDialog v-if="item" v-model="uploadOpen" :project-id="projectId" :items="[item]" @stored="onStored" />
  </el-drawer>
</template>

<style scoped>
.toolbar {
  margin-bottom: 10px;
}
.file {
  margin-bottom: 16px;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
:deep(.recycled) {
  color: var(--el-text-color-placeholder);
}
</style>
