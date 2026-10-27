<script setup lang="ts">
// 闭环与关闭页：按里程碑检查能不能关、每项卡在哪；关闭 / 强制关闭 / 重新打开；导出关闭清单、合同外新增要求清单
import { Download, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { BLOCKING, type Closeout, type IssueCode, closeoutApi } from '@/api/closeout'
import { showError } from '@/api/errors'
import type { Fmt, Lang } from '@/api/mdl'
import { type StructureTree, structureApi } from '@/api/structure'
import StatusTag from '@/components/StatusTag.vue'
import { useProjectContext } from '@/composables/useProjectContext'
import { percent } from '@/utils/status'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly, readonlyNotice, can } = useProjectContext('milestone.close')

const structure = ref<StructureTree>({ modules: [], milestones: [] })
const milestoneId = ref<number | null>(null)
const data = ref<Closeout | null>(null)
const loading = ref(false)
const problemsOnly = ref(true)
const today = () => new Date().toISOString().slice(0, 10)

async function loadStructure(): Promise<void> {
  try {
    structure.value = await structureApi.tree(projectId.value)
    const wanted = Number(route.query.milestone)
    const ids = structure.value.milestones.map((m) => m.id)
    milestoneId.value = ids.includes(wanted) ? wanted : (ids[0] ?? null)
  } catch (err) {
    showError(err)
  }
}

