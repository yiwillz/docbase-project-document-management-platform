// 页面测试跑完：把这次测试建的项目（编号以 E2E 开头）都归档，项目列表默认就看不到了（勾"显示已归档"才显示）。
// 测试后端在这之后才关；连的是测试库（helpers.newProject 建项目前检查过库名）。
import { request } from '@playwright/test'

import { API as DEFAULT_API } from '../playwright.config'

const API = process.env.E2E_API ?? DEFAULT_API

export default async function globalTeardown(): Promise<void> {
  const ctx = await request.newContext({ baseURL: API, extraHTTPHeaders: { 'X-User': 'e2e-tester', 'X-Docbase': '1' } })
  try {
    const health = await (await ctx.get('/api/health')).json()
    if (!String(health.database).endsWith('_test')) return // 只在测试库上做
    const projects: { id: number; code: string; archived_at: string | null }[] = await (await ctx.get('/api/projects')).json()
    for (const p of projects) {
      if (p.code.startsWith('E2E') && !p.archived_at) await ctx.post(`/api/projects/${p.id}/archive`, { data: {} })
    }
  } catch {
    // 后端已经关了等：不影响测试结果
  } finally {
    await ctx.dispose()
  }
}
