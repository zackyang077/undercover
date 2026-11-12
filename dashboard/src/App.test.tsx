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
  override?: (path: string, init?: RequestInit) => Response | undefined,
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
        body = initialQueue.map((c) => ({ ...c, status: detail.status }));
      else if (url === "/api/handlers") body = people;
      else if (url === "/api/policy-wording") body = wording;
      else if (url === "/api/agent/instructions")
        body = { content: "Use the policy wording to decide claims." };
      else if (url.endsWith("/runs")) {
        if (init?.method === "POST") {
          runs = [structuredClone(running)];
          detail.status = "in_progress";
        }
        body = runs;
      } else if (url.includes("/claims/")) body = detail;
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
  render(<App />);
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
  fireEvent.click(screen.getByRole("button", { name: "Agent instructions" }));
  expect(
    screen.getByText("Use the policy wording to decide claims."),
  ).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Claims" }));
  expect(await screen.findByText(fixture.claim.story)).toBeDefined();
});

it("polls an active run and shows a paid decision without reload", async () => {
  setup();
  render(<App />);
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
  expect(screen.getByRole("button", { name: "Start agent" })).toHaveProperty(
    "disabled",
    true,
  );
});

it("records sign-off once and retries a failed payment", async () => {
  let attempts = 0;
  setup((path, init) => {
    if (init?.method !== "POST") return undefined;
    if (path.endsWith("/sign-off")) {
      detail.sign_off = {
        handler_id: "HND-01",
        handler_name: "Dana Whitfield",
        created_at: "now",
      };
      detail.status = "decided";
      return Response.json(detail.sign_off);
    }
    if (path.endsWith("/payment")) {
      attempts++;
      if (attempts === 1)
        return Response.json(
          { detail: "Payment temporarily failed" },
          { status: 409 },
        );
      detail.payment = { amount: 1250, created_at: "now", simulated: true };
      return Response.json(detail.payment);
    }
    return undefined;
  });
  detail.decision = { ...approved, payout_amount: 1250 };
  detail.status = "waiting_sign_off";
  render(<App />);
  const sign = await screen.findByRole("button", { name: "Sign off and pay" });
  expect(sign).toHaveProperty("disabled", true);
  fireEvent.change(screen.getByLabelText("Handler"), {
    target: { value: "HND-01" },
  });
  fireEvent.click(sign);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Payment temporarily failed",
  );
  expect(await screen.findByText("Signed off by Dana Whitfield")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "Record payment" }));
  expect(await screen.findByText("Simulated payment recorded")).toBeDefined();
  expect(requests.filter((path) => path.endsWith("/sign-off"))).toHaveLength(1);
  expect(attempts).toBe(2);
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
    render(<App />);
    const panel = await screen.findByRole("region", { name: "Agent decision" });
    expect(
      within(panel).getByText(outcome === "refer" ? "Refer" : "Decline"),
    ).toBeDefined();
    expect(screen.queryByRole("button", { name: "Record payment" })).toBeNull();
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
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "Retry agent" }));
  expect(
    await screen.findByRole("button", { name: "Agent is working" }),
  ).toBeDefined();
});

it("shows a useful connection error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<App />);
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
  const { unmount } = render(<App />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Claim unavailable",
  );
  unmount();
  setup((path) => (path === "/api/claims" ? Response.json([]) : undefined));
  render(<App />);
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
