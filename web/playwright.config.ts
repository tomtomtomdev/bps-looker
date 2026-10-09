import { defineConfig, devices } from "@playwright/test";

/**
 * E2E specs in e2e/ (`make e2e` builds first). Runs against `pnpm start` on :3000 unless
 * E2E_BASE_URL points at a running app. Specs mock the read API (E2E_API_URL, default
 * http://localhost:8000) with route interception. The real-stack smoke is
 * playwright.stack.config.ts (e2e-stack/, `pnpm e2e:stack`).
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL, trace: "on-first-retry" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : { command: "pnpm start", url: baseURL, reuseExistingServer: !process.env.CI },
});
