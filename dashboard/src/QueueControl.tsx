import { useEffect, useState } from "react";
import { ApiError, api, label, message, money } from "./api";

export interface DispatchRun {
  run_id: string;
  status: "running" | "completed" | "stopped" | "failed";
  concurrency: number;
  claims: {
    claim_id: string;
    status: string;
    outcome: string | null;
    payout_amount: number | null;
  }[];
  summary: {
    approved: number;
    declined: number;
    referred: number;
    waiting_sign_off: { claim_id: string; payout_amount: number }[];
    needs_handler: string[];
    in_progress: string[];
  } | null;
  model_cost: string | null;
  model_cost_complete: boolean;
  elapsed_ms: number | null;
  error: string;
}

type Props = Readonly<{
  eligible: number;
  onRefresh: () => Promise<void>;
  onRunningChange: (running: boolean) => void;
  onSelect: (claimId: string) => void;
}>;

const numberOfAgents = Array.from({ length: 25 }, (_, index) => index + 1);
const costFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 8,
});
const secondsFormat = new Intl.NumberFormat("en-US", {
  style: "unit",
  unit: "second",
  maximumFractionDigits: 2,
});
const claimStatuses: Record<string, string> = {
  needs_handler: "Left for a handler",
  waiting_sign_off: "Waiting for sign-off",
};
const outcomeLabels: Record<string, string> = {
  approve: "Approved",
  decline: "Declined",
  refer: "Referred",
};

function Summary({
  summary,
}: Readonly<{ summary: NonNullable<DispatchRun["summary"]> }>) {
  const waiting = summary.waiting_sign_off.map(
    ({ claim_id, payout_amount }) => `${claim_id} (${money(payout_amount)})`,
  );
  return (
    <div className="run-summary">
      <p>
        {summary.approved} approved · {summary.declined} declined ·{" "}
        {summary.referred} referred
      </p>
      {waiting.length > 0 && <p>Waiting for sign-off · {waiting.join(", ")}</p>}
      {summary.needs_handler.length > 0 && (
        <p>Left for a handler · {summary.needs_handler.join(", ")}</p>
      )}
      {summary.in_progress.length > 0 && (
        <p>Still in progress · {summary.in_progress.join(", ")}</p>
      )}
    </div>
  );
}

function ClaimStatus({
  claim,
  onSelect,
}: Readonly<{
  claim: DispatchRun["claims"][number];
  onSelect: Props["onSelect"];
}>) {
  return (
    <li className={`dispatch-claim-${claim.status}`}>
      <a
        className="queue-current mono"
        href={`?claim=${encodeURIComponent(claim.claim_id)}`}
        onClick={(event) => {
          if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
            return;
          event.preventDefault();
          onSelect(claim.claim_id);
        }}
      >
        {claim.claim_id}
      </a>
      {claim.outcome && (
        <span>
          {outcomeLabels[claim.outcome] ?? label(claim.outcome)}
          {claim.outcome === "approve" &&
            claim.payout_amount !== null &&
            ` · ${money(claim.payout_amount)}`}
        </span>
      )}
      <strong>{claimStatuses[claim.status] ?? label(claim.status)}</strong>
    </li>
  );
}

function Progress({
  run,
  onSelect,
}: Readonly<{ run: DispatchRun; onSelect: Props["onSelect"] }>) {
  const total = run.claims.length;
  const finished = run.claims.filter((claim) =>
    ["decided", "waiting_sign_off", "needs_handler", "skipped"].includes(
      claim.status,
    ),
  ).length;
  const cost =
    run.model_cost === null
      ? "Unavailable"
      : costFormat.format(Number(run.model_cost));
  const elapsed =
    run.elapsed_ms === null ? "—" : secondsFormat.format(run.elapsed_ms / 1000);
  return (
    <div className="queue-progress">
      <div className="queue-progress-title" role="status">
        <strong>Dispatch {run.status}</strong>
        <span>
          {run.concurrency}{" "}
          {run.concurrency === 1 ? "claim agent" : "claim agents"}
        </span>
      </div>
      <p className="muted">
        Model cost {cost}
        {run.model_cost !== null && !run.model_cost_complete && " (partial)"} ·
        Elapsed time {elapsed}
      </p>
      {total > 0 && (
        <>
          <p className="mono">
            {finished} / {total} processed
          </p>
          <progress
            aria-label="Dispatch progress"
            max={total}
            value={finished}
          />
        </>
      )}
      <ol className="dispatch-claims" aria-label="Dispatch claims">
        {run.claims.map((claim) => (
          <ClaimStatus key={claim.claim_id} claim={claim} onSelect={onSelect} />
        ))}
      </ol>
      {run.summary && <Summary summary={run.summary} />}
      {run.error && <p className="queue-failure">{run.error}</p>}
    </div>
  );
}

