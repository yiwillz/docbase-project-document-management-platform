<script setup lang="ts">
// 交付与基线：交付包（选范围和目录结构，后台生成、下载，可同时作为提交）、阶段基线（冻结、查看、对比、导出、
// 按基线打包）、共享盘镜像状态
import { Download, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  type Baseline,
  type BaselineRow,
  type CompareResult,
  type Layout,
  type MirrorStatus,
  type Package,
  type PackageRequest,
  deliveryApi,
} from '@/api/delivery'
import { type Equipment, equipmentApi } from '@/api/equipment'
import { showError } from '@/api/errors'
import { type StructureTree, structureApi } from '@/api/structure'
import StatusTag from '@/components/StatusTag.vue'
import { useProjectContext } from '@/composables/useProjectContext'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly, readonlyNotice, can } = useProjectContext('export')

const TABS = ['packages', 'baselines', 'mirror'] as const
type Tab = (typeof TABS)[number]
const tab = ref<Tab>(TABS.includes(route.query.tab as Tab) ? (route.query.tab as Tab) : 'packages')
watch(tab, (v) => router.replace({ query: { ...route.query, tab: v === 'packages' ? undefined : v } }))

const structure = ref<StructureTree>({ modules: [], milestones: [] })
const equipment = ref<Equipment[]>([])
const docTypes = computed(() => structure.value.milestones.flatMap((m) => m.document_types))

function size(bytes: number | null | undefined): string {
  if (!bytes) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function time(value: string | null | undefined): string {
  return value ? String(value).slice(0, 16).replace('T', ' ') : '—'
}

// ---------------------------------------------------------------- 交付包
const form = reactive({
  milestone_ids: [] as number[],
  document_type_ids: [] as number[],
  equipment_ids: [] as number[],
  layout: 'equipment' as Layout,
  lang: 'en' as 'zh' | 'en',
  include_client: false,
  baseline_id: null as number | null,
  submit: false,
  subject: '',
  submitted_date: new Date().toISOString().slice(0, 10) as string | null,
  remarks: '',
  saving: false,
})
const estimate = ref<{ files: number; size: number; deliverables: number } | null>(null)
const packages = ref<Package[]>([])

function request(): PackageRequest {
  return {
    scope: { milestone_ids: form.milestone_ids, document_type_ids: form.document_type_ids, equipment_ids: form.equipment_ids },
    layout: form.layout,
    lang: form.lang,
    include_client: form.include_client,
    baseline_id: form.baseline_id,
    submit: form.submit
      ? { subject: form.subject.trim() || null, submitted_date: form.submitted_date, remarks: form.remarks.trim() || null }
      : null,
  }
}

let estimateTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => [form.milestone_ids, form.document_type_ids, form.equipment_ids, form.layout, form.include_client, form.baseline_id],
  () => {
    clearTimeout(estimateTimer)
    if (readonly.value) return
    estimateTimer = setTimeout(async () => {
      try {
        estimate.value = await deliveryApi.estimate(projectId.value, { ...request(), submit: null })
      } catch (err) {
        showError(err)
      }
    }, 300)
  },
  { deep: true, immediate: true },
)

