import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'ETERNALAI_');
  const backendUrl = env.ETERNALAI_BACKEND_URL || 'http://127.0.0.1:8000';

  return {
    plugins: [react()],
    server: {
      port: 5173,
      open: false,
      proxy: {
        '/api/v1': {
          target: backendUrl,
        },
      },
    },
    test: {
      // 4 核环境下先禁用文件并发，再由 package test 脚本分进程串行 Ant 组件与 OpenAPI 生成，解决 CPU 争抢的两半。
      fileParallelism: false,
      globals: true,
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
    },
  };
});
