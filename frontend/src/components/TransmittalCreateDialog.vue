<script setup lang="ts">
// 生成传送单 / 事后补记：从应交矩阵带着勾选的应交项来，或在这里自己选（默认只列还没批准的）
import { Search } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type Item, deliverablesApi } from '@/api/deliverables'
import { showError } from '@/api/errors'
import { type StructureTree, structureApi } from '@/api/structure'
import { type TransmittalDetail, transmittalsApi } from '@/api/transmittals'
import StatusTag from '@/components/StatusTag.vue'
import { OPEN_STATUSES } from '@/utils/status'

const props = defineProps<{ projectId: number; presetItems?: Item[] }>()
const emit = defineEmits<{ created: [transmittal: TransmittalDetail] }>()
const open = defineModel<boolean>({ required: true })
const { t } = useI18n()

const today = () => new Date().toISOString().slice(0, 10)
const form = reactive({
  mode: 'platform' as 'platform' | 'recorded',
  subject: '',
  submitted_date: today() as string | null,
  method: '',
  external_ref: '',
  remarks: '',
})
const saving = ref(false)

// 自己选应交项时
const picker = reactive({ milestone: null as number | null, q: '', all: false })
const structure = ref<StructureTree>({ modules: [], milestones: [] })
const candidates = ref<Item[]>([])
const chosen = ref<Item[]>([])
const loading = ref(false)
const preset = computed(() => !!props.presetItems?.length)
const items = computed(() => (preset.value ? props.presetItems! : chosen.value))

async function loadCandidates(): Promise<void> {
  loading.value = true
  try {
    const { items } = await deliverablesApi.list(props.projectId, {
      milestone_id: picker.milestone ? [picker.milestone] : undefined,
      status: picker.all ? undefined : OPEN_STATUSES,
      q: picker.q.trim() || undefined,
    })
    candidates.value = items
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

watch(open, async (value) => {
  if (!value) return
  Object.assign(form, { mode: 'platform', subject: '', submitted_date: today(), method: '', external_ref: '', remarks: '' })
  chosen.value = []
  if (!preset.value) {
    try {
      structure.value = await structureApi.tree(props.projectId)
    } catch (err) {
      showError(err)
    }
    await loadCandidates()
  }
})

function where(item: Item): string {
  return [item.equipment_tag, item.equipment_name].filter(Boolean).join(' ') || item.module_name || t('matrix.wholeType')
}

async function save(): Promise<void> {
  if (!items.value.length) return
  if (form.mode === 'recorded' && !form.submitted_date) {
    ElMessage.error(t('transmittal.dateRequired'))
    return
  }
  const text = (v: string) => v.trim() || null
  const body = {
    deliverable_ids: items.value.map((i) => i.id),
    subject: text(form.subject),
    submitted_date: form.submitted_date,
    method: form.mode === 'recorded' ? text(form.method) : null,
    external_ref: text(form.external_ref),
    remarks: text(form.remarks),
  }
  saving.value = true
  try {
    const created =
      form.mode === 'platform'
        ? await transmittalsApi.create(props.projectId, body)
        : await transmittalsApi.record(props.projectId, body)
    ElMessage.success(t('transmittal.created', { no: created.number, n: created.items.length }))
    open.value = false
    emit('created', created)
  } catch (err) {
    showError(err)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="open" :title="t('transmittal.new')" width="860px" top="5vh" data-test="transmittal-create">
    <el-form label-width="110px">
      <el-form-item :label="t('transmittal.mode')">
        <el-radio-group v-model="form.mode">
          <el-radio value="platform">{{ t('transmittal.modePlatform') }}</el-radio>
          <el-radio value="recorded">{{ t('transmittal.modeRecorded') }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item :label="t('transmittal.subject')">
        <el-input v-model="form.subject" maxlength="500" :placeholder="t('transmittal.subjectHint')" data-test="tr-subject" />
      </el-form-item>
      <el-form-item :label="t('transmittal.submittedDate')" :required="form.mode === 'recorded'">
        <el-date-picker v-model="form.submitted_date" type="date" value-format="YYYY-MM-DD" />
      </el-form-item>
      <template v-if="form.mode === 'recorded'">
        <el-form-item :label="t('transmittal.method')">
          <el-select v-model="form.method" clearable allow-create filterable>
            <el-option v-for="m in ['email', 'client_system', 'courier', 'other']" :key="m" :value="m" :label="t(`transmittal.methods.${m}`)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('transmittal.externalRef')"><el-input v-model="form.external_ref" /></el-form-item>
      </template>
      <el-form-item :label="t('transmittal.remarks')"><el-input v-model="form.remarks" /></el-form-item>
    </el-form>

    <el-divider content-position="left">{{ t('transmittal.itemsCount', { n: items.length }) }}</el-divider>

    <!-- 从应交矩阵带过来的 -->
    <el-table v-if="preset" :data="items" size="small" max-height="260">
      <el-table-column :label="t('transmittal.col.no')" width="80"><template #default="{ row }">{{ row.submittal_no }}</template></el-table-column>
      <el-table-column :label="t('transmittal.col.document')" min-width="200"><template #default="{ row }">{{ row.document_type_name }}</template></el-table-column>
      <el-table-column :label="t('transmittal.col.where')" min-width="160"><template #default="{ row }">{{ where(row as Item) }}</template></el-table-column>
      <el-table-column :label="t('matrix.col.status')" width="120">
        <template #default="{ row }"><StatusTag :status="row.status" :overdue="row.overdue" /></template>
      </el-table-column>
    </el-table>

    <!-- 在这里自己选 -->
    <template v-else>
      <div class="toolbar">
        <el-select v-model="picker.milestone" clearable :placeholder="t('matrix.allMilestones')" class="w200" @change="loadCandidates">
          <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
        </el-select>
        <el-input v-model="picker.q" :prefix-icon="Search" clearable :placeholder="t('matrix.search')" class="w200" @change="loadCandidates" />
        <el-checkbox v-model="picker.all" @change="loadCandidates">{{ t('transmittal.includeDone') }}</el-checkbox>
      </div>
      <el-table
        v-loading="loading"
        :data="candidates"
        size="small"
        max-height="300"
        row-key="id"
        data-test="tr-picker"
        @selection-change="(rows: unknown[]) => (chosen = rows as Item[])"
      >
        <el-table-column type="selection" width="36" reserve-selection />
        <el-table-column :label="t('transmittal.col.no')" width="80"><template #default="{ row }">{{ row.submittal_no }}</template></el-table-column>
        <el-table-column :label="t('transmittal.col.document')" min-width="200"><template #default="{ row }">{{ row.document_type_name }}</template></el-table-column>
        <el-table-column :label="t('transmittal.col.where')" min-width="160"><template #default="{ row }">{{ where(row as Item) }}</template></el-table-column>
        <el-table-column :label="t('matrix.col.status')" width="120">
          <template #default="{ row }"><StatusTag :status="row.status" :overdue="row.overdue" /></template>
        </el-table-column>
      </el-table>
    </template>
    <p class="muted">{{ form.mode === 'platform' ? t('transmittal.platformNote') : t('transmittal.recordedNote') }}</p>
    <template #footer>
      <el-button @click="open = false">{{ t('common.cancel') }}</el-button>
      <el-button type="primary" :disabled="!items.length" :loading="saving" data-test="tr-create" @click="save">
        {{ form.mode === 'platform' ? t('transmittal.createButton', { n: items.length }) : t('transmittal.recordButton', { n: items.length }) }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.w200 {
  width: 200px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
