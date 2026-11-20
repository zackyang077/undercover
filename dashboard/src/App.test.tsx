import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  approved,
  fixture,
  initialQueue,
  people,
  running,
  wording,
} from "../test-data";
import { api, message } from "./api";
import { App } from "./App";
import type { ClaimDetail, Run } from "./types";

let detail: ClaimDetail;
let runs: Run[];
const requests: string[] = [];

function setup(
  override?: (
    path: string,
    init?: RequestInit,
  ) => Promise<Response> | Response | undefined,
) {
  detail = structuredClone(fixture);
  runs = [];
  requests.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      requests.push(`${init?.method ?? "GET"} ${url}`);
      const custom = override?.(url, init);
      if (custom) return Promise.resolve(custom);
      let body: unknown = {};
      if (url === "/api/claims")
        body = initialQueue.map((c) => ({
          ...c,
          status: detail.status,
          outcome: detail.decision?.outcome ?? "",
          payout_amount: detail.decision?.payout_amount ?? null,
          payment_status: detail.payment ? "paid" : "unpaid",
        }));
      else if (url === "/api/policy-wording") body = wording;
      else if (url === "/api/configuration")
        body = {
          current: 1,
          versions: [
            {
              version: 1,
              instructions: "Use the policy wording to decide claims.",
              created_at: "2026-11-18",
              diff: [],
            },
          ],
        };
      else if (url === "/api/dispatch/current") body = null;
      else if (url.endsWith("/runs")) {
        if (init?.method === "POST") {
          runs = [structuredClone(running)];
          detail.status = "in_progress";
        }
        body = runs;
      } else if (url.endsWith("/settlement"))
        body = {
          status: detail.status,
          sign_off: detail.sign_off,
          payment: detail.payment,
        };
      else if (url.includes("/claims/")) body = detail;
      return Promise.resolve(Response.json(body));
    }),
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("shows the complete claim and read-only instructions", async () => {
  setup();
  render(<App handler={people[0]} />);
  expect(await screen.findByText(fixture.claim.story)).toBeDefined();
  fireEvent.click(screen.getByRole("button", { pressed: true }));
  expect(window.location.search).toBe("?claim=CLM-1001");
  expect(screen.getByText("Airport hotel")).toBeDefined();
  fireEvent.click(screen.getByText("Carrier notice"));
  expect(
    screen.getByText("The flight was canceled by the airline."),
  ).toBeDefined();
  fireEvent.click(screen.getByText("Policy and plan"));
  expect(screen.getByText("POL-2001 · Premium")).toBeDefined();
  fireEvent.click(screen.getByText("Customer and claim history"));
  expect(screen.getByText("Baggage delay in Lisbon")).toBeDefined();
  expect(screen.getAllByText("$420.00").length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
  expect(
    await screen.findByText("Use the policy wording to decide claims."),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Claims" }));
  expect(await screen.findByText(fixture.claim.story)).toBeDefined();
});

it("polls an active run and shows a paid decision without reload", async () => {
  setup();
  render(<App handler={people[0]} />);
  fireEvent.click(await screen.findByRole("button", { name: "Start agent" }));
  expect(
    await screen.findByRole("button", { name: "Agent is working" }),
  ).toHaveProperty("disabled", true);
  expect(screen.getByText("Read claim")).toBeDefined();
  detail.decision = approved;
  detail.payment = { amount: 420, created_at: "now", simulated: true };
  detail.status = "decided";
  runs = [
    {
      ...running,
      status: "completed",
      events: [
        {
          ...running.events[0],
          id: 2,
          kind: "tool_result",
          text: "Completed successfully",
        },
      ],
    },
  ];
  expect(await screen.findByText("Simulated payment recorded")).toBeDefined();
  expect(screen.getByText(approved.calculation)).toBeDefined();
  expect(screen.getByText("Policy sections 3, 10")).toBeDefined();
  const result = screen.getByRole("region", { name: "Agent decision" });
  expect(
    within(result).getByRole("heading", { name: "Claim approved" }),
  ).toBeDefined();
  expect(within(result).getByText("$420.00", { exact: true })).toBeDefined();
  const queue = screen.getByRole("complementary", { name: "Claims queue" });
  expect(await within(queue).findByText("Approved · $420.00")).toBeDefined();
  expect(within(queue).getByText("Paid (simulated)")).toBeDefined();
  expect(screen.queryByRole("button", { name: "Start agent" })).toBeNull();
});

it("signs once, then follows the agent payment across reopening the claim", async () => {
  const signOffBody = vi.fn();
  const signedAt = "2026-11-18T02:30:00Z";
  setup((path, init) => {
    if (init?.method === "POST" && path.endsWith("/sign-off")) {
      signOffBody(init.body);
      detail.sign_off = {
        handler_id: "HND-01",
        handler_name: "Dana Whitfield",
        created_at: signedAt,
      };
      detail.status = "in_progress";
      return Response.json(detail);
    }
  });
  detail.decision = { ...approved, payout_amount: 1250 };
  detail.status = "waiting_sign_off";
  const { unmount } = render(<App handler={people[0]} />);
  const sign = await screen.findByRole("button", { name: "Sign off" });
  expect(screen.queryByLabelText("Handler")).toBeNull();
  fireEvent.click(sign);
  const signed = await screen.findByText(/Signed off by Dana Whitfield/);
  expect(signOffBody).toHaveBeenCalledWith("{}");
  expect(signed.querySelector("time")?.dateTime).toBe(signedAt);
  expect(
    screen.getByText("Payment pending", { selector: "strong" }),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();

  unmount();
  render(<App handler={people[0]} />);
  expect(await screen.findByText(/Signed off by Dana Whitfield/)).toBeDefined();
  expect(screen.getAllByText("In progress").length).toBeGreaterThan(0);
  expect(screen.queryByRole("button", { name: "Record payment" })).toBeNull();

  detail.payment = {
    amount: 1250,
    created_at: "2026-11-18T02:30:02Z",
    simulated: true,
  };
  detail.status = "decided";
  expect(await screen.findByText("Simulated payment recorded")).toBeDefined();
  expect(await screen.findByText("Paid (simulated)")).toBeDefined();
  expect(requests.filter((path) => path.endsWith("/sign-off"))).toHaveLength(1);
  expect(requests.filter((path) => path.endsWith("/payment"))).toHaveLength(0);
  // One evidence read per mount; payment polling only reads settlement.
  expect(
    requests.filter((path) => path === "GET /api/claims/CLM-1001"),
  ).toHaveLength(2);
  expect(requests).toContain("GET /api/claims/CLM-1001/settlement");
  expect(screen.getByText(fixture.claim.story)).toBeDefined();
});

it("shows a paid claim while the activity log and refreshed queue are still loading", async () => {
  let firstList = true;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  setup((path) => {
    if (path.endsWith("/CLM-1001/runs"))
      return pending.then(() => Response.json([running]));
    if (path === "/api/claims") {
      if (!firstList) return pending.then(() => Response.json(initialQueue));
      firstList = false;
    }
  });
  detail.decision = approved;
  detail.status = "in_progress";
  render(<App handler={people[0]} />);
  expect(await screen.findByText(fixture.claim.story)).toBeDefined();
  detail.status = "decided";
  detail.payment = {
    amount: 420,
    created_at: "2026-11-18T10:00:00Z",
    simulated: true,
  };
  expect(await screen.findByText("Simulated payment recorded")).toBeDefined();
  release();
  expect(await screen.findByText("Read claim")).toBeDefined();
});

it.each(["decline", "refer"] as const)(
  "shows %s without payment controls",
  async (outcome) => {
    setup();
    detail.decision = { ...approved, outcome, payout_amount: 0 };
    detail.status = "decided";
    detail.customer.previous_claims = [];
    detail.policy.add_ons = ["electronics_plus"];
    detail.add_ons = [
      { name: "Electronics Plus", effect: "Raises item limit" },
    ];
    render(<App handler={people[0]} />);
    const panel = await screen.findByRole("region", { name: "Agent decision" });
    expect(
      within(panel).getByText(outcome === "refer" ? "Refer" : "Decline"),
    ).toBeDefined();
    expect(screen.queryByRole("button", { name: "Record payment" })).toBeNull();
    expect(within(panel).queryByText("Approved payout")).toBeNull();
    expect(screen.getByText("No previous claims.")).toBeDefined();
  },
);

it("retains failed activity and offers an explicit retry", async () => {
  setup();
  runs = [
    {
      ...running,
      status: "failed",
      error: "Timed out",
      events: [
        {
          ...running.events[0],
          tool_name: "",
          kind: "failed",
          text: "Timed out",
        },
      ],
    },
  ];
  render(<App handler={people[0]} />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry agent" }));
  expect(
    await screen.findByRole("button", { name: "Agent is working" }),
  ).toBeDefined();
});

it("shows a useful connection error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App handler={people[0]} />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "offline",
  );
});

it("shows a claim fetch error and an empty queue", async () => {
  setup((path) =>
    path === "/api/claims/CLM-1001"
      ? Response.json({ detail: "Claim unavailable" }, { status: 404 })
      : undefined,
  );
  const { unmount } = render(<App handler={people[0]} />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Claim unavailable",
  );
  unmount();
  setup((path) => (path === "/api/claims" ? Response.json([]) : undefined));
  render(<App handler={people[0]} />);
  expect(await screen.findByText("No claims in the queue.")).toBeDefined();
});

it("normalizes non-string API errors", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(Response.json({ detail: [] }, { status: 422 })),
  );
  await expect(api("/claims")).rejects.toThrow("Request failed");
  expect(message("unknown")).toBe("Connection failed. Please try again.");
  await waitFor(() => {
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

it("shows technical handler needs and read-only claim instructions", async () => {
  setup();
  detail.status = "needs_handler";
  render(<App handler={people[0]} />);
  expect(
    await screen.findByText(
      /Left for a handler after two unsuccessful attempts/,
    ),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Start agent" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
  expect(screen.queryByText("Queue instructions")).toBeNull();
  expect(
    await screen.findByText("Use the policy wording to decide claims."),
  ).toBeDefined();
});

it("explains a held payout to a handler without sign-off permission", async () => {
  setup();
  detail.decision = { ...approved, payout_amount: 1250 };
  detail.status = "waiting_sign_off";
  render(
    <App
      handler={{
        ...people[0],
        name: "Marcus Oyelaran",
        can_approve_over_limit: false,
      }}
    />,
  );
  expect(
    await screen.findByText(/A senior handler must sign off/),
  ).toBeDefined();
  expect(screen.queryByRole("button", { name: "Sign off" })).toBeNull();
});
