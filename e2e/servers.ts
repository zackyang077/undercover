import { env } from "node:process";

const offset = Number(env.UNDERCOVER_E2E_PORT_OFFSET ?? "0");
if (!Number.isInteger(offset) || offset < 0 || offset > 55_000) {
  throw new Error(
    "UNDERCOVER_E2E_PORT_OFFSET must be an integer from 0 to 55000",
  );
}

export const standInPort = 8003 + offset;
export const backendPort = 8010 + offset;
export const dashboardPort = 5174 + offset;
export const standInUrl = `http://127.0.0.1:${String(standInPort)}`;
export const backendUrl = `http://127.0.0.1:${String(backendPort)}`;
export const dashboardUrl = `http://127.0.0.1:${String(dashboardPort)}`;
export const fixturePassword = "local-fixture-pass";
