<script setup lang="ts">
// MDL 页：导出（内部预览 / 发给客户）、版本列表、两版对比、重新导出历史版本、导回（预览 → 确认）
import { Download, Promotion, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { showError } from '@/api/errors'
import { type Compare, type Fmt, type Lang, type MdlVersion, type ReturnPreview, mdlApi } from '@/api/mdl'
import IssueList from '@/components/IssueList.vue'
import { useProjectContext } from '@/composables/useProjectContext'

const { t, d } = useI18n()
const { projectId, readonly, readonlyNotice, can } = useProjectContext('export')

const options = reactive({ lang: 'en' as Lang, format: 'xlsx' as Fmt })
const versions = ref<MdlVersion[]>([])
const busy = ref(false)

async function loadVersions(): Promise<void> {
  try {
    versions.value = (await mdlApi.versions(projectId.value)).slice().reverse()
  } catch (err) {
    showError(err)
  }
}

async function run(fn: () => Promise<unknown>): Promise<void> {
  busy.value = true
  try {
    await fn()
  } catch (err) {
    showError(err)
  } finally {
    busy.value = false
  }
}

function exportDraft(): void {
  run(() => mdlApi.exportDraft(projectId.value, options.lang, options.format))
}

// ---------------------------------------------------------------- 发给客户
const issueDialog = reactive({ open: false, description: '' })

async function issue(): Promise<void> {
  await run(async () => {
    const result = await mdlApi.issue(
      projectId.value,
      options.lang,
      options.format,
      issueDialog.description.trim() || null,
    )
    issueDialog.open = false
    issueDialog.description = ''
    ElMessage.success({
      message: result.isNew ? t('mdl.issuedNew', { rev: result.revision }) : t('mdl.issuedSame', { rev: result.revision }),
      duration: 5000,
    })
    await loadVersions()
  })
}

function exportVersion(v: MdlVersion): void {
  run(() => mdlApi.exportVersion(projectId.value, v.rev_no, options.lang, options.format))
}

// ---------------------------------------------------------------- 对比
const compareState = reactive({ open: false, from: 0, to: 'current' as number | 'current', result: null as Compare | null })

async function openCompare(v?: MdlVersion): Promise<void> {
  compareState.from = v?.rev_no ?? versions.value[0]?.rev_no ?? 0
  compareState.to = 'current'
  compareState.open = true
  await runCompare()
}

async function runCompare(): Promise<void> {
  try {
    compareState.result = await mdlApi.compare(projectId.value, compareState.from, compareState.to)
  } catch (err) {
    showError(err)
  }
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

// ---------------------------------------------------------------- 导回
const fileInput = ref<HTMLInputElement>()
const returnState = reactive({ open: false, preview: null as ReturnPreview | null })

async function onFile(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (fileInput.value) fileInput.value.value = ''
  if (!file) return
  await run(async () => {
    returnState.preview = await mdlApi.returnPreview(projectId.value, file)
    returnState.open = true
  })
}

async function confirmReturn(): Promise<void> {
  const preview = returnState.preview
  if (!preview) return
  await run(async () => {
    const counts = await mdlApi.returnConfirm(projectId.value, preview.token)
    returnState.open = false
    ElMessage.success(t('mdl.returned', { d: counts.deliverables, r: counts.remarks }))
  })
}

watch(projectId, loadVersions)
onMounted(loadVersions)
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.mdl') }}</h2>
      <div class="spacer" />
      <el-button :icon="Refresh" @click="loadVersions">{{ t('common.refresh') }}</el-button>
    </div>

    <el-card v-if="!readonly" shadow="never">
      <template #header>{{ t('mdl.export') }}</template>
      <div class="toolbar">
        <span class="muted">{{ t('header.language') }}</span>
        <el-radio-group v-model="options.lang" size="small">
          <el-radio-button value="en">English</el-radio-button>
          <el-radio-button value="zh">{{ t('lang.zh-CN') }}</el-radio-button>
        </el-radio-group>
        <span class="muted ml">{{ t('mdl.format') }}</span>
        <el-radio-group v-model="options.format" size="small">
          <el-radio-button value="xlsx">Excel</el-radio-button>
          <el-radio-button value="pdf">PDF</el-radio-button>
        </el-radio-group>
        <div class="spacer" />
        <el-button :icon="Download" :loading="busy" data-test="export-draft" @click="exportDraft">{{ t('mdl.draft') }}</el-button>
        <el-button v-if="!readonly" type="primary" :icon="Promotion" :loading="busy" data-test="issue" @click="issueDialog.open = true">
          {{ t('mdl.issue') }}
        </el-button>
      </div>
      <p class="muted">{{ t('mdl.exportHint') }}</p>
    </el-card>

    <el-card shadow="never">
      <template #header>
        <div class="toolbar">
          <span>{{ t('mdl.versions') }}</span>
          <div class="spacer" />
          <el-button v-if="versions.length" size="small" @click="openCompare()">{{ t('mdl.compare') }}</el-button>
        </div>
      </template>
      <el-empty v-if="!versions.length" :description="t('mdl.noVersions')" :image-size="60" />
      <el-table v-else :data="versions" size="small" data-test="versions">
        <el-table-column prop="label" :label="t('mdl.revision')" width="90" />
        <el-table-column :label="t('mdl.date')" width="160">
          <template #default="{ row }">{{ d(new Date(row.frozen_at), 'long') }}</template>
        </el-table-column>
        <el-table-column prop="description" :label="t('mdl.description')" min-width="260" />
        <el-table-column prop="frozen_by_name" :label="t('mdl.by')" width="120" />
        <el-table-column :label="t('mdl.counts')" width="150">
          <template #default="{ row }">{{ t('mdl.countsText', { m: row.milestones, d: row.document_types }) }}</template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="200">
          <template #default="{ row }">
            <el-button v-if="!readonly" link type="primary" @click="exportVersion(row as MdlVersion)">{{ t('mdl.reexport') }}</el-button>
            <el-button link @click="openCompare(row as MdlVersion)">{{ t('mdl.compareWithCurrent') }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="can('mdl.import')" shadow="never">
      <template #header>{{ t('mdl.return') }}</template>
      <p class="muted">{{ t('mdl.returnHint') }}</p>
      <el-button :icon="Upload" :loading="busy" @click="fileInput?.click()">{{ t('mdl.upload') }}</el-button>
      <input ref="fileInput" type="file" accept=".xlsx" hidden data-test="return-file" @change="onFile" />
    </el-card>

    <!-- 发给客户 -->
    <el-dialog v-model="issueDialog.open" :title="t('mdl.issue')" width="520px">
      <p class="muted">{{ t('mdl.issueHint') }}</p>
      <el-input v-model="issueDialog.description" :placeholder="t('mdl.descriptionHint')" data-test="issue-description" />
      <template #footer>
        <el-button @click="issueDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :loading="busy" data-test="issue-confirm" @click="issue">{{ t('mdl.issueConfirm') }}</el-button>
      </template>
    </el-dialog>

    <!-- 对比 -->
    <el-dialog v-model="compareState.open" :title="t('mdl.compare')" width="760px">
      <div class="toolbar mb">
        <el-select v-model="compareState.from" class="w140" @change="runCompare">
          <el-option v-for="v in versions" :key="v.rev_no" :value="v.rev_no" :label="v.label" />
        </el-select>
        <span>→</span>
        <el-select v-model="compareState.to" class="w140" @change="runCompare">
          <el-option value="current" :label="t('mdl.current')" />
          <el-option v-for="v in versions" :key="v.rev_no" :value="v.rev_no" :label="v.label" />
        </el-select>
      </div>
      <template v-if="compareState.result">
        <el-empty v-if="!compareState.result.has_changes" :description="t('mdl.noChanges')" :image-size="50" />
        <template v-for="section in (['milestones', 'document_types'] as const)" :key="section">
          <template v-for="kind in (['added', 'removed', 'modified'] as const)" :key="kind">
            <div v-if="compareState.result[section][kind].length" class="diff-block" :data-test="`diff-${section}-${kind}`">
              <h4>{{ t('mdl.diffHeading', { section: t(`mdl.section.${section}`), kind: t(`mdl.diff.${kind}`), n: compareState.result[section][kind].length }) }}</h4>
              <div v-for="e in compareState.result[section][kind]" :key="e.id" class="diff-row">
                <b>{{ e.submittal_no ?? e.code }}</b> {{ e.name }}
                <div v-for="(v, f) in e.changes ?? {}" :key="f" class="muted">
                  {{ t('equipment.change', { field: t(`mdl.field.${f}`, String(f)), from: fmt(v[0]), to: fmt(v[1]) }) }}
                </div>
              </div>
            </div>
          </template>
        </template>
      </template>
    </el-dialog>

    <!-- 导回预览 -->
    <el-dialog v-model="returnState.open" :title="t('mdl.returnPreview')" width="760px">
      <template v-if="returnState.preview">
        <IssueList :errors="returnState.preview.errors" :warnings="returnState.preview.warnings" />
        <el-empty v-if="!returnState.preview.changes.length" :description="t('mdl.noReturnChanges')" :image-size="50" />
        <el-table v-else :data="returnState.preview.changes" size="small" max-height="400" data-test="return-changes">
          <el-table-column prop="row" :label="t('mdl.row')" width="60" />
          <el-table-column prop="label" :label="t('mdl.item')" min-width="220" />
          <el-table-column :label="t('mdl.changes')" min-width="300">
            <template #default="{ row }">
              <div v-for="(v, f) in row.changes" :key="f">
                {{ t('equipment.change', { field: t(`mdl.field.${f}`, String(f)), from: fmt(v[0]), to: fmt(v[1]) }) }}
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="returnState.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :disabled="!returnState.preview?.ok || !returnState.preview.changes.length"
          :loading="busy"
          data-test="return-confirm"
          @click="confirmReturn"
        >
          {{ t('import.confirm') }}
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
  margin: 0;
  font-size: 18px;
}
.spacer {
  flex: 1;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ml {
  margin-left: 12px;
}
.mb {
  margin-bottom: 10px;
}
.w140 {
  width: 140px;
}
.diff-block h4 {
  margin: 12px 0 6px;
}
.diff-row {
  padding: 4px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
</style>
