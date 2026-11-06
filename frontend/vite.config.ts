import { defineConfig, loadEnv, type ProxyOptions } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const target = env.BACKEND_URL || 'http://localhost:8000';
  const adminPath = loadEnv(mode, '..', 'ADMIN_PATH').ADMIN_PATH;
  const adminProxy: Record<string, ProxyOptions> = {};
  if (adminPath && /^\/[A-Za-z0-9][A-Za-z0-9_-]{7,63}$/.test(adminPath)) {
    adminProxy[adminPath] = { target, changeOrigin: false, configure(proxy) {
      proxy.on('proxyReq', (outgoing, incoming) => {
        outgoing.setHeader('X-Forwarded-For', incoming.socket.remoteAddress || '127.0.0.1');
        outgoing.setHeader('X-Forwarded-Proto', 'http');
      });
    } };
  }
  // Vite 6.4.5 rejects all Windows paths containing '~' when fs.strict is on,
  // even when the project root is explicitly included in fs.allow.
  const windowsTildePath = process.platform === 'win32' && process.cwd().includes('~');
  return { plugins: [react()], server: { fs: { strict: !windowsTildePath }, proxy: {
    '/api': { target, changeOrigin: true },
    '/ws': { target, changeOrigin: true, ws: true },
    ...adminProxy,
  } } };
});
