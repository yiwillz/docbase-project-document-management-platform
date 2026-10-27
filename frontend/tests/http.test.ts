// 请求封装：写操作带防跨站请求头、查询参数、错误解析、没登录时回调
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, buildUrl, setUnauthorizedHandler } from '@/api/http'

function mockFetch(status: number, body: unknown) {
  const fn = vi.fn().mockImplementation(
    async () => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }),
  )
  vi.stubGlobal('fetch', fn)
  return fn
}

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise
  } catch (err) {
    return err as ApiError
  }
  throw new Error('expected the request to fail')
}

afterEach(() => {
  vi.unstubAllGlobals()
  setUnauthorizedHandler(null)
})

describe('http', () => {
  it('builds query strings, skipping empty values and repeating arrays', () => {
    expect(buildUrl('/api/x', { a: 1, b: '', c: null, s: ['x', 'y'], f: false })).toBe('/api/x?a=1&s=x&s=y&f=false')
    expect(buildUrl('/api/x')).toBe('/api/x')
  })

  it('marks writes with X-Docbase and sends a JSON body; reads have no marker', async () => {
    const fetch = mockFetch(200, { ok: true })
    await api.post('/api/projects', { code: 'P' })
    const [url, init] = fetch.mock.calls[0]
    expect(url).toBe('/api/projects')
    expect(init.headers.get('X-Docbase')).toBe('1')
    expect(init.headers.get('X-User')).toBeNull()
    expect(init.headers.get('Content-Type')).toBe('application/json')
    expect(JSON.parse(init.body)).toEqual({ code: 'P' })
    await api.get('/api/projects')
    expect(fetch.mock.calls[1][1].headers.get('X-Docbase')).toBeNull()
  })

  it('calls the unauthorized handler on 401, except for the login endpoints', async () => {
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    mockFetch(401, { detail: { code: 'auth.required', message: 'login', params: {} } })
    expect((await failure(api.get('/api/projects'))).status).toBe(401)
    expect(handler).toHaveBeenCalledTimes(1)
    await failure(api.post('/api/auth/login', { username: 'a', password: 'b' }))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('turns business errors into ApiError with the server message', async () => {
    mockFetch(409, { detail: { code: 'project.code_taken', message: 'taken', params: { code: 'P' } } })
    const err = await failure(api.get('/api/projects'))
    expect(err).toBeInstanceOf(ApiError)
    expect([err.status, err.code, err.message, err.params]).toEqual([409, 'project.code_taken', 'taken', { code: 'P' }])
  })

  it('turns validation errors into a readable message', async () => {
    mockFetch(422, { detail: [{ loc: ['body', 'code'], msg: 'Field required' }] })
    const err = await failure(api.post('/api/projects', {}))
    expect([err.code, err.message]).toEqual(['validation', 'code: Field required'])
  })

  it('reports network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('failed')))
    const err = await failure(api.get('/api/projects'))
    expect(err.code).toBe('network')
  })
})
