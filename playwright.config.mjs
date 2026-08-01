import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./playwright",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: "http://127.0.0.1:8124",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "PERSONA_RESTORER_ROOT=./data/playwright-runtime ./.venv/bin/python -m uvicorn app.asgi:app --host 127.0.0.1 --port 8124",
    url: "http://127.0.0.1:8124/api/health",
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
