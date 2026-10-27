import { defineConfig, devices } from "@playwright/test";

const API = "http://localhost:8001";
const WEB = "http://localhost:3100";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: WEB, trace: "retain-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // The API with a fake tutor and an in-memory store, so no database or model is needed.
      command: "uv run python -m tests.e2e_server",
      cwd: "../backend",
      url: "http://localhost:8001/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npx next dev --port 3100",
      url: WEB,
      env: { NEXT_PUBLIC_API_URL: API, NEXT_TELEMETRY_DISABLED: "1" },
      reuseExistingServer: false,
      timeout: 180_000,
    },
  ],
});
