import { useCallback, useEffect, useState } from "react";
import { api, message } from "./api";
import type { ClaimSummary, Handler, Wording } from "./types";
import { ClaimQueue } from "./ClaimQueue";
import { Workspace } from "./Workspace";
import { QueueControl } from "./QueueControl";
import "./style.css";

export function App() {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [handlers, setHandlers] = useState<Handler[]>([]);
  const [wording, setWording] = useState<Wording | null>(null);
  const [instructions, setInstructions] = useState("");
  const [selected, setSelected] = useState(
    () =>
      new URLSearchParams(window.location.search).get("claim") ?? "CLM-1001",
  );
  const [tab, setTab] = useState("claims");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const [queueRunning, setQueueRunning] = useState(false);
  const selectClaim = useCallback((claimId: string) => {
    setSelected(claimId);
    window.history.replaceState(
      null,
      "",
      `?claim=${encodeURIComponent(claimId)}`,
    );
  }, []);
  const refreshQueue = useCallback(async () => {
    setClaims(await api<ClaimSummary[]>("/claims"));
  }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      api<ClaimSummary[]>("/claims"),
      api<Handler[]>("/handlers"),
      api<Wording>("/policy-wording"),
      api<{ content: string }>("/agent/instructions"),
    ])
      .then(([queue, people, policy, prompt]) => {
        if (cancelled) return;
        setClaims(queue);
        setHandlers(people);
        setWording(policy);
        setInstructions(prompt.content);
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
              setTab("claims");
            }}
          >
            Claims
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
        <span className="pilot-label">Local pilot · USD</span>
      </header>
      <main>
        <div className="page-heading">
          <div>
            <p className="eyebrow">CLAIMS OPERATIONS</p>
            <h1>{tab === "claims" ? "Claims desk" : "Agent instructions"}</h1>
            <p className="subtitle">
              {tab === "claims"
                ? "Every claim, considered."
                : "The guidance behind every decision."}
            </p>
          </div>
          <span className="team-label">
            UnderCover Agent{" "}
            <span className="availability">Policy-led review</span>
          </span>
        </div>
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        {!ready && !error && <p role="status">Loading claims desk…</p>}
        {ready && tab === "instructions" && (
          <section className="panel instructions">
            <h2>Claims handling instructions</h2>
            <p className="muted">
              Read only. These are the instructions served to the agent.
            </p>
            <pre>{instructions}</pre>
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
              waiting={claims.filter((c) => c.status === "waiting").length}
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
                  handlers={handlers}
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
