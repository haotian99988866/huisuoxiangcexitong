import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  const apiProxy = {
    '/api': {
      target: process.env.VITE_DEV_PROXY_TARGET
        || env.VITE_DEV_PROXY_TARGET
        || 'http://127.0.0.1:8000',
      changeOrigin: true,
      rewrite: path => path.replace(/^\/api/, '')
    }
  }

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: apiProxy
    },
    // 预览模式用于临时隧道对外提供构建后的页面
    preview: {
      port: 4173,
      host: '127.0.0.1',
      allowedHosts: ['.trycloudflare.com', 'app.haotianzhushou.com'],
      proxy: apiProxy
    }
  }
})