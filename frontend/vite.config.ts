import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'
import Components from 'unplugin-vue-components/vite'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

// 开发时 /api 转到后端。默认 8001：scripts/test_server.py 启动的、连测试库的后端；
// 连正式库的后端（uvicorn，8000 端口）用时在 frontend/.env.local 写 VITE_API_TARGET=http://localhost:8000
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiProxy = { '/api': { target: env.VITE_API_TARGET || 'http://localhost:8001', changeOrigin: true } }
  return {
    plugins: [
      vue(),
      // Element Plus 组件按需引入；ElMessage 等函数在用到的地方显式 import
      Components({ resolvers: [ElementPlusResolver()], dts: 'src/components.d.ts' }),
    ],
    // 开发服务器：提前把 Element Plus 的组件和样式打包好，避免打开新页面时重排依赖导致页面加载失败
    optimizeDeps: { include: ['element-plus/es', 'element-plus/es/components/*/style/css'] },
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    server: { port: 5173, proxy: apiProxy },
    // 页面测试用打包结果跑（vite preview），同样代理 /api
    preview: { proxy: apiProxy },
    build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
    test: { environment: 'jsdom', include: ['tests/**/*.test.ts'] }, // 页面测试（e2e/）用 Playwright 单独跑
  }
})
