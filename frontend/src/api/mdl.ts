// MDL 接口：导出（草稿 / 发给客户 / 历史版本）、版本列表与对比、导回
import { api, downloadResponse } from './http'
import type { Issue } from './structure'

export interface MdlVersion {
  rev_no: number
  label: string
  description: string | null
  frozen_by_name: string | null
  frozen_at: string
  milestones: number
  document_types: number
}

export interface DiffEntry {
  id: number
  code?: string
  name?: string | null
  submittal_no?: string
  changes?: Record<string, [unknown, unknown]>
}

export interface DiffSection {
  added: DiffEntry[]
  removed: DiffEntry[]
  modified: DiffEntry[]
}

export interface Compare {
  milestones: DiffSection
  document_types: DiffSection
  has_changes: boolean
}

export interface ReturnChange {
  row: number
  target: 'deliverable' | 'document_type' | 'partition'
  label: string
  changes: Record<string, [unknown, unknown]>
}

export interface ReturnPreview {
  token: string
  ok: boolean
  changes: ReturnChange[]
  warnings: Issue[]
  errors: Issue[]
}

export type Lang = 'en' | 'zh'
export type Fmt = 'xlsx' | 'pdf'

const p = (id: number) => `/api/projects/${id}`

export const mdlApi = {
  exportDraft: (pid: number, lang: Lang, format: Fmt) =>
    downloadResponse('GET', `${p(pid)}/mdl/export`, { query: { lang, format } }),
  issue: async (pid: number, lang: Lang, format: Fmt, description: string | null) => {
    const headers = await downloadResponse('POST', `${p(pid)}/mdl/issue`, { body: { lang, format, description } })
    return { revision: headers.get('x-mdl-revision'), isNew: headers.get('x-mdl-new-revision') === 'true' }
  },
  exportVersion: (pid: number, rev: number, lang: Lang, format: Fmt) =>
    downloadResponse('GET', `${p(pid)}/mdl-versions/${rev}/export`, { query: { lang, format } }),
  versions: (pid: number) => api.get<MdlVersion[]>(`${p(pid)}/mdl-versions`),
  compare: (pid: number, from: number, to: number | 'current') =>
    api.get<Compare>(`${p(pid)}/mdl-versions/compare`, { from, to }),
  returnPreview: (pid: number, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload<ReturnPreview>(`${p(pid)}/mdl/import/preview`, form)
  },
  returnConfirm: (pid: number, token: string) =>
    api.post<{ deliverables: number; remarks: number }>(`${p(pid)}/mdl/import/confirm`, { token }),
}
