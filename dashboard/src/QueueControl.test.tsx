import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueueControl } from "./QueueControl";
import type { QueueRun } from "./types";

const initial: QueueRun = {
  queue_run_id: "queue-1",
  status: "running",
  stop_requested: false,
  created_at: "2026-11-13T08:00:00Z",
  finished_at: "",
  error: "",
  items: [
    {
      claim_id: "CLM-1001",
      thread_id: "thread-1",
      status: "running",
      error: "",
    },
    { claim_id: "CLM-1002", thread_id: "", status: "pending", error: "" },
  ],
};
const refresh = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
const running = vi.fn<(active: boolean) => void>();
const select = vi.fn<(claimId: string) => void>();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("starts waiting claims with one action, displays progress, and stops after the current claim", async () => {
  let saved: QueueRun | null = null;
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url === "/api/queue/runs") saved = structuredClone(initial);
      if (url.endsWith("/stop") && saved)
        saved = { ...saved, stop_requested: true };
      return Promise.resolve(Response.json(saved));
    }),
  );
  render(
    <QueueControl
      waiting={2}
      onRefresh={refresh}
      onRunningChange={running}
      onSelect={select}
    />,
  );
  const start = screen.getByRole("button", { name: "Start queue (2)" });
  await waitFor(() => {
    expect(start).toHaveProperty("disabled", false);
  });
  fireEvent.click(start);
  expect(await screen.findByText("Processing queue")).toBeDefined();
  expect(screen.getByText("0 / 2 processed")).toBeDefined();
  expect(running).toHaveBeenLastCalledWith(true);
  fireEvent.click(
    screen.getByRole("button", { name: "View current claim: CLM-1001" }),
  );
  expect(select).toHaveBeenLastCalledWith("CLM-1001");
  fireEvent.click(
    screen.getByRole("button", { name: "Stop after current claim" }),
  );
  expect(
    await screen.findByText("Stopping after the current claim finishes."),
  ).toBeDefined();
  saved = {
    ...initial,
    status: "stopped",
    stop_requested: true,
    items: [{ ...initial.items[0], status: "completed" }, initial.items[1]],
  };
  expect(await screen.findByText("Queue stopped")).toBeDefined();
  expect(screen.getByText("1 / 2 processed")).toBeDefined();
  expect(calls.filter((call) => call === "POST /api/queue/runs")).toHaveLength(
    1,
  );
  expect(calls.filter((call) => call.endsWith("/stop"))).toHaveLength(1);
  expect(running).toHaveBeenLastCalledWith(false);
});

it("restores the latest progress and exposes failed and skipped claims", async () => {
  const completed: QueueRun = {
    ...initial,
    status: "completed",
    items: [
      { ...initial.items[0], status: "failed", error: "Provider unavailable" },
      { ...initial.items[1], status: "skipped", error: "Already decided" },
    ],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() => Promise.resolve(Response.json(completed))),
  );
  render(
    <QueueControl
      waiting={0}
      onRefresh={refresh}
      onRunningChange={running}
      onSelect={select}
    />,
  );
  expect(await screen.findByText("Queue completed")).toBeDefined();
  expect(screen.getByText("0 completed · 1 failed · 1 skipped")).toBeDefined();
  fireEvent.click(screen.getByText("Failed claims (1)"));
  expect(screen.getByText("Provider unavailable")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "CLM-1001" }));
  expect(select).toHaveBeenCalledWith("CLM-1001");
  expect(
    screen.getByRole("button", { name: "Start queue (0)" }),
  ).toHaveProperty("disabled", true);
});

it("shows API errors without claiming the queue started", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) =>
      Promise.resolve(
        url === "/api/queue/runs"
          ? Response.json(
              { detail: "The queue is already running" },
              { status: 409 },
            )
          : Response.json(null),
      ),
    ),
  );
  render(
    <QueueControl
      waiting={2}
      onRefresh={refresh}
      onRunningChange={running}
      onSelect={select}
    />,
  );
  const start = screen.getByRole("button", { name: "Start queue (2)" });
  await waitFor(() => {
    expect(start).toHaveProperty("disabled", false);
  });
  fireEvent.click(start);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "The queue is already running",
  );
  expect(screen.queryByText("Processing queue")).toBeNull();
});

it("recovers monitoring after a queue progress fetch failure", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(new Error("Cannot load queue progress"))
      .mockImplementation(() => Promise.resolve(Response.json(null))),
  );
  render(
    <QueueControl
      waiting={2}
      onRefresh={refresh}
      onRunningChange={running}
      onSelect={select}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Cannot load queue progress",
  );
  await waitFor(
    () => {
      expect(screen.queryByRole("alert")).toBeNull();
    },
    { timeout: 2500 },
  );
});
