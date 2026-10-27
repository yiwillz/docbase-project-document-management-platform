<script setup lang="ts">
// 文件浏览：左边目录树（里程碑 / 种类、设备种类 / 设备、模块三种视图，另有待归类、回收区），右边文件列表
// （筛选、关键字、排序、分页）；点文件看详情（版本、操作记录、预览、上传新版本、重新归类）；
// 选中一个应交项时可以"上传到此处"。视图、节点、筛选都写在地址栏里
import { Refresh, Search, Upload } from '@element-plus/icons-vue'
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import { type FileRow, type FileTree, RECYCLE, type RecycledVersion, type TreeNode, UNCLASSIFIED, VIEWS, type View, browseApi } from '@/api/browse'
import { type Filters, type Item, deliverablesApi } from '@/api/deliverables'
import { type Equipment, type EquipmentType, equipmentApi } from '@/api/equipment'
import { showError } from '@/api/errors'
import { SOURCES, type Source, filesApi } from '@/api/files'
import { type StructureTree, structureApi } from '@/api/structure'
import FileDetailDrawer from '@/components/FileDetailDrawer.vue'
import StatusTag from '@/components/StatusTag.vue'
import UploadDialog from '@/components/UploadDialog.vue'
import { useProjectContext } from '@/composables/useProjectContext'
import { filtersFromQuery, filtersToQuery } from '@/utils/filterQuery'
import { STATUSES } from '@/utils/status'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { projectId, readonly } = useProjectContext()

// ---------------------------------------------------------------- 地址栏里的状态
const view = computed<View>(() => (VIEWS.includes(route.query.view as View) ? (route.query.view as View) : 'milestone'))
const node = computed(() => (typeof route.query.node === 'string' ? route.query.node : ''))
const filters = computed<Filters>(() => filtersFromQuery(route.query))
const sources = computed<Source[]>(() =>
  ([] as unknown[]).concat(route.query.source ?? []).filter((s): s is Source => SOURCES.includes(s as Source)),
)
const sort = computed(() => (['name', 'uploaded_at', 'size'].includes(String(route.query.sort)) ? String(route.query.sort) : 'uploaded_at') as 'name' | 'uploaded_at' | 'size')
const order = computed(() => (route.query.order === 'asc' ? 'asc' : 'desc'))
const page = computed(() => Math.max(1, Number(route.query.page) || 1))
const PAGE_SIZE = 100

function setQuery(changes: Record<string, unknown>, resetPage = true): void {
  const query: Record<string, unknown> = { ...route.query, ...changes }
  if (resetPage && !('page' in changes)) delete query.page
  for (const [k, v] of Object.entries(query)) if (v === undefined || v === '' || (Array.isArray(v) && !v.length)) delete query[k]
  router.replace({ query: query as Record<string, string | string[]> })
}

function patchFilters(changes: Partial<Filters>): void {
  const next = filtersToQuery({ ...filters.value, ...changes })
  const keep = { view: route.query.view, node: route.query.node, source: route.query.source, sort: route.query.sort, order: route.query.order }
  router.replace({ query: { ...keep, ...next } as Record<string, string | string[]> })
}

// ---------------------------------------------------------------- 选项
const structure = ref<StructureTree>({ modules: [], milestones: [] })
const equipment = ref<Equipment[]>([])
const types = ref<EquipmentType[]>([])
const items = ref<Item[]>([])
const vendors = computed(() => [...new Set(equipment.value.map((e) => e.vendor).filter((v): v is string => !!v))].sort())
const docTypes = computed(() => structure.value.milestones.flatMap((m) => m.document_types))

async function loadOptions(): Promise<void> {
  try {
    const [s, e, ty, list] = await Promise.all([
      structureApi.tree(projectId.value),
      equipmentApi.list(projectId.value),
      equipmentApi.types(projectId.value),
      deliverablesApi.list(projectId.value),
    ])
    structure.value = s
    equipment.value = e
    types.value = ty
    items.value = list.items
  } catch (err) {
    showError(err)
  }
}

