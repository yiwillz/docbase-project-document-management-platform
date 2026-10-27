<script setup lang="ts">
// 设备页：设备条目（一条 + 台数，编号选填）、出厂编号、历史与恢复、导入（Excel / 粘贴）、设备种类与模块对应；
// 删除：没用过的真删除（编号释放），用过的只能停用（可多选批量）
import { Download, Plus, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import {
  type Equipment,
  type EquipmentType,
  type HistoryEntry,
  type ImportPreview,
  type PastedEquipmentRow,
  equipmentApi,
} from '@/api/equipment'
import { showError } from '@/api/errors'
import { download } from '@/api/http'
import { type Module, structureApi } from '@/api/structure'
import IssueList from '@/components/IssueList.vue'
import PasteDialog from '@/components/PasteDialog.vue'
import { EQUIPMENT_HEADERS } from '@/i18n/recognition'
import { useProjectContext } from '@/composables/useProjectContext'
import { useSafeDelete } from '@/composables/useSafeDelete'

const { t, d } = useI18n()
const { projectId, readonly, readonlyNotice } = useProjectContext('project.config')

const tab = ref<'items' | 'types'>('items')
const items = ref<Equipment[]>([])
const types = ref<EquipmentType[]>([])
const modules = ref<Module[]>([])
const moduleTypes = ref<Record<number, number[]>>({})
const loading = ref(false)
const keyword = ref('')
const typeFilter = ref<number | null>(null)
const showInactive = ref(false)

const typeName = computed(() => Object.fromEntries(types.value.map((ty) => [ty.id, ty.name])))
const rows = computed(() => {
  const k = keyword.value.trim().toLowerCase()
  return items.value
    .filter((e) => typeFilter.value === null || e.equipment_type_id === typeFilter.value)
    .filter((e) => !k || [e.tag ?? '', e.name, e.vendor ?? '', ...e.aliases].some((v) => v.toLowerCase().includes(k)))
})
const totalUnits = computed(() => rows.value.reduce((sum, e) => sum + (e.quantity ?? 1), 0))

async function load(): Promise<void> {
  loading.value = true
  try {
    const [list, typeList, tree] = await Promise.all([
      equipmentApi.list(projectId.value, showInactive.value),
      equipmentApi.types(projectId.value, true),
      structureApi.tree(projectId.value),
    ])
    items.value = list
    types.value = typeList
    modules.value = tree.modules
    const mapped = await Promise.all(tree.modules.map((m) => equipmentApi.moduleTypes(projectId.value, m.id)))
    moduleTypes.value = Object.fromEntries(tree.modules.map((m, i) => [m.id, mapped[i].map((ty) => ty.id)]))
  } catch (err) {
    showError(err)
  } finally {
    loading.value = false
  }
}

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

// ---------------------------------------------------------------- 新建 / 编辑
const editDialog = ref(false)
const form = reactive({
  id: 0,
  tag: '',
  name: '',
  equipment_type_id: null as number | null,
  vendor: '',
  quantity: 1,
  aliases: [] as string[],
  reason: '',
})

function openEdit(e?: Equipment): void {
  Object.assign(form, {
    id: e?.id ?? 0,
    tag: e?.tag ?? '',
    name: e?.name ?? '',
    equipment_type_id: e?.equipment_type_id ?? null,
    vendor: e?.vendor ?? '',
    quantity: e?.quantity ?? 1,
    aliases: [...(e?.aliases ?? [])],
    reason: '',
  })
  editDialog.value = true
}

async function saveEdit(): Promise<void> {
  if (!form.name.trim()) {
    ElMessage.error(t('equipment.nameRequired'))
    return
  }
  const data = {
    tag: form.tag.trim() || null,
    name: form.name.trim(),
    equipment_type_id: form.equipment_type_id,
    vendor: form.vendor.trim() || null,
    quantity: form.quantity,
    aliases: form.aliases,
  }
  const ok = await act(
    () =>
      form.id
        ? equipmentApi.update(projectId.value, form.id, { ...data, reason: form.reason.trim() || undefined })
        : equipmentApi.create(projectId.value, data),
    t('common.saved'),
  )
  if (ok) editDialog.value = false
}

async function toggleActive(e: Equipment): Promise<void> {
  await act(() => equipmentApi.setActive(projectId.value, e.id, !e.active))
}

// ---------------------------------------------------------------- 删除（单个 / 批量）
const { safeDelete, reasons } = useSafeDelete()
const selected = ref<Equipment[]>([])

function eqName(e: { tag: string | null; name: string }): string {
  return e.tag ? `${e.tag} ${e.name}` : e.name
}

async function removeEquipment(e: Equipment): Promise<void> {
  await safeDelete({
    name: eqName(e),
    check: () => equipmentApi.usage(projectId.value, e.id),
    remove: () => equipmentApi.remove(projectId.value, e.id),
    deactivate: e.active ? () => equipmentApi.setActive(projectId.value, e.id, false) : undefined,
    done: load,
  })
}

async function bulkDelete(): Promise<void> {
  const ids = selected.value.map((e) => e.id)
  try {
    await ElMessageBox.confirm(t('usage.bulkConfirm', { n: ids.length }), t('common.delete'), {
      confirmButtonText: t('common.delete'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    const r = await equipmentApi.bulkDelete(projectId.value, ids)
    if (r.deleted.length) ElMessage.success(t('usage.bulkDeleted', { n: r.deleted.length }))
    if (r.skipped.length) {
      const lines = r.skipped.map((s) => `${eqName(s)}: ${reasons(s.usage)}`).join('\n')
      try {
        await ElMessageBox.confirm(t('usage.bulkSkipped', { n: r.skipped.length, lines }), t('usage.cannotDelete'), {
          confirmButtonText: t('usage.deactivateInstead'),
          cancelButtonText: t('common.cancel'),
          type: 'info',
          customStyle: { whiteSpace: 'pre-line' },
        })
        await equipmentApi.bulkDeactivate(projectId.value, r.skipped.map((s) => s.id))
        ElMessage.success(t('usage.deactivated'))
      } catch (err) {
        if (err !== 'cancel' && err !== 'close') showError(err)
      }
    }
  } catch (err) {
    showError(err)
  } finally {
    await load()
  }
}

// ---------------------------------------------------------------- 出厂编号（展开行）
const serialText = reactive<Record<number, string>>({})

async function loadSerials(e: Equipment): Promise<void> {
  try {
    const units = await equipmentApi.units(projectId.value, e.id)
    const lines = Array.from({ length: Math.max(0, ...units.map((u) => u.seq)) }, () => '')
    for (const u of units) lines[u.seq - 1] = u.serial_no ?? ''
    serialText[e.id] = lines.join('\n')
  } catch (err) {
    showError(err)
  }
}

async function saveSerials(e: Equipment): Promise<void> {
  const lines = (serialText[e.id] ?? '').split(/\r?\n/)
  const max = e.quantity ?? 1
  if (lines.filter((l) => l.trim()).length && lines.length > max) {
    ElMessage.error(t('equipment.tooManySerials', { n: max }))
    return
  }
  const units = await equipmentApi.units(projectId.value, e.id)
  const serials = Array.from({ length: Math.max(lines.length, ...units.map((u) => u.seq), 0) }, (_, i) => ({
    seq: i + 1,
    serial_no: lines[i]?.trim() || null,
  })).filter((s) => s.seq <= max)
  await act(() => equipmentApi.setSerials(projectId.value, e.id, serials), t('common.saved'))
}

function onExpand(row: unknown, expanded: unknown): void {
  const item = row as Equipment
  if ((expanded as Equipment[]).some((e) => e.id === item.id)) loadSerials(item)
}

// ---------------------------------------------------------------- 历史
const historyDrawer = reactive({ open: false, item: null as Equipment | null, entries: [] as HistoryEntry[] })

async function openHistory(e: Equipment): Promise<void> {
  try {
    historyDrawer.entries = await equipmentApi.history(projectId.value, e.id)
    historyDrawer.item = e
    historyDrawer.open = true
  } catch (err) {
    showError(err)
  }
}

async function restore(entry: HistoryEntry): Promise<void> {
  const item = historyDrawer.item
  if (!item) return
  const ok = await act(() => equipmentApi.restore(projectId.value, item.id, entry.id), t('equipment.restored'))
  if (ok) await openHistory(item)
}

function fmt(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (Array.isArray(value)) return value.join(', ') || '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

// ---------------------------------------------------------------- 导入（Excel / 粘贴），先预览再确认
const importState = reactive({
  open: false,
  busy: false,
  file: null as File | null,
  preview: null as ImportPreview | null,
  mapping: {} as Record<string, string>,
  headerRow: null as number | null,
  deactivateMissing: false,
})
const fileInput = ref<HTMLInputElement>()
const FIELD_CHOICES = ['tag', 'name', 'equipment_type', 'vendor', 'quantity', 'aliases', 'attr', 'ignore']

async function runPreview(): Promise<void> {
  if (!importState.file) return
  importState.busy = true
  try {
    importState.preview = await equipmentApi.importPreview(projectId.value, importState.file, {
      headerRow: importState.headerRow,
      mapping: importState.mapping,
    })
    importState.headerRow = importState.preview.header_row
    importState.open = true
  } catch (err) {
    showError(err)
  } finally {
    importState.busy = false
  }
}

async function onFile(event: Event): Promise<void> {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (fileInput.value) fileInput.value.value = ''
  if (!file) return
  Object.assign(importState, { file, mapping: {}, headerRow: null, deactivateMissing: false })
  await runPreview()
}

function setMapping(column: string, field: string): void {
  importState.mapping = { ...importState.mapping, [column]: field }
}

async function confirmImport(): Promise<void> {
  const preview = importState.preview
  if (!preview) return
  importState.busy = true
  const ok = await act(
    () => equipmentApi.importConfirm(projectId.value, preview.token, importState.deactivateMissing),
    t('import.done'),
  )
  importState.busy = false
  if (ok) {
    importState.open = false
    pasteOpen.value = false
  }
}

// 粘贴多行：和 Excel 导入同样预览、确认
const pasteOpen = ref(false)
const PASTE_COLUMNS = computed(() =>
  (['tag', 'name', 'equipment_type', 'vendor', 'quantity'] as const).map((key) => ({
    key,
    label: t(`equipment.col.${key === 'equipment_type' ? 'type' : key}`),
    headers: EQUIPMENT_HEADERS[key],
  })),
)

async function previewPasted(records: Record<string, string | null>[]): Promise<void> {
  importState.busy = true
  try {
    importState.file = null
    importState.preview = await equipmentApi.previewRows(projectId.value, records as PastedEquipmentRow[])
    importState.deactivateMissing = false
    importState.open = true
  } catch (err) {
    showError(err)
  } finally {
    importState.busy = false
  }
}

function downloadTemplate(): void {
  download('/api/equipment-template', { lang: t('lang.code') }).catch(showError)
}

// ---------------------------------------------------------------- 设备种类、模块对应
const newType = ref('')

async function addType(): Promise<void> {
  const name = newType.value.trim()
  if (!name) return
  newType.value = '' // 先清空，可以接着输入下一个；失败时放回去
  if (!(await act(() => equipmentApi.createType(projectId.value, name)))) newType.value = name
}

async function renameType(id: number, name: string): Promise<void> {
  if (name.trim()) await act(() => equipmentApi.updateType(projectId.value, id, name.trim()))
}

async function toggleType(ty: EquipmentType): Promise<void> {
  await act(() => equipmentApi.setTypeActive(projectId.value, ty.id, !ty.active))
}

async function removeType(ty: EquipmentType): Promise<void> {
  await safeDelete({
    name: ty.name,
    check: () => equipmentApi.typeUsage(projectId.value, ty.id),
    remove: () => equipmentApi.deleteType(projectId.value, ty.id),
    deactivate: ty.active ? () => equipmentApi.setTypeActive(projectId.value, ty.id, false) : undefined,
    done: load,
  })
}

async function setModuleTypes(moduleId: number, typeIds: number[]): Promise<void> {
  await act(() => equipmentApi.setModuleTypes(projectId.value, moduleId, typeIds), t('common.saved'))
}

watch(showInactive, load)
watch(projectId, load)
onMounted(load)
</script>

<template>
  <div class="page">
    <el-alert v-if="readonlyNotice" type="info" :title="readonlyNotice" :closable="false" show-icon />
    <div class="toolbar">
      <h2>{{ t('menu.equipment') }}</h2>
      <el-radio-group v-model="tab" size="small">
        <el-radio-button value="items">{{ t('equipment.tabItems') }}</el-radio-button>
        <el-radio-button value="types">{{ t('equipment.tabTypes') }}</el-radio-button>
      </el-radio-group>
      <div class="spacer" />
      <el-button :icon="Refresh" @click="load">{{ t('common.refresh') }}</el-button>
    </div>

    <el-card v-show="tab === 'items'" shadow="never">
      <template #header>
        <div class="toolbar">
          <el-input v-model="keyword" :prefix-icon="Search" clearable :placeholder="t('equipment.search')" class="search" />
          <el-select v-model="typeFilter" clearable :placeholder="t('equipment.allTypes')" class="type-filter">
            <el-option v-for="ty in types" :key="ty.id" :value="ty.id" :label="ty.name" />
          </el-select>
          <el-checkbox v-model="showInactive">{{ t('equipment.showInactive') }}</el-checkbox>
          <span class="muted">{{ t('equipment.count', { n: rows.length, units: totalUnits }) }}</span>
          <div class="spacer" />
          <el-button :icon="Download" @click="downloadTemplate">{{ t('equipment.template') }}</el-button>
          <template v-if="!readonly">
            <el-button :icon="Upload" :loading="importState.busy" @click="fileInput?.click()">{{ t('equipment.importExcel') }}</el-button>
            <el-button @click="pasteOpen = true">{{ t('equipment.pasteRows') }}</el-button>
            <el-button v-if="selected.length" type="danger" plain data-test="bulk-delete" @click="bulkDelete">
              {{ t('usage.bulkDelete', { n: selected.length }) }}
            </el-button>
            <el-button type="primary" :icon="Plus" @click="openEdit()">{{ t('equipment.create') }}</el-button>
          </template>
          <input ref="fileInput" type="file" accept=".xlsx" hidden @change="onFile" />
        </div>
      </template>

      <el-table
        v-loading="loading"
        :data="rows"
        row-key="id"
        size="small"
        :row-class-name="({ row }) => (row.active ? '' : 'inactive-row')"
        data-test="equipment-table"
        @expand-change="onExpand"
        @selection-change="(v: Equipment[]) => (selected = v)"
      >
        <el-table-column v-if="!readonly" type="selection" width="36" />
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="serials">
              <p class="muted">{{ t('equipment.serialHint', { n: row.quantity ?? 1 }) }}</p>
              <el-input
                v-model="serialText[row.id]"
                type="textarea"
                :rows="4"
                :disabled="readonly"
                :placeholder="t('equipment.serialPlaceholder')"
              />
              <el-button v-if="!readonly" size="small" type="primary" class="mt" @click="saveSerials(row as Equipment)">
                {{ t('common.save') }}
              </el-button>
            </div>
          </template>
        </el-table-column>
        <el-table-column :label="t('equipment.col.tag')" width="120">
          <template #default="{ row }">
            <span v-if="row.tag">{{ row.tag }}</span>
            <el-tag v-else size="small" type="info">{{ t('equipment.noTag') }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="name" :label="t('equipment.col.name')" min-width="200" />
        <el-table-column :label="t('equipment.col.type')" width="140">
          <template #default="{ row }">{{ typeName[row.equipment_type_id] ?? '—' }}</template>
        </el-table-column>
        <el-table-column prop="vendor" :label="t('equipment.col.vendor')" width="150" />
        <el-table-column prop="quantity" :label="t('equipment.col.quantity')" width="90" align="right">
          <template #default="{ row }">{{ (row.quantity ?? 1).toLocaleString() }}</template>
        </el-table-column>
        <el-table-column :label="t('equipment.col.aliases')" min-width="140">
          <template #default="{ row }">{{ row.aliases.join(', ') }}</template>
        </el-table-column>
        <el-table-column :label="t('common.actions')" width="240" fixed="right">
          <template #default="{ row }">
            <el-button v-if="!readonly" link type="primary" @click="openEdit(row as Equipment)">{{ t('common.edit') }}</el-button>
            <el-button link @click="openHistory(row as Equipment)">{{ t('equipment.history') }}</el-button>
            <el-button v-if="!readonly" link @click="toggleActive(row as Equipment)">
              {{ row.active ? t('common.deactivate') : t('common.activate') }}
            </el-button>
            <el-button v-if="!readonly" link type="danger" data-test="equipment-delete" @click="removeEquipment(row as Equipment)">
              {{ t('common.delete') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 设备种类、模块对应 -->
    <div v-show="tab === 'types'" class="two-cols">
      <el-card shadow="never" class="types-card">
        <template #header>{{ t('equipment.types') }}</template>
        <div v-for="ty in types" :key="ty.id" class="type-row" :class="{ inactive: !ty.active }">
          <el-input
            v-model="ty.name"
            size="small"
            :disabled="readonly || !ty.active"
            @change="(v: string) => renameType(ty.id, v)"
          />
          <el-button v-if="!readonly" link @click="toggleType(ty)">
            {{ ty.active ? t('common.deactivate') : t('common.activate') }}
          </el-button>
          <el-button v-if="!readonly" link type="danger" data-test="type-delete" @click="removeType(ty)">{{ t('common.delete') }}</el-button>
        </div>
        <div v-if="!readonly" class="type-row">
          <el-input v-model="newType" size="small" :placeholder="t('equipment.newType')" @keyup.enter="addType" />
          <el-button size="small" type="primary" :icon="Plus" @click="addType">{{ t('common.add') }}</el-button>
        </div>
      </el-card>
      <el-card shadow="never" class="mapping-card">
        <template #header>{{ t('equipment.moduleMapping') }}</template>
        <p class="muted">{{ t('equipment.moduleMappingHint') }}</p>
        <el-empty v-if="!modules.length" :description="t('equipment.noModules')" :image-size="50" />
        <el-form v-else label-width="160px">
          <el-form-item v-for="m in modules" :key="m.id" :label="m.name">
            <el-select
              :model-value="moduleTypes[m.id] ?? []"
              :disabled="readonly"
              multiple
              style="width: 100%"
              :data-test="`module-types-${m.name}`"
              @change="(v: number[]) => setModuleTypes(m.id, v)"
            >
              <el-option v-for="ty in types.filter((x) => x.active)" :key="ty.id" :value="ty.id" :label="ty.name" />
            </el-select>
          </el-form-item>
        </el-form>
      </el-card>
    </div>

    <!-- 新建 / 编辑 -->
    <el-dialog v-model="editDialog" :title="form.id ? t('equipment.edit') : t('equipment.create')" width="520px">
      <el-form label-width="90px" @submit.prevent="saveEdit">
        <el-form-item :label="t('equipment.col.tag')">
          <el-input v-model="form.tag" maxlength="50" :placeholder="t('equipment.tagHint')" />
        </el-form-item>
        <el-form-item :label="t('equipment.col.name')" required>
          <el-input v-model="form.name" maxlength="500" />
        </el-form-item>
        <el-form-item :label="t('equipment.col.type')">
          <el-select v-model="form.equipment_type_id" clearable filterable style="width: 100%">
            <el-option v-for="ty in types.filter((x) => x.active)" :key="ty.id" :value="ty.id" :label="ty.name" />
          </el-select>
        </el-form-item>
        <el-form-item :label="t('equipment.col.vendor')"><el-input v-model="form.vendor" /></el-form-item>
        <el-form-item :label="t('equipment.col.quantity')">
          <el-input-number v-model="form.quantity" :min="1" :step="1" step-strictly />
        </el-form-item>
        <el-form-item :label="t('equipment.col.aliases')">
          <el-select v-model="form.aliases" multiple filterable allow-create default-first-option style="width: 100%" />
        </el-form-item>
        <el-form-item v-if="form.id" :label="t('common.reason')"><el-input v-model="form.reason" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">{{ t('common.cancel') }}</el-button>
        <el-button type="primary" @click="saveEdit">{{ t('common.save') }}</el-button>
      </template>
    </el-dialog>

    <!-- 历史 -->
    <el-drawer v-model="historyDrawer.open" :title="t('equipment.historyOf', { name: historyDrawer.item?.tag || historyDrawer.item?.name })" size="560px">
      <el-timeline>
        <el-timeline-item
          v-for="h in historyDrawer.entries"
          :key="h.id"
          :timestamp="`${d(new Date(h.created_at), 'long')}  ${h.actor_name ?? ''}`"
        >
          <div class="history-head">
            <el-tag size="small">{{ t(`history.action.${h.action}`) }}</el-tag>
            <span v-if="h.reason" class="muted">{{ h.reason }}</span>
            <el-button v-if="!readonly && h.action === 'update'" link type="primary" @click="restore(h)">
              {{ t('equipment.restoreBefore') }}
            </el-button>
          </div>
          <div v-for="(v, field) in h.changes" :key="field" class="change">
            {{ t('equipment.change', { field: t(`equipment.field.${field}`, String(field)), from: fmt(v[0]), to: fmt(v[1]) }) }}
          </div>
        </el-timeline-item>
      </el-timeline>
    </el-drawer>

    <!-- 粘贴多行 -->
    <PasteDialog
      v-model="pasteOpen"
      :title="t('equipment.pasteRows')"
      :columns="PASTE_COLUMNS"
      :hint="t('equipment.pasteHint')"
      :loading="importState.busy"
      @confirm="previewPasted"
    />

    <!-- 导入预览 -->
    <el-dialog v-model="importState.open" :title="t('equipment.importPreview')" width="860px" top="5vh">
      <template v-if="importState.preview">
        <template v-if="importState.file">
          <p class="muted">{{ t('equipment.mappingHint', { row: importState.preview.header_row }) }}</p>
          <el-table :data="importState.preview.columns" size="small" max-height="240" border>
            <el-table-column prop="column" :label="t('equipment.colLetter')" width="60" />
            <el-table-column prop="header" :label="t('equipment.colHeader')" min-width="140" />
            <el-table-column :label="t('equipment.colSamples')" min-width="180">
              <template #default="{ row }">{{ row.samples.filter(Boolean).join(' / ') }}</template>
            </el-table-column>
            <el-table-column :label="t('equipment.colField')" width="180">
              <template #default="{ row }">
                <el-select
                  :model-value="importState.mapping[row.column] ?? row.field"
                  size="small"
                  @change="(v: string) => setMapping(row.column, v)"
                >
                  <el-option v-for="f in FIELD_CHOICES" :key="f" :value="f" :label="t(`equipment.target.${f}`)" />
                </el-select>
              </template>
            </el-table-column>
          </el-table>
          <div class="toolbar mt">
            <span class="muted">{{ t('equipment.headerRow') }}</span>
            <el-input-number v-model="importState.headerRow" :min="1" size="small" />
            <el-button size="small" :loading="importState.busy" @click="runPreview">{{ t('equipment.repreview') }}</el-button>
          </div>
        </template>
        <IssueList :errors="importState.preview.errors" :warnings="importState.preview.warnings" />
        <template v-if="importState.preview.diff">
          <el-descriptions :column="5" border size="small" class="mt">
            <el-descriptions-item v-for="(n, k) in importState.preview.diff.summary" :key="k" :label="t(`equipment.diff.${k}`)">
              {{ n }}
            </el-descriptions-item>
          </el-descriptions>
          <p v-if="importState.preview.diff.new_types.length" class="muted">
            {{ t('equipment.newTypes', { names: importState.preview.diff.new_types.join(', ') }) }}
          </p>
          <el-collapse class="mt">
            <el-collapse-item v-if="importState.preview.diff.added.length" :title="t('equipment.diff.added') + ` (${importState.preview.diff.added.length})`">
              <div v-for="a in importState.preview.diff.added" :key="a.row" class="change">{{ a.tag ?? t('equipment.noTag') }} · {{ a.name }}</div>
            </el-collapse-item>
            <el-collapse-item v-if="importState.preview.diff.modified.length" :title="t('equipment.diff.modified') + ` (${importState.preview.diff.modified.length})`">
              <div v-for="m in importState.preview.diff.modified" :key="m.row" class="change">
                <b>{{ m.tag ?? m.name }}</b>
                <span v-for="(v, f) in m.changes" :key="f" class="ml">
                  {{ t('equipment.change', { field: t(`equipment.field.${f}`, String(f)), from: fmt(v[0]), to: fmt(v[1]) }) }}
                </span>
              </div>
            </el-collapse-item>
            <el-collapse-item v-if="importState.preview.diff.missing.length" :title="t('equipment.diff.missing') + ` (${importState.preview.diff.missing.length})`">
              <div v-for="m in importState.preview.diff.missing" :key="`${m.tag}-${m.name}`" class="change">{{ m.tag ?? t('equipment.noTag') }} · {{ m.name }}</div>
            </el-collapse-item>
          </el-collapse>
          <el-checkbox v-if="importState.preview.diff.missing.length" v-model="importState.deactivateMissing" class="mt">
            {{ t('equipment.deactivateMissing') }}
          </el-checkbox>
        </template>
      </template>
      <template #footer>
        <el-button @click="importState.open = false">{{ t('common.cancel') }}</el-button>
        <el-button
          type="primary"
          :disabled="!importState.preview?.ok"
          :loading="importState.busy"
          data-test="import-confirm"
          @click="confirmImport"
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
.search {
  width: 220px;
}
.type-filter {
  width: 160px;
}
.muted {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.serials {
  padding: 4px 48px;
  max-width: 520px;
}
.mt {
  margin-top: 8px;
}
.two-cols {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.types-card {
  width: 360px;
}
.mapping-card {
  flex: 1;
}
.type-row {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 6px;
}
.type-row.inactive {
  opacity: 0.6;
}
.history-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
}
.ml {
  margin-left: 8px;
}
.change {
  font-size: 13px;
  line-height: 1.6;
}
:deep(.inactive-row) {
  opacity: 0.55;
}
</style>
