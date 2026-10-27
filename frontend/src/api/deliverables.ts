// 应交项接口：列表（分组）、矩阵、汇总、修改（单个 / 批量）、缺件清单
import { api, buildUrl } from './http'
import type { Responsible, Status } from './settings'

export interface Item {
  id: number
  kind: 'whole' | 'per_module' | 'per_equipment'
  milestone_id: number
  milestone_code: string
  milestone_name: string | null
  document_type_id: number
  submittal_no: string
  document_type_name: string
  category: string | null
  origin: string
  in_client_mdl: boolean
  module_id: number | null
  module_name: string | null
  equipment_id: number | null
  equipment_tag: string | null
  equipment_name: string | null
  equipment_type_id: number | null
  equipment_type_name: string | null
  vendor: string | null
  status: Status
  responsible: Responsible
  due_date: string | null
  due_source: 'deliverable' | 'document_type' | 'milestone' | null
  overdue: boolean
  submitted_date: string | null
  status_date: string | null
  status_basis: string | null
  remarks: string | null
  acceptance: string | null
  transmittal_id: number | null
  transmittal_no: string | null
  review_code: string | null
  review_final: boolean | null
  reply_date: string | null
  reply_ref: string | null
  review_due: string | null
  review_overdue: boolean
  no_evidence: boolean
  current_version: number | null
  file_count: number
  received: boolean // 收集情况：平台里有这一项的文件
  outstanding: boolean // 缺件：没收到文件且没批准，或需修改重交
}

export interface Stats {
  total: number
  applicable: number
  approved: number
  completion: number | null
  overdue: number
  review_overdue: number
  no_evidence: number
  received: number // 应交的（不含不适用）里已收到文件的
  received_rate: number | null
  outstanding: number
  pending: { ours: number; vendor: number; client: number; unassigned: number }
  by_status: Partial<Record<Status, number>>
}

export interface TreeModule {
  module_id: number | null
  module_name: string | null
  stats: Stats
  items: Item[]
}

export interface TreeDoc {
  document_type_id: number
  submittal_no: string
  name: string
  kind: Item['kind']
  origin: string
  in_client_mdl: boolean
  stats: Stats
  modules: TreeModule[]
  items: Item[]
}

export interface TreeMilestone {
  milestone_id: number
  code: string
  name: string | null
  stats: Stats
  document_types: TreeDoc[]
}

export interface Summary {
  active: number
  overall: Stats
  milestones: ({ milestone_id: number; code: string; name: string | null; modules: ({ module_id: number | null; module_name: string | null } & Stats)[] } & Stats)[]
  modules: ({ module_id: number | null; module_name: string | null } & Stats)[]
  by_origin: Record<string, Stats>
}

export interface MatrixCell {
  deliverable_id: number
  status: Status
  responsible: Responsible
  overdue: boolean
  due_date: string | null
  current_version: number | null
  received: boolean
}

export interface Matrix {
  modules: { id: number; name: string }[]
  equipment_groups: {
    equipment_type_id: number | null
    name: string | null
    equipment: { id: number; tag: string | null; name: string; vendor: string | null }[]
  }[]
  rows: {
    document_type_id: number
    submittal_no: string
    name: string
    milestone_id: number
    milestone_code: string
    kind: Item['kind']
    stats: Stats
    cells: Record<string, MatrixCell>
  }[]
}

// 筛选条件：多选的用数组，其余用 true / false
export interface Filters {
  milestone_id?: number[]
  document_type_id?: number[]
  module_id?: number[]
  equipment_type_id?: number[]
  equipment_id?: number[]
  vendor?: string[]
  status?: Status[]
  responsible?: Responsible[]
  origin?: string[]
  overdue?: boolean
  review_overdue?: boolean
  no_evidence?: boolean
  received?: boolean
  outstanding?: boolean
  in_client_mdl?: boolean
  q?: string
}

export interface DeliverableChanges {
  status?: Status
  responsible?: Responsible
  due_date?: string | null
  submitted_date?: string | null
  status_date?: string | null
  status_basis?: string | null
  remarks?: string | null
}

const p = (id: number) => `/api/projects/${id}/deliverables`

export const deliverablesApi = {
  list: (pid: number, filters: Filters = {}, limit = 5000) =>
    api.get<{ total: number; items: Item[] }>(p(pid), { ...filters, limit }),
  tree: (pid: number, filters: Filters = {}) =>
    api.get<{ stats: Stats; milestones: TreeMilestone[] }>(`${p(pid)}/tree`, { ...filters }),
  matrix: (pid: number, filters: Filters = {}) => api.get<Matrix>(`${p(pid)}/matrix`, { ...filters }),
  summary: (pid: number, filters: Filters = {}) => api.get<Summary>(`${p(pid)}/summary`, { ...filters }),
  update: (pid: number, id: number, changes: DeliverableChanges & { reason?: string }) =>
    api.patch<Item>(`${p(pid)}/${id}`, changes),
  bulkUpdate: (pid: number, ids: number[], changes: DeliverableChanges, reason?: string) =>
    api.post<{ updated: number; unchanged: number }>(`${p(pid)}/bulk-update`, { ids, changes, reason }),
  outstandingUrl: (pid: number, filters: Filters, lang: string) =>
    buildUrl(`${p(pid)}/outstanding.xlsx`, { ...filters, lang }),
}