async function loadPackages(): Promise<void> {
  try {
    packages.value = await deliveryApi.packages(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function createPackage(): Promise<void> {
  form.saving = true
  try {
    const pkg = await deliveryApi.createPackage(projectId.value, request())
    ElMessage.success(pkg.transmittal_no ? t('delivery.createdWithTransmittal', { no: pkg.transmittal_no }) : t('delivery.created'))
    form.submit = false
    await loadPackages()
  } catch (err) {
    showError(err)
  } finally {
    form.saving = false
  }
}

function scopeText(pkg: Package): string {
  const s = pkg.scope
  const parts: string[] = []
  if (pkg.baseline_id) parts.push(t('delivery.fromBaseline', { name: baselines.value.find((b) => b.id === pkg.baseline_id)?.name ?? `#${pkg.baseline_id}` }))
  if (s.milestone_ids?.length) parts.push(t('delivery.scopeMilestones', { n: s.milestone_ids.length }))
  if (s.document_type_ids?.length) parts.push(t('delivery.scopeTypes', { n: s.document_type_ids.length }))
  if (s.equipment_ids?.length) parts.push(t('delivery.scopeEquipment', { n: s.equipment_ids.length }))
  if (!parts.length) parts.push(t('delivery.scopeAll'))
  parts.push(t(`delivery.layout.${pkg.layout}`))
  if (pkg.include_client) parts.push(t('delivery.withClient'))
  return parts.join(t('common.listSep'))
}

const PKG_TAG: Record<string, '' | 'success' | 'warning' | 'info' | 'danger'> = {
  queued: 'info',
  running: 'warning',
  paused: 'info',
  succeeded: 'success',
  failed: 'danger',
}

// ---------------------------------------------------------------- 基线
const baselines = ref<Baseline[]>([])
const freezeForm = reactive({ name: '', description: '', saving: false })

async function loadBaselines(): Promise<void> {
  try {
    baselines.value = await deliveryApi.baselines(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function freeze(): Promise<void> {
  if (!freezeForm.name.trim()) return
  freezeForm.saving = true
  try {
    await deliveryApi.freeze(projectId.value, freezeForm.name.trim(), freezeForm.description.trim())
    ElMessage.success(t('delivery.frozen'))
    Object.assign(freezeForm, { name: '', description: '' })
    await loadBaselines()
  } catch (err) {
    showError(err)
  } finally {
    freezeForm.saving = false
  }
}

const view = reactive({ open: false, baseline: null as (Baseline & { items: BaselineRow[] }) | null, loading: false })

async function openBaseline(b: Baseline): Promise<void> {
  Object.assign(view, { open: true, baseline: null, loading: true })
  try {
    view.baseline = await deliveryApi.baseline(projectId.value, b.id)
  } catch (err) {
    showError(err)
  } finally {
    view.loading = false
  }
}

const cmp = reactive({ open: false, base: null as Baseline | null, other: null as number | null, result: null as CompareResult | null, loading: false })

async function runCompare(): Promise<void> {
  if (!cmp.base) return
  cmp.loading = true
  try {
    cmp.result = await deliveryApi.compare(projectId.value, cmp.base.id, cmp.other)
  } catch (err) {
    showError(err)
  } finally {
    cmp.loading = false
  }
}

function openCompare(b: Baseline): void {
  Object.assign(cmp, { open: true, base: b, other: null, result: null })
  runCompare()
}

watch(() => cmp.other, () => cmp.open && runCompare())

function packFromBaseline(b: Baseline): void {
  form.baseline_id = b.id
  tab.value = 'packages'
}

function where(r: { submittal_no?: string; document_type_name?: string; where?: string }): string {
  return [`${r.submittal_no ?? ''} ${r.document_type_name ?? ''}`.trim(), r.where].filter(Boolean).join(' / ')
}

// ---------------------------------------------------------------- 镜像
const mirror = ref<MirrorStatus | null>(null)

async function loadMirror(): Promise<void> {
  try {
    mirror.value = await deliveryApi.mirror(projectId.value)
  } catch (err) {
    showError(err)
  }
}

async function syncMirror(): Promise<void> {
  try {
    mirror.value = await deliveryApi.mirrorSync(projectId.value)
    ElMessage.success(t('delivery.mirrorQueued'))
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 加载、刷新
async function loadOptions(): Promise<void> {
  try {
    ;[structure.value, equipment.value] = await Promise.all([structureApi.tree(projectId.value), equipmentApi.list(projectId.value)])
  } catch (err) {
    showError(err)
  }
}

function loadAll(): void {
  loadOptions()
  loadPackages()
  loadBaselines()
  loadMirror()
}

let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  loadAll()
  timer = setInterval(() => {
    if (packages.value.some((p) => ['queued', 'running'].includes(p.status))) loadPackages()
    if (tab.value === 'mirror') loadMirror()
  }, 2000)
})
onUnmounted(() => clearInterval(timer))
watch(projectId, loadAll)
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.delivery') }}</h2>
      <el-radio-group v-model="tab" size="small">
        <el-radio-button v-for="x in TABS" :key="x" :value="x" :data-test="`tab-${x}`">{{ t(`delivery.tab.${x}`) }}</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <el-button :icon="Refresh" size="small" @click="loadAll">{{ t('common.refresh') }}</el-button>
    </div>

    <!-- 交付包 -->
    <template v-if="tab === 'packages'">
      <el-card v-if="!readonly" shadow="never">
        <el-form label-width="110px" class="form">
          <el-form-item :label="t('delivery.scope')">
            <div class="row">
              <el-select v-model="form.milestone_ids" multiple collapse-tags clearable :placeholder="t('matrix.allMilestones')" class="w200">
                <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
              </el-select>
              <el-select v-model="form.document_type_ids" multiple collapse-tags clearable filterable :placeholder="t('browse.docType')" class="w220">
                <el-option v-for="dt in docTypes" :key="dt.id" :value="dt.id" :label="`${dt.submittal_no} ${dt.name}`" />
              </el-select>
              <el-select v-model="form.equipment_ids" multiple collapse-tags clearable filterable :placeholder="t('browse.equipment')" class="w220">
                <el-option v-for="e in equipment" :key="e.id" :value="e.id" :label="[e.tag, e.name].filter(Boolean).join(' ')" />
              </el-select>
            </div>
          </el-form-item>
          <el-form-item :label="t('delivery.layoutLabel')">
            <el-radio-group v-model="form.layout">
              <el-radio value="equipment">{{ t('delivery.layout.equipment') }}</el-radio>
              <el-radio value="milestone">{{ t('delivery.layout.milestone') }}</el-radio>
            </el-radio-group>
            <span class="muted ml">{{ t(`delivery.layoutHint.${form.layout}`) }}</span>
          </el-form-item>
          <el-form-item :label="t('delivery.options')">
            <el-radio-group v-model="form.lang" size="small" class="mr">
              <el-radio-button value="en">EN</el-radio-button>
              <el-radio-button value="zh">{{ t('lang.zh-CN') }}</el-radio-button>
            </el-radio-group>
            <el-checkbox v-model="form.include_client">{{ t('delivery.includeClient') }}</el-checkbox>
            <el-select v-model="form.baseline_id" clearable :placeholder="t('delivery.currentVersions')" class="w220 ml">
              <el-option v-for="b in baselines" :key="b.id" :value="b.id" :label="t('delivery.fromBaseline', { name: b.name })" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="can('deliverable.update')" :label="t('delivery.submit')">
            <el-checkbox v-model="form.submit" data-test="pkg-submit">{{ t('delivery.submitHint') }}</el-checkbox>
          </el-form-item>
          <template v-if="form.submit">
            <el-form-item :label="t('transmittal.subject')"><el-input v-model="form.subject" class="w360" /></el-form-item>
            <el-form-item :label="t('transmittal.submittedDate')">
              <el-date-picker v-model="form.submitted_date" type="date" value-format="YYYY-MM-DD" />
            </el-form-item>
          </template>
          <el-form-item>
            <el-button type="primary" :loading="form.saving" :disabled="!estimate?.files" data-test="pkg-create" @click="createPackage">
              {{ t('delivery.create') }}
            </el-button>
            <span class="muted ml" data-test="pkg-estimate">
              {{ estimate ? t('delivery.estimate', { n: estimate.files, size: size(estimate.size) }) : '' }}
            </span>
          </el-form-item>
        </el-form>
      </el-card>

      <el-card shadow="never">
        <el-empty v-if="!packages.length" :description="t('delivery.noPackages')" :image-size="60" />
        <el-table v-else :data="packages" size="small" data-test="pkg-list">
          <el-table-column prop="id" label="#" width="50" />
          <el-table-column :label="t('delivery.created_at')" width="150">
            <template #default="{ row }">{{ time(row.created_at) }}<div class="muted">{{ row.created_by }}</div></template>
          </el-table-column>
          <el-table-column :label="t('delivery.scope')" min-width="240">
            <template #default="{ row }">{{ scopeText(row as Package) }}</template>
          </el-table-column>
          <el-table-column :label="t('delivery.content')" width="140">
            <template #default="{ row }">{{ t('delivery.files', { n: row.files }) }}<div class="muted">{{ size(row.size) }}</div></template>
          </el-table-column>
          <el-table-column :label="t('imports.status')" width="170">
            <template #default="{ row }">
              <el-tag :type="PKG_TAG[row.status] || undefined" size="small" data-test="pkg-status">{{ t(`delivery.status.${row.status}`) }}</el-tag>
              <el-progress v-if="row.status === 'running' && row.total" :percentage="Math.floor((row.progress * 100) / row.total)" />
              <div v-if="row.error" class="err">{{ row.error }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('delivery.transmittal')" width="170">
            <template #default="{ row }">
              <router-link v-if="row.transmittal_no" :to="{ name: 'transmittals', params: { projectId } }">{{ row.transmittal_no }}</router-link>
            </template>
          </el-table-column>
          <el-table-column width="110" fixed="right">
            <template #default="{ row }">
              <a v-if="row.available && !readonly" :href="deliveryApi.packageUrl(projectId, row.id)" data-test="pkg-download">
                <el-icon><Download /></el-icon> {{ t('common.download') }}
              </a>
              <span v-else-if="row.status === 'succeeded'" class="muted">{{ t('delivery.expired') }}</span>
            </template>
          </el-table-column>
        </el-table>
        <p class="muted">{{ t('delivery.packageNote') }}</p>
      </el-card>
    </template>

    <!-- 基线 -->
    <template v-else-if="tab === 'baselines'">
      <el-card v-if="can('deliverable.update')" shadow="never">
        <el-form inline>
          <el-form-item :label="t('delivery.baselineName')"><el-input v-model="freezeForm.name" class="w240" data-test="baseline-name" /></el-form-item>
          <el-form-item :label="t('transmittal.remarks')"><el-input v-model="freezeForm.description" class="w300" /></el-form-item>
          <el-form-item>
            <el-button type="primary" :disabled="!freezeForm.name.trim()" :loading="freezeForm.saving" data-test="baseline-freeze" @click="freeze">
              {{ t('delivery.freeze') }}
            </el-button>
          </el-form-item>
        </el-form>
        <p class="muted">{{ t('delivery.freezeNote') }}</p>
      </el-card>
      <el-card shadow="never">
        <el-empty v-if="!baselines.length" :description="t('delivery.noBaselines')" :image-size="60" />
        <el-table v-else :data="baselines" size="small" data-test="baseline-list">
          <el-table-column prop="name" :label="t('delivery.baselineName')" min-width="180">
            <template #default="{ row }">{{ row.name }}<div class="muted">{{ row.description }}</div></template>
          </el-table-column>
          <el-table-column :label="t('delivery.frozenAt')" width="160">
            <template #default="{ row }">{{ time(row.frozen_at) }}<div class="muted">{{ row.frozen_by }}</div></template>
          </el-table-column>
          <el-table-column :label="t('imports.summary')" min-width="220">
            <template #default="{ row }">
              {{ t('delivery.baselineCounts', { n: row.counts.deliverables ?? 0, files: row.counts.files ?? 0, missing: row.counts.missing ?? 0 }) }}
            </template>
          </el-table-column>
          <el-table-column :label="t('common.actions')" width="330" fixed="right">
            <template #default="{ row }">
              <el-button link type="primary" data-test="baseline-view" @click="openBaseline(row as Baseline)">{{ t('delivery.view') }}</el-button>
              <el-button link type="primary" data-test="baseline-compare" @click="openCompare(row as Baseline)">{{ t('delivery.compare') }}</el-button>
              <el-button v-if="!readonly" link type="primary" @click="deliveryApi.exportBaseline(projectId, row.id, form.lang).catch(showError)">{{ t('delivery.exportList') }}</el-button>
              <el-button v-if="!readonly" link type="primary" data-test="baseline-pack" @click="packFromBaseline(row as Baseline)">{{ t('delivery.packBaseline') }}</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 镜像 -->
    <el-card v-else shadow="never" data-test="mirror-status">
      <template v-if="mirror">
        <el-alert v-if="!mirror.enabled" type="info" :closable="false" show-icon :title="t('delivery.mirrorDisabled')" />
        <template v-else>
          <el-descriptions :column="2" border size="small" class="mb">
            <el-descriptions-item :label="t('delivery.mirrorDir')">{{ mirror.dir }}</el-descriptions-item>
            <el-descriptions-item :label="t('delivery.mirrorMode')">{{ t(`delivery.mode.${mirror.mode}`) }}</el-descriptions-item>
            <el-descriptions-item :label="t('delivery.lastSync')">{{ time(mirror.last_sync) }}</el-descriptions-item>
            <el-descriptions-item :label="t('delivery.pending')">{{ mirror.pending_events }}</el-descriptions-item>
            <el-descriptions-item v-if="mirror.last_job" :label="t('delivery.lastJob')" :span="2">
              {{ t(`delivery.status.${mirror.last_job.status}`) }}
              <span v-if="mirror.last_job.result" class="muted ml">
                {{ t('delivery.mirrorResult', { added: mirror.last_job.result.added ?? 0, removed: mirror.last_job.result.removed ?? 0, linked: mirror.last_job.result.linked ?? 0, copied: mirror.last_job.result.copied ?? 0 }) }}
              </span>
              <div v-if="mirror.last_job.error" class="err">{{ mirror.last_job.error }}</div>
            </el-descriptions-item>
          </el-descriptions>
          <el-button v-if="can('project.config')" @click="syncMirror">{{ t('delivery.syncNow') }}</el-button>
        </template>
        <p class="muted">{{ t('delivery.mirrorNote') }}</p>
      </template>
    </el-card>

    <!-- 基线详情 -->
    <el-drawer v-model="view.open" :title="view.baseline?.name ?? ''" size="75%" data-test="baseline-detail">
      <el-table v-loading="view.loading" :data="view.baseline?.items ?? []" size="small">
        <el-table-column prop="milestone_code" :label="t('matrix.milestone')" width="80" />
        <el-table-column :label="t('browse.location')" min-width="220">
          <template #default="{ row }">{{ where(row) }}</template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.status')" width="120">
          <template #default="{ row }"><StatusTag :status="row.status" :overdue="false" /></template>
        </el-table-column>
        <el-table-column :label="t('upload.col.files')" min-width="320">
          <template #default="{ row }">
            <div v-for="f in (row as BaselineRow).files" :key="f.version_id">V{{ f.version_no }} {{ f.filename }}</div>
            <el-tag v-if="!row.files.length" type="danger" size="small" effect="plain">{{ t('delivery.missing') }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-drawer>

    <!-- 对比 -->
    <el-dialog v-model="cmp.open" :title="t('delivery.compareTitle', { name: cmp.base?.name ?? '' })" width="900px" data-test="compare-dialog">
      <div class="row mb">
        <span>{{ t('delivery.compareWith') }}</span>
        <el-select v-model="cmp.other" clearable :placeholder="t('delivery.now')" class="w240">
          <el-option v-for="b in baselines.filter((x) => x.id !== cmp.base?.id)" :key="b.id" :value="b.id" :label="b.name" />
        </el-select>
      </div>
      <el-empty v-if="cmp.result && !cmp.result.rows.length" :description="t('delivery.noDiff')" :image-size="50" />
      <el-table v-else v-loading="cmp.loading" :data="cmp.result?.rows ?? []" size="small" max-height="460">
        <el-table-column :label="t('browse.location')" min-width="240">
          <template #default="{ row }">{{ row.milestone_code }} {{ where(row) }}</template>
        </el-table-column>
        <el-table-column :label="t('delivery.changes')" min-width="380">
          <template #default="{ row }">
            <div v-for="(c, i) in row.changes" :key="i" data-test="compare-change">
              <el-tag size="small" effect="plain">{{ t(`delivery.change.${c.kind}`) }}</el-tag>
              <span class="ml">
                <template v-if="c.kind === 'status'">{{ t(`status.${c.from}`) }} → {{ t(`status.${c.to}`) }}</template>
                <template v-else>{{ [c.from, c.to].filter(Boolean).join(' → ') }}</template>
              </span>
            </div>
          </template>
        </el-table-column>
      </el-table>
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
}
.toolbar h2 {
  margin: 0 12px 0 0;
  font-size: 18px;
}
.spacer {
  flex: 1;
}
.row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  align-items: center;
}
.form :deep(.el-form-item) {
  margin-bottom: 12px;
}
.w200 {
  width: 200px;
}
.w220 {
  width: 220px;
}
.w240 {
  width: 240px;
}
.w300 {
  width: 300px;
}
.w360 {
  width: 360px;
}
.ml {
  margin-left: 8px;
}
.mr {
  margin-right: 12px;
}
.mb {
  margin-bottom: 10px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.err {
  color: var(--el-color-danger);
  font-size: 12px;
}
</style>
