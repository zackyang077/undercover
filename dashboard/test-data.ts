import type {
  ClaimDetail,
  ClaimSummary,
  Handler,
  Run,
  Wording,
} from "./src/types";

export const fixture: ClaimDetail = {
  claim: {
    claim_id: "CLM-1001",
    summary: "Flight home canceled",
    story: "My flight was canceled because of crew availability.",
    amount_claimed: 420,
    claim_type: "trip_delay",
    submitted_on: "2026-06-24",
    incident_date: "2026-06-18",
    incident_location: "Rome, Italy",
    items: [
      { description: "Airport hotel", category: "accommodation", amount: 420 },
    ],
    documents: [
      {
        name: "Carrier notice",
        type: "carrier_report",
        content: "The flight was canceled by the airline.",
      },
    ],
  },
  policy: {
    policy_id: "POL-2001",
    plan: "premium",
    purchased_on: "2026-04-22",
    status: "active",
    add_ons: [],
    travelers: ["Maya Patel"],
    sold_through: "Airline checkout",
    trip: {
      destination: "Rome",
      departs_on: "2026-06-10",
      returns_on: "2026-06-18",
      trip_cost: 3100,
    },
  },
  customer: {
    name: "Maya Patel",
    email: "maya@example.com",
    phone: "555-0100",
    date_of_birth: "1988-03-14",
    address: { city: "Jersey City" },
    previous_claims: [
      {
        claim_id: "CLM-0412",
        filed_on: "2024-08-19",
        summary: "Baggage delay in Lisbon",
        outcome: "approved",
        amount_paid: 140,
      },
    ],
  },
  plan: {
    plan: "premium",
    name: "Premium",
    deductible: 50,
    trip_delay_waiting_hours: 6,
  },
  add_ons: [],
  deductible_applies_to: ["baggage", "emergency_medical"],
  approval_limit: 1000,
  status: "waiting",
  decision: null,
  sign_off: null,
  payment: null,
};
export const approved = {
  outcome: "approve" as const,
  payout_amount: 420,
  explanation: "The delay is covered. Receipts support the costs.",
  calculation: "420 eligible = 420 payable",
  policy_references: ["3", "10"],
};
export const people: Handler[] = [
  {
    handler_id: "HND-01",
    name: "Dana Whitfield",
    email: "dana.whitfield@undercover.example",
    can_approve_over_limit: true,
  },
];
export const wording: Wording = {
  version: "2026.1",
  content: "Full policy wording, sections 1 through 12.",
};
export const initialQueue: ClaimSummary[] = [
  {
    claim_id: "CLM-1001",
    customer_name: "Maya Patel",
    summary: "Flight home canceled",
    claim_type: "trip_delay",
    amount_claimed: 420,
    status: "waiting",
    outcome: "",
    payout_amount: null,
    payment_status: "not_applicable",
  },
];
export const running: Run = {
  thread_id: "thread-1",
  status: "running",
  started_at: "2026-11-12T08:00:00Z",
  error: "",
  events: [
    {
      id: 1,
      kind: "tool_started",
      tool_name: "read_claim",
      text: "Calling read_claim",
      created_at: "2026-11-12T08:00:00Z",
    },
  ],
};
