// 项目结构接口：里程碑、模块、文件种类、分区、对应设备、顺序、复制、标准模板导入
import { api } from './http'
import type { Usage } from '@/composables/useSafeDelete'

export type Origin = 'contract' | 'client' | 'internal'
export type Acceptance = 'pending' | 'accepted' | 'declined'

export interface Milestone {
  id: number
  code: string
  number: string
  name: string | null
  description: string | null
  goal: string | null
  remarks: string | null
  due_date: string | null
  sort_order: number
  active: boolean
}

export interface Module {
  id: number
  name: string
  sort_order: number
  active: boolean
}

export interface DocumentType {
  id: number
  milestone_id: number
  submittal_no: string
  name: string
  category: string | null
  per_equipment: boolean
  per_module: boolean
  due_date: string | null
  content: string | null
  description: string | null
  purpose: string | null
  mdl_status_text: string | null
  mdl_remarks: string | null
  mdl_submitted_date: string | null
  origin: Origin
  required_date: string | null
  basis: string | null
  changed_date: string | null
  change_ref: string | null
  in_client_mdl: boolean
  acceptance: Acceptance | null
  acceptance_ref: string | null
  variation_ref: string | null
  keywords: string[]
  sort_order: number
  active: boolean
}

export interface TreeDocumentType extends DocumentType {
  partitions: { module_id: number; module_name: string; active: boolean }[]
  equipment: { equipment_id: number; tag: string | null; equipment_name: string; active: boolean }[]
}

export interface TreeMilestone extends Milestone {
  document_types: TreeDocumentType[]
}

export interface StructureTree {
  modules: Module[]
  milestones: TreeMilestone[]
}

export type DocumentTypeFields = Partial<
  Pick<
    DocumentType,
    | 'name'
    | 'category'
    | 'per_equipment'
    | 'due_date'
    | 'content'
    | 'description'
    | 'purpose'
    | 'mdl_remarks'
    | 'origin'
    | 'required_date'
    | 'basis'
    | 'changed_date'
    | 'change_ref'
    | 'in_client_mdl'
    | 'acceptance'
    | 'acceptance_ref'
    | 'variation_ref'
    | 'keywords'
  >
>

export interface EquipmentSelection {
  all_equipment?: boolean
  equipment_type_ids?: number[]
  module_ids?: number[]
  equipment_ids?: number[]
}

export interface Issue {
  code: string
  message: string
  row?: number | null
  column?: string | null
  sheet?: string | null
}

export interface StructureImportPreview {
  token: string
  ok: boolean
  errors: Issue[]
  warnings: Issue[]
  summary: Record<string, number>
}

const p = (id: number) => `/api/projects/${id}`

export const structureApi = {
  tree: (pid: number, includeInactive = false) =>
    api.get<StructureTree>(`${p(pid)}/structure`, { include_inactive: includeInactive }),

  createMilestone: (pid: number, data: Partial<Milestone> & { position?: number }) =>
    api.post<Milestone>(`${p(pid)}/milestones`, data),
  updateMilestone: (pid: number, id: number, data: Partial<Milestone> & { reason?: string }) =>
    api.patch<Milestone>(`${p(pid)}/milestones/${id}`, data),
  deleteMilestone: (pid: number, id: number) => api.delete<void>(`${p(pid)}/milestones/${id}`),
  setMilestoneActive: (pid: number, id: number, active: boolean) =>
    api.post<Milestone>(`${p(pid)}/milestones/${id}/${active ? 'activate' : 'deactivate'}`),

  createModule: (pid: number, name: string) => api.post<Module>(`${p(pid)}/modules`, { name }),
  updateModule: (pid: number, id: number, name: string) =>
    api.patch<Module>(`${p(pid)}/modules/${id}`, { name }),
  setModuleActive: (pid: number, id: number, active: boolean) =>
    api.post<Module>(`${p(pid)}/modules/${id}/${active ? 'activate' : 'deactivate'}`),
  moduleUsage: (pid: number, id: number) => api.get<Usage>(`${p(pid)}/modules/${id}/usage`),
  deleteModule: (pid: number, id: number) => api.delete<void>(`${p(pid)}/modules/${id}`),

  bulkCreate: (pid: number, milestoneId: number, rows: DocumentTypeFields[], position?: number) =>
    api.post<DocumentType[]>(`${p(pid)}/document-types/bulk`, { milestone_id: milestoneId, rows, position }),
  updateDocumentType: (
    pid: number,
    id: number,
    data: DocumentTypeFields & { milestone_id?: number; reason?: string },
  ) => api.patch<DocumentType>(`${p(pid)}/document-types/${id}`, data),
  deleteDocumentType: (pid: number, id: number) => api.delete<void>(`${p(pid)}/document-types/${id}`),
  setDocumentTypeActive: (pid: number, id: number, active: boolean) =>
    api.post<DocumentType>(`${p(pid)}/document-types/${id}/${active ? 'activate' : 'deactivate'}`),
  setModules: (pid: number, id: number, moduleNames: string[]) =>
    api.put<DocumentType>(`${p(pid)}/document-types/${id}/modules`, { module_names: moduleNames }),
  bulkModules: (pid: number, ids: number[], moduleNames: string[], mode: 'add' | 'remove' | 'replace') =>
    api.post<DocumentType[]>(`${p(pid)}/document-types/bulk-modules`, {
      document_type_ids: ids,
      module_names: moduleNames,
      mode,
    }),
  bulkEquipment: (
    pid: number,
    ids: number[],
    selection: EquipmentSelection,
    mode: 'add' | 'remove' | 'replace',
  ) =>
    api.post<DocumentType[]>(`${p(pid)}/document-types/bulk-equipment`, {
      document_type_ids: ids,
      mode,
      ...selection,
    }),

  reorder: (pid: number, entity: 'milestones' | 'modules' | 'document_types', ids: number[], milestoneId?: number) =>
    api.post<void>(`${p(pid)}/reorder`, { entity, ids, milestone_id: milestoneId }),
  copyFrom: (pid: number, sourceProjectId: number) =>
    api.post<Record<string, number>>(`${p(pid)}/copy-structure`, { source_project_id: sourceProjectId }),

  importPreview: (pid: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload<StructureImportPreview>(`${p(pid)}/structure-import/preview`, form)
  },
  importConfirm: (pid: number, token: string) =>
    api.post<Record<string, number>>(`${p(pid)}/structure-import/confirm`, { token }),
}
