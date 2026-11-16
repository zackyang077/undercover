Phase 3 connects fresh claim helpers to section lookup and recorded section text,
separate claim/queue instructions, bounded model retries, deadlines, and the new
DeepSeek default. Scripted manual scenarios and lost-payment-response recovery
pass without model calls.

## Harness implementation progress

Phase 1 adds typed All/capped queue requests, persisted to-dos, atomic cap admission,
claim locks, and a two-failure handler state. Existing databases upgrade without
reseeding; old pilot failures do not consume the new attempt budget. Phase 2 adds versioned manual/wording section lookup, validated manual references,
decision provenance, and deduplicated platform claim history. Python checks pass
with 68 tests and 98.62% coverage. Section-read logging is connected in phase 3;
agent-led execution and automatic retry follow.

# Status

The Week 1 Day 3 claims pilot is implemented locally. All six phases have code
and automated acceptance coverage. The remaining validation limits are listed below.

## Built

- FastAPI read APIs serve the 25 queue claims, full evidence, linked policies,
  plans/add-ons, customer history, handlers, policy wording, and agent instructions.
- SQLite seeds once and persists decisions, sign-offs, simulated payments, runs,
  and activity. Imports use an explicit allowlist; evaluation and manual files
  are never loaded. No arbitrary business-file access is exposed.
- The backend requires sign-off for approved payouts greater than $1,000.
  Exactly $1,000 does not require it. Payment is a separate operation requiring
  the full approved amount; transactions block duplicate and concurrent payments.
- `agent.main.run(claim_id, thread_id)` uses LangChain `create_agent` with
  backend-only tools. Instructions and policy wording come from the API.
  The agent has no sign-off tool and cannot target another claim through its tools.
- Background runs publish ordered tool activity. Failed attempts can be retried
  explicitly before a decision. A restart marks unfinished runs Interrupted;
  there is no durable execution or automatic resumption.
- The responsive dashboard shows the queue, complete claim evidence, decision
  explanation/calculation/references, activity, and read-only instructions.
  Handler sign-off and simulated payment use separate API calls. A failed payment
  after sign-off can be retried without another sign-off. The selected claim
  survives reload through the URL. Brand fonts are bundled locally.
- Approved claims show a full-width result card above evidence and activity,
  with a prominent approval heading, approved payout, and simulated payment
  state before the reason and calculation. The queue shows the approved payout
  and payment/sign-off status. Claimed amounts are labeled separately, and the
  Start agent action is removed once a decision has been recorded.
- Start queue processes all currently Waiting claims sequentially with the
  existing agent. Progress, failures, and the current claim are shown above
  the queue. Stop after current claim leaves the remaining claims Waiting.
  Duplicate starts are blocked; refreshing restores progress. See
  [QUEUE_RUN.md](QUEUE_RUN.md) for the behavior and API contract.

## Run

Use Python 3.12, uv, and Node 26. From the repository root:

```text
uv sync
npm install
npm run dev
```

- Dashboard: http://localhost:5173
- Backend health: http://127.0.0.1:8000/api/health
- API documentation: http://127.0.0.1:8000/docs

Keep `OPENROUTER_API_KEY` in `.env`. The current local configuration is
`MODEL_NAME=x-ai/grok-4.7`, using Grok 4.7 through OpenRouter. Model overrides
do not force a reasoning setting. If `MODEL_NAME` is unset, the course fallback
is `openai/gpt-6-luna` with low reasoning effort. Restart the backend after
changing `.env`.

`UNDERCOVER_DB` selects the SQLite file; the default is `undercover.sqlite3` in
this directory, ignored by git. `BACKEND_URL` selects the agent's HTTP backend
and the Vite API proxy; its default is `http://127.0.0.1:8000`.
These environment variables can be set before launching the process. Keep the
normal database when restarting to retain the pilot's records.

Select a Waiting claim and choose Start agent. Follow the activity until the
agent records approve, decline, or refer. For a payout over $1,000, choose an
eligible handler and Sign off and pay. Selecting a handler records attribution;
there is no sign-in in this pilot. All payments are simulated.

To process the Waiting queue, choose Start queue (N). It uses the configured
model for each captured claim, one at a time. Review progress and failures in
the queue panel, or choose Stop after current claim to leave the rest for later.
Claims awaiting sign-off retain their handler approval requirement.

## Validation

