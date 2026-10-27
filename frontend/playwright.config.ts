// 页面自动测试（Playwright）：自己启动连测试库的后端和前端开发服务器，跑完自动关掉。
// 用电脑上已装的 Chrome，不另外下载浏览器。用法：npm run e2e
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from '@playwright/test'

const BACKEND_PORT = 8021
const FRONTEND_PORT = 5193
export const BACKEND_DIR = resolve(fileURLToPath(new URL('.', import.meta.url)), '..', 'backend')
export const PYTHON = resolve(BACKEND_DIR, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
// 存量导入的页面测试在这个临时目录里造源文件（后端和 worker 都只允许导入这里）
export const IMPORT_ROOT = resolve(tmpdir(), 'docbase-e2e-import')
// 共享盘镜像的页面测试用这个临时目录
export const MIRROR_DIR = resolve(tmpdir(), 'docbase-e2e-mirror')
const backend = BACKEND_DIR
const python = PYTHON

export default defineConfig({
  testDir: 'e2e',
  // 跑完把测试建的项目归档，免得测试库的项目列表越来越长
  globalTeardown: './e2e/global-teardown.ts',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    channel: 'chrome',
    headless: true,
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
  },
  webServer: [
    {
      // scripts/test_server.py 会检查测试库配置，不满足就拒绝启动，不会碰正式库
      command: `"${python}" "${resolve(backend, 'scripts', 'test_server.py')}" ${BACKEND_PORT}`,
      url: `http://localhost:${BACKEND_PORT}/api/health`,
      env: { DOCBASE_IMPORT_ROOTS: IMPORT_ROOT, DOCBASE_MIRROR_DIR: MIRROR_DIR },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      // 先打包再用 vite preview 跑：和正式运行的代码一样，也避开开发服务器首次加载时的依赖重排
      command: `npx vite build && npx vite preview --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      env: { VITE_API_TARGET: `http://localhost:${BACKEND_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})

export const API = `http://localhost:${BACKEND_PORT}`
