import { useCallback, useEffect, useState } from "react";
import { api, label, message, money } from "./api";
import type { ClaimSummary, Handler, Wording } from "./types";
import { Workspace } from "./Workspace";
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
            <div className="desk">
              <aside className="panel queue" aria-label="Claims queue">
                <div className="section-heading">
                  <h2>Claims queue</h2>
                  <span className="count">{claims.length}</span>
                </div>
                <p className="muted queue-caption">Select a claim to review</p>
                <div className="queue-list">
                  {claims.map((claim) => (
                    <button
                      className={`queue-row ${selected === claim.claim_id ? "selected" : ""}`}
                      key={claim.claim_id}
                      aria-pressed={selected === claim.claim_id}
                      onClick={() => {
                        setSelected(claim.claim_id);
                        window.history.replaceState(
                          null,
                          "",
                          `?claim=${encodeURIComponent(claim.claim_id)}`,
                        );
                      }}
                    >
                      <span className="queue-meta">
                        <span className="mono">{claim.claim_id}</span>
                        <strong className="mono">
                          {money(claim.amount_claimed)}
                        </strong>
                      </span>
                      <strong className="customer-name">
                        {claim.customer_name}
                      </strong>
                      <span className="queue-summary">{claim.summary}</span>
                      <span className={`status ${claim.status}`}>
                        {label(
                          claim.status === "waiting_sign_off"
                            ? "waiting for sign-off"
                            : claim.status,
                        )}
                      </span>
                    </button>
                  ))}
                </div>
                {claims.length === 0 && (
                  <p className="empty">No claims in the queue.</p>
                )}
              </aside>
              {claims.length > 0 && wording && (
                <Workspace
                  key={selected}
                  claimId={selected}
                  handlers={handlers}
                  wording={wording}
                  onChange={refreshQueue}
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
