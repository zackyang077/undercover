import { useCallback, useEffect, useState } from "react";
import { api, message } from "./api";
import type { ClaimSummary, Handler, Wording } from "./types";
import { ClaimQueue } from "./ClaimQueue";
import { Workspace } from "./Workspace";
import { QueueControl } from "./QueueControl";
import { Scoreboard } from "./Scoreboard";
import { SignOut } from "./SignOut";
import "./style.css";

const pageTitles = {
  claims: "Claims desk",
  scoreboard: "Scoreboard",
  instructions: "Agent instructions",
};
type DashboardTab = keyof typeof pageTitles;

function PageHeading({ tab }: Readonly<{ tab: DashboardTab }>) {
  const subtitles = {
    claims: "Every claim, considered.",
    scoreboard: "",
    instructions: "The guidance behind every decision.",
  };
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">CLAIMS OPERATIONS</p>
        <h1>{pageTitles[tab]}</h1>
        {subtitles[tab] && <p className="subtitle">{subtitles[tab]}</p>}
      </div>
      <span className="team-label">
        UnderCover Agent <span className="availability">Policy-led review</span>
      </span>
    </div>
  );
}

export function App({ handler }: Readonly<{ handler: Handler }>) {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [wording, setWording] = useState<Wording | null>(null);
  const [instructions, setInstructions] = useState({ claim: "" });
  const [selected, setSelected] = useState(
    () =>
      new URLSearchParams(window.location.search).get("claim") ?? "CLM-1001",
  );
  const [evaluationId, setEvaluationId] = useState(() =>
    new URLSearchParams(window.location.search).get("evaluation"),
  );
  const [tab, setTab] = useState<DashboardTab>(() => {
    const query = new URLSearchParams(window.location.search);
    if (query.has("evaluation") || query.get("page") === "scoreboard") {
      return "scoreboard";
    }
    return "claims";
  });
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [queueRunning, setQueueRunning] = useState(false);
  const selectClaim = useCallback((claimId: string) => {
    setSelected(claimId);
    setTab("claims");
    window.history.replaceState(
      null,
      "",
      `?claim=${encodeURIComponent(claimId)}`,
    );
  }, []);
  const selectEvaluation = useCallback((runId: string) => {
    setEvaluationId(runId);
    setTab("scoreboard");
    window.history.replaceState(
      null,
      "",
      `?evaluation=${encodeURIComponent(runId)}`,
    );
  }, []);
  const refreshQueue = useCallback(async () => {
    setClaims(await api<ClaimSummary[]>("/claims"));
  }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      api<ClaimSummary[]>("/claims"),
      api<Wording>("/policy-wording"),
      api<{ claim: string }>("/agent/instructions"),
    ])
      .then(([queue, policy, prompt]) => {
        if (cancelled) return;
        setClaims(queue);
        setWording(policy);
        setInstructions(prompt);
        setReady(true);
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(message(failure));
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="UnderCover home">
          <span className="brand-mark">u</span>UnderCover
          <span className="brand-caption">TRAVEL INSURANCE</span>
        </a>
        <nav aria-label="Main navigation">
          <button
            aria-current={tab === "claims" ? "page" : undefined}
            onClick={() => {
              selectClaim(selected);
            }}
          >
            Claims
          </button>
          <button
            aria-current={tab === "scoreboard" ? "page" : undefined}
            onClick={() => {
              setEvaluationId(null);
              setTab("scoreboard");
              window.history.replaceState(null, "", "?page=scoreboard");
            }}
          >
            Scoreboard
          </button>
          <button
            aria-current={tab === "instructions" ? "page" : undefined}
            onClick={() => {
              setTab("instructions");
            }}
          >
            Agent instructions
          </button>
        </nav>
        <div className="handler-menu">
          <span>{handler.name}</span>
          <SignOut onError={setError} />
        </div>
      </header>
      <main>
        <PageHeading tab={tab} />
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        {!ready && !error && <p role="status">Loading claims desk…</p>}
        {tab === "scoreboard" && (
          <Scoreboard
            evaluationId={evaluationId}
            onSelectEvaluation={selectEvaluation}
          />
        )}
        {ready && tab === "instructions" && (
          <section className="panel instructions">
            <h2>Claims handling instructions</h2>
            <p className="muted">
              Read only. These are the instructions served to the agent.
            </p>
            <pre>{instructions.claim}</pre>
          </section>
        )}
        {ready && tab === "claims" && (
          <>
            <section className="metrics" aria-label="Queue overview">
              <div>
                <span>In queue</span>
                <strong>
                  {
                    claims.filter(
                      (c) =>
                        c.status === "waiting" || c.status === "in_progress",
                    ).length
                  }
                </strong>
              </div>
              <div>
                <span>Waiting for sign-off</span>
                <strong>
                  {claims.filter((c) => c.status === "waiting_sign_off").length}
                </strong>
              </div>
              <div>
                <span>Decided</span>
                <strong>
                  {claims.filter((c) => c.status === "decided").length}
                </strong>
              </div>
            </section>
            <QueueControl
              eligible={
                claims.filter(
                  (c) =>
                    c.status === "waiting" ||
                    c.status === "in_progress" ||
                    (c.status === "decided" && c.payment_status === "unpaid"),
                ).length
              }
              onRefresh={refreshQueue}
              onRunningChange={setQueueRunning}
              onSelect={selectClaim}
            />
            <div className="desk">
              <ClaimQueue
                claims={claims}
                selected={selected}
                onSelect={selectClaim}
              />
              {claims.length > 0 && wording && (
                <Workspace
                  key={selected}
                  claimId={selected}
                  handler={handler}
                  wording={wording}
                  onChange={refreshQueue}
                  queueRunning={queueRunning}
                />
              )}
            </div>
          </>
        )}
      </main>
      <footer>
        UnderCover Travel Insurance{" "}
        <span>Simulated payments · All amounts in USD</span>
      </footer>
    </div>
  );
}
