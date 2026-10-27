// 变更记录接口
import { api } from './http'

export interface ChangeLog {
  id: number
  project_id: number | null
  entity_type: string
  entity_id: string
  label: string | null
  summary: 'recalculate' | 'purge' | 'transmittal_reply' | null
  action: 'create' | 'update' | 'delete' | 'restore'
  changes: Record<string, [unknown, unknown]>
  actor_name: string | null
  reason: string | null
  created_at: string
}

export interface ChangeLogQuery {
  project_id: number
  entity_type?: string
  actor?: string
  since?: string
  until?: string
  limit?: number
  offset?: number
}

export const changeLogsApi = {
  list: (query: ChangeLogQuery) => api.get<{ total: number; items: ChangeLog[] }>('/api/change-logs', { ...query }),
}
