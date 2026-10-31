import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  testMatch: '*.spec.js',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:4187', browserName: 'chromium', channel: 'msedge', headless: true },
  webServer: { command: 'node preview.mjs', url: 'http://127.0.0.1:4187/admin/', env: { PORT: '4187' }, reuseExistingServer: false },
});