// ---------------------------------------------------------------- 目录树
const tree = ref<FileTree | null>(null)
const onlyWithFiles = ref(false)
const treeRef = ref<{ filter: (v: unknown) => void; setCurrentKey: (k: string | null) => void }>()

const treeData = computed<TreeNode[]>(() => {
  const tr = tree.value
  if (!tr) return []
  return [
    ...tr.nodes,
    { key: UNCLASSIFIED, label: t('drop.unclassified'), count: tr.unclassified, children: [] },
    { key: RECYCLE, label: t('browse.recycle'), count: tr.recycle, children: [] },
  ]
})

function nodeLabel(n: TreeNode): string {
  if (n.untyped) return t('browse.untyped')
  if (n.unassigned) return t('browse.noModule')
  return n.label || t('matrix.wholeType')
}

async function loadTree(): Promise<void> {
  try {
    tree.value = await browseApi.tree(projectId.value, view.value)
  } catch (err) {
    showError(err)
  }
}

function filterNode(_value: unknown, data: Record<string, unknown>): boolean {
  const n = data as unknown as TreeNode
  return !onlyWithFiles.value || n.count > 0 || n.key === UNCLASSIFIED || n.key === RECYCLE
}

watch(onlyWithFiles, () => treeRef.value?.filter(onlyWithFiles.value))

function findNode(nodes: TreeNode[], key: string): TreeNode | null {
  for (const n of nodes) {
    if (n.key === key) return n
    const hit = findNode(n.children, key)
    if (hit) return hit
  }
  return null
}

const current = computed(() => (node.value ? findNode(treeData.value, node.value) : null))
const uploadTarget = computed(() => {
  const id = current.value?.deliverable_id
  return id ? items.value.find((i) => i.id === id) ?? null : null
})

// ---------------------------------------------------------------- 文件列表
const rows = ref<FileRow[]>([])
const recycled = ref<RecycledVersion[]>([])
const total = ref(0)
const loading = ref(false)
const keyword = ref(filters.value.q ?? '')

let listSeq = 0

async function loadList(): Promise<void> {
  const seq = ++listSeq // 打开页面时和改条件时都会查，只用最后一次的结果（先发的请求可能后回来）
  loading.value = true
  try {
    if (node.value === RECYCLE) {
      const recycledRows = await browseApi.recycle(projectId.value)
      if (seq !== listSeq) return
      recycled.value = recycledRows
      rows.value = []
      total.value = recycled.value.length
      return
    }
    const r = await browseApi.list(projectId.value, {
      ...filters.value,
      view: view.value,
      node: node.value || undefined,
      source: sources.value.length ? sources.value : undefined,
      sort: sort.value,
      order: order.value,
      offset: (page.value - 1) * PAGE_SIZE,
      limit: PAGE_SIZE,
    })
    if (seq !== listSeq) return
    rows.value = r.items
    total.value = r.total
  } catch (err) {
    if (seq === listSeq) showError(err)
  } finally {
    if (seq === listSeq) loading.value = false
  }
}

function onSort({ prop, order: o }: { prop: string | null; order: 'ascending' | 'descending' | null }): void {
  const key = prop === 'filename' ? 'name' : prop === 'size' ? 'size' : 'uploaded_at'
  setQuery({ sort: o ? key : undefined, order: o === 'ascending' ? 'asc' : o ? 'desc' : undefined })
}

function where(loc: FileRow['locations'][number]): string {
  return [`${loc.submittal_no ?? ''} ${loc.document_type_name ?? ''}`.trim(), loc.where].filter(Boolean).join(' / ')
}