async function load(): Promise<void> {
  if (!milestoneId.value) {
    data.value = null
    return
  }
  loading.value = true
  try {
    data.value = await closeoutApi.check(projectId.value, milestoneId.value)
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

watch(milestoneId, (id) => {
  if (id && Number(route.query.milestone) !== id) router.replace({ query: { ...route.query, milestone: String(id) } })
  load()
})

const isBlocking = (code: IssueCode) => BLOCKING.includes(code)
const issueCodes = computed(() => (Object.keys(data.value?.counts ?? {}) as IssueCode[]).filter((c) => data.value!.counts[c]))
const items = computed(() => (data.value?.items ?? []).filter((i) => !problemsOnly.value || i.issues.length))
const documents = computed(() => (data.value?.documents ?? []).filter((d) => d.issues.length))

function where(item: Closeout['items'][number]): string {
  return [item.equipment_tag, item.equipment_name].filter(Boolean).join(' ') || item.module_name || t('matrix.wholeType')
}

function openMatrix(item: Closeout['items'][number]): void {
  router.push({ name: 'matrix', params: { projectId: projectId.value }, query: { milestone_id: String(item.milestone_id), q: item.submittal_no } })
}

// ---------------------------------------------------------------- 关闭 / 重新打开
const closing = reactive({ open: false, closed_date: today() as string | null, basis: '', reason: '', saving: false })

function openClose(): void {
  Object.assign(closing, { open: true, closed_date: today(), basis: '', reason: '' })
}

async function saveClose(): Promise<void> {
  const d = data.value
  if (!d) return
  const force = !d.closable
  if (force && !closing.reason.trim()) {
    ElMessage.error(t('closeout.forceNeedsReason'))
    return
  }
  closing.saving = true
  try {
    await closeoutApi.close(projectId.value, d.milestone.id, {
      closed_date: closing.closed_date,
      basis: closing.basis.trim() || null,
      force,
      reason: closing.reason.trim() || null,
    })
    ElMessage.success(t('closeout.closedOk', { code: d.milestone.code }))
    closing.open = false
    await load()
  } catch (err) {
    showError(err)
  } finally {
    closing.saving = false
  }
}

async function reopen(): Promise<void> {
  const d = data.value
  if (!d) return
  try {
    const { value } = await ElMessageBox.prompt(t('closeout.reopenPrompt', { code: d.milestone.code }), t('closeout.reopen'), {
      confirmButtonText: t('common.ok'),
      cancelButtonText: t('common.cancel'),
      inputValidator: (v: string) => !!v?.trim() || t('closeout.reasonRequired'),
    })
    await closeoutApi.reopen(projectId.value, d.milestone.id, value.trim())
    ElMessage.success(t('closeout.reopened'))
    await load()
  } catch (err) {
    if (err !== 'cancel' && err !== 'close') showError(err)
  }
}

// ---------------------------------------------------------------- 导出
const exportOptions = reactive({ lang: 'en' as Lang, format: 'xlsx' as Fmt })

function exportList(): void {
  if (milestoneId.value)
    closeoutApi.exportList(projectId.value, milestoneId.value, exportOptions.lang, exportOptions.format).catch(showError)
}

function exportRequirements(): void {
  closeoutApi.exportClientRequirements(projectId.value, exportOptions.lang).catch(showError)
}

watch(projectId, async () => {
  await loadStructure()
  await load()
})
onMounted(async () => {
  await loadStructure()
  await load()
})
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.closeout') }}</h2>
      <el-select v-model="milestoneId" class="w260" data-test="co-milestone">
        <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
      </el-select>
      <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
      <div class="spacer" />
      <el-radio-group v-model="exportOptions.lang" size="small">
        <el-radio-button value="en">EN</el-radio-button>
        <el-radio-button value="zh">{{ t('lang.zh-CN') }}</el-radio-button>
      </el-radio-group>
      <el-radio-group v-model="exportOptions.format" size="small">
        <el-radio-button value="xlsx">Excel</el-radio-button>
        <el-radio-button value="pdf">PDF</el-radio-button>
      </el-radio-group>
      <el-button v-if="can('export')" :icon="Download" :disabled="!milestoneId" data-test="co-export" @click="exportList">{{ t('closeout.exportList') }}</el-button>
      <el-button v-if="can('export')" :icon="Download" data-test="co-requirements" @click="exportRequirements">{{ t('closeout.exportRequirements') }}</el-button>
    </div>

    <el-empty v-if="!structure.milestones.length" :description="t('closeout.noMilestones')" />

    <template v-else-if="data">
      <!-- 结论 -->
      <el-card shadow="never" data-test="co-verdict">
        <div class="verdict">
          <div>
            <el-tag v-if="data.milestone.closed" type="success" size="large">{{ t('closeout.isClosed') }}</el-tag>
            <el-tag v-else-if="data.closable" type="success" size="large" effect="plain">{{ t('closeout.closable') }}</el-tag>
            <el-tag v-else type="danger" size="large" effect="plain">{{ t('closeout.notClosable', { n: data.blocking }) }}</el-tag>
            <span v-if="data.warnings" class="ml muted">{{ t('closeout.warningsCount', { n: data.warnings }) }}</span>
          </div>
          <div class="stats">
            <span>{{ t('closeout.progress', { a: data.stats.approved, n: data.stats.applicable, p: percent(data.stats.completion) }) }}</span>
            <span v-if="data.milestone.due_date">{{ t('closeout.due', { d: data.milestone.due_date }) }}</span>
          </div>
          <div class="spacer" />
          <template v-if="!readonly">
            <el-button v-if="data.milestone.closed" data-test="co-reopen" @click="reopen">{{ t('closeout.reopen') }}</el-button>
            <el-button v-else :type="data.closable ? 'primary' : 'danger'" :plain="!data.closable" data-test="co-close" @click="openClose">
              {{ data.closable ? t('closeout.close') : t('closeout.forceClose') }}
            </el-button>
          </template>
        </div>
        <p v-if="data.milestone.closed" class="muted closed-info">
          {{
            t('closeout.closedInfo', {
              date: data.milestone.closed_date ?? '—',
              by: data.milestone.closed_by ?? '—',
              basis: data.milestone.close_basis ?? '—',
            })
          }}
        </p>
        <el-alert
          v-if="data.milestone.closed && data.milestone.changed_after_close"
          type="warning"
          :closable="false"
          show-icon
          :title="t('closeout.changedAfterClose')"
          data-test="co-changed"
        />
        <div v-if="issueCodes.length" class="issues">
          <el-tag v-for="c in issueCodes" :key="c" :type="isBlocking(c) ? 'danger' : 'warning'" effect="plain">
            {{ t('closeout.issueCount', { issue: t(`closeout.issue.${c}`), n: data.counts[c] }) }}
          </el-tag>
        </div>
        <p class="muted">{{ t('closeout.rules') }}</p>
      </el-card>

      <!-- 种类层面的问题 -->
      <el-card v-if="documents.length" shadow="never">
        <template #header>{{ t('closeout.documentIssues') }}</template>
        <el-table :data="documents" size="small">
          <el-table-column prop="submittal_no" :label="t('transmittal.col.no')" width="80" />
          <el-table-column prop="name" :label="t('transmittal.col.document')" min-width="220" />
          <el-table-column :label="t('structure.col.origin')" width="120">
            <template #default="{ row }">{{ t(`origin.${row.origin}`) }}</template>
          </el-table-column>
          <el-table-column :label="t('closeout.issues')" min-width="240">
            <template #default="{ row }">
              <el-tag v-for="c in row.issues" :key="c" :type="isBlocking(c) ? 'danger' : 'warning'" size="small" class="mr">
                {{ t(`closeout.issue.${c}`) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <!-- 应交项 -->
      <el-card shadow="never">
        <template #header>
          <div class="toolbar">
            <span>{{ t('closeout.items', { n: items.length }) }}</span>
            <div class="spacer" />
            <el-switch v-model="problemsOnly" :active-text="t('closeout.problemsOnly')" />
          </div>
        </template>
        <el-empty v-if="!items.length" :description="t('closeout.noProblems')" :image-size="60" />
        <el-table v-else v-loading="loading" :data="items" size="small" max-height="520" data-test="co-items" @row-dblclick="openMatrix">
          <el-table-column prop="submittal_no" :label="t('transmittal.col.no')" width="80" />
          <el-table-column prop="document_type_name" :label="t('transmittal.col.document')" min-width="200" />
          <el-table-column :label="t('transmittal.col.where')" min-width="150">
            <template #default="{ row }">{{ where(row as Closeout['items'][number]) }}</template>
          </el-table-column>
          <el-table-column :label="t('matrix.col.status')" width="120">
            <template #default="{ row }"><StatusTag :status="row.status" :overdue="row.overdue" /></template>
          </el-table-column>
          <el-table-column :label="t('matrix.col.transmittal')" width="190">
            <template #default="{ row }">{{ row.transmittal_no ?? '' }} {{ row.review_code ?? '' }}</template>
          </el-table-column>
          <el-table-column :label="t('closeout.issues')" min-width="260">
            <template #default="{ row }">
              <el-tag v-for="c in row.issues" :key="c" :type="isBlocking(c) ? 'danger' : 'warning'" size="small" class="mr">
                {{ t(`closeout.issue.${c}`) }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p class="muted">{{ t('closeout.dblclickHint') }}</p>
      </el-card>
    </template>

    <!-- 关闭 -->
    <el-dialog v-model="closing.open" :title="data?.closable ? t('closeout.close') : t('closeout.forceClose')" width="520px" data-test="co-close-dialog">
      <el-alert v-if="data && !data.closable" type="error" :closable="false" show-icon :title="t('closeout.forceWarning', { n: data.blocking })" class="mb" />
      <el-form label-width="110px">
        <el-form-item :label="t('closeout.closedDate')">
          <el-date-picker v-model="closing.closed_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('closeout.basis')">
          <el-input v-model="closing.basis" :placeholder="t('closeout.basisHint')" data-test="co-basis" />
        </el-form-item>
        <el-form-item :label="t('common.reason')" :required="!!data && !data.closable">
          <el-input v-model="closing.reason" data-test="co-reason" />
        </el-form-item>
      </el-form>
      <p class="muted">{{ t('closeout.closeNote') }}</p>
      <template #footer>
        <el-button @click="closing.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          :type="data?.closable ? 'primary' : 'danger'"
          :loading="closing.saving"
          data-test="co-close-save"
          @click="saveClose"
        >
          {{ data?.closable ? t('closeout.close') : t('closeout.forceClose') }}
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
.w260 {
  width: 260px;
}
.verdict {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.stats {
  display: flex;
  gap: 16px;
  color: var(--el-text-color-regular);
}
.issues {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}
.closed-info {
  margin: 8px 0;
}
.ml {
  margin-left: 8px;
}
.mr {
  margin-right: 4px;
}
.mb {
  margin-bottom: 10px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
