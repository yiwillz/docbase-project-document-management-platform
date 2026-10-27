// 存量导入：新建批次（后台扫描）→ 预演报告 → 改报告导回 → 执行 → 暂停 / 继续 → 整批撤销
import type { Reason } from './files'
import { api, download } from './http'
import type { Lang } from './mdl'

export type BatchStatus = 'scanning' | 'ready' | 'executing' | 'done' | 'undoing' | 'undone' | 'failed'
export type ItemState = 'new' | 'duplicate' | 'existing' | 'old_version' | 'unreadable'
export type ItemResult = 'pending' | 'done' | 'failed' | 'skipped' | 'undone' | 'kept'

export interface BatchJob {
  id: number
  kind: string
  status: 'queued' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  total: number | null
  error: string | null
}

export interface Batch {
  id: number
  source_dir: string
  description: string | null
  status: BatchStatus
  counts: Record<string, number>
  error: string | null
  created_by: string | null
  created_at: string
  executed_at: string | null
  undone_at: string | null
  job: BatchJob | null
}

export interface ImportRow {
  id: number
  path: string
  size: number
  modified: string | null
  state: ItemState
  duplicate_of: number | null
  level: 'high' | 'medium' | 'low' | null
  reasons: Reason[]
  suspected_equipment: string | null
  action: 'import' | 'skip'
  deliverable_ids: number[]
  location: string
  manual: boolean // 位置是人改过的（不是识别出的）
  source: string | null
  title: string | null
  version_rank: number | null
  planned_name: string | null
  result: ItemResult
  file_id: number | null
  error: string | null
}

export interface ItemQuery {
  state?: ItemState
  result?: ItemResult
  unclassified?: boolean
  suspected?: boolean
  offset?: number
  limit?: number
}

export interface ReportPreview {
  token: string
  ok: boolean
  changes: { item_id: number; row: number; path: string; changes: Record<string, [unknown, unknown]> }[]
  errors: { row: number; message: string }[]
}

const p = (pid: number) => `/api/projects/${pid}/imports`

export const importsApi = {
  roots: (pid: number) => api.get<string[]>(`${p(pid)}/roots`),
  list: (pid: number) => api.get<Batch[]>(p(pid)),
  get: (pid: number, id: number) => api.get<Batch>(`${p(pid)}/${id}`),
  create: (pid: number, sourceDir: string, description?: string) =>
    api.post<Batch>(p(pid), { source_dir: sourceDir, description: description || undefined }),
  items: (pid: number, id: number, query: ItemQuery = {}) =>
    api.get<{ total: number; items: ImportRow[] }>(`${p(pid)}/${id}/items`, { ...query }),
  report: (pid: number, id: number, lang: Lang) => download(`${p(pid)}/${id}/report.xlsx`, { lang }),
  reportPreview: (pid: number, id: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload<ReportPreview>(`${p(pid)}/${id}/report/preview`, form)
  },
  reportConfirm: (pid: number, id: number, token: string) =>
    api.post<{ updated: number; batch: Batch }>(`${p(pid)}/${id}/report/confirm`, { token }),
  execute: (pid: number, id: number) => api.post<Batch>(`${p(pid)}/${id}/execute`),
  pause: (pid: number, id: number) => api.post<Batch>(`${p(pid)}/${id}/pause`),
  resume: (pid: number, id: number) => api.post<Batch>(`${p(pid)}/${id}/resume`),
  undo: (pid: number, id: number) => api.post<Batch>(`${p(pid)}/${id}/undo`),
}
