<script setup lang="ts">
// 传送单页：列表（未回复 / 客户超期标出）、生成 / 补记、详情（客户回复、视为批准、改表头、导出、作废）、客户填好后导回
import { Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { showError } from '@/api/errors'
import type { Fmt, Lang } from '@/api/mdl'
import { type ReviewCode, settingsApi } from '@/api/settings'
import {
  type ReturnPreview,
  type TransmittalDetail,
  type TransmittalItem,
  type TransmittalRow,
  transmittalsApi,
} from '@/api/transmittals'
import IssueList from '@/components/IssueList.vue'
import StatusTag from '@/components/StatusTag.vue'
import TransmittalCreateDialog from '@/components/TransmittalCreateDialog.vue'
import { useProjectContext } from '@/composables/useProjectContext'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly, readonlyNotice, can } = useProjectContext('deliverable.update')

const rows = ref<TransmittalRow[]>([])
const includeVoid = ref(false)
const loading = ref(false)
const codes = ref<ReviewCode[]>([])
const createOpen = ref(false)
const today = new Date().toISOString().slice(0, 10)

async function load(): Promise<void> {
  loading.value = true
  try {
    ;[rows.value, codes.value] = await Promise.all([
      transmittalsApi.list(projectId.value, includeVoid.value),
      settingsApi.reviewCodes(projectId.value),
    ])
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

function state(row: TransmittalRow): 'void' | 'replied' | 'partial' | 'overdue' | 'waiting' {
  if (row.void) return 'void'
  if (row.items && row.replied === row.items) return 'replied'
  if (row.review_due && row.review_due < today) return 'overdue'
  return row.replied ? 'partial' : 'waiting'
}

const STATE_TAG = { void: 'info', replied: 'success', partial: 'warning', overdue: 'danger', waiting: '' } as const

// ---------------------------------------------------------------- 详情
const detail = ref<TransmittalDetail | null>(null)
const drawer = ref(false)

async function openDetail(id: number): Promise<void> {
  try {
    detail.value = await transmittalsApi.get(projectId.value, id)
    selectedItems.value = []
    drawer.value = true
    if (Number(route.query.open) !== id) router.replace({ query: { ...route.query, open: String(id) } })
  } catch (err) {
    showError(err)
  }
}

watch(drawer, (value) => {
  if (!value && route.query.open) router.replace({ query: { ...route.query, open: undefined } })
})

async function refreshDetail(): Promise<void> {
  if (detail.value) detail.value = await transmittalsApi.get(projectId.value, detail.value.id)
  await load()
}

function where(item: TransmittalItem): string {
  const d = item.deliverable
  if (!d) return ''
  return [d.equipment_tag, d.equipment_name].filter(Boolean).join(' ') || d.module_name || t('matrix.wholeType')
}

function codeLabel(code: string | null): string {
  if (!code) return ''
  const c = codes.value.find((x) => x.code === code)
  return c ? `${c.code} ${t('lang.code') === 'zh' && c.label_zh ? c.label_zh : c.label_en}` : code
}

// ---------------------------------------------------------------- 客户回复
const selectedItems = ref<TransmittalItem[]>([])
const itemsTable = ref<{ clearSelection: () => void }>()
const reply = reactive({
  open: false,
  scope: 'all' as 'all' | 'selected',
  code: '',
  reply_date: today as string | null,
  reply_ref: '',
  reply_comments: '',
  deemed: false,
  deemed_basis: '',
  saving: false,
})

function openReply(): void {
  Object.assign(reply, {
    open: true,
    scope: selectedItems.value.length ? 'selected' : 'all',
    code: '',
    reply_date: today,
    reply_ref: '',
    reply_comments: '',
    deemed: false,
    deemed_basis: '',
  })
}

async function saveReply(): Promise<void> {
  const tr = detail.value
  if (!tr || !reply.code) return
  if (reply.deemed && !reply.deemed_basis.trim()) {
    ElMessage.error(t('transmittal.deemedNeedsBasis'))
    return
  }
  const common = {
    reply_date: reply.reply_date,
    reply_ref: reply.reply_ref.trim() || null,
    reply_comments: reply.reply_comments.trim() || null,
    deemed: reply.deemed,
    deemed_basis: reply.deemed ? reply.deemed_basis.trim() : null,
  }
  const body =
    reply.scope === 'all'
      ? { ...common, code: reply.code }
      : { ...common, items: selectedItems.value.map((i) => ({ item_id: i.item_id, code: reply.code })) }
  reply.saving = true
  try {
    const result = await transmittalsApi.reply(projectId.value, tr.id, body)
    ElMessage.success(t('transmittal.replied', { n: result.updated }))
    reply.open = false
    itemsTable.value?.clearSelection()
    selectedItems.value = []
    await refreshDetail()
  } catch (err) {
    showError(err)
  } finally {
    reply.saving = false
  }
}

// ---------------------------------------------------------------- 改表头、作废、导出
const header = reactive({ open: false, subject: '', submitted_date: null as string | null, external_ref: '', remarks: '', reason: '' })

function openHeader(): void {
  const tr = detail.value
  if (!tr) return
  Object.assign(header, {
    open: true,
    subject: tr.subject ?? '',
    submitted_date: tr.submitted_date,
    external_ref: tr.external_ref ?? '',
    remarks: tr.remarks ?? '',
    reason: '',
  })
}

async function saveHeader(): Promise<void> {
  const tr = detail.value
  if (!tr) return
  try {
    await transmittalsApi.update(projectId.value, tr.id, {
      subject: header.subject.trim() || null,
      submitted_date: header.submitted_date,
      external_ref: header.external_ref.trim() || null,
      remarks: header.remarks.trim() || null,
      reason: header.reason.trim() || undefined,
    })
    header.open = false
    ElMessage.success(t('common.saved'))
    await refreshDetail()
  } catch (err) {
    showError(err)
  }
}

async function voidTransmittal(): Promise<void> {
  const tr = detail.value
  if (!tr) return
  try {
    const { value } = await ElMessageBox.prompt(t('transmittal.voidPrompt', { no: tr.number }), t('transmittal.void'), {
      confirmButtonText: t('common.ok'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => !!v?.trim() || t('transmittal.voidReasonRequired'),
    })
    await transmittalsApi.void(projectId.value, tr.id, value.trim())
    ElMessage.success(t('transmittal.voided'))
    await refreshDetail()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') showError(err)
  }
}

const exportOptions = reactive({ lang: 'en' as Lang, format: 'xlsx' as Fmt })

function exportTransmittal(): void {
  const tr = detail.value
  if (tr) transmittalsApi.export(projectId.value, tr.id, exportOptions.lang, exportOptions.format).catch(showError)
}

// ---------------------------------------------------------------- 客户填好的传送单导回
const fileInput = ref<HTMLInputElement>()
const back = reactive({ open: false, preview: null as ReturnPreview | null, busy: false })

async function onFile(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (fileInput.value) fileInput.value.value = ''
  if (!file) return
  back.busy = true
  try {
    back.preview = await transmittalsApi.returnPreview(projectId.value, file)
    back.open = true
  } catch (err) {
    showError(err)
  } finally {
    back.busy = false
  }
}

async function confirmReturn(): Promise<void> {
  const preview = back.preview
  if (!preview) return
  back.busy = true
  try {
    const result = await transmittalsApi.returnConfirm(projectId.value, preview.token)
    ElMessage.success(t('transmittal.replied', { n: result.updated }))
    back.open = false
    await load()
  } catch (err) {
    showError(err)
  } finally {
    back.busy = false
  }
}

function fmt(value: unknown): string {
  return value === null || value === undefined || value === '' ? '—' : String(value)
}

const replyCount = computed(() => detail.value?.items.filter((i) => i.review_code).length ?? 0)

watch(includeVoid, load)
watch(projectId, load)
onMounted(async () => {
  await load()
  const id = Number(route.query.open)
  if (id) await openDetail(id)
})
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.transmittals') }}</h2>
      <el-checkbox v-model="includeVoid">{{ t('transmittal.showVoid') }}</el-checkbox>
      <div class="spacer" />
      <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
      <template v-if="!readonly">
        <el-button :icon="Upload" :loading="back.busy" @click="fileInput?.click()">{{ t('transmittal.importReturn') }}</el-button>
        <el-button type="primary" :icon="Plus" data-test="tr-new" @click="createOpen = true">{{ t('transmittal.new') }}</el-button>
      </template>
      <input ref="fileInput" type="file" accept=".xlsx" hidden data-test="tr-return-file" @change="onFile" />
    </div>

    <el-card shadow="never">
      <el-empty v-if="!rows.length && !loading" :description="t('transmittal.none')" />
      <el-table v-else v-loading="loading" :data="rows" size="small" data-test="tr-list" @row-click="(row: TransmittalRow) => openDetail(row.id)">
        <el-table-column prop="number" :label="t('transmittal.number')" width="190" />
        <el-table-column prop="subject" :label="t('transmittal.subject')" min-width="220" />
        <el-table-column prop="submitted_date" :label="t('transmittal.submittedDate')" width="110" />
        <el-table-column :label="t('transmittal.source')" width="110">
          <template #default="{ row }">{{ t(`transmittal.sources.${row.source}`) }}</template>
        </el-table-column>
        <el-table-column :label="t('transmittal.progress')" width="110">
          <template #default="{ row }">{{ t('transmittal.repliedOf', { a: row.replied, n: row.items }) }}</template>
        </el-table-column>
        <el-table-column prop="review_due" :label="t('transmittal.reviewDue')" width="110" />
        <el-table-column :label="t('transmittal.state')" width="120">
          <template #default="{ row }">
            <el-tag :type="STATE_TAG[state(row as TransmittalRow)] || undefined" size="small">{{ t(`transmittal.states.${state(row as TransmittalRow)}`) }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <TransmittalCreateDialog v-model="createOpen" :project-id="projectId" @created="(tr) => { load(); openDetail(tr.id) }" />

    <!-- 详情 -->
    <el-drawer v-model="drawer" :title="detail ? `${detail.number}  ${detail.subject ?? ''}` : ''" size="88%" data-test="tr-detail">
      <template v-if="detail">
        <el-alert v-if="detail.void" type="info" :closable="false" show-icon :title="t('transmittal.isVoid', { reason: detail.void_reason })" />
        <el-descriptions :column="4" border size="small" class="mb">
          <el-descriptions-item :label="t('transmittal.submittedDate')">{{ fmt(detail.submitted_date) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.reviewDue')">{{ fmt(detail.review_due) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.source')">{{ t(`transmittal.sources.${detail.source}`) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.progress')">{{ t('transmittal.repliedOf', { a: replyCount, n: detail.items.length }) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.externalRef')">{{ fmt(detail.external_ref) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.method')">{{ detail.method ? t(`transmittal.methods.${detail.method}`, detail.method) : '—' }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.createdBy')">{{ fmt(detail.created_by_name) }}</el-descriptions-item>
          <el-descriptions-item :label="t('transmittal.remarks')">{{ fmt(detail.remarks) }}</el-descriptions-item>
        </el-descriptions>

        <div class="toolbar mb">
          <template v-if="!readonly && !detail.void">
            <el-button type="primary" data-test="tr-reply" @click="openReply">
              {{ selectedItems.length ? t('transmittal.replySelected', { n: selectedItems.length }) : t('transmittal.replyAll') }}
            </el-button>
            <el-button @click="openHeader">{{ t('transmittal.editHeader') }}</el-button>
            <el-button type="danger" plain @click="voidTransmittal">{{ t('transmittal.void') }}</el-button>
          </template>
          <div class="spacer" />
          <el-radio-group v-model="exportOptions.lang" size="small">
            <el-radio-button value="en">EN</el-radio-button>
            <el-radio-button value="zh">{{ t('lang.zh-CN') }}</el-radio-button>
          </el-radio-group>
          <el-radio-group v-model="exportOptions.format" size="small">
            <el-radio-button value="xlsx">Excel</el-radio-button>
            <el-radio-button value="pdf">PDF</el-radio-button>
          </el-radio-group>
          <el-button v-if="can('export')" data-test="tr-export" @click="exportTransmittal">{{ t('transmittal.export') }}</el-button>
        </div>
        <p class="muted">{{ t('transmittal.exportHint') }}</p>

        <el-table
          ref="itemsTable"
          :data="detail.items"
          size="small"
          row-key="item_id"
          data-test="tr-items"
          @selection-change="(sel: unknown[]) => (selectedItems = sel as TransmittalItem[])"
        >
          <el-table-column v-if="!readonly && !detail.void" type="selection" width="36" />
          <el-table-column :label="t('transmittal.col.no')" width="70">
            <template #default="{ row }">{{ row.deliverable?.submittal_no }}</template>
          </el-table-column>
          <el-table-column :label="t('transmittal.col.document')" min-width="160">
            <template #default="{ row }">{{ row.deliverable?.document_type_name }}</template>
          </el-table-column>
          <el-table-column :label="t('transmittal.col.where')" min-width="140">
            <template #default="{ row }">{{ where(row as TransmittalItem) }}</template>
          </el-table-column>
          <el-table-column prop="revision" :label="t('transmittal.col.revision')" width="60" />
          <el-table-column :label="t('transmittal.col.code')" min-width="160">
            <template #default="{ row }">
              {{ codeLabel(row.review_code) }}
              <el-tag v-if="row.review_code && row.review_final === false" size="small" type="warning">{{ t('transmittal.notFinal') }}</el-tag>
              <el-tag v-if="row.deemed" size="small" type="info">{{ t('transmittal.deemed') }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reply_date" :label="t('transmittal.col.replyDate')" width="100" />
          <el-table-column prop="reply_ref" :label="t('transmittal.col.replyRef')" width="120" />
          <el-table-column prop="reply_comments" :label="t('transmittal.col.comments')" min-width="140" show-overflow-tooltip />
          <el-table-column :label="t('matrix.col.status')" width="120">
            <template #default="{ row }">
              <StatusTag v-if="row.deliverable" :status="row.deliverable.status" :overdue="row.deliverable.overdue" />
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-drawer>

    <!-- 录入客户回复 -->
    <el-dialog v-model="reply.open" :title="t('transmittal.replyTitle')" width="560px" data-test="tr-reply-dialog">
      <el-form label-width="110px">
        <el-form-item :label="t('transmittal.replyScope')">
          <el-radio-group v-model="reply.scope">
            <el-radio value="all">{{ t('transmittal.scopeAll', { n: detail?.items.length ?? 0 }) }}</el-radio>
            <el-radio value="selected" :disabled="!selectedItems.length">{{ t('transmittal.scopeSelected', { n: selectedItems.length }) }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="t('transmittal.col.code')" required>
          <el-select v-model="reply.code" data-test="tr-code">
            <el-option v-for="c in codes" :key="c.code" :value="c.code" :label="codeLabel(c.code)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('transmittal.col.replyDate')">
          <el-date-picker v-model="reply.reply_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('transmittal.col.replyRef')">
          <el-input v-model="reply.reply_ref" :placeholder="t('transmittal.replyRefHint')" />
        </el-form-item>
        <el-form-item :label="t('transmittal.col.comments')"><el-input v-model="reply.reply_comments" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('transmittal.deemed')">
          <el-switch v-model="reply.deemed" />
          <span class="muted ml">{{ t('transmittal.deemedHint') }}</span>
        </el-form-item>
        <el-form-item v-if="reply.deemed" :label="t('transmittal.deemedBasis')" required>
          <el-input v-model="reply.deemed_basis" :placeholder="t('transmittal.deemedBasisHint')" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="reply.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!reply.code" :loading="reply.saving" data-test="tr-reply-save" @click="saveReply">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 改表头 -->
    <el-dialog v-model="header.open" :title="t('transmittal.editHeader')" width="520px">
      <el-form label-width="110px">
        <el-form-item :label="t('transmittal.subject')"><el-input v-model="header.subject" /></el-form-item>
        <el-form-item :label="t('transmittal.submittedDate')">
          <el-date-picker v-model="header.submitted_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('transmittal.externalRef')"><el-input v-model="header.external_ref" /></el-form-item>
        <el-form-item :label="t('transmittal.remarks')"><el-input v-model="header.remarks" /></el-form-item>
        <el-form-item :label="t('common.reason')"><el-input v-model="header.reason" /></el-form-item>
      </el-form>
      <p class="muted">{{ t('transmittal.headerNote') }}</p>
      <template #footer>
        <el-button @click="header.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveHeader">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 客户填好的传送单导回 -->
    <el-dialog v-model="back.open" :title="t('transmittal.importReturn')" width="760px">
      <template v-if="back.preview">
        <p v-if="back.preview.number">
          {{ t('transmittal.returnFor', { no: back.preview.number, ref: back.preview.reply_ref ?? '—' }) }}
        </p>
        <IssueList :errors="back.preview.errors" :warnings="back.preview.warnings" />
        <el-empty v-if="back.preview.ok && !back.preview.changes.length" :description="t('mdl.noReturnChanges')" :image-size="50" />
        <el-table v-else-if="back.preview.changes.length" :data="back.preview.changes" size="small" max-height="380" data-test="tr-return-changes">
          <el-table-column prop="row" :label="t('mdl.row')" width="60" />
          <el-table-column prop="label" :label="t('mdl.item')" min-width="160" />
          <el-table-column :label="t('mdl.changes')" min-width="300">
            <template #default="{ row }">
              <div v-for="(v, f) in row.changes" :key="f">
                {{ t('equipment.change', { field: t(`transmittal.field.${f}`, String(f)), from: fmt(v[0]), to: fmt(v[1]) }) }}
              </div>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button @click="back.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :disabled="!back.preview?.ok || !back.preview.changes.length"
          :loading="back.busy"
          data-test="tr-return-confirm"
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
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
:deep(.el-table__row) {
  cursor: pointer;
}
</style>
