// 文件浏览与检索：目录树（三种视图）、文件列表、文件详情、重新归类、回收区、在线预览
import type { Filters } from './deliverables'
import type { Source } from './files'
import { api } from './http'
import type { Status } from './settings'

export type View = 'milestone' | 'equipment' | 'module'
export const VIEWS: View[] = ['milestone', 'equipment', 'module']
export const UNCLASSIFIED = 'unclassified'
export const RECYCLE = 'recycle'

export interface TreeNode {
  key: string
  label: string
  count: number
  deliverable_id?: number
  untyped?: boolean
  unassigned?: boolean
  children: TreeNode[]
}

export interface FileTree {
  view: View
  nodes: TreeNode[]
  unclassified: number
  recycle: number
  total: number
  view_total: number // 这个视图里能看到的（含待归类）
}

export interface Location {
  deliverable_id: number
  milestone_code?: string
  submittal_no?: string
  document_type_name?: string
  where?: string
  status?: Status
}

export interface FileRow {
  id: number
  filename: string
  title: string | null
  source: Source
  current_version: number
  version_id: number | null
  original_name: string | null
  file_date: string | null
  size: number | null
  uploaded_by: string | null
  uploaded_at: string | null
  preview: boolean
  deliverable_ids: number[]
  locations: Location[]
}

export interface RecycledVersion {
  id: number
  file_id: number
  version_no: number
  filename: string
  original_name: string
  size: number | null
  recycled_at: string
  purge_on: string
  days_left: number
  preview: boolean
  locations: Location[]
}

export interface DetailVersion {
  id: number
  version_no: number
  filename: string
  stored_name: string
  original_name: string
  file_date: string | null
  date_source: string | null
  size: number | null
  uploaded_by: string | null
  uploaded_at: string
  recycled_at: string | null
  purged_at: string | null
  protected: boolean
  preview: boolean
}

export interface FileLog {
  id: number
  created_at: string
  actor: string | null
  action: string
  entity_type: string
  label: string | null
  changes: Record<string, [unknown, unknown]>
  reason: string | null
}

export interface FileDetail {
  id: number
  title: string | null
  source: Source
  current_version: number
  filename: string
  deliverable_ids: number[]
  locations: Location[]
  submitted: boolean
  versions: DetailVersion[]
  logs: FileLog[]
}

export interface ListQuery extends Filters {
  view: View
  node?: string
  source?: Source[]
  sort?: 'name' | 'uploaded_at' | 'size'
  order?: 'asc' | 'desc'
  offset?: number
  limit?: number
}

const p = (id: number) => `/api/projects/${id}`

export const browseApi = {
  tree: (pid: number, view: View) => api.get<FileTree>(`${p(pid)}/files/tree`, { view }),
  list: (pid: number, query: ListQuery) =>
    api.get<{ total: number; offset: number; limit: number; items: FileRow[] }>(`${p(pid)}/files`, { ...query }),
  recycle: (pid: number) => api.get<RecycledVersion[]>(`${p(pid)}/files/recycle`),
  detail: (pid: number, fileId: number) => api.get<FileDetail>(`${p(pid)}/files/${fileId}`),
  move: (pid: number, fileId: number, deliverableIds: number[], reason?: string) =>
    api.post<{ file_id: number; filename: string | null }>(`${p(pid)}/files/${fileId}/move`, {
      deliverable_ids: deliverableIds,
      reason,
    }),
  previewUrl: (pid: number, versionId: number) => `${p(pid)}/versions/${versionId}/preview`,
}
