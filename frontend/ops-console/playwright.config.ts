import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:3100",
    locale: "en-GB",
    timezoneId: "Europe/London",
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  webServer: {
    command: "npm run build && npm run start",
    url: "http://localhost:3100/sign-in",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: { CASEWARD_OPS_API_URL: "http://127.0.0.1:9", CASEWARD_DEV_SIGN_IN: "1" },
  },
});
