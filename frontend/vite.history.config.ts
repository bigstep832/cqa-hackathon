import { defineConfig, mergeConfig } from 'vitest/config';
import base from './vite.config';

// Test-only proxy: C permits POST origins on 5173/8000, not this isolated 5174.
// Rewrite Origin only on the server-to-server hop; never alter browser assets.
// The normal development proxy and backend boundary remain unchanged.
const proxy = { target: 'http://127.0.0.1:8001', headers: { Origin: 'http://127.0.0.1:5173' } };
export default mergeConfig(base, defineConfig({
  server: { port: 5174, proxy: { '/api': proxy, '/health': proxy } },
}));
