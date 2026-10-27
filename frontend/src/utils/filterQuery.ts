// 应交矩阵的筛选条件 ↔ 地址栏（可以收藏、发链接给同事）
import type { LocationQuery, LocationQueryRaw } from 'vue-router'

import type { Filters } from '@/api/deliverables'

const NUMBER_LISTS = ['milestone_id', 'document_type_id', 'module_id', 'equipment_type_id', 'equipment_id'] as const
const TEXT_LISTS = ['vendor', 'status', 'responsible', 'origin'] as const
const FLAGS = ['overdue', 'review_overdue', 'no_evidence', 'received', 'outstanding', 'in_client_mdl'] as const

function list(value: LocationQuery[string]): string[] {
  if (value === undefined || value === null) return []
  return (Array.isArray(value) ? value : [value]).filter((v): v is string => v !== null)
}

export function filtersFromQuery(query: LocationQuery): Filters {
  const f: Record<string, unknown> = {}
  for (const key of NUMBER_LISTS) {
    const values = list(query[key]).map(Number).filter((n) => Number.isInteger(n))
    if (values.length) f[key] = values
  }
  for (const key of TEXT_LISTS) {
    const values = list(query[key])
    if (values.length) f[key] = values
  }
  for (const key of FLAGS) {
    const [value] = list(query[key])
    if (value === 'true' || value === 'false') f[key] = value === 'true'
  }
  const [q] = list(query.q)
  if (q) f.q = q
  return f as Filters
}

export function filtersToQuery(filters: Filters): LocationQueryRaw {
  const out: LocationQueryRaw = {}
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) {
      if (value.length) out[key] = value.map(String)
    } else {
      out[key] = String(value)
    }
  }
  return out
}
