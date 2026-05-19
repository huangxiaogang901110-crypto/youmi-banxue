import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 360_000,       // 6 min per test (识别+辅导可能慢)
  expect: { timeout: 30_000 },
  retries: 1,
  use: {
    baseURL: 'http://localhost:3001',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        viewport: { width: 430, height: 932 },  // iPhone 14 Pro Max
      },
    },
  ],
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  outputDir: 'test-results/',
});