function size(bytes: number | null): string {
  if (bytes === null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

const activeFilters = computed(() => Object.keys(filtersToQuery(filters.value)).length + (sources.value.length ? 1 : 0))

function clearFilters(): void {
  keyword.value = ''
  router.replace({ query: { view: route.query.view, node: route.query.node } as Record<string, string> })
}

// ---------------------------------------------------------------- 详情、上传
const detailOpen = ref(false)
const detailId = ref<number | null>(null)
const uploadOpen = ref(false)

function openDetail(id: number): void {
  detailId.value = id
  detailOpen.value = true
}

function refresh(): void {
  loadTree()
  loadList()
}

watch(() => [view.value, projectId.value], loadTree)
watch(() => [route.query, projectId.value], loadList, { deep: true })
watch(projectId, loadOptions)
watch(tree, () => {
  if (node.value) treeRef.value?.setCurrentKey(node.value)
  if (onlyWithFiles.value) treeRef.value?.filter(true)
})
onMounted(() => {
  loadOptions()
  loadTree()
  loadList()
})
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <h2>{{ t('menu.files') }}</h2>
      <el-radio-group :model-value="view" size="small" @change="(v: string | number | boolean | undefined) => setQuery({ view: v === 'milestone' ? undefined : v, node: undefined })">
        <el-radio-button v-for="v in VIEWS" :key="v" :value="v" :data-test="`view-${v}`">{{ t(`browse.view.${v}`) }}</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <el-button :icon="Refresh" size="small" @click="refresh">{{ t('common.refresh') }}</el-button>
    </div>

    <div class="body">
      <!-- 目录树 -->
      <el-card shadow="never" class="tree-card">
        <div class="tree-head">
          <el-button link size="small" :type="node ? 'primary' : 'default'" @click="setQuery({ node: undefined })">
            {{ t('browse.all', { n: tree?.view_total ?? 0 }) }}
          </el-button>
          <el-checkbox v-model="onlyWithFiles" size="small">{{ t('browse.onlyWithFiles') }}</el-checkbox>
        </div>
        <el-tree
          ref="treeRef"
          :data="treeData"
          node-key="key"
          :props="{ label: 'label', children: 'children' }"
          :default-expanded-keys="node ? [node] : []"
          :filter-node-method="filterNode"
          highlight-current
          :expand-on-click-node="false"
          data-test="file-tree"
          @node-click="(n: TreeNode) => setQuery({ node: n.key })"
        >
          <template #default="{ data }">
            <span class="tree-node" :class="{ empty: !(data as TreeNode).count }">
              <span>{{ nodeLabel(data as TreeNode) }}</span>
              <span class="count">{{ (data as TreeNode).count }}</span>
            </span>
          </template>
        </el-tree>
      </el-card>

      <!-- 文件列表 -->
      <el-card shadow="never" class="list-card">
        <div class="filters">
          <el-input
            v-model="keyword"
            :prefix-icon="Search"
            clearable
            :placeholder="t('browse.search')"
            class="w240"
            data-test="files-search"
            @change="() => patchFilters({ q: keyword.trim() || undefined })"
          />
          <el-select :model-value="filters.milestone_id ?? []" multiple collapse-tags clearable :placeholder="t('matrix.allMilestones')" class="w160" @change="(v: number[]) => patchFilters({ milestone_id: v })">
            <el-option v-for="m in structure.milestones" :key="m.id" :value="m.id" :label="`${m.code} ${m.name ?? ''}`" />
          </el-select>
          <el-select :model-value="filters.document_type_id ?? []" multiple collapse-tags clearable filterable :placeholder="t('browse.docType')" class="w180" @change="(v: number[]) => patchFilters({ document_type_id: v })">
            <el-option v-for="d in docTypes" :key="d.id" :value="d.id" :label="`${d.submittal_no} ${d.name}`" />
          </el-select>
          <el-select :model-value="filters.module_id ?? []" multiple collapse-tags clearable :placeholder="t('matrix.module')" class="w140" @change="(v: number[]) => patchFilters({ module_id: v })">
            <el-option v-for="m in structure.modules" :key="m.id" :value="m.id" :label="m.name" />
          </el-select>
          <el-select :model-value="filters.equipment_type_id ?? []" multiple collapse-tags clearable :placeholder="t('browse.equipmentType')" class="w140" @change="(v: number[]) => patchFilters({ equipment_type_id: v })">
            <el-option v-for="ty in types" :key="ty.id" :value="ty.id" :label="ty.name" />
          </el-select>
          <el-select :model-value="filters.equipment_id ?? []" multiple collapse-tags clearable filterable :placeholder="t('browse.equipment')" class="w180" data-test="filter-equipment" @change="(v: number[]) => patchFilters({ equipment_id: v })">
            <el-option v-for="e in equipment" :key="e.id" :value="e.id" :label="[e.tag, e.name].filter(Boolean).join(' ')" />
          </el-select>
          <el-select :model-value="filters.vendor ?? []" multiple collapse-tags clearable :placeholder="t('browse.vendor')" class="w140" @change="(v: string[]) => patchFilters({ vendor: v })">
            <el-option v-for="v in vendors" :key="v" :value="v" :label="v" />
          </el-select>
          <el-select :model-value="sources" multiple collapse-tags clearable :placeholder="t('upload.source')" class="w120" data-test="filter-source" @change="(v: Source[]) => setQuery({ source: v })">
            <el-option v-for="s in SOURCES" :key="s" :value="s" :label="t(`upload.sources.${s}`)" />
          </el-select>
          <el-select :model-value="filters.status ?? []" multiple collapse-tags clearable :placeholder="t('matrix.col.status')" class="w140" @change="(v: Filters['status']) => patchFilters({ status: v })">
            <el-option v-for="s in STATUSES" :key="s" :value="s" :label="t(`status.${s}`)" />
          </el-select>
          <el-button v-if="activeFilters" link type="primary" @click="clearFilters">{{ t('browse.clear', { n: activeFilters }) }}</el-button>
        </div>

        <div class="toolbar mb">
          <span class="muted">
            {{ current ? nodeLabel(current) : t('browse.allFiles') }} · {{ t('browse.count', { n: total }) }}
          </span>
          <div class="spacer" />
          <el-button v-if="uploadTarget && !readonly" type="primary" size="small" :icon="Upload" data-test="upload-here" @click="uploadOpen = true">
            {{ t('browse.uploadHere') }}
          </el-button>
        </div>

        <!-- 回收区 -->
        <el-table v-if="node === RECYCLE" v-loading="loading" :data="recycled" size="small" data-test="recycle-list">
          <el-table-column :label="t('upload.col.filename')" min-width="300">
            <template #default="{ row }">
              {{ row.filename }} <span class="muted">V{{ row.version_no }}</span>
              <div class="muted">{{ (row as RecycledVersion).locations.map(where).join(t('common.listSep')) }}</div>
            </template>
          </el-table-column>
          <el-table-column :label="t('browse.recycledAt')" width="120">
            <template #default="{ row }">{{ String(row.recycled_at).slice(0, 10) }}</template>
          </el-table-column>
          <el-table-column :label="t('browse.purgeOn')" width="150">
            <template #default="{ row }">{{ t('browse.purgeLine', { date: row.purge_on, n: row.days_left }) }}</template>
          </el-table-column>
          <el-table-column width="130">
            <template #default="{ row }">
              <a v-if="row.preview" :href="browseApi.previewUrl(projectId, row.id)" target="_blank" class="link">{{ t('browse.preview') }}</a>
              <a :href="filesApi.downloadUrl(projectId, row.id)" class="link">{{ t('common.download') }}</a>
            </template>
          </el-table-column>
        </el-table>

        <template v-else>
          <el-empty v-if="!rows.length && !loading" :description="t('browse.empty')" :image-size="60" />
          <el-table
            v-else
            v-loading="loading"
            :data="rows"
            size="small"
            data-test="files-list"
            :default-sort="{ prop: sort === 'name' ? 'filename' : sort, order: order === 'asc' ? 'ascending' : 'descending' }"
            @sort-change="onSort"
            @row-dblclick="(row: FileRow) => openDetail(row.id)"
          >
            <el-table-column prop="filename" :label="t('upload.col.filename')" min-width="280" sortable="custom">
              <template #default="{ row }">
                <el-button link type="primary" class="name" data-test="open-file" @click="openDetail(row.id)">{{ row.filename }}</el-button>
                <div class="muted">{{ t('upload.originalName', { name: row.original_name ?? '' }) }}</div>
              </template>
            </el-table-column>
            <el-table-column :label="t('upload.col.version')" width="60">
              <template #default="{ row }">V{{ row.current_version }}</template>
            </el-table-column>
            <el-table-column :label="t('upload.source')" width="80">
              <template #default="{ row }">{{ t(`upload.sources.${row.source}`) }}</template>
            </el-table-column>
            <el-table-column :label="t('browse.location')" min-width="170">
              <template #default="{ row }">
                <template v-if="(row as FileRow).locations.length">
                  {{ where((row as FileRow).locations[0]!) }}
                  <span v-if="row.locations.length > 1" class="muted">{{ t('browse.more', { n: row.locations.length - 1 }) }}</span>
                </template>
                <el-tag v-else type="info" size="small">{{ t('drop.unclassified') }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column :label="t('matrix.col.status')" width="105">
              <template #default="{ row }">
                <StatusTag v-if="(row as FileRow).locations[0]?.status" :status="(row as FileRow).locations[0]!.status!" :overdue="false" />
              </template>
            </el-table-column>
            <el-table-column prop="size" :label="t('upload.col.size')" width="80" sortable="custom">
              <template #default="{ row }">{{ size(row.size) }}</template>
            </el-table-column>
            <el-table-column prop="uploaded_at" :label="t('upload.col.uploaded')" width="140" sortable="custom">
              <template #default="{ row }">
                {{ row.uploaded_by ?? '' }}
                <div class="muted">{{ (row.uploaded_at ?? '').slice(0, 16).replace('T', ' ') }}</div>
              </template>
            </el-table-column>
          </el-table>
          <el-pagination
            v-if="total > PAGE_SIZE"
            layout="prev, pager, next, total"
            :total="total"
            :page-size="PAGE_SIZE"
            :current-page="page"
            class="pager"
            @current-change="(p: number) => setQuery({ page: p > 1 ? String(p) : undefined }, false)"
          />
        </template>
      </el-card>
    </div>

    <FileDetailDrawer v-model="detailOpen" :project-id="projectId" :file-id="detailId" :items="items" :readonly="readonly" @changed="refresh" />
    <UploadDialog v-if="uploadTarget" v-model="uploadOpen" :project-id="projectId" :items="[uploadTarget]" @stored="refresh" />
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
.body {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.tree-card {
  flex: 0 0 280px;
  max-height: calc(100vh - 150px);
  overflow: auto;
}
.list-card {
  flex: 1;
  min-width: 0;
}
.tree-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.tree-node {
  display: flex;
  justify-content: space-between;
  flex: 1;
  gap: 8px;
  padding-right: 6px;
  font-size: 13px;
}
.tree-node.empty {
  color: var(--el-text-color-placeholder);
}
.count {
  color: var(--el-text-color-secondary);
}
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.w120 {
  width: 120px;
}
.w140 {
  width: 140px;
}
.w160 {
  width: 160px;
}
.w180 {
  width: 180px;
}
.w240 {
  width: 240px;
}
.mb {
  margin-bottom: 8px;
}
.name {
  white-space: normal;
  text-align: left;
  height: auto;
}
.link {
  margin-right: 8px;
  font-size: 12px;
}
.pager {
  margin-top: 10px;
  justify-content: flex-end;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
</style>
