import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for HEAXHub frontend.
 *
 * The dev stack (backend + frontend + mailhog) must be running externally:
 *   - frontend:  http://localhost:5173
 *   - backend:   http://localhost:8000
 *   - mailhog:   http://localhost:8025 (HTTP API for token extraction)
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false, // tests share registration state
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