function useDispatchMonitor(
  onRefresh: Props["onRefresh"],
  onRunningChange: Props["onRunningChange"],
) {
  const [run, setRun] = useState<DispatchRun | null>(null);
  const [watch, setWatch] = useState<{ runId: string | null }>({ runId: null });
  const [loaded, setLoaded] = useState(false);
  const [fetchError, setFetchError] = useState("");

  useEffect(() => {
    let cancelled = false;
    let seenRun = false;
    let timer: ReturnType<typeof setTimeout>;
    const path = `/dispatch/${watch.runId === null ? "current" : encodeURIComponent(watch.runId)}`;

    async function poll() {
      try {
        const [next] = await Promise.all([
          api<DispatchRun | null>(path),
          onRefresh(),
        ]);
        if (cancelled) return;
        seenRun = next !== null;
        setRun(next);
        setLoaded(true);
        setFetchError("");
        onRunningChange(next?.status === "running");
        if (next?.status === "running")
          timer = setTimeout(() => void poll(), 700);
      } catch (failure) {
        if (cancelled) return;
        if (
          watch.runId !== null &&
          !seenRun &&
          failure instanceof ApiError &&
          failure.status === 404
        ) {
          timer = setTimeout(() => void poll(), 700);
          return;
        }
        setFetchError(message(failure));
        timer = setTimeout(() => void poll(), 1500);
      }
    }
    void poll();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [watch, onRefresh, onRunningChange]);

  function follow(runId: string | null) {
    if (runId !== null) setRun(null);
    setWatch({ runId });
  }

  const pending = watch.runId !== null && run?.run_id !== watch.runId;
  return { run, pending, loaded, fetchError, follow };
}

export function QueueControl({
  eligible,
  onRefresh,
  onRunningChange,
  onSelect,
}: Props) {
  const [concurrency, setConcurrency] = useState(3);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const { run, pending, loaded, fetchError, follow } = useDispatchMonitor(
    onRefresh,
    onRunningChange,
  );

  async function start() {
    setStarting(true);
    setStartError("");
    try {
      const accepted = await api<{ run_id: string }>("/dispatch", {
        concurrency,
      });
      follow(accepted.run_id);
      onRunningChange(true);
    } catch (failure) {
      setStartError(message(failure));
      if (failure instanceof ApiError && failure.status === 409) follow(null);
    } finally {
      setStarting(false);
    }
  }

  const running = run?.status === "running";
  const busy = starting || pending || running;
  return (
    <section className="panel queue-control" aria-label="Queue processing">
      <div className="run-options">
        <label>
          Claim agents at once
          <select
            value={running ? run.concurrency : concurrency}
            disabled={busy}
            onChange={(event) => {
              setConcurrency(Number(event.target.value));
            }}
          >
            {numberOfAgents.map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="queue-control-heading">
        <div>
          <h2>Process the queue</h2>
          {eligible === 0 && !busy && (
            <p className="muted">No claims ready for dispatch.</p>
          )}
        </div>
        <button
          className="primary"
          disabled={!loaded || busy || eligible === 0}
          onClick={() => void start()}
        >
          Start dispatch
        </button>
      </div>
      {(starting || pending) && <p role="status">Starting dispatch…</p>}
      {(startError || fetchError) && (
        <p role="alert" className="error-banner">
          {startError || fetchError}
        </p>
      )}
      {run && <Progress run={run} onSelect={onSelect} />}
    </section>
  );
}
