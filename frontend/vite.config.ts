import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '');
  const target = env.BACKEND_URL || 'http://localhost:8000';
  // Vite 6.4.5 rejects all Windows paths containing '~' when fs.strict is on,
  // even when the project root is explicitly included in fs.allow.
  const windowsTildePath = process.platform === 'win32' && process.cwd().includes('~');
  return { plugins: [react()], server: { fs: { strict: !windowsTildePath }, proxy: {
    '/api': { target, changeOrigin: true },
    '/ws': { target, changeOrigin: true, ws: true },
  } } };
});
