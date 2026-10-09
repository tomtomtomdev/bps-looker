import { defineConfig, devices } from "@playwright/test";

/**
 * Stack smoke (U7): e2e-stack/ specs against a running web + api + db seeded with the recorded
 * fixtures (`bps seed-fixtures`) — nothing is mocked. `make e2e-stack` brings the compose `ui`
 * profile up and runs this; CI does the same. E2E_BASE_URL defaults to the compose web port.
 */
const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e-stack",
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never", outputFolder: "playwright-report" }]]
    : "list",
  use: { baseURL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "stack-chromium", use: { ...devices["Desktop Chrome"] } }],
});
