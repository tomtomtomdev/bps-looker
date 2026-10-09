import { defineConfig, devices } from "@playwright/test";

/**
 * E2E scaffold: specs land in e2e/ from U2 on (`make e2e`). Runs against `pnpm start` on :3000
 * (build first) unless E2E_BASE_URL points at a running app, e.g. the compose stack (U7).
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
