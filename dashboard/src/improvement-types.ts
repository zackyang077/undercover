export interface Measures {
  accuracy: number | null;
  grade: number | null;
  cost_per_claim: number | null;
  seconds_per_claim: number | null;
}
export interface Proposal {
  id: string;
  kind: "configuration" | "recipe";
  args: {
    reason: string;
    instructions?: string;
    model?: string;
    reasoning_effort?: string;
    recipe?: string;
  };
  previous:
    | {
        model?: string;
        reasoning_effort?: string;
        instructions?: string;
        recipe?: string;
      }
    | string;
  diff: string[];
  created_at: string;
}
export interface Round {
  id: string;
  status:
    | "baseline"
    | "working"
    | "candidate"
    | "configuration_review"
    | "recipe_review"
    | "verdict"
    | "finished"
    | "stopped";
  progress: number;
  base_version: number;
  version: number | null;
  recipe_version: number;
  settings: {
    budget: number;
    concurrency: number;
    objectives: { accuracy: number; grade: number };
    noise: Measures;
    tolerance: Measures;
  };
  reason: string;
  before: Measures | null;
  after: Measures | null;
  verdict: "kept" | "rolled_back" | null;
  why: string;
  started_at: string;
  started_by: string;
  ended_at: string | null;
  stop_reason?: string;
  stopped_by?: string | null;
  proposal: Proposal | null;
  reviews: {
    proposal: Proposal | string;
    choice: "approve" | "reject";
    reason: string;
    handler_id: string;
    created_at: string;
  }[];
  costs: { total: number; improver: number; claim: number; judge: number };
}
interface Recipe {
  version: number;
  recipe: string;
  diff: string[];
  reason: string;
  approved_by: string;
  created_at: string;
}
export interface ImprovementView {
  rounds: Round[];
  recipes: Recipe[];
  current_version: number;
}

const stateLabels: Record<Round["status"], string> = {
  baseline: "Baseline evaluations",
  working: "Improver working",
  candidate: "Candidate evaluations",
  configuration_review: "Configuration awaiting review",
  recipe_review: "Recipe awaiting review",
  verdict: "Verdict recorded",
  finished: "Finished",
  stopped: "Stopped",
};
export function roundStage(round: Round) {
  const text = stateLabels[round.status];
  if (round.status !== "baseline" && round.status !== "candidate") return text;
  return round.progress
    ? `${text} · ${String(round.progress)} of 3`
    : `${text} · preparing`;
}

export function roundVerdict(round: Round) {
  if (round.verdict === "kept") return "Kept";
  if (round.verdict === "rolled_back") return "Rolled back";
  if (round.status === "stopped") return "Stopped";
  if (round.status !== "finished") return roundStage(round);
  return round.version === null ? "No version tried" : "Awaiting verdict";
}

export const cost = (value: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 6,
  }).format(value);
export const dateLabel = (value: string) =>
  new Date(value).toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  });
