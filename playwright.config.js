import { defineConfig, devices } from '@playwright/test';
export default defineConfig({
  testDir: './browser-tests', workers: 1, timeout: 120000,
  forbidOnly: !!process.env.CI, retries: 0,
  use: {baseURL: 'http://localhost:8095', trace: 'retain-on-failure', screenshot: 'only-on-failure'},
  projects: [{name: 'chromium', use: {...devices['Desktop Chrome']}}],
});
