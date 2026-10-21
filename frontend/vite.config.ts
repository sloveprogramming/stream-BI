import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    // 后端端口：8000 被一个残留进程占着时，临时用 8010（见 backend 启动脚本）
    proxy: {
      '/api': process.env.BISQL_API_TARGET || 'http://127.0.0.1:8010',
    },
  },
})
