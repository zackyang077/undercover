export type QueueStatus =
  "waiting" | "in_progress" | "waiting_sign_off" | "decided";
export interface ClaimSummary {
  claim_id: string;
  customer_name: string;
  summary: string;
  claim_type: string;
  amount_claimed: number;
  status: QueueStatus;
  outcome: string;
  payout_amount: number | null;
  payment_status: string;
}
export interface Handler {
  handler_id: string;
  name: string;
  can_approve_over_limit: boolean;
}
export interface Decision {
  outcome: "approve" | "decline" | "refer";
  payout_amount: number;
  explanation: string;
  calculation: string;
  policy_references: string[];
}
export interface ClaimDetail {
  claim: {
    claim_id: string;
    summary: string;
    story: string;
    amount_claimed: number;
    claim_type: string;
    submitted_on: string;
    incident_date: string;
    incident_location: string;
    items: { description: string; category: string; amount: number }[];
    documents: { name: string; type: string; content: string }[];
  };
  policy: {
    policy_id: string;
    plan: string;
    purchased_on: string;
    status: string;
    add_ons: string[];
    travelers: string[];
    sold_through: string;
    trip: {
      destination: string;
      departs_on: string;
      returns_on: string;
      trip_cost: number;
    };
  };
  customer: {
    name: string;
    email: string;
    phone: string;
    date_of_birth: string;
    address: Record<string, string>;
    previous_claims: {
      claim_id: string;
      filed_on: string;
      summary: string;
      outcome: string;
      amount_paid: number;
    }[];
  };
  plan: Record<string, number | string>;
  add_ons: Record<string, unknown>[];
  deductible_applies_to: string[];
  approval_limit: number;
  status: QueueStatus;
  decision: Decision | null;
  sign_off: {
    handler_id: string;
    handler_name: string;
    created_at: string;
  } | null;
  payment: { amount: number; created_at: string; simulated: boolean } | null;
}
export interface Run {
  thread_id: string;
  status: string;
  started_at: string;
  error: string;
  events: {
    id: number;
    kind: string;
    tool_name: string;
    text: string;
    created_at: string;
  }[];
}
export interface Wording {
  version: string;
  content: string;
}
