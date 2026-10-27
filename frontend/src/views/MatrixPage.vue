<script setup lang="ts">
// 应交矩阵页：顶部汇总（点数字即筛选）+ 筛选栏（条件写进地址栏）+ 列表 / 矩阵两种看法 + 改状态（单个 / 批量）+ 缺件清单
import { Download, Filter, Refresh, Search } from '@element-plus/icons-vue'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import {
  type Filters,
  type Item,
  type Matrix,
  type Stats,
  type Summary,
  type TreeMilestone,
  deliverablesApi,
} from '@/api/deliverables'
import { type Equipment, type EquipmentType, equipmentApi } from '@/api/equipment'
import { showError } from '@/api/errors'
import { download } from '@/api/http'
import type { Responsible } from '@/api/settings'
import { type StructureTree, structureApi } from '@/api/structure'
import DeliverableEditDialog from '@/components/DeliverableEditDialog.vue'
import FilesDrawer from '@/components/FilesDrawer.vue'
import TransmittalCreateDialog from '@/components/TransmittalCreateDialog.vue'
import UploadDialog from '@/components/UploadDialog.vue'
import StatusTag from '@/components/StatusTag.vue'
import { useProjectContext } from '@/composables/useProjectContext'
import { filtersFromQuery, filtersToQuery } from '@/utils/filterQuery'
import { COLORS, OPEN_STATUSES, STATUSES, displayKey, percent } from '@/utils/status'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly, readonlyNotice, can } = useProjectContext()

// ---------------------------------------------------------------- 筛选条件（地址栏里）
const filters = computed<Filters>(() => filtersFromQuery(route.query))
const view = computed(() => (route.query.view === 'matrix' ? 'matrix' : 'list'))
const keyword = ref(filters.value.q ?? '')
const moreOpen = ref(false)

function setFilters(next: Filters): void {
  const query = filtersToQuery(next)
  if (view.value === 'matrix') query.view = 'matrix'
  router.replace({ query })
}

function patch(changes: Partial<Filters>): void {
  setFilters({ ...filters.value, ...changes })
}

function setView(v: 'list' | 'matrix'): void {
  router.replace({ query: { ...route.query, view: v === 'matrix' ? 'matrix' : undefined } })
}

function clearFilters(): void {
  keyword.value = ''
  setFilters({})
}

const activeCount = computed(() => Object.keys(filtersToQuery(filters.value)).length)

// ---------------------------------------------------------------- 选项（结构、设备）
const structure = ref<StructureTree>({ modules: [], milestones: [] })
const equipment = ref<Equipment[]>([])
const equipmentTypes = ref<EquipmentType[]>([])
const docOptions = computed(() => structure.value.milestones.flatMap((m) => m.document_types))
const vendors = computed(() => [...new Set(equipment.value.map((e) => e.vendor).filter((v): v is string => !!v))].sort())

