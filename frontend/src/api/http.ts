// 接口请求封装：登录身份在 cookie 里（浏览器自动带）；写操作加防跨站请求头；统一解析错误；没登录时跳到登录页。后端的业务错误格式：
// {"detail": {"code": "...", "message": "...", "params": {...}}}；参数校验错误（422）的 detail 是列表。

export class ApiError extends Error {
  status: number
  code: string
  params: Record<string, unknown>

  constructor(status: number, code: string, message: string, params: Record<string, unknown> = {}) {
    super(message)
    this.status = status
    this.code = code
    this.params = params
  }
}

type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>

export interface RequestOptions {
  query?: Query
  body?: unknown
  form?: FormData
  raw?: Blob // 原始字节（分块上传）
  signal?: AbortSignal
}

// 没登录 / 登录过期（401）时调用（router 里设成跳到登录页）
let onUnauthorized: (() => void) | null = null

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn
}

export function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === '') continue
    for (const v of Array.isArray(value) ? value : [value]) params.append(key, String(v))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

function headers(json: boolean, method = 'GET'): Headers {
  const h = new Headers()
  // 写操作必须带：别的网站的页面发不出自定义请求头，挡住跨站伪造
  if (method !== 'GET' && method !== 'HEAD') h.set('X-Docbase', '1')
  if (json) h.set('Content-Type', 'application/json')
  return h
}

function unauthorized(resp: Response, path: string): void {
  if (resp.status === 401 && onUnauthorized && !path.startsWith('/api/auth/')) onUnauthorized()
}

export async function toApiError(resp: Response): Promise<ApiError> {
  let detail: unknown
  try {
    detail = (await resp.json())?.detail
  } catch {
    detail = undefined
  }
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'message' in detail) {
    const d = detail as { code?: string; message: string; params?: Record<string, unknown> }
    return new ApiError(resp.status, d.code ?? 'error', d.message, d.params ?? {})
  }
  if (Array.isArray(detail) && detail.length) {
    // 参数校验错误：取第一条，带上字段名
    const first = detail[0] as { loc?: unknown[]; msg?: string }
    const field = (first.loc ?? []).slice(1).join('.')
    return new ApiError(resp.status, 'validation', field ? `${field}: ${first.msg}` : String(first.msg))
  }
  return new ApiError(resp.status, 'http', '')
}

export async function request<T>(method: string, path: string, options: RequestOptions = {}): Promise<T> {
  const json = options.body !== undefined
  let resp: Response
  try {
    resp = await fetch(buildUrl(path, options.query), {
      method,
      headers: headers(json, method),
      body: options.form ?? options.raw ?? (json ? JSON.stringify(options.body) : undefined),
      signal: options.signal,
    })
  } catch (err) {
    if ((err as Error).name === 'AbortError') throw err
    throw new ApiError(0, 'network', '')
  }
  if (!resp.ok) {
    unauthorized(resp, path)
    throw await toApiError(resp)
  }
  if (resp.status === 204) return undefined as T
  const type = resp.headers.get('content-type') ?? ''
  return (type.includes('application/json') ? await resp.json() : await resp.blob()) as T
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>('GET', path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, { body: body ?? {} }),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, { body }),
  patch: <T>(path: string, body?: unknown) => request<T>('PATCH', path, { body }),
  delete: <T>(path: string) => request<T>('DELETE', path),
  upload: <T>(path: string, form: FormData) => request<T>('POST', path, { form }),
  putRaw: <T>(path: string, raw: Blob, query?: Query) => request<T>('PUT', path, { raw, query }),
}

// 下载文件：取响应头里的文件名，触发浏览器保存；返回响应头（如 MDL 版本号）
export async function downloadResponse(
  method: string,
  path: string,
  options: { query?: Query; body?: unknown } = {},
): Promise<Headers> {
  let resp: Response
  try {
    resp = await fetch(buildUrl(path, options.query), {
      method,
      headers: headers(options.body !== undefined, method),
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    })
  } catch {
    throw new ApiError(0, 'network', '')
  }
  if (!resp.ok) {
    unauthorized(resp, path)
    throw await toApiError(resp)
  }
  const disposition = resp.headers.get('content-disposition') ?? ''
  const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition) ?? /filename="?([^";]+)"?/i.exec(disposition)
  const name = match ? decodeURIComponent(match[1]) : 'download'
  const url = URL.createObjectURL(await resp.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
  return resp.headers
}

export async function download(path: string, query?: Query): Promise<void> {
  await downloadResponse('GET', path, { query })
}
