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
      // 4 核环境下文件级并发与 OpenAPI 生成子进程争抢调度，导致 Ant 组件测试 5s 超时；实测失败率 35% → 0%。
      fileParallelism: false,
      globals: true,
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
    },
  };
});
