// 传送单接口：生成 / 补记、列表、详情、改表头、作废、客户回复、导出、客户填好后导回
import type { Item } from './deliverables'
import { api, downloadResponse } from './http'
import type { Lang, Fmt } from './mdl'
import type { Status } from './settings'
import type { Issue } from './structure'

export interface Transmittal {
  id: number
  number: string
  subject: string | null
  submitted_date: string | null
  source: 'platform' | 'recorded' | 'mdl_return'
  method: string | null
  external_ref: string | null
  remarks: string | null
  created_by_name: string | null
  void: boolean
  void_reason: string | null
  created_at: string
  review_due: string | null
}

export interface TransmittalRow extends Transmittal {
  items: number
  replied: number
}

export interface TransmittalItem {
  item_id: number
  deliverable_id: number
  revision: string | null
  review_code: string | null
  review_status: Status | null
  review_final: boolean | null
  reply_date: string | null
  reply_ref: string | null
  reply_comments: string | null
  deemed: boolean
  deemed_basis: string | null
  reply_source: string | null
  replied_by: string | null
  deliverable: Item | null
}

export interface TransmittalDetail extends Transmittal {
  items: TransmittalItem[]
}

export interface TransmittalCreate {
  deliverable_ids: number[]
  subject?: string | null
  submitted_date?: string | null
  method?: string | null
  external_ref?: string | null
  remarks?: string | null
}

export interface Reply {
  code?: string | null
  reply_date?: string | null
  reply_ref?: string | null
  reply_comments?: string | null
  deemed?: boolean
  deemed_basis?: string | null
  items?: { item_id: number; code?: string | null; reply_date?: string | null; reply_comments?: string | null }[]
  reason?: string
}

export interface ReturnPreview {
  token: string
  ok: boolean
  number: string | null
  reply_ref: string | null
  changes: { item_id: number; row: number; label: string; changes: Record<string, [unknown, unknown]> }[]
  warnings: Issue[]
  errors: Issue[]
}

const p = (id: number) => `/api/projects/${id}/transmittals`

export const transmittalsApi = {
  list: (pid: number, includeVoid = false) => api.get<TransmittalRow[]>(p(pid), { include_void: includeVoid }),
  get: (pid: number, id: number) => api.get<TransmittalDetail>(`${p(pid)}/${id}`),
  create: (pid: number, data: TransmittalCreate) => api.post<TransmittalDetail>(p(pid), data),
  record: (pid: number, data: TransmittalCreate) => api.post<TransmittalDetail>(`${p(pid)}/record`, data),
  update: (pid: number, id: number, data: Partial<Transmittal> & { reason?: string }) =>
    api.patch<Transmittal>(`${p(pid)}/${id}`, data),
  void: (pid: number, id: number, reason: string) => api.post<Transmittal>(`${p(pid)}/${id}/void`, { reason }),
  reply: (pid: number, id: number, reply: Reply) =>
    api.post<{ updated: number; unchanged: number }>(`${p(pid)}/${id}/reply`, reply),
  export: (pid: number, id: number, lang: Lang, format: Fmt) =>
    downloadResponse('GET', `${p(pid)}/${id}/export`, { query: { lang, format } }),
  returnPreview: (pid: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload<ReturnPreview>(`${p(pid)}/import/preview`, form)
  },
  returnConfirm: (pid: number, token: string) =>
    api.post<{ updated: number; unchanged: number }>(`${p(pid)}/import/confirm`, { token }),
}
