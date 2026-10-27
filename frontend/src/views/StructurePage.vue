<script setup lang="ts">
// 项目结构页：左边里程碑，右边该里程碑的文件种类表格（常用字段直接改，不常用的在详情抽屉里）
import { ArrowDown, ArrowUp, Delete, Download, Edit, Plus, Refresh, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { type Equipment, type EquipmentType, equipmentApi } from '@/api/equipment'
import { showError } from '@/api/errors'
import { ApiError } from '@/api/http'
import { download } from '@/api/http'
import {
  type Acceptance,
  type DocumentTypeFields,
  type Milestone,
  type Module,
  type Origin,
  type StructureImportPreview,
  type StructureTree,
  structureApi,
  type TreeDocumentType,
} from '@/api/structure'
import IssueList from '@/components/IssueList.vue'
import { DOC_TYPE_HEADERS, ORIGIN_WORDS, YES_WORDS } from '@/i18n/recognition'
import PasteDialog from '@/components/PasteDialog.vue'
import { useProjectContext } from '@/composables/useProjectContext'
import { useSafeDelete } from '@/composables/useSafeDelete'
import { useProjectStore } from '@/stores/projects'

const { t } = useI18n()
const { projectId, readonly, readonlyNotice } = useProjectContext('project.config')
const projects = useProjectStore()

const ORIGINS: Origin[] = ['contract', 'client', 'internal']
const ACCEPTANCES: Acceptance[] = ['pending', 'accepted', 'declined']

const tree = ref<StructureTree>({ modules: [], milestones: [] })
const loading = ref(false)
const showInactive = ref(false)
const selectedId = ref<number | null>(null)

const milestone = computed(() => tree.value.milestones.find((m) => m.id === selectedId.value) ?? null)
const docs = computed(() => milestone.value?.document_types ?? [])
const moduleNames = computed(() => tree.value.modules.filter((m) => m.active).map((m) => m.name))
const isEmpty = computed(() => !tree.value.milestones.length)

async function load(): Promise<void> {
  loading.value = true
  try {
    tree.value = await structureApi.tree(projectId.value, showInactive.value)
    if (!tree.value.milestones.some((m) => m.id === selectedId.value)) {
      selectedId.value = tree.value.milestones[0]?.id ?? null
    }
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

// 写操作统一：执行、提示、刷新；出错时提示并刷新（把界面恢复成服务器上的样子）
async function act(fn: () => Promise<unknown>, success?: string): Promise<boolean> {
  try {
    await fn()
    if (success) ElMessage.success(success)
    return true
  } catch (err) {
    showError(err)
    return false
  } finally {
    await load()
  }
}

async function confirmBox(message: string): Promise<boolean> {
  try {
    await ElMessageBox.confirm(message, t('common.confirm'), {
      type: 'warning',
      confirmButtonText: t('common.ok'),
      cancelButtonText: t('common.cancel'),
    })
    return true
  } catch {
    return false
  }
}

// ---------------------------------------------------------------- 里程碑
const msDialog = ref(false)
const msForm = reactive({ id: 0, name: '', due_date: null as string | null, goal: '', description: '', remarks: '' })

function openMilestone(m?: Milestone): void {
  Object.assign(msForm, {
    id: m?.id ?? 0,
    name: m?.name ?? '',
    due_date: m?.due_date ?? null,
    goal: m?.goal ?? '',
    description: m?.description ?? '',
    remarks: m?.remarks ?? '',
  })
  msDialog.value = true
}

async function saveMilestone(): Promise<void> {
  const data = {
    name: msForm.name.trim() || null,
    due_date: msForm.due_date,
    goal: msForm.goal.trim() || null,
    description: msForm.description.trim() || null,
    remarks: msForm.remarks.trim() || null,
  }
  const ok = await act(async () => {
    if (msForm.id) await structureApi.updateMilestone(projectId.value, msForm.id, data)
    else selectedId.value = (await structureApi.createMilestone(projectId.value, data)).id
  }, t('common.saved'))
  if (ok) msDialog.value = false
}

async function moveMilestone(index: number, delta: number): Promise<void> {
  const ids = tree.value.milestones.map((m) => m.id)
  const [id] = ids.splice(index, 1)
  ids.splice(index + delta, 0, id)
  await act(() => structureApi.reorder(projectId.value, 'milestones', ids))
}

async function toggleMilestone(m: Milestone): Promise<void> {
  await act(() => structureApi.setMilestoneActive(projectId.value, m.id, !m.active))
}

async function deleteMilestone(m: Milestone): Promise<void> {
  if (!(await confirmBox(t('structure.deleteMilestoneConfirm', { code: m.code })))) return
  await act(() => structureApi.deleteMilestone(projectId.value, m.id), t('common.deleted'))
}

// ---------------------------------------------------------------- 文件种类：表格里直接改
async function updateDoc(doc: TreeDocumentType, changes: DocumentTypeFields): Promise<void> {
  await act(() => structureApi.updateDocumentType(projectId.value, doc.id, changes))
}

async function updateModules(doc: TreeDocumentType, names: string[]): Promise<void> {
  await act(() => structureApi.setModules(projectId.value, doc.id, names))
}

async function moveDoc(index: number, delta: number): Promise<void> {
  const ids = docs.value.map((d) => d.id)
  const [id] = ids.splice(index, 1)
  ids.splice(index + delta, 0, id)
  await act(() => structureApi.reorder(projectId.value, 'document_types', ids, selectedId.value ?? undefined))
}

async function toggleDoc(doc: TreeDocumentType): Promise<void> {
  await act(() => structureApi.setDocumentTypeActive(projectId.value, doc.id, !doc.active))
}

async function deleteDoc(doc: TreeDocumentType): Promise<void> {
  if (!(await confirmBox(t('structure.deleteDocConfirm', { no: doc.submittal_no, name: doc.name })))) return
  try {
    await structureApi.deleteDocumentType(projectId.value, doc.id)
    ElMessage.success(t('common.deleted'))
  } catch (err) {
    // 用过的（挂了文件、进过传送单或基线）只能停用：说明原因，提供"改为停用"
    if (err instanceof ApiError && err.status === 409 && doc.active) {
      if (await confirmBox(t('usage.docUsed', { no: doc.submittal_no, reason: err.message }))) {
        await act(() => structureApi.setDocumentTypeActive(projectId.value, doc.id, false), t('usage.deactivated'))
        return
      }
    } else {
      showError(err)
    }
  }
  await load()
}

// 新建一行：在最后，或插到某一行前面
const newName = ref('')
const insertAt = ref<number | null>(null)

async function addDoc(): Promise<void> {
  const name = newName.value.trim()
  if (!name || !selectedId.value) return
  const position = insertAt.value ?? undefined
  newName.value = '' // 先清空，可以接着输入下一个；失败时放回去
  insertAt.value = null
  const ok = await act(() => structureApi.bulkCreate(projectId.value, selectedId.value!, [{ name }], position))
  if (!ok) {
    newName.value = name
    insertAt.value = position ?? null
  }
}

// ---------------------------------------------------------------- 从 Excel 粘贴多行
const pasteOpen = ref(false)
const pasting = ref(false)
const PASTE_COLUMNS = computed(() =>
  (['name', 'category', 'origin', 'per_equipment', 'due_date', 'basis'] as const).map((key) => ({
    key,
    label: t(`structure.col.${{ per_equipment: 'perEquipment', due_date: 'due' }[key as string] ?? key}`),
    headers: DOC_TYPE_HEADERS[key],
  })),
)

async function pasteDocs(records: Record<string, string | null>[]): Promise<void> {
  const problems: string[] = []
  const rows: DocumentTypeFields[] = records.map((r, i) => {
    const row: DocumentTypeFields = { name: r.name ?? '' }
    if (!r.name) problems.push(t('paste.rowMissing', { row: i + 1, field: t('structure.col.name') }))
    if (r.category) row.category = r.category
    if (r.origin) {
      const origin = ORIGIN_WORDS[r.origin.toLowerCase()]
      if (!origin) problems.push(t('paste.rowBadValue', { row: i + 1, value: r.origin }))
      else row.origin = origin
    }
    if (r.per_equipment) row.per_equipment = YES_WORDS.includes(r.per_equipment.toLowerCase())
    if (r.due_date) {
      const d = r.due_date.replace(/[/.]/g, '-')
      if (!/^\d{4}-\d{1,2}-\d{1,2}$/.test(d)) problems.push(t('paste.rowBadValue', { row: i + 1, value: r.due_date }))
      else row.due_date = d.replace(/-(\d)(?=-|$)/g, '-0$1')
    }
    if (r.basis) row.basis = r.basis
    return row
  })
  if (problems.length) {
    ElMessage.error({ message: problems.slice(0, 5).join(t('common.listSep')), duration: 6000, showClose: true })
    return
  }
  pasting.value = true
  const ok = await act(
    () => structureApi.bulkCreate(projectId.value, selectedId.value!, rows),
    t('structure.pasted', { n: rows.length }),
  )
  pasting.value = false
  if (ok) pasteOpen.value = false
}

// ---------------------------------------------------------------- 详情抽屉（不常用字段）
const drawer = ref(false)
const DETAIL_FIELDS = [
  'name', 'category', 'origin', 'required_date', 'basis', 'changed_date', 'change_ref', 'acceptance',
  'acceptance_ref', 'variation_ref', 'due_date', 'in_client_mdl', 'mdl_remarks', 'content', 'description', 'purpose',
  'keywords',
] as const
type DetailForm = Pick<TreeDocumentType, (typeof DETAIL_FIELDS)[number]>
const detail = reactive({} as DetailForm)
const detailDoc = ref<TreeDocumentType | null>(null)

function openDetail(doc: TreeDocumentType): void {
  detailDoc.value = doc
  Object.assign(detail, Object.fromEntries(DETAIL_FIELDS.map((f) => [f, f === 'keywords' ? [...(doc.keywords ?? [])] : doc[f]])))
  drawer.value = true
}

async function saveDetail(): Promise<void> {
  const doc = detailDoc.value
  if (!doc) return
  const changes: Record<string, unknown> = {}
  for (const f of DETAIL_FIELDS) {
    const raw = detail[f]
    const value = typeof raw === 'string' ? raw.trim() || null : raw
    if (Array.isArray(value) ? JSON.stringify(value) !== JSON.stringify(doc[f]) : value !== doc[f]) changes[f] = value
  }
  if (!Object.keys(changes).length) {
    drawer.value = false
    return
  }
  const ok = await act(
    () => structureApi.updateDocumentType(projectId.value, doc.id, changes as DocumentTypeFields),
    t('common.saved'),
  )
  if (ok) drawer.value = false
}

// ---------------------------------------------------------------- 批量：模块、对应设备、停用
const selection = ref<TreeDocumentType[]>([])

const modulesDialog = reactive({ open: false, names: [] as string[], mode: 'add' as 'add' | 'remove' | 'replace' })

async function applyBulkModules(): Promise<void> {
  const ids = selection.value.map((d) => d.id)
  const ok = await act(
    () => structureApi.bulkModules(projectId.value, ids, modulesDialog.names, modulesDialog.mode),
    t('common.saved'),
  )
  if (ok) modulesDialog.open = false
}

async function bulkDeactivate(): Promise<void> {
  await act(async () => {
    for (const d of selection.value) await structureApi.setDocumentTypeActive(projectId.value, d.id, false)
  }, t('common.saved'))
}

// 对应设备：全部 / 按设备种类 / 按模块 / 逐台；替换 / 加上 / 去掉
const equipDialog = reactive({
  open: false,
  docs: [] as TreeDocumentType[],
  by: 'all' as 'all' | 'types' | 'modules' | 'items',
  mode: 'replace' as 'add' | 'remove' | 'replace',
  typeIds: [] as number[],
  moduleIds: [] as number[],
  equipmentIds: [] as number[],
})
const equipment = ref<Equipment[]>([])
const equipmentTypes = ref<EquipmentType[]>([])

async function openEquipment(targets: TreeDocumentType[]): Promise<void> {
  try {
    ;[equipment.value, equipmentTypes.value] = await Promise.all([
      equipmentApi.list(projectId.value),
      equipmentApi.types(projectId.value),
    ])
  } catch (err) {
    showError(err)
    return
  }
  Object.assign(equipDialog, {
    open: true,
    docs: targets,
    by: 'items',
    mode: 'replace',
    typeIds: [],
    moduleIds: [],
    equipmentIds: targets.length === 1 ? targets[0].equipment.map((e) => e.equipment_id) : [],
  })
}

function equipmentLabel(e: { tag: string | null; name?: string; equipment_name?: string }): string {
  const name = e.name ?? e.equipment_name ?? ''
  return e.tag ? `${e.tag}  ${name}` : name
}

async function applyEquipment(): Promise<void> {
  const selectionBody =
    equipDialog.by === 'all'
      ? { all_equipment: true }
      : equipDialog.by === 'types'
        ? { equipment_type_ids: equipDialog.typeIds }
        : equipDialog.by === 'modules'
          ? { module_ids: equipDialog.moduleIds }
          : { equipment_ids: equipDialog.equipmentIds }
  const ids = equipDialog.docs.map((d) => d.id)
  const ok = await act(
    () => structureApi.bulkEquipment(projectId.value, ids, selectionBody, equipDialog.mode),
    t('common.saved'),
  )
  if (ok) equipDialog.open = false
}

// ---------------------------------------------------------------- 模块管理
const moduleMgr = reactive({ open: false, newName: '' })

async function addModule(): Promise<void> {
  const name = moduleMgr.newName.trim()
  if (!name) return
  moduleMgr.newName = '' // 先清空，可以接着输入下一个；失败时放回去
  if (!(await act(() => structureApi.createModule(projectId.value, name)))) moduleMgr.newName = name
}

async function renameModule(id: number, name: string): Promise<void> {
  if (name.trim()) await act(() => structureApi.updateModule(projectId.value, id, name.trim()))
}

async function toggleModule(m: Module): Promise<void> {
  await act(() => structureApi.setModuleActive(projectId.value, m.id, !m.active))
}

const { safeDelete } = useSafeDelete()
async function removeModule(m: Module): Promise<void> {
  await safeDelete({
    name: m.name,
    check: () => structureApi.moduleUsage(projectId.value, m.id),
    remove: () => structureApi.deleteModule(projectId.value, m.id),
    deactivate: m.active ? () => structureApi.setModuleActive(projectId.value, m.id, false) : undefined,
    done: load,
  })
}

async function moveModule(index: number, delta: number): Promise<void> {
  const ids = tree.value.modules.map((m) => m.id)
  const [id] = ids.splice(index, 1)
  ids.splice(index + delta, 0, id)
  await act(() => structureApi.reorder(projectId.value, 'modules', ids))
}

// ---------------------------------------------------------------- 复制结构、标准模板
const copyDialog = reactive({ open: false, source: null as number | null })
const copySources = computed(() => projects.projects.filter((p) => p.id !== projectId.value))

async function copyStructure(): Promise<void> {
  if (!copyDialog.source) return
  const ok = await act(
    () => structureApi.copyFrom(projectId.value, copyDialog.source!),
    t('structure.copied'),
  )
  if (ok) copyDialog.open = false
}

const importDialog = reactive({ open: false, preview: null as StructureImportPreview | null, busy: false })
const fileInput = ref<HTMLInputElement>()

async function onTemplateFile(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  importDialog.busy = true
  try {
    importDialog.preview = await structureApi.importPreview(projectId.value, file)
    importDialog.open = true
  } catch (err) {
    showError(err)
  } finally {
    importDialog.busy = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

async function confirmTemplate(): Promise<void> {
  if (!importDialog.preview) return
  const ok = await act(
    () => structureApi.importConfirm(projectId.value, importDialog.preview!.token),
    t('import.done'),
  )
  if (ok) importDialog.open = false
}

function downloadTemplate(): void {
  download('/api/structure-template', { lang: t('lang.code') }).catch(showError)
}

watch(showInactive, load)
watch(projectId, load)
onMounted(load)
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />

    <div class="toolbar">
      <h2>{{ t('menu.structure') }}</h2>
      <el-checkbox v-model="showInactive">{{ t('structure.showInactive') }}</el-checkbox>
      <div class="spacer" />
      <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
      <el-button v-if="!readonly" @click="moduleMgr.open = true">{{ t('structure.manageModules') }}</el-button>
      <el-button v-if="!readonly && isEmpty" @click="copyDialog.open = true">{{ t('structure.copyFrom') }}</el-button>
      <el-button :icon="Download" @click="downloadTemplate">{{ t('structure.downloadTemplate') }}</el-button>
      <el-button v-if="!readonly" :icon="Upload" :loading="importDialog.busy" @click="fileInput?.click()">
        {{ t('structure.importTemplate') }}
      </el-button>
      <input ref="fileInput" type="file" accept=".xlsx" hidden @change="onTemplateFile" />
    </div>

    <div v-loading="loading" class="body">
      <!-- 里程碑 -->
      <el-card shadow="never" class="milestones" body-class="ms-body">
        <template #header>
          <div class="card-head">
            <span>{{ t('structure.milestones') }}</span>
            <el-button v-if="!readonly" size="small" type="primary" :icon="Plus" @click="openMilestone()">
              {{ t('common.create') }}
            </el-button>
          </div>
        </template>
        <el-empty v-if="isEmpty" :description="t('structure.noMilestones')" :image-size="60" />
        <div
          v-for="(m, i) in tree.milestones"
          :key="m.id"
          class="ms"
          :class="{ active: m.id === selectedId, inactive: !m.active }"
          data-test="milestone"
          @click="selectedId = m.id"
        >
          <div class="ms-title">
            <b>{{ m.code }}</b> {{ m.name }}
            <el-tag v-if="!m.active" size="small" type="info">{{ t('common.inactive') }}</el-tag>
          </div>
          <div class="ms-sub">
            {{ m.due_date ? t('structure.dueOn', { date: m.due_date }) : t('structure.noDue') }} ·
            {{ t('structure.docCount', { n: m.document_types.length }) }}
          </div>
          <div v-if="!readonly && m.id === selectedId" class="ms-actions" @click.stop>
            <el-button link :icon="Edit" @click="openMilestone(m)" />
            <el-button link :icon="ArrowUp" :disabled="i === 0" @click="moveMilestone(i, -1)" />
            <el-button link :icon="ArrowDown" :disabled="i === tree.milestones.length - 1" @click="moveMilestone(i, 1)" />
            <el-button link @click="toggleMilestone(m)">{{ m.active ? t('common.deactivate') : t('common.activate') }}</el-button>
            <el-button link type="danger" :icon="Delete" @click="deleteMilestone(m)" />
          </div>
        </div>
      </el-card>

      <!-- 文件种类 -->
      <el-card shadow="never" class="docs">
        <template #header>
          <div class="card-head">
            <span v-if="milestone">
              <b>{{ milestone.code }}</b> {{ milestone.name }}
              <span class="muted">{{ milestone.due_date ? t('structure.dueOn', { date: milestone.due_date }) : '' }}</span>
            </span>
            <div class="spacer" />
            <template v-if="!readonly && milestone">
              <template v-if="selection.length">
                <span class="muted">{{ t('structure.selected', { n: selection.length }) }}</span>
                <el-button size="small" @click="modulesDialog.open = true">{{ t('structure.setModules') }}</el-button>
                <el-button size="small" @click="openEquipment(selection)">{{ t('structure.setEquipment') }}</el-button>
                <el-button size="small" @click="bulkDeactivate">{{ t('common.deactivate') }}</el-button>
              </template>
              <el-button size="small" @click="pasteOpen = true">{{ t('structure.pasteRows') }}</el-button>
            </template>
          </div>
        </template>

        <el-empty v-if="!milestone" :description="t('structure.pickMilestone')" />
        <template v-else>
          <el-table
            :data="docs"
            row-key="id"
            size="small"
            :row-class-name="({ row }) => (row.active ? '' : 'inactive-row')"
            data-test="doc-table"
            @selection-change="(rows: unknown[]) => (selection = rows as TreeDocumentType[])"
          >
            <el-table-column v-if="!readonly" type="selection" width="36" />
            <el-table-column prop="submittal_no" :label="t('structure.col.no')" width="64" />
            <el-table-column :label="t('structure.col.name')" min-width="220">
              <template #default="{ row }">
                <!-- 文字框要双向绑定，否则打字时会被弹回原值；改完（回车或离开）才保存 -->
                <el-input
                  v-model="row.name"
                  :disabled="readonly"
                  size="small"
                  @change="(v: string) => (v.trim() ? updateDoc(row as TreeDocumentType, { name: v.trim() }) : load())"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.category')" width="130">
              <template #default="{ row }">
                <el-input
                  v-model="row.category"
                  :disabled="readonly"
                  size="small"
                  @change="(v: string) => updateDoc(row as TreeDocumentType, { category: v.trim() || null })"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.origin')" width="120">
              <template #default="{ row }">
                <el-select
                  :model-value="row.origin"
                  :disabled="readonly"
                  size="small"
                  @change="(v: Origin) => updateDoc(row as TreeDocumentType, { origin: v })"
                >
                  <el-option v-for="o in ORIGINS" :key="o" :value="o" :label="t(`origin.${o}`)" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.perEquipment')" width="80" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.per_equipment"
                  :disabled="readonly"
                  size="small"
                  @change="(v: string | number | boolean) => updateDoc(row as TreeDocumentType, { per_equipment: Boolean(v) })"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.modules')" min-width="190">
              <template #default="{ row }">
                <el-select
                  :model-value="row.partitions.filter((p: { active: boolean }) => p.active).map((p: { module_name: string }) => p.module_name)"
                  :disabled="readonly"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  collapse-tags
                  collapse-tags-tooltip
                  size="small"
                  :placeholder="t('structure.wholeType')"
                  @change="(v: string[]) => updateModules(row as TreeDocumentType, v)"
                >
                  <el-option v-for="name in moduleNames" :key="name" :value="name" :label="name" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.equipment')" width="110" align="center">
              <template #default="{ row }">
                <el-button
                  v-if="row.per_equipment"
                  link
                  type="primary"
                  :disabled="readonly"
                  @click="openEquipment([row as TreeDocumentType])"
                >
                  {{ t('structure.equipmentCount', { n: row.equipment.filter((e: { active: boolean }) => e.active).length }) }}
                </el-button>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.due')" width="140">
              <template #default="{ row }">
                <el-date-picker
                  :model-value="row.due_date"
                  :disabled="readonly"
                  type="date"
                  value-format="YYYY-MM-DD"
                  size="small"
                  :placeholder="t('structure.inherit')"
                  style="width: 125px"
                  @change="(v: string | null) => updateDoc(row as TreeDocumentType, { due_date: v })"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('structure.col.inMdl')" width="80" align="center">
              <template #default="{ row }">
                <el-switch
                  :model-value="row.in_client_mdl"
                  :disabled="readonly || row.origin === 'internal'"
                  size="small"
                  @change="(v: string | number | boolean) => updateDoc(row as TreeDocumentType, { in_client_mdl: Boolean(v) })"
                />
              </template>
            </el-table-column>
            <el-table-column :label="t('common.actions')" width="190" fixed="right">
              <template #default="{ row, $index }">
                <el-button link type="primary" @click="openDetail(row as TreeDocumentType)">{{ t('structure.details') }}</el-button>
                <template v-if="!readonly">
                  <el-button link :icon="ArrowUp" :disabled="$index === 0" @click="moveDoc($index, -1)" />
                  <el-button link :icon="ArrowDown" :disabled="$index === docs.length - 1" @click="moveDoc($index, 1)" />
                  <el-dropdown trigger="click">
                    <el-button link>{{ t('common.more') }}</el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item @click="insertAt = $index + 1">{{ t('structure.insertAbove') }}</el-dropdown-item>
                        <el-dropdown-item @click="toggleDoc(row as TreeDocumentType)">
                          {{ row.active ? t('common.deactivate') : t('common.activate') }}
                        </el-dropdown-item>
                        <el-dropdown-item @click="deleteDoc(row as TreeDocumentType)">{{ t('common.delete') }}</el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                </template>
              </template>
            </el-table-column>
          </el-table>

          <div v-if="!readonly" class="add-row">
            <el-tag v-if="insertAt" closable @close="insertAt = null">
              {{ t('structure.insertingAt', { no: docs[insertAt - 1]?.submittal_no }) }}
            </el-tag>
            <el-input
              v-model="newName"
              :placeholder="t('structure.newDocPlaceholder')"
              data-test="new-doc"
              @keyup.enter="addDoc"
            />
            <el-button type="primary" :icon="Plus" :disabled="!newName.trim()" @click="addDoc">{{ t('common.add') }}</el-button>
          </div>
        </template>
      </el-card>
    </div>

    <!-- 里程碑对话框 -->
    <el-dialog v-model="msDialog" :title="msForm.id ? t('structure.editMilestone') : t('structure.newMilestone')" width="520px">
      <el-form label-width="90px" @submit.prevent="saveMilestone">
        <el-form-item :label="t('structure.msName')"><el-input v-model="msForm.name" data-test="ms-name" /></el-form-item>
        <el-form-item :label="t('structure.col.due')">
          <el-date-picker v-model="msForm.due_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('structure.goal')"><el-input v-model="msForm.goal" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('structure.description')"><el-input v-model="msForm.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('structure.remarks')"><el-input v-model="msForm.remarks" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="msDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveMilestone">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 粘贴多行 -->
    <PasteDialog
      v-model="pasteOpen"
      :title="t('structure.pasteTitle', { code: milestone?.code ?? '' })"
      :columns="PASTE_COLUMNS"
      :hint="t('structure.pasteHint')"
      :loading="pasting"
      @confirm="pasteDocs"
    />

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawer" :title="detailDoc ? `${detailDoc.submittal_no}  ${detailDoc.name}` : ''" size="520px" data-test="doc-detail">
      <el-form v-if="detailDoc" label-width="110px" :disabled="readonly">
        <el-form-item :label="t('structure.col.name')"><el-input v-model="detail.name" /></el-form-item>
        <el-form-item :label="t('structure.col.category')"><el-input v-model="detail.category" /></el-form-item>
        <el-form-item :label="t('structure.keywords')">
          <el-select
            v-model="detail.keywords"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            :placeholder="t('structure.keywordsHint')"
            data-test="doc-keywords"
          />
        </el-form-item>
        <el-divider content-position="left">{{ t('structure.requirement') }}</el-divider>
        <el-form-item :label="t('structure.col.origin')">
          <el-select v-model="detail.origin">
            <el-option v-for="o in ORIGINS" :key="o" :value="o" :label="t(`origin.${o}`)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('structure.requiredDate')">
          <el-date-picker v-model="detail.required_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('structure.col.basis')">
          <el-input v-model="detail.basis" :placeholder="t(`structure.basisHint.${detail.origin}`)" />
        </el-form-item>
        <template v-if="detail.origin === 'client'">
          <el-form-item :label="t('structure.acceptance')">
            <el-select v-model="detail.acceptance">
              <el-option v-for="a in ACCEPTANCES" :key="a" :value="a" :label="t(`acceptance.${a}`)" />
            </el-select>
          </el-form-item>
          <el-form-item :label="t('structure.acceptanceRef')">
            <el-input v-model="detail.acceptance_ref" :placeholder="t('structure.acceptanceRefHint')" />
          </el-form-item>
          <el-form-item :label="t('structure.variationRef')"><el-input v-model="detail.variation_ref" /></el-form-item>
        </template>
        <el-form-item :label="t('structure.changedDate')">
          <el-date-picker v-model="detail.changed_date" type="date" value-format="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item :label="t('structure.changeRef')"><el-input v-model="detail.change_ref" /></el-form-item>
        <el-divider content-position="left">{{ t('structure.mdlAndDue') }}</el-divider>
        <el-form-item :label="t('structure.col.due')">
          <el-date-picker v-model="detail.due_date" type="date" value-format="YYYY-MM-DD" :placeholder="t('structure.inherit')" />
        </el-form-item>
        <el-form-item :label="t('structure.col.inMdl')">
          <el-switch v-model="detail.in_client_mdl" :disabled="detail.origin === 'internal'" />
        </el-form-item>
        <el-form-item :label="t('structure.mdlRemarks')"><el-input v-model="detail.mdl_remarks" type="textarea" :rows="2" /></el-form-item>
        <el-divider content-position="left">{{ t('structure.contentSection') }}</el-divider>
        <el-form-item :label="t('structure.content')"><el-input v-model="detail.content" type="textarea" :rows="3" /></el-form-item>
        <el-form-item :label="t('structure.description')"><el-input v-model="detail.description" type="textarea" :rows="2" /></el-form-item>
        <el-form-item :label="t('structure.purpose')"><el-input v-model="detail.purpose" type="textarea" :rows="2" /></el-form-item>
      </el-form>
      <template v-if="!readonly" #footer>
        <el-button @click="drawer = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveDetail">{{ t('common.save') }}</el-button>
      </template>
    </el-drawer>

    <!-- 批量设模块 -->
    <el-dialog v-model="modulesDialog.open" :title="t('structure.setModules')" width="480px">
      <el-radio-group v-model="modulesDialog.mode" class="mb">
        <el-radio value="add">{{ t('structure.modeAdd') }}</el-radio>
        <el-radio value="remove">{{ t('structure.modeRemove') }}</el-radio>
        <el-radio value="replace">{{ t('structure.modeReplace') }}</el-radio>
      </el-radio-group>
      <el-select v-model="modulesDialog.names" multiple filterable allow-create default-first-option style="width: 100%">
        <el-option v-for="name in moduleNames" :key="name" :value="name" :label="name" />
      </el-select>
      <template #footer>
        <el-button @click="modulesDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="applyBulkModules">{{ t('common.ok') }}</el-button>
      </template>
    </el-dialog>

    <!-- 对应设备 -->
    <el-dialog
      v-model="equipDialog.open"
      :title="t('structure.equipmentFor', { names: equipDialog.docs.map((d) => d.submittal_no).join(', ') })"
      width="640px"
    >
      <el-form label-width="90px">
        <el-form-item :label="t('structure.pickBy')">
          <el-radio-group v-model="equipDialog.by" data-test="equip-by">
            <el-radio value="all">{{ t('structure.byAll') }}</el-radio>
            <el-radio value="types">{{ t('structure.byTypes') }}</el-radio>
            <el-radio value="modules">{{ t('structure.byModules') }}</el-radio>
            <el-radio value="items">{{ t('structure.byItems') }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="equipDialog.by === 'types'" :label="t('structure.byTypes')">
          <el-select v-model="equipDialog.typeIds" multiple style="width: 100%">
            <el-option v-for="ty in equipmentTypes" :key="ty.id" :value="ty.id" :label="ty.name" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="equipDialog.by === 'modules'" :label="t('structure.byModules')">
          <el-select v-model="equipDialog.moduleIds" multiple style="width: 100%">
            <el-option v-for="m in tree.modules.filter((x) => x.active)" :key="m.id" :value="m.id" :label="m.name" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="equipDialog.by === 'items'" :label="t('structure.byItems')">
          <el-select v-model="equipDialog.equipmentIds" multiple filterable collapse-tags collapse-tags-tooltip :max-collapse-tags="8" style="width: 100%">
            <el-option v-for="e in equipment" :key="e.id" :value="e.id" :label="equipmentLabel(e)" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('structure.mode')">
          <el-radio-group v-model="equipDialog.mode">
            <el-radio value="replace">{{ t('structure.modeReplace') }}</el-radio>
            <el-radio value="add">{{ t('structure.modeAdd') }}</el-radio>
            <el-radio value="remove">{{ t('structure.modeRemove') }}</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <p class="muted">{{ t('structure.equipmentNote') }}</p>
      <template #footer>
        <el-button @click="equipDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" data-test="equip-ok" @click="applyEquipment">{{ t('common.ok') }}</el-button>
      </template>
    </el-dialog>

    <!-- 模块管理 -->
    <el-dialog v-model="moduleMgr.open" :title="t('structure.manageModules')" width="520px">
      <div v-for="(m, i) in tree.modules" :key="m.id" class="module-row">
        <el-input v-model="m.name" size="small" :disabled="!m.active" @change="(v: string) => renameModule(m.id, v)" />
        <el-button link :icon="ArrowUp" :disabled="i === 0" @click="moveModule(i, -1)" />
        <el-button link :icon="ArrowDown" :disabled="i === tree.modules.length - 1" @click="moveModule(i, 1)" />
        <el-button link @click="toggleModule(m)">
          {{ m.active ? t('common.deactivate') : t('common.activate') }}
        </el-button>
        <el-button link type="danger" data-test="module-delete" @click="removeModule(m)">{{ t('common.delete') }}</el-button>
      </div>
      <div class="module-row">
        <el-input v-model="moduleMgr.newName" size="small" :placeholder="t('structure.newModule')" @keyup.enter="addModule" />
        <el-button size="small" type="primary" :icon="Plus" @click="addModule">{{ t('common.add') }}</el-button>
      </div>
      <p class="muted">{{ t('structure.modulesNote') }}</p>
    </el-dialog>

    <!-- 复制结构 -->
    <el-dialog v-model="copyDialog.open" :title="t('structure.copyFrom')" width="480px">
      <p class="muted">{{ t('structure.copyNote') }}</p>
      <el-select v-model="copyDialog.source" filterable style="width: 100%">
        <el-option v-for="p in copySources" :key="p.id" :value="p.id" :label="`${p.code}  ${p.name}`" />
      </el-select>
      <template #footer>
        <el-button @click="copyDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!copyDialog.source" @click="copyStructure">{{ t('common.ok') }}</el-button>
      </template>
    </el-dialog>

    <!-- 标准模板导入预览 -->
    <el-dialog v-model="importDialog.open" :title="t('structure.importTemplate')" width="620px">
      <template v-if="importDialog.preview">
        <el-descriptions :column="2" border size="small">
          <el-descriptions-item v-for="(n, k) in importDialog.preview.summary" :key="k" :label="t(`import.summary.${k}`)">
            {{ n }}
          </el-descriptions-item>
        </el-descriptions>
        <IssueList :errors="importDialog.preview.errors" :warnings="importDialog.preview.warnings" />
      </template>
      <template #footer>
        <el-button @click="importDialog.open = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" :disabled="!importDialog.preview?.ok" @click="confirmTemplate">
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
.toolbar,
.card-head {
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
.milestones {
  width: 280px;
  flex-shrink: 0;
}
.docs {
  flex: 1;
  min-width: 0;
}
:deep(.ms-body) {
  padding: 6px;
}
.ms {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.ms:hover {
  background: var(--el-fill-color-light);
}
.ms.active {
  background: var(--el-color-primary-light-9);
}
.ms.inactive {
  opacity: 0.6;
}
.ms-sub,
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ms-actions {
  margin-top: 4px;
}
.add-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  align-items: center;
}
.module-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.mb {
  margin-bottom: 10px;
}
:deep(.inactive-row) {
  opacity: 0.55;
}
</style>