async function loadOptions(): Promise<void> {
  try {
    ;[structure.value, equipment.value, equipmentTypes.value] = await Promise.all([
      structureApi.tree(projectId.value),
      equipmentApi.list(projectId.value),
      equipmentApi.types(projectId.value),
    ])
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 数据
const summary = ref<Summary | null>(null)
const tree = ref<TreeMilestone[]>([])
const matrix = ref<Matrix | null>(null)
const loading = ref(false)
const matrixMilestone = ref<number | null>(null)

async function load(): Promise<void> {
  loading.value = true
  try {
    summary.value = await deliverablesApi.summary(projectId.value, filters.value)
    if (view.value === 'list') {
      tree.value = (await deliverablesApi.tree(projectId.value, filters.value)).milestones
    } else {
      const ms = matrixMilestone.value ?? filters.value.milestone_id?.[0] ?? structure.value.milestones[0]?.id ?? null
      matrixMilestone.value = ms
      matrix.value = ms
        ? await deliverablesApi.matrix(projectId.value, { ...filters.value, milestone_id: [ms] })
        : null
    }
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 汇总条：点数字即筛选
const overall = computed<Stats | null>(() => summary.value?.overall ?? null)
const showBreakdown = ref(false)

// 卡片：点一下筛出来，再点一下取消（顶部数字按里程碑 / 模块等范围统计，点卡片不会变）
type Flag = 'overdue' | 'review_overdue' | 'no_evidence' | 'outstanding'

function toggleFlag(flag: Flag): void {
  patch({ [flag]: filters.value[flag] ? undefined : true })
}

const notReceivedActive = computed(() => filters.value.received === false)
function toggleNotReceived(): void {
  patch({ received: notReceivedActive.value ? undefined : false })
}

function pendingActive(who: Responsible | 'unassigned'): boolean {
  const r = filters.value.responsible
  return r?.length === 1 && r[0] === (who === 'unassigned' ? 'none' : who) && (filters.value.status?.length ?? 0) > 0
}

function filterPending(who: Responsible | 'unassigned'): void {
  if (pendingActive(who)) patch({ responsible: undefined, status: undefined })
  else patch({ responsible: [who === 'unassigned' ? 'none' : who], status: OPEN_STATUSES })
}

// ---------------------------------------------------------------- 列表：里程碑 → 种类 →（模块）→ 应交项
interface Row {
  key: string
  kind: 'ms' | 'doc' | 'group' | 'item'
  label: string
  stats?: Stats
  item?: Item
  children?: Row[]
}

const rows = computed<Row[]>(() =>
  tree.value.map((ms) => ({
    key: `m${ms.milestone_id}`,
    kind: 'ms',
    label: `${ms.code} ${ms.name ?? ''}`,
    stats: ms.stats,
    children: ms.document_types.map((doc) => ({
      key: `d${doc.document_type_id}`,
      kind: 'doc',
      label: `${doc.submittal_no} ${doc.name}`,
      stats: doc.stats,
      children: doc.modules.length
        ? doc.modules.map((g) => ({
            key: `g${doc.document_type_id}-${g.module_id ?? 0}`,
            kind: 'group' as const,
            label: g.module_name ?? t('matrix.noModule'),
            stats: g.stats,
            children: g.items.map(itemRow),
          }))
        : doc.items.map(itemRow),
    })),
  })),
)

function itemRow(item: Item): Row {
  const where =
    item.kind === 'per_equipment'
      ? [item.equipment_tag, item.equipment_name].filter(Boolean).join('  ')
      : item.kind === 'per_module'
        ? (item.module_name ?? '')
        : t('matrix.wholeType')
  return { key: `i${item.id}`, kind: 'item', label: where, item }
}

const itemCount = computed(() => tree.value.reduce((n, ms) => n + ms.stats.total, 0))
// 应交项不多（≤ 300）时全部展开；多的时候只展开里程碑和行数少（≤ 20）的文件种类，
// 其余点开再看（一次画几百行会慢）。筛选后行数少了会自动全部展开。
const expandedKeys = computed(() => {
  const all = itemCount.value <= 300
  const count = (r: Row): number => (r.kind === 'item' ? 1 : (r.children ?? []).reduce((n, c) => n + count(c), 0))
  return rows.value.flatMap((ms) => [
    ms.key,
    ...(ms.children ?? [])
      .filter((doc) => all || count(doc) <= 20)
      .flatMap((doc) => [doc.key, ...(doc.children ?? []).filter((c) => c.kind === 'group').map((c) => c.key)]),
  ])
})

const selected = ref<Item[]>([])
const transmittalOpen = ref(false)

function transmittalLink(id: number | null) {
  return { name: 'transmittals', params: { projectId: projectId.value }, query: id ? { open: String(id) } : {} }
}

function onTransmittalCreated(): void {
  load()
}

// ---------------------------------------------------------------- 文件
const upload = reactive({ open: false, items: [] as Item[] })
const filesOf = reactive({ open: false, item: null as Item | null })

function openUpload(items: Item[]): void {
  Object.assign(upload, { open: true, items })
}

function openFiles(item: Item): void {
  Object.assign(filesOf, { open: true, item })
}

// 勾选的能不能一起传一个文件：同一个种类；多项时必须是按设备的（一个文件挂几台设备）
const canUploadSelected = computed(() => {
  const s = selected.value
  if (!s.length) return false
  const doc = s[0]!.document_type_id
  return s.every((i) => i.document_type_id === doc) && (s.length === 1 || s.every((i) => i.kind === 'per_equipment'))
})

function onSelection(selection: unknown[]): void {
  selected.value = (selection as Row[]).filter((r) => r.kind === 'item' && r.item).map((r) => r.item!)
}

// ---------------------------------------------------------------- 矩阵
const matrixColumns = computed(() => {
  const m = matrix.value
  if (!m) return { modules: [], groups: [], hasWhole: false }
  return {
    modules: m.modules,
    groups: m.equipment_groups,
    hasWhole: m.rows.some((r) => r.cells.whole),
  }
})

function cellClass(cell: { received: boolean } | undefined): Record<string, boolean> {
  return { cell: true, got: !!cell?.received }
}

function cellStyle(cell: { status: Item['status']; overdue: boolean } | undefined): Record<string, string> {
  if (!cell) return {}
  const c = COLORS[displayKey(cell.status, cell.overdue)]
  return { background: c.bg, color: c.fg }
}

function cellLetter(cell: { status: Item['status']; overdue: boolean }): string {
  return t(`matrix.letter.${displayKey(cell.status, cell.overdue)}`)
}

async function openCell(documentTypeId: number, deliverableId: number): Promise<void> {
  try {
    const { items } = await deliverablesApi.list(projectId.value, { document_type_id: [documentTypeId] })
    const item = items.find((i) => i.id === deliverableId)
    if (item) openEdit(item)
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 改状态
const edit = reactive({ open: false, item: null as Item | null, ids: [] as number[] })

function openEdit(item: Item): void {
  if (readonly.value) return
  Object.assign(edit, { open: true, item, ids: [] })
}

function openBulk(): void {
  Object.assign(edit, { open: true, item: null, ids: selected.value.map((i) => i.id) })
}

// ---------------------------------------------------------------- 缺件清单
const outstanding = reactive({ open: false, vendor: [] as string[], lang: 'en' })

function exportOutstanding(): void {
  const f: Filters = { milestone_id: filters.value.milestone_id, vendor: outstanding.vendor.length ? outstanding.vendor : undefined }
  download(`/api/projects/${projectId.value}/deliverables/outstanding.xlsx`, { ...f, lang: outstanding.lang })
    .then(() => (outstanding.open = false))
    .catch(showError)
}

function dueText(item: Item): string {
  if (!item.due_date) return '—'
  return item.due_source === 'deliverable' ? `${item.due_date} *` : item.due_date
}

watch(
  () => route.query,
  () => {
    keyword.value = filters.value.q ?? ''
    load()
  },
)
watch(matrixMilestone, (v, old) => old !== null && v !== old && load())
watch(projectId, async () => {
  await loadOptions()
  await load()
})
onMounted(async () => {
  await loadOptions()
  await load()
})
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />

    <!-- 汇总条 -->
    <el-card v-if="overall" shadow="never" class="summary" body-class="summary-body" data-test="summary">
      <div class="kpi big">
        <div class="kpi-value">{{ percent(overall.completion) }}</div>
        <div class="kpi-label">{{ t('matrix.completion', { a: overall.approved, n: overall.applicable }) }}</div>
      </div>
      <button class="kpi" :class="{ active: notReceivedActive }" data-test="kpi-received" :title="t('matrix.receivedTip')" @click="toggleNotReceived">
        <div class="kpi-value">{{ percent(overall.received_rate) }}</div>
        <div class="kpi-label">{{ t('matrix.receivedRate', { a: overall.received, n: overall.applicable }) }}</div>
      </button>
      <button class="kpi danger" :class="{ active: !!filters.outstanding }" data-test="kpi-outstanding" :title="t('matrix.outstandingTip')" @click="toggleFlag('outstanding')">
        <div class="kpi-value">{{ overall.outstanding }}</div>
        <div class="kpi-label">{{ t('matrix.outstandingCount') }}</div>
      </button>
      <button class="kpi danger" :class="{ active: !!filters.overdue }" data-test="kpi-overdue" @click="toggleFlag('overdue')">
        <div class="kpi-value">{{ overall.overdue }}</div>
        <div class="kpi-label">{{ t('matrix.overdue') }}</div>
      </button>
      <button class="kpi warn" :class="{ active: !!filters.review_overdue }" @click="toggleFlag('review_overdue')">
        <div class="kpi-value">{{ overall.review_overdue }}</div>
        <div class="kpi-label">{{ t('matrix.reviewOverdue') }}</div>
      </button>
      <button class="kpi warn" :class="{ active: !!filters.no_evidence }" @click="toggleFlag('no_evidence')">
        <div class="kpi-value">{{ overall.no_evidence }}</div>
        <div class="kpi-label">{{ t('matrix.noEvidence') }}</div>
      </button>
      <div class="kpi-group">
        <div class="kpi-label">{{ t('matrix.pending') }}</div>
        <div class="pending">
          <button
            v-for="who in ['ours', 'vendor', 'client', 'unassigned'] as const"
            :key="who"
            class="chip"
            :class="{ active: pendingActive(who) }"
            :data-test="`chip-${who}`"
            @click="filterPending(who)"
          >
            {{ t(`matrix.who.${who}`) }} <b>{{ overall.pending[who] }}</b>
          </button>
        </div>
      </div>
      <div class="spacer" />
      <el-button link type="primary" @click="showBreakdown = !showBreakdown">
        {{ showBreakdown ? t('matrix.hideBreakdown') : t('matrix.showBreakdown') }}
      </el-button>
    </el-card>
    <el-card v-if="showBreakdown && summary" shadow="never">
      <div class="breakdown">
        <el-table :data="summary.milestones" size="small" border>
          <el-table-column :label="t('matrix.milestone')" min-width="160">
            <template #default="{ row }">{{ row.code }} {{ row.name }}</template>
          </el-table-column>
          <el-table-column :label="t('matrix.completionCol')" width="110">
            <template #default="{ row }">{{ percent(row.completion) }} ({{ row.approved }}/{{ row.applicable }})</template>
          </el-table-column>
          <el-table-column prop="overdue" :label="t('matrix.overdue')" width="70" />
          <el-table-column :label="t('matrix.pending')" min-width="200">
            <template #default="{ row }">
              {{ t('matrix.who.ours') }} {{ row.pending.ours }} · {{ t('matrix.who.vendor') }} {{ row.pending.vendor }} ·
              {{ t('matrix.who.client') }} {{ row.pending.client }} · {{ t('matrix.who.unassigned') }} {{ row.pending.unassigned }}
            </template>
          </el-table-column>
        </el-table>
        <el-table :data="summary.modules" size="small" border>
          <el-table-column :label="t('matrix.module')" min-width="140">
            <template #default="{ row }">{{ row.module_name ?? t('matrix.noModule') }}</template>
          </el-table-column>
          <el-table-column :label="t('matrix.completionCol')" width="110">
            <template #default="{ row }">{{ percent(row.completion) }} ({{ row.approved }}/{{ row.applicable }})</template>
          </el-table-column>
          <el-table-column prop="overdue" :label="t('matrix.overdue')" width="70" />
        </el-table>
      </div>
    </el-card>

    <!-- 筛选栏 -->
    <el-card shadow="never" body-class="filter-body">
      <div class="toolbar">
        <el-select
          :model-value="filters.milestone_id ?? []"
          multiple
          collapse-tags
          clearable
          :placeholder="t('matrix.allMilestones')"
          class="w180"
          @change="(v: number[]) => patch({ milestone_id: v })"
        >
          <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
        </el-select>
        <el-select
          :model-value="filters.status ?? []"
          multiple
          collapse-tags
          clearable
          :placeholder="t('matrix.allStatuses')"
          class="w180"
          data-test="filter-status"
          @change="(v: Item['status'][]) => patch({ status: v })"
        >
          <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.${s}`)" />
        </el-select>
        <el-select
          :model-value="filters.module_id ?? []"
          multiple
          collapse-tags
          clearable
          :placeholder="t('matrix.allModules')"
          class="w160"
          @change="(v: number[]) => patch({ module_id: v })"
        >
          <el-option v-for="m in structure.modules" :key="m.id" :value="m.id" :label="m.name" />
        </el-select>
        <el-input
          v-model="keyword"
          :prefix-icon="Search"
          clearable
          :placeholder="t('matrix.search')"
          class="w200"
          @change="(v: string) => patch({ q: v.trim() || undefined })"
        />
        <el-button :icon="Filter" @click="moreOpen = !moreOpen">{{ t('matrix.moreFilters') }}</el-button>
        <el-button v-if="activeCount" link type="primary" data-test="clear-filters" @click="clearFilters">
          {{ t('matrix.clearFilters', { n: activeCount }) }}
        </el-button>
        <div class="spacer" />
        <el-radio-group :model-value="view" size="small" @change="(v: string | number | boolean | undefined) => setView(v as 'list' | 'matrix')">
          <el-radio-button value="list">{{ t('matrix.viewList') }}</el-radio-button>
          <el-radio-button value="matrix" data-test="view-matrix">{{ t('matrix.viewMatrix') }}</el-radio-button>
        </el-radio-group>
        <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
        <el-button v-if="can('export')" :icon="Download" @click="outstanding.open = true">{{ t('matrix.outstanding') }}</el-button>
      </div>
      <div v-if="moreOpen" class="toolbar more">
        <el-select :model-value="filters.document_type_id ?? []" multiple collapse-tags clearable filterable :placeholder="t('matrix.docTypes')" class="w220" @change="(v: number[]) => patch({ document_type_id: v })">
          <el-option v-for="d in docOptions" :key="d.id" :value="d.id" :label="`${d.submittal_no} ${d.name}`" />
        </el-select>
        <el-select :model-value="filters.equipment_type_id ?? []" multiple collapse-tags clearable :placeholder="t('matrix.equipmentTypes')" class="w180" @change="(v: number[]) => patch({ equipment_type_id: v })">
          <el-option v-for="ty in equipmentTypes" :key="ty.id" :value="ty.id" :label="ty.name" />
        </el-select>
        <el-select :model-value="filters.equipment_id ?? []" multiple collapse-tags clearable filterable :placeholder="t('matrix.equipment')" class="w220" @change="(v: number[]) => patch({ equipment_id: v })">
          <el-option v-for="e in equipment" :key="e.id" :value="e.id" :label="e.tag ? `${e.tag} ${e.name}` : e.name" />
        </el-select>
        <el-select :model-value="filters.vendor ?? []" multiple collapse-tags clearable :placeholder="t('matrix.vendor')" class="w180" @change="(v: string[]) => patch({ vendor: v })">
          <el-option v-for="v in vendors" :key="v" :value="v" :label="v" />
        </el-select>
        <el-select :model-value="filters.responsible ?? []" multiple collapse-tags clearable :placeholder="t('matrix.col.responsible')" class="w160" @change="(v: Responsible[]) => patch({ responsible: v })">
          <el-option v-for="r in ['ours', 'vendor', 'client', 'none'] as const" :key="r" :value="r" :label="t(`responsible.${r}`)" />
        </el-select>
        <el-select :model-value="filters.origin ?? []" multiple collapse-tags clearable :placeholder="t('structure.col.origin')" class="w160" @change="(v: string[]) => patch({ origin: v })">
          <el-option v-for="o in ['contract', 'client', 'internal']" :key="o" :value="o" :label="t(`origin.${o}`)" />
        </el-select>
        <el-select
          :model-value="filters.received === undefined ? undefined : String(filters.received)"
          clearable
          :placeholder="t('matrix.receivedFilter')"
          class="w140"
          data-test="filter-received"
          @change="(v: string | undefined) => patch({ received: v === undefined || v === '' ? undefined : v === 'true' })"
        >
          <el-option value="true" :label="t('matrix.received')" />
          <el-option value="false" :label="t('matrix.notReceived')" />
        </el-select>
        <el-checkbox :model-value="!!filters.outstanding" @change="(v: string | number | boolean) => patch({ outstanding: v ? true : undefined })">{{ t('matrix.outstandingCount') }}</el-checkbox>
        <el-checkbox :model-value="!!filters.overdue" @change="(v: string | number | boolean) => patch({ overdue: v ? true : undefined })">{{ t('matrix.overdue') }}</el-checkbox>
        <el-checkbox :model-value="!!filters.review_overdue" @change="(v: string | number | boolean) => patch({ review_overdue: v ? true : undefined })">{{ t('matrix.reviewOverdue') }}</el-checkbox>
        <el-checkbox :model-value="!!filters.no_evidence" @change="(v: string | number | boolean) => patch({ no_evidence: v ? true : undefined })">{{ t('matrix.noEvidence') }}</el-checkbox>
        <el-checkbox :model-value="filters.in_client_mdl === true" @change="(v: string | number | boolean) => patch({ in_client_mdl: v ? true : undefined })">{{ t('matrix.clientMdlOnly') }}</el-checkbox>
      </div>
    </el-card>

    <!-- 列表 -->
    <el-card v-if="view === 'list'" v-loading="loading" shadow="never">
      <div v-if="selected.length && !readonly" class="toolbar mb">
        <span>{{ t('matrix.selected', { n: selected.length }) }}</span>
        <el-button size="small" type="primary" data-test="bulk-edit" @click="openBulk">{{ t('matrix.bulkEdit') }}</el-button>
        <el-button size="small" data-test="make-transmittal" @click="transmittalOpen = true">{{ t('transmittal.fromSelection') }}</el-button>
        <el-tooltip :disabled="canUploadSelected" :content="t('upload.selectionHint')">
          <span>
            <el-button size="small" :disabled="!canUploadSelected" data-test="upload-selected" @click="openUpload(selected)">
              {{ t('upload.toSelected') }}
            </el-button>
          </span>
        </el-tooltip>
      </div>
      <el-empty v-if="!rows.length && !loading" :description="t('matrix.empty')" />
      <el-table
        v-else
        :data="rows"
        row-key="key"
        :expand-row-keys="expandedKeys"
        :tree-props="{ children: 'children' }"
        size="small"
        :row-class-name="({ row }) => `row-${row.kind}`"
        data-test="matrix-list"
        @selection-change="onSelection"
        @row-dblclick="(row: Row) => row.item && openEdit(row.item)"
      >
        <el-table-column v-if="!readonly" type="selection" width="36" :selectable="(row: Row) => row.kind === 'item'" />
        <el-table-column :label="t('matrix.col.name')" min-width="300">
          <template #default="{ row }">
            <span :class="`label-${row.kind}`">{{ row.label }}</span>
            <span v-if="row.stats" class="muted ml">
              {{ t('matrix.groupStats', { a: row.stats.approved, n: row.stats.applicable }) }}
              <span v-if="row.stats.overdue" class="overdue-count">{{ t('matrix.overdueCount', { n: row.stats.overdue }) }}</span>
            </span>
          </template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.status')" width="130">
          <template #default="{ row }">
            <StatusTag v-if="row.item" :status="row.item.status" :overdue="row.item.overdue" />
          </template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.received')" width="100">
          <template #default="{ row }">
            <el-button v-if="row.item?.file_count" link type="primary" data-test="item-files" @click.stop="openFiles(row.item)">
              {{ t('matrix.received') }} V{{ row.item.current_version }}<template v-if="row.item.file_count > 1"> ×{{ row.item.file_count }}</template>
            </el-button>
            <span v-else-if="row.item && row.item.status !== 'not_applicable'" class="not-received" data-test="not-received">{{ t('matrix.notReceived') }}</span>
          </template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.responsible')" width="80">
          <template #default="{ row }">{{ row.item ? t(`responsible.${row.item.responsible}`) : '' }}</template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.due')" width="110">
          <template #default="{ row }">{{ row.item ? dueText(row.item) : '' }}</template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.submitted')" width="100">
          <template #default="{ row }">{{ row.item?.submitted_date ?? '' }}</template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.statusDate')" width="100">
          <template #default="{ row }">{{ row.item?.status_date ?? '' }}</template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.transmittal')" width="190">
          <template #default="{ row }">
            <template v-if="row.item?.transmittal_no">
              <router-link :to="transmittalLink(row.item.transmittal_id)" @click.stop>{{ row.item.transmittal_no }}</router-link>
              <el-tag v-if="row.item.review_code" size="small">{{ row.item.review_code }}</el-tag>
            </template>
          </template>
        </el-table-column>
        <el-table-column :label="t('matrix.col.flags')" width="150">
          <template #default="{ row }">
            <el-tag v-if="row.item?.review_overdue" size="small" type="warning">{{ t('matrix.reviewOverdue') }}</el-tag>
            <el-tag v-if="row.item?.no_evidence" size="small" type="danger">{{ t('matrix.noEvidence') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column v-if="!readonly" width="110" fixed="right">
          <template #default="{ row }">
            <template v-if="row.item">
              <el-button link type="primary" data-test="upload-item" @click.stop="openUpload([row.item])">{{ t('upload.upload') }}</el-button>
              <el-button v-if="can('deliverable.update')" link type="primary" data-test="edit-item" @click="openEdit(row.item)">{{ t('common.edit') }}</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
      <p class="muted">{{ t('matrix.dueNote') }}<template v-if="itemCount > 300"> {{ t('matrix.collapsedNote', { n: itemCount }) }}</template></p>
    </el-card>

    <!-- 矩阵 -->
    <el-card v-else v-loading="loading" shadow="never">
      <div class="toolbar mb">
        <span>{{ t('matrix.milestone') }}</span>
        <el-select v-model="matrixMilestone" class="w220">
          <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
        </el-select>
        <div class="legend">
          <span v-for="k in ['approved', 'under_review', 'submitted', 'revise_resubmit', 'not_submitted', 'not_applicable', 'overdue'] as const" :key="k" class="legend-item">
            <span class="swatch" :style="{ background: COLORS[k].bg, color: COLORS[k].fg }">{{ t(`matrix.letter.${k}`) }}</span>
            {{ k === 'overdue' ? t('status.overdue') : t(`status.${k}`) }}
          </span>
          <span class="legend-item"><span class="swatch cell got" />{{ t('matrix.receivedDot') }}</span>
        </div>
      </div>
      <el-empty v-if="!matrix || !matrix.rows.length" :description="t('matrix.empty')" />
      <div v-else class="matrix-wrap" data-test="matrix-grid">
        <table class="matrix">
          <thead>
            <tr>
              <th rowspan="2" class="sticky">{{ t('matrix.col.name') }}</th>
              <th v-if="matrixColumns.hasWhole" rowspan="2">{{ t('matrix.wholeType') }}</th>
              <th v-if="matrixColumns.modules.length" :colspan="matrixColumns.modules.length">{{ t('matrix.modules') }}</th>
              <th v-for="g in matrixColumns.groups" :key="g.equipment_type_id ?? 0" :colspan="g.equipment.length">
                {{ g.name ?? t('matrix.noType') }}
              </th>
            </tr>
            <tr>
              <th v-for="m in matrixColumns.modules" :key="`m${m.id}`" class="col">{{ m.name }}</th>
              <template v-for="g in matrixColumns.groups" :key="`g${g.equipment_type_id ?? 0}`">
                <th v-for="e in g.equipment" :key="`e${e.id}`" class="col" :title="e.name">{{ e.tag || e.name }}</th>
              </template>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in matrix.rows" :key="r.document_type_id">
              <td class="sticky name">
                {{ r.submittal_no }} {{ r.name }}
                <span class="muted">{{ t('matrix.groupStats', { a: r.stats.approved, n: r.stats.applicable }) }}</span>
              </td>
              <td v-if="matrixColumns.hasWhole" :class="cellClass(r.cells.whole)" :style="cellStyle(r.cells.whole)" @click="r.cells.whole && openCell(r.document_type_id, r.cells.whole.deliverable_id)">
                {{ r.cells.whole ? cellLetter(r.cells.whole) : '' }}
              </td>
              <td v-for="m in matrixColumns.modules" :key="`m${m.id}`" :class="cellClass(r.cells[`m:${m.id}`])" :style="cellStyle(r.cells[`m:${m.id}`])" @click="r.cells[`m:${m.id}`] && openCell(r.document_type_id, r.cells[`m:${m.id}`].deliverable_id)">
                {{ r.cells[`m:${m.id}`] ? cellLetter(r.cells[`m:${m.id}`]) : '' }}
              </td>
              <template v-for="g in matrixColumns.groups" :key="`g${g.equipment_type_id ?? 0}`">
                <td
                  v-for="e in g.equipment"
                  :key="`e${e.id}`"
                  :class="cellClass(r.cells[`e:${e.id}`])"
                  :style="cellStyle(r.cells[`e:${e.id}`])"
                  :title="r.cells[`e:${e.id}`] ? `${e.tag ?? ''} ${e.name}` : ''"
                  @click="r.cells[`e:${e.id}`] && openCell(r.document_type_id, r.cells[`e:${e.id}`].deliverable_id)"
                >
                  {{ r.cells[`e:${e.id}`] ? cellLetter(r.cells[`e:${e.id}`]) : '' }}
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted">{{ t('matrix.matrixNote') }}</p>
    </el-card>

    <DeliverableEditDialog v-model="edit.open" :project-id="projectId" :item="edit.item" :ids="edit.ids" @saved="load" />
    <UploadDialog v-model="upload.open" :project-id="projectId" :items="upload.items" @stored="load" />
    <FilesDrawer v-model="filesOf.open" :project-id="projectId" :item="filesOf.item" :readonly="readonly" @changed="load" />
    <TransmittalCreateDialog v-model="transmittalOpen" :project-id="projectId" :preset-items="selected" @created="onTransmittalCreated" />

    <el-dialog v-model="outstanding.open" :title="t('matrix.outstanding')" width="480px">
      <p class="muted">{{ t('matrix.outstandingHint') }}</p>
      <el-form label-width="90px">
        <el-form-item :label="t('matrix.vendor')">
          <el-select v-model="outstanding.vendor" multiple clearable :placeholder="t('matrix.allVendors')" style="width: 100%">
            <el-option v-for="v in vendors" :key="v" :value="v" :label="v" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('header.language')">
          <el-radio-group v-model="outstanding.lang">
            <el-radio value="en">English</el-radio>
            <el-radio value="zh">{{ t('lang.zh-CN') }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="outstanding.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="exportOutstanding">{{ t('common.download') }}</el-button>
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
:deep(.summary-body) {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
}
.kpi {
  border: 1px solid var(--el-border-color-lighter);
  background: #fff;
  border-radius: 8px;
  padding: 6px 14px;
  min-width: 96px;
  text-align: left;
  cursor: pointer;
  font: inherit;
}
.kpi.big {
  cursor: default;
  border-color: var(--el-color-primary-light-7);
}
.kpi:not(.big):hover {
  border-color: var(--el-color-primary);
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--brand-navy);
}
.kpi.danger .kpi-value {
  color: #9c0006;
}
.kpi.warn .kpi-value {
  color: #9c4400;
}
.kpi-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.pending {
  display: flex;
  gap: 6px;
  margin-top: 4px;
}
.chip {
  border: 1px solid var(--el-border-color);
  background: #fff;
  border-radius: 12px;
  padding: 2px 10px;
  cursor: pointer;
  font: inherit;
  font-size: 12px;
}
.chip:hover {
  border-color: var(--el-color-primary);
}
.kpi.active,
.chip.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
}
.not-received {
  color: var(--el-color-danger);
  font-size: 12px;
}
/* 矩阵格子：收到文件的右上角一个小圆点 */
.cell.got {
  position: relative;
}
.cell.got::after {
  content: '';
  position: absolute;
  top: 3px;
  right: 3px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--brand-navy);
}
.breakdown {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 12px;
}
:deep(.filter-body) {
  padding: 10px 16px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.toolbar.more {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.spacer {
  flex: 1;
}
.w160 {
  width: 160px;
}
.w180 {
  width: 180px;
}
.w200 {
  width: 200px;
}
.w220 {
  width: 220px;
}
.mb {
  margin-bottom: 8px;
}
.ml {
  margin-left: 8px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.overdue-count {
  color: #9c0006;
  margin-left: 6px;
}
.label-ms {
  font-weight: 700;
  color: var(--brand-navy);
}
.label-doc {
  font-weight: 600;
}
.label-group {
  color: var(--el-text-color-regular);
  font-style: italic;
}
:deep(.row-ms) {
  background: var(--el-color-primary-light-9);
}
.legend {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  margin-left: 16px;
  font-size: 12px;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.swatch {
  display: inline-block;
  min-width: 18px;
  text-align: center;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 3px;
  font-weight: 700;
}
.matrix-wrap {
  overflow: auto;
  max-height: 70vh;
}
.matrix {
  border-collapse: collapse;
  font-size: 12px;
}
.matrix th,
.matrix td {
  border: 1px solid var(--el-border-color-lighter);
  padding: 3px 6px;
  white-space: nowrap;
}
.matrix thead th {
  background: var(--brand-navy);
  color: #fff;
  position: sticky;
  top: 0;
  z-index: 1;
}
.matrix thead tr:nth-child(2) th {
  top: 24px;
}
.matrix .sticky {
  position: sticky;
  left: 0;
  z-index: 2;
  background: #fff;
}
.matrix thead .sticky {
  background: var(--brand-navy);
  z-index: 3;
}
.matrix td.name {
  min-width: 260px;
}
.matrix th.col {
  min-width: 34px;
  font-weight: 500;
}
.matrix td.cell {
  text-align: center;
  cursor: pointer;
  font-weight: 700;
}
.matrix td.cell:hover {
  outline: 2px solid var(--el-color-primary);
}
</style>
