import { useEffect, useState } from "react";
import { api, ApiError, label, message, money } from "./api";

type Dataset = "evaluation" | "held_out";
type Size = "full" | "tiny";

interface EvaluationScore {
  claim_count: number;
  worked_count: number;
  decision_accuracy: number | null;
  accuracy: number | null;
  cost_of_mistakes_cents: number;
  straight_through: number | null;
  model_cost: string | null;
  model_cost_per_claim: string | null;
  model_cost_complete: boolean;
  work_ms: number;
  work_ms_per_claim: number | null;
  work_complete: boolean;
  elapsed_ms: number | null;
}

interface EvaluationClaim {
  claim_id: string;
  source_id: string;
  status: string;
  agent_outcome: string | null;
  agent_payout_cents: number | null;
  rationale: string;
  handler_outcome: string;
  handler_payout_cents: number;
  accurate: boolean;
  model_cost: string | null;
  work_ms: number;
}

interface EvaluationRun {
  run_id: string;
  status: "running" | "completed" | "stopped" | "failed";
  concurrency: number;
  dataset: Dataset;
  size: Size;
  model: string;
  reasoning_effort: string | null;
  error: string;
  created_at: string;
  finished_at: string | null;
  score: EvaluationScore | null;
  claims: EvaluationClaim[];
}

type Props = Readonly<{
  evaluationId?: string | null;
  onSelectEvaluation: (id: string) => void;
}>;

const datasetLabels: Record<Dataset, string> = {
  evaluation: "Evaluation set",
  held_out: "Held-out set",
};
const sizeLabels: Record<Size, string> = {
  full: "Full set",
  tiny: "Tiny (5 claims)",
};
const pollMs = 600;
const creationRetries = 8;
const unavailable = "Unavailable";
const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 8,
});
const percent = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});
const seconds = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const dateTime = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

function modelCost(value: string | null, complete = true) {
  if (value === null) return unavailable;
  return usd.format(Number(value)) + (complete ? "" : " (partial)");
}

function duration(value: number | null, complete = true) {
  if (value === null) return unavailable;
  return `${seconds.format(value / 1000)} s${complete ? "" : " (partial)"}`;
}

function percentage(value: number | null) {
  return value === null ? unavailable : percent.format(value);
}

