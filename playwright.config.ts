import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  workers: 1,
  use: { baseURL: "http://localhost:5174" },
  webServer: [
    {
      command:
        "uv run -m uvicorn backend.tests.notification_server:app --port 8003",
      url: "http://127.0.0.1:8003/health",
      reuseExistingServer: false,
    },
    {
      command: "uv run -m uvicorn backend.tests.browser_server:app --port 8010",
      url: "http://127.0.0.1:8010/api/health",
      env: {
        BACKEND_URL: "http://127.0.0.1:8010",
        UNDERCOVER_AGENT: "http://127.0.0.1:8003",
        DATABASE_URL: "",
        LANGFUSE_SECRET_KEY: "",
        LANGFUSE_PUBLIC_KEY: "",
        OPENROUTER_API_KEY: "",
      },
      reuseExistingServer: false,
    },
    {
      command: "npx vite dashboard --port 5174 --strictPort",
      url: "http://localhost:5174",
      env: { BACKEND_URL: "http://127.0.0.1:8010" },
      reuseExistingServer: false,
    },
  ],
});
