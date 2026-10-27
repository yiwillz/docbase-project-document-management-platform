// 闭环检查、里程碑关闭 / 重新打开、关闭清单、合同外新增要求清单
import type { Item, Stats } from './deliverables'
import { api, downloadResponse } from './http'
import type { Fmt, Lang } from './mdl'

export type IssueCode =
  | 'not_done'
  | 'not_final'
  | 'no_evidence'
  | 'requirement_unconfirmed'
  | 'new_version_after_approval'
  | 'no_requirement_basis'
  | 'internal_open'

export const BLOCKING: IssueCode[] = [
  'not_done',
  'not_final',
  'no_evidence',
  'requirement_unconfirmed',
  'new_version_after_approval',
]

export interface CloseoutMilestone {
  id: number
  code: string
  name: string | null
  due_date: string | null
  closed: boolean
  closed_at: string | null
  closed_date: string | null
  closed_by: string | null
  close_basis: string | null
  changed_after_close: boolean
}

export interface Closeout {
  milestone: CloseoutMilestone
  closable: boolean
  blocking: number
  warnings: number
  counts: Record<IssueCode, number>
  stats: Stats
  documents: { document_type_id: number; submittal_no: string; name: string; origin: string; issues: IssueCode[] }[]
  items: (Item & { revision: string | null; issues: IssueCode[] })[]
}

const p = (id: number) => `/api/projects/${id}`

export const closeoutApi = {
  check: (pid: number, milestoneId: number) => api.get<Closeout>(`${p(pid)}/milestones/${milestoneId}/closeout`),
  close: (
    pid: number,
    milestoneId: number,
    body: { closed_date?: string | null; basis?: string | null; force?: boolean; reason?: string | null },
  ) => api.post<CloseoutMilestone>(`${p(pid)}/milestones/${milestoneId}/close`, body),
  reopen: (pid: number, milestoneId: number, reason: string) =>
    api.post<CloseoutMilestone>(`${p(pid)}/milestones/${milestoneId}/reopen`, { reason }),
  exportList: (pid: number, milestoneId: number, lang: Lang, format: Fmt) =>
    downloadResponse('GET', `${p(pid)}/milestones/${milestoneId}/closeout/export`, { query: { lang, format } }),
  exportClientRequirements: (pid: number, lang: Lang) =>
    downloadResponse('GET', `${p(pid)}/client-requirements.xlsx`, { query: { lang } }),
}
