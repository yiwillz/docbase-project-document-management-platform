// 设备接口：设备种类、设备、单台出厂编号、历史与恢复、模块-设备种类对应、导入（Excel / 粘贴）
import { api } from './http'
import type { Usage } from '@/composables/useSafeDelete'
import type { Issue } from './structure'

export interface EquipmentType {
  id: number
  name: string
  sort_order: number
  active: boolean
}

export interface Equipment {
  id: number
  tag: string | null
  name: string
  equipment_type_id: number | null
  vendor: string | null
  quantity: number | null
  aliases: string[]
  attrs: Record<string, unknown>
  sort_order: number
  active: boolean
}

export interface Unit {
  seq: number
  serial_no: string | null
  active: boolean
}

export interface HistoryEntry {
  id: number
  action: string
  changes: Record<string, [unknown, unknown]>
  actor_name: string | null
  reason: string | null
  created_at: string
}

export interface ImportColumn {
  column: string
  index: number
  header: string
  field: string
  samples: (string | null)[]
  detected: boolean
}

export interface ImportDiff {
  added: { tag: string | null; name: string; row: number; new_type: boolean }[]
  modified: { tag: string | null; name: string; row: number; changes: Record<string, [unknown, unknown]> }[]
  reactivated: { tag: string | null; name: string; row: number }[]
  missing: { tag: string | null; name: string }[]
  new_types: string[]
  summary: { added: number; modified: number; reactivated: number; missing: number; unchanged: number }
}

export interface ImportPreview {
  token: string
  ok: boolean
  sheet: string | null
  sheets: string[]
  header_row: number | null
  columns: ImportColumn[]
  rows: number
  errors: Issue[]
  warnings: Issue[]
  diff: ImportDiff | null
}

export interface PastedEquipmentRow {
  tag?: string | null
  name?: string | null
  equipment_type?: string | null
  vendor?: string | null
  quantity?: string | null
}

export type EquipmentFields = Partial<
  Pick<Equipment, 'tag' | 'name' | 'equipment_type_id' | 'vendor' | 'quantity' | 'aliases'>
>

const p = (id: number) => `/api/projects/${id}`

export const equipmentApi = {
  types: (pid: number, includeInactive = false) =>
    api.get<EquipmentType[]>(`${p(pid)}/equipment-types`, { include_inactive: includeInactive }),
  createType: (pid: number, name: string) => api.post<EquipmentType>(`${p(pid)}/equipment-types`, { name }),
  updateType: (pid: number, id: number, name: string) =>
    api.patch<EquipmentType>(`${p(pid)}/equipment-types/${id}`, { name }),
  setTypeActive: (pid: number, id: number, active: boolean) =>
    api.post<EquipmentType>(`${p(pid)}/equipment-types/${id}/${active ? 'activate' : 'deactivate'}`),
  // 删除：没用过的才能删（先看 usage）；用过的只能停用
  typeUsage: (pid: number, id: number) => api.get<Usage>(`${p(pid)}/equipment-types/${id}/usage`),
  deleteType: (pid: number, id: number) => api.delete<void>(`${p(pid)}/equipment-types/${id}`),
  moduleTypes: (pid: number, moduleId: number) =>
    api.get<EquipmentType[]>(`${p(pid)}/modules/${moduleId}/equipment-types`),
  setModuleTypes: (pid: number, moduleId: number, typeIds: number[]) =>
    api.put<EquipmentType[]>(`${p(pid)}/modules/${moduleId}/equipment-types`, { equipment_type_ids: typeIds }),

  list: (pid: number, includeInactive = false) =>
    api.get<Equipment[]>(`${p(pid)}/equipment`, { include_inactive: includeInactive }),
  create: (pid: number, data: EquipmentFields) => api.post<Equipment>(`${p(pid)}/equipment`, data),
  update: (pid: number, id: number, data: EquipmentFields & { reason?: string }) =>
    api.patch<Equipment>(`${p(pid)}/equipment/${id}`, data),
  setActive: (pid: number, id: number, active: boolean) =>
    api.post<Equipment>(`${p(pid)}/equipment/${id}/${active ? 'activate' : 'deactivate'}`),
  usage: (pid: number, id: number) => api.get<Usage>(`${p(pid)}/equipment/${id}/usage`),
  remove: (pid: number, id: number) => api.delete<void>(`${p(pid)}/equipment/${id}`),
  bulkDelete: (pid: number, ids: number[]) =>
    api.post<{ deleted: number[]; skipped: { id: number; tag: string | null; name: string; usage: Record<string, number> }[] }>(
      `${p(pid)}/equipment/bulk-delete`,
      { ids },
    ),
  bulkDeactivate: (pid: number, ids: number[]) => api.post<{ deactivated: number[] }>(`${p(pid)}/equipment/bulk-deactivate`, { ids }),
  history: (pid: number, id: number) => api.get<HistoryEntry[]>(`${p(pid)}/equipment/${id}/history`),
  restore: (pid: number, id: number, changeLogId: number) =>
    api.post<Equipment>(`${p(pid)}/equipment/${id}/restore`, { change_log_id: changeLogId }),
  units: (pid: number, id: number) => api.get<Unit[]>(`${p(pid)}/equipment/${id}/units`),
  setSerials: (pid: number, id: number, serials: { seq: number; serial_no: string | null }[]) =>
    api.put<Unit[]>(`${p(pid)}/equipment/${id}/units`, { serials }),

  importPreview: (
    pid: number,
    file: File,
    options: { headerRow?: number | null; mapping?: Record<string, string> } = {},
  ) => {
    const form = new FormData()
    form.append('file', file)
    if (options.headerRow) form.append('header_row', String(options.headerRow))
    if (options.mapping && Object.keys(options.mapping).length) form.append('mapping', JSON.stringify(options.mapping))
    return api.upload<ImportPreview>(`${p(pid)}/equipment-import/preview`, form)
  },
  previewRows: (pid: number, rows: PastedEquipmentRow[]) =>
    api.post<ImportPreview>(`${p(pid)}/equipment-import/preview-rows`, { rows }),
  importConfirm: (pid: number, token: string, deactivateMissing = false) =>
    api.post<Record<string, number>>(`${p(pid)}/equipment-import/confirm`, {
      token,
      deactivate_missing: deactivateMissing,
    }),
}