function Measurements({ score }: Readonly<{ score: EvaluationScore }>) {
  const readings = [
    ["Decision accuracy", percentage(score.decision_accuracy)],
    ["Accuracy", percentage(score.accuracy)],
    ["Straight-through rate", percentage(score.straight_through)],
    ["Cost of mistakes", money(score.cost_of_mistakes_cents / 100)],
    [
      "Model cost per claim",
      modelCost(score.model_cost_per_claim, score.model_cost_complete),
    ],
    ["Time per claim", duration(score.work_ms_per_claim, score.work_complete)],
    ["Whole-run time", duration(score.elapsed_ms)],
  ];
  return (
    <dl className="facts">
      {readings.map(([name, value]) => (
        <div key={name}>
          <dt>{name}</dt>
          <dd className="mono">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function StartEvaluation({
  running,
  onSelectEvaluation,
}: Readonly<{
  running: boolean;
  onSelectEvaluation: Props["onSelectEvaluation"];
}>) {
  const [dataset, setDataset] = useState<Dataset>("evaluation");
  const [size, setSize] = useState<Size>("full");
  const [parallel, setParallel] = useState("3");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const concurrency = Number(parallel);
  const valid =
    Number.isInteger(concurrency) && concurrency >= 1 && concurrency <= 25;
  const disabled = running || submitting;

  async function start() {
    if (disabled || !valid) return;
    setSubmitting(true);
    setError("");
    try {
      const { run_id } = await api<{ run_id: string }>("/evaluations", {
        dataset,
        size,
        concurrency,
      });
      onSelectEvaluation(run_id);
    } catch (failure) {
      setError(message(failure));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      className="panel queue-control"
      aria-label="Start an evaluation"
      onSubmit={(event) => {
        event.preventDefault();
        void start();
      }}
    >
      <div className="run-options">
        <label>
          Dataset
          <select
            value={dataset}
            disabled={disabled}
            onChange={(event) => {
              setDataset(event.target.value as Dataset);
            }}
          >
            <option value="evaluation">{datasetLabels.evaluation}</option>
            <option value="held_out">{datasetLabels.held_out}</option>
          </select>
        </label>
        <label>
          Claims to evaluate
          <select
            value={size}
            disabled={disabled}
            onChange={(event) => {
              setSize(event.target.value as Size);
            }}
          >
            <option value="full">{sizeLabels.full}</option>
            <option value="tiny">{sizeLabels.tiny}</option>
          </select>
        </label>
        <label>
          Claim agents
          <input
            type="number"
            min={1}
            max={25}
            step={1}
            required
            value={parallel}
            disabled={disabled}
            onChange={(event) => {
              setParallel(event.target.value);
            }}
          />
        </label>
      </div>
      <button type="submit" className="primary" disabled={disabled || !valid}>
        {submitting ? "Starting evaluation" : "Start evaluation"}
      </button>
      {error && (
        <p role="alert" className="error-banner">
          {error}
        </p>
      )}
    </form>
  );
}

function EvaluationSettings({ run }: Readonly<{ run: EvaluationRun }>) {
  return (
    <>
      <p>
        <strong>{label(run.status)}</strong> · {run.concurrency} claim agents
      </p>
      <dl className="facts">
        <div>
          <dt>Model</dt>
          <dd>{run.model}</dd>
        </div>
        <div>
          <dt>Reasoning effort</dt>
          <dd>{run.reasoning_effort ?? "None"}</dd>
        </div>
        {run.finished_at && (
          <div>
            <dt>Finished</dt>
            <dd>
              <time dateTime={run.finished_at}>
                {dateTime.format(new Date(run.finished_at))}
              </time>
            </dd>
          </div>
        )}
      </dl>
      {run.status === "stopped" && (
        <p>
          Partial results
          {run.score &&
            ` · ${String(run.score.worked_count)} of ${String(run.score.claim_count)} claims worked`}
        </p>
      )}
      {run.error && (
        <p role="alert" className="error-banner">
          {run.error}
        </p>
      )}
    </>
  );
}

function Comparison({
  claim,
  scored,
}: Readonly<{ claim: EvaluationClaim; scored: boolean }>) {
  const agentOutcome = claim.agent_outcome
    ? label(claim.agent_outcome)
    : "No decision";
  const payout =
    claim.agent_payout_cents === null
      ? null
      : money(claim.agent_payout_cents / 100);
  return (
    <li className="panel queue-control">
      <div className="section-heading">
        <a
          href={`?claim=${encodeURIComponent(claim.claim_id)}`}
          className="queue-current mono"
        >
          {claim.source_id}
        </a>
        {scored && !claim.accurate && (
          <strong className="queue-failure">Mismatch</strong>
        )}
      </div>
      <p className="muted">{label(claim.status)}</p>
      <dl className="facts">
        <div>
          <dt>Handler</dt>
          <dd>
            {label(claim.handler_outcome)} ·{" "}
            {money(claim.handler_payout_cents / 100)}
          </dd>
        </div>
        <div>
          <dt>Agent</dt>
          <dd>
            {agentOutcome}
            {payout && ` · ${payout}`}
          </dd>
        </div>
        <div>
          <dt>Model cost</dt>
          <dd>{modelCost(claim.model_cost)}</dd>
        </div>
        <div>
          <dt>Active work</dt>
          <dd>{duration(claim.work_ms)}</dd>
        </div>
      </dl>
      {claim.rationale && <p className="decision-reason">{claim.rationale}</p>}
    </li>
  );
}

function EvaluationDetail({ run }: Readonly<{ run: EvaluationRun }>) {
  return (
    <>
      <section className="panel queue-control" aria-label="Evaluation summary">
        <h2>
          {datasetLabels[run.dataset]} · {sizeLabels[run.size]}
        </h2>
        <p className="muted">
          Started{" "}
          <time dateTime={run.created_at}>
            {dateTime.format(new Date(run.created_at))}
          </time>
        </p>
        <EvaluationSettings run={run} />
        {run.score && <Measurements score={run.score} />}
      </section>
      <section aria-label="Evaluation claims">
        <h2>Claim comparisons</h2>
        <ul className="queue-list">
          {run.claims.map((claim) => (
            <Comparison
              key={claim.claim_id}
              claim={claim}
              scored={run.score !== null}
            />
          ))}
        </ul>
      </section>
    </>
  );
}

function History({ runs }: Readonly<{ runs: EvaluationRun[] }>) {
  if (!runs.length) return <p className="empty">No evaluations yet.</p>;
  return (
    <ul className="queue-list" aria-label="Evaluations">
      {runs.map((run) => (
        <li key={run.run_id} className="panel queue-control">
          <h2>
            <a href={`?evaluation=${encodeURIComponent(run.run_id)}`}>
              {datasetLabels[run.dataset]} · {sizeLabels[run.size]} ·{" "}
              <time dateTime={run.created_at}>
                {dateTime.format(new Date(run.created_at))}
              </time>
            </a>
          </h2>
          <EvaluationSettings run={run} />
          {run.score && <Measurements score={run.score} />}
        </li>
      ))}
    </ul>
  );
}

interface View {
  evaluationId: string | null;
  runs: EvaluationRun[];
  error: string;
}

function useEvaluations(evaluationId: string | null) {
  const [view, setView] = useState<View | null>(null);
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let misses = 0;
    let watchingRun = false;
    async function refresh() {
      try {
        const runs = evaluationId
          ? [
              await api<EvaluationRun>(
                `/evaluations/${encodeURIComponent(evaluationId)}`,
              ),
            ]
          : await api<EvaluationRun[]>("/evaluations");
        if (cancelled) return;
        setView({ evaluationId, runs, error: "" });
        watchingRun = runs.some((run) => run.status === "running");
        if (watchingRun) later();
      } catch (failure) {
        if (cancelled) return;
        const awaitingCreation =
          evaluationId &&
          failure instanceof ApiError &&
          failure.status === 404 &&
          misses < creationRetries;
        if (awaitingCreation) {
          misses += 1;
          later();
          return;
        }
        setView((previous) => ({
          evaluationId,
          runs: previous?.evaluationId === evaluationId ? previous.runs : [],
          error: message(failure),
        }));
        if (watchingRun) later();
      }
    }
    function later() {
      timer = setTimeout(() => {
        void refresh();
      }, pollMs);
    }
    void refresh();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [evaluationId]);
  return view?.evaluationId === evaluationId ? view : null;
}

export function Scoreboard({ evaluationId = null, onSelectEvaluation }: Props) {
  const view = useEvaluations(evaluationId);
  const run = view?.runs[0];
  return (
    <>
      {evaluationId ? (
        <p>
          <a href="?page=scoreboard" className="queue-current">
            Back to Scoreboard
          </a>
        </p>
      ) : (
        <StartEvaluation
          running={
            view?.runs.some((item) => item.status === "running") ?? false
          }
          onSelectEvaluation={onSelectEvaluation}
        />
      )}
      {!view && <p role="status">Loading evaluations…</p>}
      {view?.error && (
        <p role="alert" className="error-banner">
          {view.error}
        </p>
      )}
      {evaluationId
        ? run && <EvaluationDetail run={run} />
        : view &&
          (!view.error || view.runs.length > 0) && <History runs={view.runs} />}
    </>
  );
}
