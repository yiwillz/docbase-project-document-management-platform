// 交付与基线：交付包（估算、生成、列表、下载）、阶段基线（冻结、列表、查看、对比、导出）、共享盘镜像状态
import { api, download } from './http'
import type { Status } from './settings'

export type Layout = 'equipment' | 'milestone'

export interface Scope {
  milestone_ids: number[]
  document_type_ids: number[]
  equipment_ids: number[]
}

export interface PackageRequest {
  scope: Scope
  layout: Layout
  lang: 'zh' | 'en'
  include_client: boolean
  baseline_id?: number | null
  submit?: { subject?: string | null; submitted_date?: string | null; remarks?: string | null } | null
}

export interface Package {
  id: number
  status: 'queued' | 'running' | 'paused' | 'succeeded' | 'failed' | 'cancelled'
  progress: number
  total: number | null
  error: string | null
  created_at: string
  created_by: string | null
  layout: Layout
  lang: 'zh' | 'en'
  include_client: boolean
  scope: Scope
  baseline_id: number | null
  transmittal_no: string | null
  files: number
  size: number | null
  available: boolean
  download_name: string
}

export interface Baseline {
  id: number
  name: string
  description: string | null
  frozen_at: string
  frozen_by: string | null
  counts: { deliverables?: number; with_files?: number; missing?: number; files?: number; by_status?: Record<string, number> }
}

export interface BaselineRow {
  deliverable_id: number
  milestone_code?: string
  submittal_no?: string
  document_type_name?: string
  where?: string
  status: Status
  responsible: string
  files: { version_id: number; file_id: number; version_no: number | null; filename: string | null; original_name: string | null }[]
}

export type ChangeKind = 'status' | 'new_version' | 'new_file' | 'file_removed' | 'now_missing' | 'now_delivered' | 'added' | 'removed'

export interface CompareResult {
  a: Baseline
  b: Baseline | null
  rows: {
    deliverable_id: number
    milestone_code?: string
    submittal_no?: string
    document_type_name?: string
    where?: string
    changes: { kind: ChangeKind; from?: string; to?: string }[]
  }[]
  counts: Record<ChangeKind, number>
}

export interface MirrorStatus {
  enabled: boolean
  dir: string | null
  mode: string
  last_sync: string | null
  pending_events: number
  last_job: { status: string; finished_at: string | null; result: Record<string, number> | null; error: string | null } | null
}

const p = (pid: number) => `/api/projects/${pid}`

export const deliveryApi = {
  estimate: (pid: number, body: PackageRequest) =>
    api.post<{ files: number; size: number; deliverables: number }>(`${p(pid)}/packages/estimate`, body),
  createPackage: (pid: number, body: PackageRequest) => api.post<Package>(`${p(pid)}/packages`, body),
  packages: (pid: number) => api.get<Package[]>(`${p(pid)}/packages`),
  packageUrl: (pid: number, id: number) => `${p(pid)}/packages/${id}/download`,
  freeze: (pid: number, name: string, description?: string) =>
    api.post<Baseline>(`${p(pid)}/baselines`, { name, description: description || undefined }),
  baselines: (pid: number) => api.get<Baseline[]>(`${p(pid)}/baselines`),
  baseline: (pid: number, id: number) => api.get<Baseline & { items: BaselineRow[] }>(`${p(pid)}/baselines/${id}`),
  compare: (pid: number, id: number, other?: number | null) =>
    api.get<CompareResult>(`${p(pid)}/baselines/${id}/compare`, { other: other ?? undefined }),
  exportBaseline: (pid: number, id: number, lang: 'zh' | 'en') => download(`${p(pid)}/baselines/${id}/export.xlsx`, { lang }),
  mirror: (pid: number) => api.get<MirrorStatus>(`${p(pid)}/mirror`),
  mirrorSync: (pid: number) => api.post<MirrorStatus>(`${p(pid)}/mirror/sync`),
}
