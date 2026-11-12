import react from "@vitejs/plugin-react";
import { env } from "node:process";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": env.BACKEND_URL ?? "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    coverage: {
      include: ["src/**"],
      exclude: ["src/main.tsx"],
      thresholds: { statements: 80 },
    },
  },
});
