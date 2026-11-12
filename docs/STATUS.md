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

Keep `OPENROUTER_API_KEY` in `.env`. The default model is `openai/gpt-6-luna`
with low reasoning effort. Set `MODEL_NAME` to use another model available to
that OpenRouter account, such as `openrouter/free`; overrides do not force the
reasoning setting. Restart the backend after changing `.env`.

`UNDERCOVER_DB` selects the SQLite file; the default is `undercover.sqlite3` in
this directory, ignored by git. `BACKEND_URL` selects the agent's HTTP backend
and the Vite API proxy; its default is `http://127.0.0.1:8000`.
These environment variables can be set before launching the process. Keep the
normal database when restarting to retain the pilot's records.

Select a Waiting claim and choose Start agent. Follow the activity until the
agent records approve, decline, or refer. For a payout over $1,000, choose an
eligible handler and Sign off and pay. Selecting a handler records attribution;
there is no sign-in in this pilot. All payments are simulated.

## Validation

`npm run check` runs Python formatting/lint/types/tests, TypeScript
formatting/lint/types/tests, and Playwright. Individual checks are
`npm run check:python`, `npm run check:ts`, and `npm run e2e`.

The pre-commit hook is enabled. No thresholds, rules, or coverage exclusions
were weakened. The installation commands and normal `npm run dev` were verified.

Latest automated results on November 12, 2026:

- Python: 26 tests passed, 99.57% coverage.
- Dashboard: 9 tests passed, 98.31% statement coverage.
- Playwright: 8 tests passed, including approvals, declines, referrals, the
  $1,000 boundary, sign-off enforcement, duplicate-payment rejection, failed-run
  retry, reload persistence, keyboard access, and desktop/mobile layout.

Automated tests make no model calls. Playwright launches a dedicated scripted
agent server on port 8010 and Vite on 5174, with a new temporary database per
run. The normal database and model configuration are not used by those tests.
The scripted outcomes are test scenarios, not evaluation answers or claims of
model accuracy. No evaluation files were opened or used for prompt tuning.
Screenshots are generated under `e2e/test-results/` and are ignored by git.

## Validation limits

- The separate live smoke run used the normal dashboard, existing `.env`, and
  `openai/gpt-6-luna` on CLM-1001. OpenRouter returned
  `OpenAIPermissionDeniedError` after about 1.6 seconds, before any claim tool
  ran. The run is recorded as Failed; no decision or payment was created.
  Live model completion is unverified until model/account access is resolved.
  The existing `.env` was preserved and no fallback model was silently selected.
- Playwright MCP is not available. The CUA browser tool returned
  `The browser access host is unavailable`. Automated Playwright interactions,
  console-error assertions, and screenshot review were completed instead;
  the plan's manual MCP inspection criteria remain unchecked.
- This is a local, single-process pilot. The claims manual, evaluation runner,
  authentication, real payments, customer messaging, and cloud deployment are
  outside scope. No AWS resources were created or deleted.
