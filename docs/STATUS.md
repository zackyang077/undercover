# Status

The harness is implemented locally. Automated acceptance and desktop/mobile
screenshot review pass. The five-claim live-model acceptance trial is pending
explicit authorization for the external data transfer; it has not started.

## Built

- One queue `create_agent` invocation chooses claim order, writes visible to-dos,
  delegates each claim to a fresh helper, and checks for missed work before finishing.
- All waiting claims or a positive distinct-claim cap can be selected. The default
  UI choice is a five-claim trial. Retries consume the same slot; the backend
  enforces scope/cap/locks transactionally. Stop finishes only the active helper.
- A failed undecided claim returns to Waiting and gets one fresh automatic retry.
  Two failures leave it for a handler, distinct from a policy referral or financial
  sign-off. Failure counts survive subsequent starts/restarts; old pilot failures
  do not retroactively consume the new budget. Named-claim runs share these rules.
- Helpers read evidence/history through HTTP and search/read relevant numbered
  manual and wording sections. Claim activity preserves section titles, versions,
  and complete text. The instructions page shows both queue and claim instructions.
- New decisions include policy/manual references, versions, and agent attribution.
  Older decisions remain readable. Customer history includes platform claims with
  deduplicated IDs and filing dates, excluding the current claim and future filings.
- SQLite retains decisions, sign-offs, payments, attempts, activity, and latest
  queue monitoring. Existing databases upgrade without reseeding. Restart marks
  active work Interrupted and releases locks; no agent resumes automatically.
- Separate simulated-payment operations still require the full approved amount.
  Exactly $1,000 needs no sign-off; $1,000.01 does. Only a handler can sign off.
  Saved decisions/payments are reconciled after errors rather than repeated.
- The responsive claims desk preserves the prominent decision/payout result,
  evidence, handler actions, selected-claim URL, and bundled brand fonts.

## Run

From the repository root, with Python 3.12, uv, and Node 26:

```text
uv sync
npm install
npm run dev
```

Dashboard: http://localhost:5173. Backend: http://127.0.0.1:8000/api/health.
API docs: http://127.0.0.1:8000/docs.

Keep `OPENROUTER_API_KEY` in `.env`. Both agent roles default to
`deepseek/deepseek-v4.1-flash` with low reasoning effort through OpenRouter.
`MODEL_NAME` still overrides that selection; another override does not receive a
forced reasoning setting. Existing `.env` values were preserved. Restart the
backend after changing environment variables.

`UNDERCOVER_DB` selects the SQLite file (default `undercover.sqlite3`).
`BACKEND_URL` selects the agent HTTP backend and Vite proxy (default
`http://127.0.0.1:8000`). The normal database was not reseeded for this build.

Use Limited trial and Claim limit, or All waiting claims, then Start queue.
Follow the to-do list and select any task to inspect its claim. Start agent still
works for a named Waiting claim. Left for a handler means automatic processing
has stopped; inspect its attempts. Over-limit approvals retain Sign off and pay.
All payments are simulated. See [QUEUE_RUN.md](QUEUE_RUN.md) for API behavior.

## Reseeding and live trials

Stop the server using the selected database first. The explicit-path command
preserves an existing database in a timestamped `.bak` file, then seeds 25 Waiting
claims. Test data and pilot records must use different database files.

```text
uv run -m backend.reseed harness-trial.sqlite3
```

For a live trial, reseed first, explicitly choose the default model if `.env`
overrides it, and use a cap of five. On macOS/zsh:

```sh
UNDERCOVER_DB=harness-trial.sqlite3 MODEL_NAME=deepseek/deepseek-v4.1-flash npm run dev
```

On Windows/PowerShell:

```powershell
$env:UNDERCOVER_DB = "harness-trial.sqlite3"
$env:MODEL_NAME = "deepseek/deepseek-v4.1-flash"
npm run dev
```

## Validation

`npm run check` runs ruff formatting/lint, pyright, pytest, Prettier, ESLint,
strict TypeScript, Vitest, and Playwright. Coverage thresholds remain at 80%.
The pre-commit hook stays enabled; no checks/rules/exclusions were weakened.

On November 16, 2026, `npm run check` passed: 80 Python tests with 98.06%
coverage, 16 dashboard tests with 97.23% statement coverage, and 9 Playwright tests.

The browser scenarios use an isolated temporary SQLite database and scripted
queue/claim models on ports 8010/5174. They exercise cap enforcement, model-chosen
order, missed-work correction, retries, handler handoff, stop, reload, section
reads, responsive layout, sign-off, and duplicate-payment rejection. Unit tests
cover the additional failure, deadline, migration, and concurrency cases.
Automated tests make no model calls. No evaluation files were opened.

Playwright MCP is unavailable. Automated Playwright interactions and screenshot
inspection cover desktop/mobile queue, active/finished work, handler needs,
section text, instructions, and financial flows; no MCP inspection is claimed.

## Live acceptance status

A fresh separate trial database was prepared at
`/private/tmp/undercover-harness-live.sqlite3` with the normal backend/dashboard
and the requested default model. Automatic approval review rejected starting
the capped run because transmitting claim/customer records to DeepSeek through
OpenRouter requires explicit payload/destination authorization. No queue run or
model request was started; all 25 trial claims remain Waiting. The temporary
trial servers were stopped after this verification. There is no live
harness accuracy, timing, or five-claim success result to report yet.

This is a local single-process build. Evaluation tooling, authentication,
customer messaging, real payments, durable execution, and cloud deployment
remain outside scope. No AWS resources were created or deleted.

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
