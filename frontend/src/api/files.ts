// 存文件：上传预览 → 改预览 → 确认存入（方式一指定位置、方式二智能投递）；分块上传（断点续传）；
// 待归类与归类；应交项的文件和版本；下载；命名模板
import { ApiError, api } from './http'

export type Source = 'client' | 'vendor' | 'ours'
export const SOURCES: Source[] = ['vendor', 'ours', 'client']

export interface Duplicate {
  file_id: number
  version_id: number
  version_no: number
  filename: string
  recycled: boolean
  deliverable_ids: number[]
}

export interface Candidate {
  file_id: number
  title: string | null
  source: Source
  current_version: number
  deliverable_ids: number[]
}

export interface UploadPlan {
  token: string
  size: number
  sha256: string
  deliverable_ids: number[]
  unclassified: boolean
  document_type_id: number | null
  per_equipment: boolean
  mode: 'new' | 'version'
  file_id: number | null
  version_no: number
  source: Source
  title: string
  file_date: string
  date_source: 'filename' | 'modified' | 'upload' | 'manual'
  filename: string
  original_name: string
  original_version: string | null
  duplicate: Duplicate | null
  candidates: Candidate[]
  ok: boolean
  recognition?: Recognition
}

export interface Reason {
  kind: string
  text: string
  where: 'filename' | 'folder' | 'description' | '-'
}

export interface Located {
  deliverable_ids: number[]
  score: number
  level: Level
  reasons: Reason[]
}

export type Level = 'high' | 'medium' | 'low'

export interface ClientReplyHint {
  code: string
  code_text: string
  transmittal_id: number | null
  transmittal_no: string | null
  item_ids: number[]
}

export interface Recognition {
  candidates: Located[]
  source: Source | null
  client_reply: ClientReplyHint | null
  level: Level
}

export interface DropContext {
  description?: string
  folder?: string
  vendor?: string
}

export interface Overrides {
  source?: Source
  title?: string
  file_date?: string
  file_id?: number | 'new'
  deliverable_ids?: number[]
  record_reply?: boolean
}

export interface StoredResult {
  file_id: number
  version_id: number
  version_no: number
  filename: string
  mode: 'new' | 'version'
  recycled: number[]
  unclassified: boolean
  reply: { transmittal_no: string | null; code: string; updated: number } | null
}

export interface FileVersion {
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
}

export interface StoredFile {
  id: number
  title: string | null
  source: Source
  current_version: number
  deliverable_ids: number[]
  versions: FileVersion[]
}

export interface NamingConfig {
  defaults: Record<string, string | number>
  templates: Record<string, string | number>
  fields: string[]
  samples: Record<string, string>
}

const p = (id: number) => `/api/projects/${id}`

// 大于这个的文件分块上传（每块 8 MB），断了接着传；小的一次传完
export const CHUNK = 8 * 1024 * 1024
export type Progress = (sent: number, total: number) => void

interface UploadState {
  upload_id: string
  received: number
  size: number
}

function resumeKey(pid: number, file: File): string {
  return `docbase.upload.${pid}.${file.name}.${file.size}.${file.lastModified}`
}

function remember(key: string, id: string | null): void {
  try {
    if (id) localStorage.setItem(key, id)
    else localStorage.removeItem(key)
  } catch {
    // 浏览器不让存时只是不能在刷新后接着传
  }
}

function recall(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** 分块上传：同一个文件刷新页面后再传，会从上次收到的地方接着传；每块失败重试 3 次。返回上传编号。 */
export async function uploadChunked(pid: number, file: File, onProgress?: Progress): Promise<string> {
  const key = resumeKey(pid, file)
  let state: UploadState | null = null
  const saved = recall(key)
  if (saved) {
    try {
      state = await api.get<UploadState>(`${p(pid)}/uploads/${saved}`)
    } catch {
      state = null // 已失效（超过 24 小时或已用过），重新开始
    }
  }
  if (!state) {
    state = await api.post<UploadState>(`${p(pid)}/uploads`, { filename: file.name, size: file.size })
    remember(key, state.upload_id)
  }
  let received = state.received
  onProgress?.(received, file.size)
  while (received < file.size) {
    const chunk = file.slice(received, Math.min(received + CHUNK, file.size))
    let attempt = 0
    for (;;) {
      try {
        const r = await api.putRaw<UploadState>(`${p(pid)}/uploads/${state.upload_id}`, chunk, { offset: received })
        received = r.received
        break
      } catch (err) {
        if (err instanceof ApiError && err.code === 'storage.bad_offset') {
          received = Number(err.params.received) // 服务器已收到的比我们以为的多 / 少：从那里接着传
          break
        }
        if (++attempt >= 3 || (err instanceof ApiError && err.status && err.status < 500)) throw err
        await new Promise((resolve) => setTimeout(resolve, 1000 * attempt))
      }
    }
    onProgress?.(received, file.size)
  }
  return state.upload_id
}

async function fileForm(pid: number, file: File, onProgress?: Progress): Promise<FormData> {
  const form = new FormData()
  if (file.size > CHUNK) {
    form.append('upload_id', await uploadChunked(pid, file, onProgress))
  } else {
    form.append('file', file)
  }
  if (file.lastModified) form.append('modified', String(file.lastModified))
  return form
}

function forget(pid: number, file: File): void {
  remember(resumeKey(pid, file), null)
}

export const filesApi = {
  preview: async (pid: number, deliverableIds: number[], file: File, onProgress?: Progress) => {
    const form = await fileForm(pid, file, onProgress)
    form.append('deliverable_ids', deliverableIds.join(','))
    const plan = await api.upload<UploadPlan>(`${p(pid)}/files/preview`, form)
    forget(pid, file)
    return plan
  },
  dropPreview: async (pid: number, file: File, ctx: DropContext, onProgress?: Progress) => {
    const form = await fileForm(pid, file, onProgress)
    for (const [k, v] of Object.entries(ctx)) if (v) form.append(k, v)
    const plan = await api.upload<UploadPlan>(`${p(pid)}/drop/preview`, form)
    forget(pid, file)
    return plan
  },
  replan: (pid: number, token: string, overrides: Overrides) =>
    api.post<UploadPlan>(`${p(pid)}/files/uploads/${token}/plan`, overrides),
  confirm: (pid: number, token: string, overrides: Overrides) =>
    api.post<StoredResult>(`${p(pid)}/files/uploads/${token}/confirm`, overrides),
  ofDeliverable: (pid: number, deliverableId: number) =>
    api.get<StoredFile[]>(`${p(pid)}/deliverables/${deliverableId}/files`),
  unclassified: (pid: number) => api.get<StoredFile[]>(`${p(pid)}/files/unclassified`),
  classify: (pid: number, fileId: number, deliverableIds: number[], reason?: string) =>
    api.post<{ file_id: number; filename: string | null }>(`${p(pid)}/files/${fileId}/classify`, {
      deliverable_ids: deliverableIds,
      reason,
    }),
  // 直接用链接下载（大文件不经过浏览器内存）
  downloadUrl: (pid: number, versionId: number) => `${p(pid)}/versions/${versionId}/download`,
  naming: (pid: number) => api.get<NamingConfig>(`${p(pid)}/naming`),
  namingPreview: (pid: number, templates: Record<string, string | number>) =>
    api.post<Record<string, string>>(`${p(pid)}/naming/preview`, { templates }),
}
