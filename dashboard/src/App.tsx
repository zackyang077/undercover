import { useCallback, useEffect, useState } from "react";
import { api, message } from "./api";
import type { ClaimSummary, Handler, Wording } from "./types";
import { ClaimQueue } from "./ClaimQueue";
import { Workspace } from "./Workspace";
import { QueueControl } from "./QueueControl";
import { Scoreboard } from "./Scoreboard";
import { SignOut } from "./SignOut";
import { Configuration } from "./Configuration";
import { Improvement } from "./Improvement";
import { Settings } from "./Settings";
import { HandlerNames } from "./handler-names";
import { usePageData } from "./usePageData";
import "./style.css";
import "./platform.css";
import "./round.css";

const pageTitles = {
  claims: "Claims desk",
  copy: "Evaluation claim",
  scoreboard: "Scoreboard",
  configuration: "Configuration",
  improvement: "Improvement",
  settings: "Settings",
};
type DashboardTab = keyof typeof pageTitles;

function initialTab(): DashboardTab {
  const query = new URLSearchParams(window.location.search);
  if (query.get("claim")?.startsWith("EVAL-")) return "copy";
  if (query.has("evaluation") || query.get("page") === "scoreboard") {
    return "scoreboard";
  }
  const page = query.get("page");
  return page === "configuration" ||
    page === "settings" ||
    page === "improvement"
    ? page
    : "claims";
}

function QueueOverview({ claims }: Readonly<{ claims: ClaimSummary[] }>) {
  return (
    <section className="metrics" aria-label="Queue overview">
      <div>
        <span>In queue</span>
        <strong>
          {
            claims.filter(
              (c) => c.status === "waiting" || c.status === "in_progress",
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
        <strong>{claims.filter((c) => c.status === "decided").length}</strong>
      </div>
    </section>
  );
}
function PageHeading({ tab }: Readonly<{ tab: DashboardTab }>) {
  const subtitles = {
    claims:
      "Review claim evidence, agent decisions, and payouts awaiting sign-off.",
    copy: "Read-only claim evidence and the decision recorded for this evaluation.",
    scoreboard: "Compare evaluation results with handler decisions.",
    configuration:
      "Review the claim agent’s instructions, model, and version history.",
    improvement:
      "Review proposed changes and compare held-out performance across rounds.",
    settings:
      "Manage payout approvals, improvement objectives, and spending limits.",
  };
  return (
    <div className="page-heading">
      <div>
        <h1>{pageTitles[tab]}</h1>
        {subtitles[tab] && <p className="subtitle">{subtitles[tab]}</p>}
      </div>
    </div>
  );
}

export function App({ handler }: Readonly<{ handler: Handler }>) {
  const { data } = usePageData<Handler[]>("/handlers");
  const names = new Map(
    (data ?? [handler]).map((person) => [person.handler_id, person]),
  );
  return (
    <HandlerNames.Provider value={names}>
      <Dashboard handler={handler} />
    </HandlerNames.Provider>
  );
}

type NavTab = Exclude<DashboardTab, "copy">;
function Header({
  tab,
  navigate,
  name,
  onError,
}: Readonly<{
  tab: DashboardTab;
  navigate: (page: NavTab) => void;
  name: string;
  onError: (text: string) => void;
}>) {
  const selected = tab === "copy" ? "scoreboard" : tab;
  return (
    <header className="topbar">
      <a className="brand" href="/" aria-label="UnderCover home">
        <span className="brand-mark">u</span>UnderCover
        <span className="brand-caption">TRAVEL INSURANCE</span>
      </a>
      <nav aria-label="Main navigation">
        {(
          [
            "claims",
            "scoreboard",
            "configuration",
            "improvement",
            "settings",
          ] as const
        ).map((page) => (
          <button
            key={page}
            aria-current={page === selected ? "page" : undefined}
            onClick={() => {
              navigate(page);
            }}
          >
            {page === "claims" ? "Claims" : pageTitles[page]}
          </button>
        ))}
      </nav>
      <div className="handler-menu">
        <span>{name}</span>
        <SignOut onError={onError} />
      </div>
    </header>
  );
}
function Dashboard({ handler }: Readonly<{ handler: Handler }>) {
  const [claims, setClaims] = useState<ClaimSummary[]>([]);
  const [wording, setWording] = useState<Wording | null>(null);
  const [selected, setSelected] = useState(
    () =>
      new URLSearchParams(window.location.search).get("claim") ?? "CLM-1001",
  );
  const [evaluationId, setEvaluationId] = useState(() =>
    new URLSearchParams(window.location.search).get("evaluation"),
  );
  const [tab, setTab] = useState<DashboardTab>(initialTab);
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
  function navigate(page: NavTab) {
    if (page === "claims") {
      selectClaim(
        selected.startsWith("EVAL-")
          ? (claims[0]?.claim_id ?? "CLM-1001")
          : selected,
      );
      return;
    }
    setEvaluationId(null);
    setTab(page);
    window.history.replaceState(null, "", "?page=" + page);
  }
  const refreshQueue = useCallback(async () => {
    setClaims(await api<ClaimSummary[]>("/claims"));
  }, []);
  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      api<ClaimSummary[]>("/claims"),
      api<Wording>("/policy-wording"),
    ])
      .then(([queue, policy]) => {
        if (cancelled) return;
        setClaims(queue);
        setWording(policy);
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
      <Header
        tab={tab}
        navigate={navigate}
        name={handler.name}
        onError={setError}
      />
      <main>
        <PageHeading tab={tab} />
        {error && (
          <p role="alert" className="error-banner">
            {error}
          </p>
        )}
        {["claims", "copy"].includes(tab) && !ready && !error && (
          <p role="status">Loading {pageTitles[tab].toLowerCase()}…</p>
        )}
        {tab === "copy" && wording && (
          <Workspace
            key={selected}
            claimId={selected}
            handler={handler}
            wording={wording}
            onChange={refreshQueue}
            queueRunning={false}
          />
        )}
        {tab === "scoreboard" && (
          <Scoreboard
            evaluationId={evaluationId}
            onSelectEvaluation={selectEvaluation}
          />
        )}
        {tab === "configuration" && <Configuration />}
        {tab === "improvement" && (
          <Improvement writable={handler.can_approve_over_limit} />
        )}
        {tab === "settings" && (
          <Settings writable={handler.can_approve_over_limit} />
        )}
        {ready && tab === "claims" && (
          <>
            <QueueOverview claims={claims} />
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