`npm run check` runs Python formatting/lint/types/tests, TypeScript
formatting/lint/types/tests, and Playwright. Individual checks are
`npm run check:python`, `npm run check:ts`, and `npm run e2e`.

The pre-commit hook is enabled. No thresholds, rules, or coverage exclusions
were weakened. The installation commands and normal `npm run dev` were verified.

Latest automated results on November 13, 2026:

- Python: 58 tests passed, 99.17% product coverage, including 25 notebook
  retry regression cases and two Ark output cases.
- Dashboard: 13 tests passed, 96.96% statement coverage.
- Playwright: 9 tests passed, including approvals, declines, referrals, the
  $1,000 boundary, sign-off enforcement, duplicate-payment rejection, failed-run
  retry, reload persistence, keyboard access, and desktop/mobile layout.
  Approval checks also verify the payout is in the desktop viewport and appears
  before the activity history on mobile, including after a reload.
  Queue coverage includes one-click start, duplicate rejection, stop/continue,
  reload persistence, failure continuation, and preserved payment gating.

Automated tests make no model calls. Playwright launches a dedicated scripted
agent server on port 8010 and Vite on 5174, with a new temporary database per
run. The normal database and model configuration are not used by those tests.
The scripted outcomes are test scenarios, not evaluation answers or claims of
model accuracy. No evaluation files were opened or used for prompt tuning.
Screenshots are generated under `e2e/test-results/` and are ignored by git.

## Week 1 Day 2 notebook retries

`labs/week1day2.ipynb` now requests strict JSON and validates raw responses
against `ClaimDecision`. Markdown-fenced JSON is unwrapped before validation;
prose, invalid fields, empty responses, and incomplete completions get up to
three total attempts. Validation retries include the failed output and field
errors. Transient connection/API failures use the same budget with 1s/2s
backoff; nested SDK retries are disabled for this call. Permanent API errors
and refusals stop immediately.

`decide(..., max_attempts=3)` returns the existing result fields plus `attempts`
and `error`. Exhausted requests have `decision="error"`, never a fabricated
approval, decline, or referral. Batch evaluation continues and reports its
failure count. Cost sums all responses that report usage, including invalid
outputs; requests without reported usage may have unreported provider charges.

Rerun the imports, `ClaimDecision`, `decide`, and `run` definition cells before
rerunning an experiment in an existing notebook kernel. Regression tests load
the actual notebook definitions with synthetic claims and an HTTP stub; they
make no live model calls and do not open evaluation files. This change applies
to the lab's LLM comparison, separately from the pilot agent's run lifecycle.

## Volcengine notebook output

`labs/volcengine.ipynb` raises `max_tokens` from 16 to 4,096 and renders
`message.content` as Markdown. It prints `finish_reason` and reports when
generation reaches a length limit. Rerun the cell to generate a new response;
the earlier short response cannot be expanded by changing its display.
Regression coverage uses long synthetic content with both `stop` and `length`
responses, without calling Ark or loading local credentials.

## Live model verification

The local configuration was changed to `x-ai/grok-4.7` at the student's request.
A new run on CLM-1001 through the normal dashboard completed in about 25.6
seconds on November 12, 2026. It called `read_claim`, `read_policy_wording`,
`record_decision`, and `pay_claim`, with activity persisted throughout.

The agent approved $420.00: hotel $245.00, meals $95.00, and taxis $80.00.
Its three-sentence explanation cited policy sections 1, 3, 10, and 11, applied
the six-hour Premium waiting period and $800 Trip Delay maximum, and recognized
continued coverage on the delayed return journey. A separate $420.00 simulated
payment was recorded. This is one successful live smoke case, not an aggregate
accuracy evaluation. Earlier failed runs remain in the claim's history.

Run ID: `bcfa4ddc-9b24-4fed-8d03-1aef0d638068`.
The `.env` change only sets `MODEL_NAME`; credentials were preserved.

## Validation limits

- Playwright MCP is not available. The CUA browser tool returned
  `The browser access host is unavailable`. Automated Playwright interactions,
  console-error assertions, and screenshot review were completed instead;
  the plan's manual MCP inspection criteria remain unchecked.
- This is a local, single-process pilot. The claims manual, evaluation runner,
  authentication, real payments, customer messaging, and cloud deployment are
  outside scope. No AWS resources were created or deleted.
