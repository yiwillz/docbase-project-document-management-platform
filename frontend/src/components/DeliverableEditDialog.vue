<script setup lang="ts">
// 改应交项：单个（可改全部字段）或批量（同一组改动用到选中的每一项）。
// 状态日期填真实发生的日期（如客户批准那天）；依据填函件号等；责任方不选时按状态自动带出。
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type DeliverableChanges, type Item, deliverablesApi } from '@/api/deliverables'
import { showError } from '@/api/errors'
import type { Responsible, Status } from '@/api/settings'
import { STATUSES } from '@/utils/status'

const props = defineProps<{ projectId: number; item?: Item | null; ids?: number[] }>()
const emit = defineEmits<{ saved: [] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

const RESPONSIBLES: Responsible[] = ['ours', 'vendor', 'client', 'none']
const bulk = computed(() => !props.item)
const saving = ref(false)
const form = reactive({
  status: null as Status | null,
  status_date: null as string | null,
  status_basis: '',
  responsible: null as Responsible | null,
  due_date: null as string | null,
  submitted_date: null as string | null,
  remarks: '',
  reason: '',
})

watch(open, (value) => {
  if (!value) return
  const it = props.item
  Object.assign(form, {
    status: it?.status ?? null,
    status_date: it?.status_date ?? null,
    status_basis: it?.status_basis ?? '',
    responsible: null,
    due_date: it?.due_source === 'deliverable' ? it.due_date : null,
    submitted_date: it?.submitted_date ?? null,
    remarks: it?.remarks ?? '',
    reason: '',
  })
})

// 只提交改过的字段（批量时只提交填了的）
function changes(): DeliverableChanges {
  const it = props.item
  const out: DeliverableChanges = {}
  const text = (v: string) => v.trim() || null
  if (form.status && form.status !== it?.status) out.status = form.status
  if (form.responsible) out.responsible = form.responsible
  if (bulk.value) {
    if (form.status_date) out.status_date = form.status_date
    if (text(form.status_basis)) out.status_basis = text(form.status_basis)
    if (form.submitted_date) out.submitted_date = form.submitted_date
    if (form.due_date) out.due_date = form.due_date
    return out
  }
  if (out.status || form.status_date !== it?.status_date) out.status_date = form.status_date
  if (text(form.status_basis) !== (it?.status_basis ?? null)) out.status_basis = text(form.status_basis)
  if (form.submitted_date !== it?.submitted_date) out.submitted_date = form.submitted_date
  const ownDue = it?.due_source === 'deliverable' ? it.due_date : null
  if (form.due_date !== ownDue) out.due_date = form.due_date
  if (text(form.remarks) !== (it?.remarks ?? null)) out.remarks = text(form.remarks)
  return out
}

async function save(): Promise<void> {
  const body = changes()
  if (!Object.keys(body).length) {
    open.value = false
    return
  }
  saving.value = true
  try {
    const reason = form.reason.trim() || undefined
    if (props.item) {
      await deliverablesApi.update(props.projectId, props.item.id, { ...body, reason })
      ElMessage.success(t('common.saved'))
    } else {
      const result = await deliverablesApi.bulkUpdate(props.projectId, props.ids ?? [], body, reason)
      ElMessage.success(t('matrix.bulkDone', { n: result.updated }))
    }
    open.value = false
    emit('saved')
  } catch (err) {
    showError(err)
  } finally {
    saving.value = false
  }
}

const title = computed(() => {
  const it = props.item
  if (!it) return t('matrix.bulkTitle', { n: props.ids?.length ?? 0 })
  const where = it.equipment_tag || it.equipment_name || it.module_name
  return `${it.submittal_no} ${it.document_type_name}${where ? ` / ${where}` : ''}`
})
</script>

<template>
  <el-dialog v-model="open" :title="title" width="560px" data-test="edit-dialog">
    <p v-if="bulk" class="muted">{{ t('matrix.bulkHint') }}</p>
    <el-form label-width="110px" @submit.prevent="save">
      <el-form-item :label="t('matrix.col.status')">
        <el-select v-model="form.status" :placeholder="bulk ? t('matrix.keep') : ''" clearable data-test="edit-status">
          <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.${s}`)" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('matrix.col.statusDate')">
        <el-date-picker v-model="form.status_date" type="date" value-format="YYYY-MM-DD" data-test="edit-status-date" />
        <span class="muted ml">{{ t('matrix.statusDateHint') }}</span>
      </el-form-item>
      <el-form-item :label="t('matrix.basis')">
        <el-input v-model="form.status_basis" :placeholder="t('matrix.basisHint')" data-test="edit-basis" />
      </el-form-item>
      <el-form-item :label="t('matrix.col.responsible')">
        <el-select v-model="form.responsible" clearable :placeholder="t('matrix.responsibleAuto')">
          <el-option v-for="r in RESPONSIBLES" :key="r" :value="r" :label="t(`responsible.${r}`)" />
        </el-select>
      </el-form-item>
      <el-form-item :label="t('matrix.col.submitted')">
        <el-date-picker v-model="form.submitted_date" type="date" value-format="YYYY-MM-DD" />
      </el-form-item>
      <el-form-item :label="t('matrix.ownDue')">
        <el-date-picker v-model="form.due_date" type="date" value-format="YYYY-MM-DD" :placeholder="t('matrix.dueInherit')" />
      </el-form-item>
      <el-form-item v-if="!bulk" :label="t('matrix.remarks')">
        <el-input v-model="form.remarks" type="textarea" :rows="2" :placeholder="t('matrix.remarksHint')" />
      </el-form-item>
      <el-form-item :label="t('common.reason')"><el-input v-model="form.reason" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="open = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :loading="saving" data-test="edit-save" @click="save">{{ t('common.save') }}</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ml {
  margin-left: 8px;
}
</style>
