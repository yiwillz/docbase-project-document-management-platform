// 项目接口
import { api } from './http'

export interface Project {
  id: number
  code: string
  name: string
  description: string | null
  archived_at: string | null
  client_name: string | null
  contractor_name: string | null
  mdl_doc_no: string | null
  mdl_prepared_by: string | null
  mdl_checked_by: string | null
  mdl_approved_by: string | null
  review_days: number | null
  numbering_rules: Record<string, unknown>
  naming_templates: Record<string, unknown>
  created_at: string
  updated_at: string
  // 项目列表才有：待归类的文件数；完成率（已批准 ÷ 应交，没有应交项时为 null）、缺件数、逾期数
  unclassified_count?: number
  completion?: number | null
  outstanding_count?: number
  overdue_count?: number
}

export interface ProjectCreate {
  code: string
  name: string
  client_name?: string | null
  contractor_name?: string | null
}

export const projectsApi = {
  list: (includeArchived = false) =>
    api.get<Project[]>('/api/projects', { include_archived: includeArchived }),
  get: (id: number) => api.get<Project>(`/api/projects/${id}`),
  create: (data: ProjectCreate) => api.post<Project>('/api/projects', data),
  update: (id: number, data: Partial<Project> & { reason?: string }) =>
    api.patch<Project>(`/api/projects/${id}`, data),
  archive: (id: number, reason?: string) => api.post<Project>(`/api/projects/${id}/archive`, { reason }),
  // 删除空项目（系统管理员）：先看能不能删，再输入项目编号确认
  deleteCheck: (id: number) =>
    api.get<{ deletable: boolean; blockers: Record<string, number> }>(`/api/projects/${id}/delete-check`),
  remove: (id: number, confirmCode: string, reason?: string) =>
    api.post<{ deleted: number }>(`/api/projects/${id}/delete`, { confirm_code: confirmCode, reason }),
  unarchive: (id: number, reason?: string) => api.post<Project>(`/api/projects/${id}/unarchive`, { reason }),
}
