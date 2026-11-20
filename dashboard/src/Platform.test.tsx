import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Configuration } from "./Configuration";
import { Settings } from "./Settings";
import { watchTheme } from "./theme";

const first = {
  version: 1,
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  instructions: "Previous instructions",
  created_at: "2026-11-20T01:00:00Z",
  reason: "Seed version",
  proposed_by: "Seed",
  approved_by: "Seed",
  diff: [],
  held_out: null,
};
const second = {
  ...first,
  version: 2,
  model: "Next model",
  reasoning_effort: "medium",
  instructions: "New instructions",
  previous_model: first.model,
  previous_reasoning_effort: first.reasoning_effort,
  diff: ["-Previous instructions", "+New instructions"],
  held_out: {
    accuracy: 0.8,
    grade: 1.85,
    cost_per_claim: 0.001,
    seconds_per_claim: 8,
  },
};
const margins = {
  accuracy: 0.1,
  grade: 0.15,
  cost_per_claim: 0.15,
  seconds_per_claim: 0.15,
};
const settings = {
  approval_limit: 1000,
  budget: 0.5,
  concurrency: 25,
  objectives: { accuracy: 0.6, grade: 1 },
  noise: margins,
  tolerance: { ...margins, cost_per_claim: 1, seconds_per_claim: 0.25 },
};
const settingsView = {
  settings,
  models: [
    {
      model: first.model,
      reasoning_efforts: ["low"],
      input_price: 0.1,
      output_price: 0.5,
    },
  ],
  history: [],
};
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
  };
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

it("selects versions and shows their held-out results, attribution, and instruction changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(Response.json({ current: 2, versions: [first, second] })),
    ),
  );
  render(<Configuration />);
  expect(await screen.findByText("80.0%")).toBeDefined();
  expect(screen.getByText("openai/gpt-6-luna → Next model")).toBeDefined();
  expect(screen.getByLabelText("Text changes").textContent).toContain(
    "-Previous instructions",
  );
  expect(screen.getByText("Awaiting evaluation")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Version 1" }));
  expect(screen.getByText("Previous instructions")).toBeDefined();
});

it("retries a failed configuration read and shows an empty state", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ detail: "Unavailable" }, { status: 503 }),
    )
    .mockResolvedValueOnce(Response.json({ current: 1, versions: [] }));
  vi.stubGlobal("fetch", fetch);
  render(<Configuration />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Reload versions" }),
  );
  expect(
    await screen.findByText(/No configuration versions yet/),
  ).toBeDefined();
});

it("confirms an edited setting and reads its audit history", async () => {
  const updated = { ...settings, approval_limit: 1500 };
  const fetch = vi.fn((_: string, init: RequestInit) =>
    Promise.resolve(
      Response.json(
        init.method === "PUT"
          ? {
              ...settingsView,
              settings: updated,
              history: [
                {
                  created_at: "2026-11-20T12:10:00Z",
                  handler_id: "HND-01",
                  before: settings,
                  after: updated,
                },
              ],
            }
          : settingsView,
      ),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<Settings writable />);
  const limit = await screen.findByRole("spinbutton", {
    name: "Approval limit (USD)",
  });
  fireEvent.change(limit, { target: { value: "1500" } });
  fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
  const dialog = screen.getByRole("dialog");
  expect(dialog.textContent).toContain("1000 → 1500");
  expect(fetch).toHaveBeenCalledTimes(1);
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Confirm changes" }),
  );
  expect(await screen.findByText("Settings saved.")).toBeDefined();
  expect(screen.getByText("HND-01")).toBeDefined();
  const write = fetch.mock.calls.find(([, init]) => init.method === "PUT");
  expect(write?.[1].body).toBe(JSON.stringify(updated));
});

it("protects platform settings while allowing personal appearance changes", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(Response.json(settingsView))),
  );
  render(<Settings writable={false} />);
  expect(await screen.findByText(/Only a senior handler/)).toBeDefined();
  expect(screen.queryByRole("button", { name: "Save settings" })).toBeNull();
  const theme = screen.getByLabelText("Appearance", { exact: true });
  fireEvent.change(theme, { target: { value: "dark" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(theme).toHaveProperty("value", "system");
  fireEvent.change(theme, { target: { value: "dark" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply appearance" }));
  const stop = watchTheme();
  expect(document.documentElement.dataset.theme).toBe("dark");
  stop();
});
