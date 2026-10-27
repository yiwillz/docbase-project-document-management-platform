// 设置页用到的接口：审核代码、状态映射
import { api } from './http'

export type Status =
  | 'not_submitted'
  | 'submitted'
  | 'under_review'
  | 'approved'
  | 'revise_resubmit'
  | 'not_applicable'
export type Responsible = 'ours' | 'vendor' | 'client' | 'none'

export interface ReviewCode {
  id?: number
  code: string
  label_en: string
  label_zh: string | null
  status: Status
  final: boolean
  aliases: string[]
}

export interface StatusRule {
  id?: number
  pattern: string
  match_type: 'equals' | 'contains' | 'regex'
  status: Status
  responsible: Responsible | null
  is_definite: boolean
  priority: number
}

export interface StatusTestResult {
  text: string
  status: Status | null
  responsible: Responsible | null
  definite: boolean
  rule_id: number | null
}

const p = (id: number) => `/api/projects/${id}`

export const settingsApi = {
  reviewCodes: (pid: number) => api.get<ReviewCode[]>(`${p(pid)}/review-codes`),
  saveReviewCodes: (pid: number, codes: ReviewCode[], reason?: string) =>
    api.put<ReviewCode[]>(`${p(pid)}/review-codes`, { codes, reason }),
  statusRules: (pid: number) => api.get<StatusRule[]>(`${p(pid)}/status-mappings`),
  saveStatusRules: (pid: number, rules: StatusRule[], reason?: string) =>
    api.put<StatusRule[]>(`${p(pid)}/status-mappings`, { rules, reason }),
  testStatus: (pid: number, text: string) =>
    api.post<StatusTestResult | null>(`${p(pid)}/status-mappings/test`, { text }),
}
