import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueueControl, type DispatchRun } from "./QueueControl";

const running: DispatchRun = {
  run_id: "dispatch-1",
  status: "running",
  concurrency: 25,
  claims: [
    {
      claim_id: "CLM-1001",
      status: "in_progress",
      outcome: null,
      payout_amount: null,
    },
    {
      claim_id: "CLM-1002",
      status: "waiting",
      outcome: null,
      payout_amount: null,
    },
  ],
  summary: null,
  model_cost: null,
  model_cost_complete: false,
  elapsed_ms: null,
  error: "",
};
const completed: DispatchRun = {
  ...running,
  status: "completed",
  claims: [
    {
      claim_id: "CLM-1001",
      status: "waiting_sign_off",
      outcome: "approve",
      payout_amount: 1250,
    },
    {
      claim_id: "CLM-1002",
      status: "needs_handler",
      outcome: null,
      payout_amount: null,
    },
    {
      claim_id: "CLM-1003",
      status: "in_progress",
      outcome: null,
      payout_amount: null,
    },
  ],
  summary: {
    approved: 1,
    declined: 0,
    referred: 0,
    waiting_sign_off: [{ claim_id: "CLM-1001", payout_amount: 1250 }],
    needs_handler: ["CLM-1002"],
    in_progress: ["CLM-1003"],
  },
  model_cost: "0.0042",
  model_cost_complete: false,
  elapsed_ms: 2460,
};
const refresh = vi.fn<() => Promise<void>>().mockResolvedValue(undefined);
const runningChange = vi.fn<(active: boolean) => void>();
const select = vi.fn<(claimId: string) => void>();

function renderControl(eligible = 3) {
  render(
    <QueueControl
      eligible={eligible}
      onRefresh={refresh}
      onRunningChange={runningChange}
      onSelect={select}
    />,
  );
}

async function startButton() {
  const button = screen.getByRole("button", { name: "Start dispatch" });
  await waitFor(() => {
    expect(button).toHaveProperty("disabled", false);
  });
  return button;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("starts a dispatch at the selected concurrency and waits for its record", async () => {
  let recordReads = 0;
  let saved = running;
  const fetch = vi.fn((url: string) => {
    if (url === "/api/dispatch")
      return Promise.resolve(
        Response.json({ run_id: running.run_id }, { status: 202 }),
      );
    if (url === `/api/dispatch/${running.run_id}`) {
      recordReads += 1;
      return Promise.resolve(
        recordReads === 1
          ? Response.json({ detail: "Record not found" }, { status: 404 })
          : Response.json(saved),
      );
    }
    return Promise.resolve(Response.json(null));
  });
  vi.stubGlobal("fetch", fetch);
  renderControl();
  const agents = screen.getByRole("combobox", { name: "Claim agents at once" });
  expect(agents).toHaveProperty("value", "3");
  const options = screen.getAllByRole("option");
  expect(options).toHaveLength(25);
  expect(options[0]).toHaveProperty("value", "1");
  expect(options.at(-1)).toHaveProperty("value", "25");
  fireEvent.change(agents, { target: { value: "25" } });
  const start = await startButton();
  fireEvent.click(start);
  expect(await screen.findByText("Starting dispatch…")).toBeDefined();
  expect(start).toHaveProperty("disabled", true);
  expect(screen.queryByRole("alert")).toBeNull();
  expect(fetch).toHaveBeenCalledWith(
    "/api/dispatch",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ concurrency: 25 }),
    }),
  );

  expect(await screen.findByText("Dispatch running")).toBeDefined();
  expect(screen.getByText("0 / 2 processed")).toBeDefined();
  expect(screen.getByText("In progress", { exact: true })).toBeDefined();
  expect(runningChange).toHaveBeenLastCalledWith(true);
  const claim = screen.getByRole("link", { name: "CLM-1001" });
  expect(claim.getAttribute("href")).toBe("?claim=CLM-1001");
  fireEvent.click(claim);
  expect(select).toHaveBeenCalledWith("CLM-1001");

  saved = completed;
  expect(await screen.findByText("Dispatch completed")).toBeDefined();
  expect(runningChange).toHaveBeenLastCalledWith(false);
});

it("restores the latest summary, cost and time with no eligible claims", async () => {
  const fetch = vi.fn().mockResolvedValue(Response.json(completed));
  vi.stubGlobal("fetch", fetch);
  renderControl(0);
  expect(await screen.findByText("Dispatch completed")).toBeDefined();
  expect(fetch).toHaveBeenCalledWith("/api/dispatch/current", { headers: {} });
  expect(
    screen.getByText("1 approved · 0 declined · 0 referred"),
  ).toBeDefined();
  expect(
    screen.getByText("Waiting for sign-off · CLM-1001 ($1,250.00)"),
  ).toBeDefined();
  expect(screen.getByText("Approved · $1,250.00")).toBeDefined();
  expect(screen.getByText("Left for a handler · CLM-1002")).toBeDefined();
  expect(screen.getByText("Still in progress · CLM-1003")).toBeDefined();
  expect(
    screen.getByText("Model cost $0.0042 (partial) · Elapsed time 2.46 sec"),
  ).toBeDefined();
  expect(
    screen.getByRole("progressbar", { name: "Dispatch progress" }),
  ).toHaveProperty("value", 2);
  expect(screen.getByRole("button", { name: "Start dispatch" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("displays a running dispatch after reload and refreshes its stopped state", async () => {
  let saved = { ...running, concurrency: 1 };
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.resolve(Response.json(saved))),
  );
  renderControl();
  expect(await screen.findByText("Dispatch running")).toBeDefined();
  expect(screen.getByText("1 claim agent")).toBeDefined();
  expect(
    screen.getByRole("combobox", { name: "Claim agents at once" }),
  ).toHaveProperty("value", "1");
  expect(screen.getByRole("button", { name: "Start dispatch" })).toHaveProperty(
    "disabled",
    true,
  );
  saved = { ...saved, status: "stopped", error: "The agent service restarted" };
  expect(await screen.findByText("Dispatch stopped")).toBeDefined();
  expect(screen.getByText("The agent service restarted")).toBeDefined();
  expect(runningChange).toHaveBeenLastCalledWith(false);
});

it.each([
  [409, "A dispatch or evaluation is already running"],
  [503, "The agent service is unavailable"],
])(
  "shows a %s start error without claiming the dispatch started",
  async (status, detail) => {
    const fetch = vi.fn((url: string) =>
      Promise.resolve(
        url === "/api/dispatch"
          ? Response.json({ detail }, { status })
          : Response.json(null),
      ),
    );
    vi.stubGlobal("fetch", fetch);
    renderControl();
    fireEvent.click(await startButton());
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      detail,
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/dispatch",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ concurrency: 3 }),
      }),
    );
    expect(runningChange).not.toHaveBeenCalledWith(true);
    expect(screen.queryByText("Dispatch running")).toBeNull();
  },
);

it("retries a failed progress fetch", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockRejectedValueOnce(new Error("Cannot load dispatch progress"))
      .mockImplementation(() => Promise.resolve(Response.json(null))),
  );
  renderControl();
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Cannot load dispatch progress",
  );
  await waitFor(
    () => {
      expect(screen.queryByRole("alert")).toBeNull();
    },
    { timeout: 2500 },
  );
  expect(screen.getByRole("button", { name: "Start dispatch" })).toHaveProperty(
    "disabled",
    false,
  );
});
