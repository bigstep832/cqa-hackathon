import { defineConfig, devices } from '@playwright/test';

const python = process.env.CQA_TEST_PYTHON ?? (process.platform === 'win32' ? '../.venv/Scripts/python.exe' : '../.venv/bin/python');
export default defineConfig({
  testDir: './tests/integration',
  outputDir: './test-results/history-integration',
  fullyParallel: false, workers: 1, retries: 0, forbidOnly: !!process.env.CI, reporter: 'list',
  use: {
    ...devices['Desktop Chrome'], baseURL: 'http://127.0.0.1:5174',
    channel: process.env.CQA_BROWSER_CHANNEL, trace: 'retain-on-failure', screenshot: 'only-on-failure',
  },
  webServer: [
    { command: `"${python}" -B tests/support/history_backend.py`, url: 'http://127.0.0.1:8001/health', reuseExistingServer: false, timeout: 30000 },
    { command: 'npm run dev -- --config vite.history.config.ts', url: 'http://127.0.0.1:5174', reuseExistingServer: false, timeout: 30000 },
  ],
});
