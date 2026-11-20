import { useEffect, useState } from "react";
import { api, message } from "./api";
import { Confirm, Diff } from "./Review";
import { RemotePanel } from "./RemotePanel";
import { Recipes, Rounds } from "./RoundHistory";
import { RoundChart } from "./RoundScores";
import {
  cost,
  roundStage,
  type ImprovementView,
  type Proposal,
  type Round,
} from "./improvement-types";

function useImprovement() {
  const [data, setData] = useState<ImprovementView>();
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const result = await api<ImprovementView>("/improvement");
        if (active) {
          setData(result);
          setError("");
        }
      } catch (failure) {
        if (active) setError(message(failure));
      }
      if (active)
        timer = setTimeout(() => {
          void poll();
        }, 1200);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [refresh]);
  return {
    data,
    error,
    retry: () => {
      setRefresh((n) => n + 1);
    },
  };
}

function ProposalView({ proposal }: Readonly<{ proposal: Proposal }>) {
  const args = proposal.args;
  const previous =
    typeof proposal.previous === "object" ? proposal.previous : {};
  return (
    <div className="proposal-review">
      <h3>
        {proposal.kind === "configuration"
          ? "Configuration proposal"
          : "Recipe proposal"}
      </h3>
      <p>{args.reason}</p>
      {proposal.kind === "configuration" && (
        <dl className="facts">
          <div>
            <dt>Model</dt>
            <dd>
              {previous.model} → {args.model}
            </dd>
          </div>
          <div>
            <dt>Reasoning effort</dt>
            <dd>
              {previous.reasoning_effort} → {args.reasoning_effort}
            </dd>
          </div>
        </dl>
      )}
      <Diff lines={proposal.diff} />
    </div>
  );
}

type Choice = "approve" | "reject";
interface Operation {
  title: string;
  label: string;
  explanation: string;
  path: string;
  body: Record<string, string>;
}

const verdictLabel = (verdict: Round["verdict"]) =>
  verdict === "kept" ? "Kept" : "Rolled back";

function Control({
  round,
  writable,
  current,
  onSaved,
}: Readonly<{
  round?: Round;
  writable: boolean;
  current: number;
  onSaved: () => void;
}>) {
  const [failed, setFailed] = useState("");
  const [busy, setBusy] = useState(false);
  const [op, setOp] = useState<Operation | null>(null);
  const [reason, setReason] = useState("");
  const prefix = "/improvement/rounds";
  const proposal = round?.proposal;
  function review(choice: Choice) {
    if (!round || !proposal) return;
    const verb = choice === "approve" ? "Approve" : "Reject";
    const effect =
      proposal.kind === "configuration"
        ? "Approval allows the candidate to be evaluated against the current version."
        : "Approval saves this recipe for the next round.";
    setOp({
      title: `${verb} this ${proposal.kind} proposal?`,
      label: `${verb} proposal`,
      explanation: choice === "approve" ? effect : reason,
      path: `${prefix}/${encodeURIComponent(round.id)}/review`,
      body: {
        proposal_id: proposal.id,
        choice,
        reason: choice === "reject" ? reason.trim() : "",
      },
    });
  }
  async function perform(operation: Operation) {
    setOp(null);
    setFailed("");
    setBusy(true);
    try {
      await api(operation.path, operation.body);
      setReason("");
      onSaved();
    } catch (failure) {
      setFailed(message(failure));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel round-control" aria-label="Current round">
      <div className="section-heading">
        <h2>{round ? "Open round" : "Start a round"}</h2>
        {round && (
          <span className={`status round-status ${round.status}`}>
            {roundStage(round)}
          </span>
        )}
      </div>
      {round ? (
        <p>
          Configuration v{round.base_version} · Recipe v{round.recipe_version} ·{" "}
          {cost(round.costs.total)} of {cost(round.settings.budget)} spent
        </p>
      ) : (
        <p>
          Evaluate configuration v{current} and review the improver’s suggested
          change.
        </p>
      )}
      {round?.verdict && (
        <p>
          <strong>{verdictLabel(round.verdict)}.</strong> {round.why}
        </p>
      )}
      {proposal && <ProposalView proposal={proposal} />}
      {!writable && (
        <p className="muted">
          Only a senior handler can start or stop a round and review proposals.
        </p>
      )}
      {writable && (
        <div className="round-actions">
          {proposal && (
            <>
              <button
                className="primary"
                disabled={busy}
                onClick={() => {
                  review("approve");
                }}
              >
                Approve proposal
              </button>
              <label>
                Rejection reason
                <input
                  value={reason}
                  onChange={(event) => {
                    setReason(event.target.value);
                  }}
                  placeholder="Explain the requested change"
                />
              </label>
              <button
                className="secondary"
                disabled={busy || !reason.trim()}
                onClick={() => {
                  review("reject");
                }}
              >
                Reject proposal
              </button>
            </>
          )}
          {round ? (
            <button
              className="secondary"
              disabled={busy}
              onClick={() => {
                setOp({
                  title: "Stop this round?",
                  label: "Stop round",
                  explanation:
                    "Work in progress will stop. Any verdict already recorded will stand.",
                  path: `${prefix}/${encodeURIComponent(round.id)}/stop`,
                  body: {},
                });
              }}
            >
              Stop round
            </button>
          ) : (
            <button
              className="primary"
              disabled={busy}
              onClick={() => {
                setOp({
                  title: "Start an improvement round?",
                  label: "Start round",
                  explanation:
                    "The round uses the current objectives and spending limit.",
                  path: prefix,
                  body: {},
                });
              }}
            >
              Start round
            </button>
          )}
        </div>
      )}
      {failed && (
        <p className="error-banner" role="alert">
          {failed}
        </p>
      )}
      {op && (
        <Confirm
          title={op.title}
          confirmLabel={op.label}
          onCancel={() => {
            setOp(null);
          }}
          onConfirm={() => {
            void perform(op);
          }}
        >
          <p>{op.explanation}</p>
        </Confirm>
      )}
    </section>
  );
}

export function Improvement({ writable }: Readonly<{ writable: boolean }>) {
  const { data, error, retry } = useImprovement();
  if (!data) return <RemotePanel page="rounds" error={error} retry={retry} />;
  const rounds = [...data.rounds].sort((a, b) =>
    b.started_at.localeCompare(a.started_at),
  );
  const current = rounds.find(
    (r) => r.status !== "finished" && r.status !== "stopped",
  );
  return (
    <div className="platform-page">
      {error && (
        <p role="alert" className="error-banner">
          {error} Retrying round updates…
        </p>
      )}
      <Control
        key={current?.proposal?.id ?? current?.id ?? "new"}
        round={current}
        current={data.current_version}
        writable={writable}
        onSaved={retry}
      />
      <section className="panel">
        <h2>Held-out performance</h2>
        <RoundChart rounds={rounds} />
      </section>
      <Rounds rounds={rounds} />
      <Recipes recipes={data.recipes} />
    </div>
  );
}
