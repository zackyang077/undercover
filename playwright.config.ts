import { defineConfig } from "@playwright/test";
import {
  fixturePassword,
  backendPort,
  backendUrl,
  dashboardPort,
  dashboardUrl,
  standInPort,
  standInUrl,
} from "./e2e/servers";

const env = {
  E2E_LOGIN_PASSWORD: fixturePassword,
  BACKEND_URL: backendUrl,
  UNDERCOVER_AGENT: standInUrl,
  VITE_SUPABASE_URL: standInUrl,
  VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_browser_fixture",
  UNDERCOVER_TEST_ORIGIN: dashboardUrl,
  DATABASE_URL: "",
  LANGFUSE_SECRET_KEY: "",
  LANGFUSE_PUBLIC_KEY: "",
  OPENROUTER_API_KEY: "",
  MODEL_NAME: "",
};

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/test-results",
  reporter: "list",
  workers: 1,
  use: { baseURL: dashboardUrl },
  webServer: [
    {
      command: `uv run -m uvicorn backend.tests.notification_server:app --port ${String(standInPort)}`,
      url: `${standInUrl}/health`,
      env,
      reuseExistingServer: false,
    },
    {
      command: `uv run -m uvicorn backend.tests.browser_server:app --port ${String(backendPort)}`,
      url: `${backendUrl}/api/health`,
      env,
      reuseExistingServer: false,
    },
    {
      command: `npx vite dashboard --host 127.0.0.1 --mode test --port ${String(dashboardPort)} --strictPort`,
      url: dashboardUrl,
      env,
      reuseExistingServer: false,
    },
  ],
});
