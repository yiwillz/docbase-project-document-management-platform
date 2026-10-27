<script setup lang="ts">
// 设置页：项目信息（含 MDL 表头、客户审核期限、编号前缀）、审核代码、状态映射、命名、成员、归档
import { Delete, Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { showError } from '@/api/errors'
import { projectsApi } from '@/api/projects'
import { type ReviewCode, type Status, type StatusRule, type StatusTestResult, settingsApi } from '@/api/settings'
import MembersSettings from '@/components/MembersSettings.vue'
import NamingSettings from '@/components/NamingSettings.vue'
import { useProjectContext } from '@/composables/useProjectContext'
import { useProjectStore } from '@/stores/projects'

const { t } = useI18n()
const { projectId, project, readonly, readonlyNotice, can } = useProjectContext('project.config')
const store = useProjectStore()

const tab = ref<'info' | 'codes' | 'rules' | 'naming' | 'members'>('info')
const STATUSES: Status[] = ['not_submitted', 'submitted', 'under_review', 'approved', 'revise_resubmit', 'not_applicable']
const REVIEW_STATUSES: Status[] = ['approved', 'revise_resubmit', 'under_review', 'not_applicable']

// ---------------------------------------------------------------- 项目信息
const info = reactive({
  code: '',
  name: '',
  description: '',
  client_name: '',
  contractor_name: '',
  mdl_doc_no: '',
  mdl_prepared_by: '',
  mdl_checked_by: '',
  mdl_approved_by: '',
  review_days: null as number | null,
  milestone_prefix: '',
  transmittal_prefix: '',
  reason: '',
})

function fillInfo(): void {
  const p = project.value
  if (!p) return
  const rules = p.numbering_rules as Record<string, string>
  Object.assign(info, {
    code: p.code,
    name: p.name,
    description: p.description ?? '',
    client_name: p.client_name ?? '',
    contractor_name: p.contractor_name ?? '',
    mdl_doc_no: p.mdl_doc_no ?? '',
    mdl_prepared_by: p.mdl_prepared_by ?? '',
    mdl_checked_by: p.mdl_checked_by ?? '',
    mdl_approved_by: p.mdl_approved_by ?? '',
    review_days: p.review_days,
    milestone_prefix: rules.milestone_code_prefix ?? '',
    transmittal_prefix: rules.transmittal_prefix ?? '',
    reason: '',
  })
}

async function saveInfo(): Promise<void> {
  const p = project.value
  if (!p) return
  const text = (v: string) => v.trim() || null
  const rules: Record<string, unknown> = { ...p.numbering_rules }
  for (const [key, value] of [
    ['milestone_code_prefix', info.milestone_prefix],
    ['transmittal_prefix', info.transmittal_prefix],
  ] as const) {
    if (value.trim()) rules[key] = value.trim()
    else delete rules[key]
  }
  try {
    const updated = await projectsApi.update(p.id, {
      code: info.code.trim(),
      name: info.name.trim(),
      description: text(info.description),
      client_name: text(info.client_name),
      contractor_name: text(info.contractor_name),
      mdl_doc_no: text(info.mdl_doc_no),
      mdl_prepared_by: text(info.mdl_prepared_by),
      mdl_checked_by: text(info.mdl_checked_by),
      mdl_approved_by: text(info.mdl_approved_by),
      review_days: info.review_days,
      numbering_rules: rules,
      reason: text(info.reason) ?? undefined,
    })
    store.upsert(updated)
    fillInfo()
    ElMessage.success(t('common.saved'))
  } catch (err) {
    showError(err)
  }
}

async function toggleArchive(): Promise<void> {
  const p = project.value
  if (!p) return
  try {
    if (!p.archived_at) {
      await ElMessageBox.confirm(t('project.archiveConfirm', { code: p.code }), t('project.archive'), {
        type: 'warning',
        confirmButtonText: t('common.ok'),
        cancelButtonText: t('common.cancel'),
      })
    }
    store.upsert(p.archived_at ? await projectsApi.unarchive(p.id) : await projectsApi.archive(p.id))
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') showError(err)
  }
}

// ---------------------------------------------------------------- 审核代码
const codes = ref<ReviewCode[]>([])

async function loadCodes(): Promise<void> {
  try {
    codes.value = await settingsApi.reviewCodes(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function saveCodes(): Promise<void> {
  try {
    codes.value = await settingsApi.saveReviewCodes(
      projectId.value,
      codes.value.map(({ code, label_en, label_zh, status, final, aliases }) => ({
        code,
        label_en,
        label_zh: label_zh || null,
        status,
        final,
        aliases,
      })),
    )
    ElMessage.success(t('common.saved'))
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 状态映射
const rules = ref<StatusRule[]>([])
const testText = ref('')
const testResult = ref<StatusTestResult | null | undefined>(undefined)

async function loadRules(): Promise<void> {
  try {
    rules.value = await settingsApi.statusRules(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function saveRules(): Promise<void> {
  try {
    rules.value = await settingsApi.saveStatusRules(
      projectId.value,
      rules.value.map(({ pattern, match_type, status, responsible, is_definite, priority }) => ({
        pattern,
        match_type,
        status,
        responsible,
        is_definite,
        priority,
      })),
    )
    ElMessage.success(t('common.saved'))
  } catch (err) {
    showError(err)
  }
}

async function runTest(): Promise<void> {
  try {
    testResult.value = await settingsApi.testStatus(projectId.value, testText.value)
  } catch (err) {
    showError(err)
  }
}

watch(project, fillInfo, { immediate: true })
watch(projectId, () => Promise.all([loadCodes(), loadRules()]))
onMounted(() => Promise.all([loadCodes(), loadRules()]))
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.settings') }}</h2>
      <el-radio-group v-model="tab" size="small">
        <el-radio-button value="info">{{ t('settings.tabInfo') }}</el-radio-button>
        <el-radio-button value="codes">{{ t('settings.tabCodes') }}</el-radio-button>
        <el-radio-button value="rules">{{ t('settings.tabRules') }}</el-radio-button>
        <el-radio-button value="naming" data-test="tab-naming">{{ t('settings.tabNaming') }}</el-radio-button>
        <el-radio-button value="members" data-test="tab-members">{{ t('settings.tabMembers') }}</el-radio-button>
      </el-radio-group>
    </div>

    <el-card v-show="tab === 'info'" shadow="never">
      <el-form label-width="150px" class="info-form" :disabled="readonly">
        <el-divider content-position="left">{{ t('settings.basic') }}</el-divider>
        <el-form-item :label="t('project.code')"><el-input v-model="info.code" /></el-form-item>
        <el-form-item :label="t('project.name')"><el-input v-model="info.name" /></el-form-item>
        <el-form-item :label="t('settings.description')"><el-input v-model="info.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('project.client')"><el-input v-model="info.client_name" /></el-form-item>
        <el-form-item :label="t('project.contractor')"><el-input v-model="info.contractor_name" /></el-form-item>
        <el-divider content-position="left">{{ t('settings.mdlHeader') }}</el-divider>
        <el-form-item :label="t('settings.mdlDocNo')">
          <el-input v-model="info.mdl_doc_no" :placeholder="t('settings.mdlDocNoHint', { code: info.code })" />
        </el-form-item>
        <el-form-item :label="t('settings.preparedBy')"><el-input v-model="info.mdl_prepared_by" /></el-form-item>
        <el-form-item :label="t('settings.checkedBy')"><el-input v-model="info.mdl_checked_by" /></el-form-item>
        <el-form-item :label="t('settings.approvedBy')"><el-input v-model="info.mdl_approved_by" /></el-form-item>
        <el-divider content-position="left">{{ t('settings.review') }}</el-divider>
        <el-form-item :label="t('settings.reviewDays')">
          <el-input-number v-model="info.review_days" :min="0" :value-on-clear="null" />
          <span class="muted ml">{{ t('settings.reviewDaysHint') }}</span>
        </el-form-item>
        <el-divider content-position="left">{{ t('settings.numbering') }}</el-divider>
        <el-form-item :label="t('settings.milestonePrefix')">
          <el-input v-model="info.milestone_prefix" placeholder="M" class="short" />
        </el-form-item>
        <el-form-item :label="t('settings.transmittalPrefix')">
          <el-input v-model="info.transmittal_prefix" placeholder="TR" class="short" />
          <span class="muted ml">{{ t('settings.transmittalPrefixHint', { code: info.code, prefix: info.transmittal_prefix || 'TR' }) }}</span>
        </el-form-item>
        <el-form-item :label="t('common.reason')"><el-input v-model="info.reason" /></el-form-item>
        <el-form-item>
          <el-button type="primary" data-test="save-info" @click="saveInfo">{{ t('common.save') }}</el-button>
        </el-form-item>
      </el-form>
      <el-divider content-position="left">{{ t('settings.archive') }}</el-divider>
      <p class="muted">{{ t('settings.archiveHint') }}</p>
      <el-button :disabled="!project?.archived_at && !can('project.manage')" @click="toggleArchive">{{ project?.archived_at ? t('project.unarchive') : t('project.archive') }}</el-button>
    </el-card>

    <el-card v-show="tab === 'codes'" shadow="never">
      <p class="muted">{{ t('settings.codesHint') }}</p>
      <el-table :data="codes" size="small" border>
        <el-table-column :label="t('settings.code')" width="90">
          <template #default="{ row }"><el-input v-model="row.code" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.labelEn')" min-width="170">
          <template #default="{ row }"><el-input v-model="row.label_en" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.labelZh')" min-width="130">
          <template #default="{ row }"><el-input v-model="row.label_zh" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.platformStatus')" width="160">
          <template #default="{ row }">
            <el-select v-model="row.status" size="small" :disabled="readonly">
              <el-option v-for="s in REVIEW_STATUSES" :key="s" :value="s" :label="t(`status.${s}`)" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('settings.final')" width="90" align="center">
          <template #default="{ row }"><el-switch v-model="row.final" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.aliases')" min-width="220">
          <template #default="{ row }">
            <el-select v-model="row.aliases" multiple filterable allow-create default-first-option size="small" :disabled="readonly" style="width: 100%" />
          </template>
        </el-table-column>
        <el-table-column v-if="!readonly" width="50">
          <template #default="{ $index }">
            <el-button link type="danger" :icon="Delete" @click="codes.splice($index, 1)" />
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!readonly" class="toolbar mt">
        <el-button :icon="Plus" @click="codes.push({ code: '', label_en: '', label_zh: '', status: 'approved', final: true, aliases: [] })">
          {{ t('common.add') }}
        </el-button>
        <el-button type="primary" @click="saveCodes">{{ t('common.save') }}</el-button>
      </div>
    </el-card>

    <el-card v-show="tab === 'rules'" shadow="never">
      <p class="muted">{{ t('settings.rulesHint') }}</p>
      <el-table :data="rules" size="small" border>
        <el-table-column :label="t('settings.pattern')" min-width="180">
          <template #default="{ row }"><el-input v-model="row.pattern" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.matchType')" width="120">
          <template #default="{ row }">
            <el-select v-model="row.match_type" size="small" :disabled="readonly">
              <el-option v-for="m in ['equals', 'contains', 'regex']" :key="m" :value="m" :label="t(`settings.match.${m}`)" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('settings.platformStatus')" width="160">
          <template #default="{ row }">
            <el-select v-model="row.status" size="small" :disabled="readonly">
              <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.${s}`)" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('settings.responsible')" width="130">
          <template #default="{ row }">
            <el-select v-model="row.responsible" size="small" clearable :disabled="readonly">
              <el-option v-for="r in ['ours', 'vendor', 'client', 'none']" :key="r" :value="r" :label="t(`responsible.${r}`)" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column :label="t('settings.definite')" width="80" align="center">
          <template #default="{ row }"><el-switch v-model="row.is_definite" size="small" :disabled="readonly" /></template>
        </el-table-column>
        <el-table-column :label="t('settings.priority')" width="110">
          <template #default="{ row }"><el-input-number v-model="row.priority" size="small" :min="0" :disabled="readonly" controls-position="right" /></template>
        </el-table-column>
        <el-table-column v-if="!readonly" width="50">
          <template #default="{ $index }">
            <el-button link type="danger" :icon="Delete" @click="rules.splice($index, 1)" />
          </template>
        </el-table-column>
      </el-table>
      <div v-if="!readonly" class="toolbar mt">
        <el-button
          :icon="Plus"
          @click="rules.push({ pattern: '', match_type: 'contains', status: 'approved', responsible: null, is_definite: true, priority: 100 })"
        >
          {{ t('common.add') }}
        </el-button>
        <el-button type="primary" @click="saveRules">{{ t('common.save') }}</el-button>
      </div>
      <el-divider content-position="left">{{ t('settings.tryIt') }}</el-divider>
      <div class="toolbar">
        <el-input v-model="testText" :placeholder="t('settings.tryPlaceholder')" class="try" @keyup.enter="runTest" />
        <el-button @click="runTest">{{ t('settings.try') }}</el-button>
        <span v-if="testResult === null" class="muted">{{ t('settings.tryNone') }}</span>
        <span v-else-if="testResult">
          {{ testResult.status ? t(`status.${testResult.status}`) : t('settings.noMatch') }}
          <template v-if="testResult.responsible"> · {{ t(`responsible.${testResult.responsible}`) }}</template>
          · {{ testResult.definite ? t('settings.definite') : t('settings.notDefinite') }}
        </span>
      </div>
    </el-card>

    <el-card v-if="tab === 'naming'" shadow="never">
      <NamingSettings :project-id="projectId" :readonly="readonly" @saved="(p) => store.upsert(p)" />
    </el-card>

    <el-card v-if="tab === 'members'" shadow="never">
      <MembersSettings :project-id="projectId" :readonly="!can('member.manage')" />
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
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.info-form {
  max-width: 720px;
}
.short {
  width: 120px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ml {
  margin-left: 10px;
}
.mt {
  margin-top: 10px;
}
.try {
  width: 320px;
}
</style>
