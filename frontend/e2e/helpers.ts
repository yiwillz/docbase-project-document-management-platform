// 页面测试共用：直接调后端接口造数据（测试后端认 X-User 请求头，当作系统管理员）、在浏览器里登录
import { type ChildProcess, spawn, spawnSync } from 'node:child_process'

import { type APIRequestContext, type Page, expect } from '@playwright/test'

import { BACKEND_DIR, API as DEFAULT_API, IMPORT_ROOT, MIRROR_DIR, PYTHON } from '../playwright.config'

// 平时连 playwright.config 启动的测试后端；E2E_API 可指向已经开着的测试后端
const API = process.env.E2E_API ?? DEFAULT_API

export const USER = 'e2e-tester'
export const PASSWORD = 'e2e-password'

export async function api<T>(request: APIRequestContext, method: string, path: string, data?: unknown): Promise<T> {
  const resp = await request.fetch(API + path, { method, data, headers: { 'X-User': USER, 'X-Docbase': '1' } })
  expect(resp.ok(), `${method} ${path} → ${resp.status()} ${await resp.text()}`).toBeTruthy()
  return resp.status() === 204 ? (undefined as T) : ((await resp.json()) as T)
}

export async function newProject(request: APIRequestContext, prefix: string): Promise<{ id: number; code: string }> {
  const health = await api<{ database: string }>(request, 'GET', '/api/health')
  expect(health.database.endsWith('_test')).toBeTruthy() // 只在测试库上跑
  const code = `${prefix}-${Date.now().toString().slice(-6)}`
  return api(request, 'POST', '/api/projects', { code, name: `${prefix} project`, client_name: 'Demo Client' })
}

// 建一个用户（已有就重设密码），返回用户 id；不给项目角色
export async function ensureUser(
  request: APIRequestContext,
  username: string,
  password: string,
  isSysadmin = false,
): Promise<number> {
  const users = await api<{ id: number; username: string }[]>(request, 'GET', '/api/users')
  const found = users.find((u) => u.username === username)
  if (found) {
    await api(request, 'POST', `/api/users/${found.id}/password`, { password })
    await api(request, 'PATCH', `/api/users/${found.id}`, { is_sysadmin: isSysadmin, active: true })
    return found.id
  }
  const created = await api<{ id: number }>(request, 'POST', '/api/users', { username, password, is_sysadmin: isSysadmin })
  return created.id
}

// 在浏览器里真正登录（cookie 不分端口，后端端口上登录后前端页面也带着）；默认是系统管理员 e2e-tester
export async function asUser(page: Page, username = USER, password = PASSWORD): Promise<void> {
  let resp = await page.request.post(`${API}/api/auth/login`, { data: { username, password } })
  if (!resp.ok() && username === USER) {
    await api(page.request, 'GET', '/api/auth/me') // 测试后端按 X-User 自动建出这个系统管理员
    await ensureUser(page.request, USER, PASSWORD, true)
    resp = await page.request.post(`${API}/api/auth/login`, { data: { username, password } })
  }
  expect(resp.ok(), `login ${username} → ${resp.status()}`).toBeTruthy()
}

// 启动一个连测试库的后台 worker（存量导入、交付包、镜像都要它）；用完调 stopWorker
export function startWorker(): ChildProcess {
  return spawn(PYTHON, ['scripts/worker.py', '--test'], {
    cwd: BACKEND_DIR,
    env: { ...process.env, DOCBASE_IMPORT_ROOTS: IMPORT_ROOT, DOCBASE_MIRROR_DIR: MIRROR_DIR, PYTHONIOENCODING: 'utf-8' },
    stdio: 'ignore',
  })
}

export function stopWorker(worker: ChildProcess | null): void {
  if (!worker?.pid) return
  // Windows 上虚拟环境的 python.exe 会再起一个子进程：连子进程一起关
  if (process.platform === 'win32') spawnSync('taskkill', ['/PID', String(worker.pid), '/T', '/F'])
  else worker.kill()
}
