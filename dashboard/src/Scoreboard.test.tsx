import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Scoreboard } from "./Scoreboard";

const completed = {
  run_id: "evaluation-1",
  status: "completed",
  concurrency: 3,
  dataset: "evaluation",
  size: "full",
  model: "openai/gpt-6-luna",
  reasoning_effort: "low",
  error: "",
  created_at: "2026-11-19T10:12:00Z",
  finished_at: "2026-11-19T10:12:13Z",
  score: {
    grade: 1.42,
    judge_cost: "0.00005",
    claim_count: 5,
    worked_count: 4,
    decision_accuracy: 0.8,
    accuracy: 0.6,
    cost_of_mistakes_cents: 125_000,
    straight_through: 0.4,
    model_cost: "0.00241",
    model_cost_per_claim: "0.000482",
    model_cost_complete: false,
    work_ms: 18_000,
    work_ms_per_claim: 3_600,
    work_complete: false,
    elapsed_ms: 13_000,
  },
  claims: [
    {
      grade: 1.42,
      letter: "F",
      same_grounds: 0.9,
      same_section: 0.3,
      claim_id: "EVAL-abc",
      source_id: "CLM-1001",
      status: "completed",
      agent_outcome: "approve",
      agent_payout_cents: 125_000,
      rationale: "Receipts support the documented expenses.",
      handler_outcome: "decline",
      handler_payout_cents: 0,
      accurate: false,
      model_cost: "0.00241",
      work_ms: 4_000,
    },
    {
      claim_id: "EVAL-def",
      source_id: "CLM-1002",
      status: "pending",
      agent_outcome: null,
      agent_payout_cents: null,
      rationale: "",
      handler_outcome: "refer",
      handler_payout_cents: 0,
      accurate: true,
      model_cost: null,
      work_ms: 0,
    },
  ],
};
const stopped = {
  ...completed,
  run_id: "evaluation-2",
  status: "stopped",
  dataset: "held_out",
  reasoning_effort: null,
  size: "tiny",
  created_at: "2026-11-19T10:30:00Z",
  finished_at: "2026-11-19T10:30:13Z",
};
const select = vi.fn<(id: string) => void>();

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

it("starts the chosen evaluation and sends only the required inputs", async () => {
  const fetch = vi.fn((url: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === "POST"
        ? Response.json({ run_id: "evaluation-3" }, { status: 202 })
        : Response.json([]),
    ),
  );
  vi.stubGlobal("fetch", fetch);
  render(<Scoreboard onSelectEvaluation={select} />);

  expect(await screen.findByText("No evaluations yet.")).toBeDefined();
  const agents = screen.getByRole<HTMLInputElement>("spinbutton", {
    name: "Claim agents",
  });
  const start = screen.getByRole("button", { name: "Start evaluation" });
  expect(agents.value).toBe("3");
  fireEvent.change(agents, { target: { value: "26" } });
  expect(start).toHaveProperty("disabled", true);
  fireEvent.change(agents, { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText("Dataset"), {
    target: { value: "held_out" },
  });
  fireEvent.change(screen.getByLabelText("Claims to evaluate"), {
    target: { value: "tiny" },
  });
  fireEvent.click(start);

  expect(fetch).toHaveBeenCalledWith("/api/evaluations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dataset: "held_out", size: "tiny", concurrency: 5 }),
  });
  expect(await screen.findByText("Start evaluation")).toBeDefined();
  expect(select).toHaveBeenCalledWith("evaluation-3");
});

it("shows the service's start error and leaves the selector usable", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) =>
      Promise.resolve(
        init?.method === "POST"
          ? Response.json(
              { detail: "An evaluation or dispatch is running." },
              { status: 409 },
            )
          : Response.json([]),
      ),
    ),
  );
  render(<Scoreboard onSelectEvaluation={select} />);
  const start = screen.getByRole("button", { name: "Start evaluation" });
  fireEvent.click(start);

  expect((await screen.findByRole("alert")).textContent).toBe(
    "An evaluation or dispatch is running.",
  );
  expect(start).toHaveProperty("disabled", false);
  expect(select).not.toHaveBeenCalled();
});

it("shows newest-first evaluation links and summary metrics", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json([stopped, completed])),
  );
  render(<Scoreboard onSelectEvaluation={select} />);

  const history = within(
    await screen.findByRole("list", { name: "Evaluations" }),
  );
  expect(
    history.getAllByRole("link").map((link) => link.getAttribute("href")),
  ).toEqual(["?evaluation=evaluation-2", "?evaluation=evaluation-1"]);
  expect(history.getByText("Stopped")).toBeDefined();
  expect(history.getByText("Completed")).toBeDefined();
  expect(history.getAllByText("Decision accuracy")).toHaveLength(2);
  expect(history.getAllByText("$0.000482 (partial)")).toHaveLength(2);
  expect(history.getAllByText("3.6 s (partial)")).toHaveLength(2);
});

it("polls while an evaluation is running and shows its final scores", async () => {
  vi.stubGlobal(
    "fetch",
    vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          ...completed,
          status: "running",
          finished_at: null,
          score: null,
        }),
      )
      .mockResolvedValueOnce(Response.json(completed)),
  );
  render(
    <Scoreboard evaluationId="evaluation-1" onSelectEvaluation={select} />,
  );

  expect(await screen.findByText("Running")).toBeDefined();
  expect(screen.queryByText("Decision accuracy")).toBeNull();
  expect(await screen.findByText("Decision accuracy")).toBeDefined();
  expect(screen.getByText("80%")).toBeDefined();
  expect(screen.getByText("F · 1.42 / 3")).toBeDefined();
  expect(screen.getByText("Yes")).toBeDefined();
  expect(screen.getByText("No")).toBeDefined();
});

it("retries a newly accepted detail, then shows stopped results and claim links", async () => {
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(
      Response.json({ detail: "Run not found" }, { status: 404 }),
    )
    .mockResolvedValueOnce(Response.json(stopped));
  vi.stubGlobal("fetch", fetch);
  render(
    <Scoreboard evaluationId="evaluation-2" onSelectEvaluation={select} />,
  );

  expect(screen.getByRole("status").textContent).toBe("Loading evaluations…");
  expect(
    await screen.findByText("Partial results · 4 of 5 claims worked"),
  ).toBeDefined();
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(fetch).toHaveBeenCalledWith("/api/evaluations/evaluation-2", {
    headers: {},
  });
  expect(screen.getByText("None")).toBeDefined();
  expect(screen.getByText("Mismatch")).toBeDefined();
  expect(screen.getByText("No decision")).toBeDefined();
  expect(screen.getByText("Decline · $0.00")).toBeDefined();
  expect(screen.getByText("Approve · $1,250.00")).toBeDefined();
  expect(
    screen.getByText("Receipts support the documented expenses."),
  ).toBeDefined();
  expect(
    screen.getByRole("link", { name: "CLM-1001" }).getAttribute("href"),
  ).toBe("?claim=EVAL-abc");
  expect(
    screen
      .getByRole("link", { name: "Back to Scoreboard" })
      .getAttribute("href"),
  ).toBe("?page=scoreboard");
});
