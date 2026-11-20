# Status

Run `uv sync`, `npm install`, and `npm run dev` from the repository root. The
dashboard listens on 5173, the business API on 8000, and the agent service on
8002. Start live-queue dispatches from the Claims desk and evaluations from
Scoreboard. Use `uv run -m agent CLM-1001` for a single claim.

Development reads `.env` when present. Set `DATABASE_URL` to the Supabase
session pooler or use `UNDERCOVER_DB` for a local SQLite file. SQLite defaults
to `undercover.sqlite3` and saves checkpoints in a sibling file ending in
`.checkpoints.sqlite3`. Live model calls require `OPENROUTER_API_KEY`.
Set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_BASE_URL` for traces.

Stop the application before reseeding. `npm run seed` rebuilds the configured
hosted `undercover` data, including dispatch and evaluation records. For a
local trial, `uv run -m backend.reseed trial.sqlite3` backs up the selected
business and checkpoint files before rebuilding them.

Signing off records the handler and time, then notifies the service after
commit. A signed live claim remains In progress until the saved thread pays.
Agent startup stops unfinished dispatch and evaluation runs. Start a new
dispatch to continue eligible live claims on their saved threads.

## Swarm · Phase 1

The claim agent defaults to GPT-6 Luna with low reasoning. `MODEL_NAME`
selects an override with no reasoning effort set. The backend records each
OpenRouter usage report and active claim work by thread and executing run.
Sign-off waits are excluded. An interrupted invocation retains its last
recorded progress and a separate invocation records the resumed work.

In an isolated checkout without `.env`, `npm run check:python` passed with
148 tests and 95.33% coverage. A rolled-back PostgreSQL transaction verified
the two new tables, call replay and work progress updates. The SQLite upgrade
fixture reopened a Durable database with its original decision intact. Live
model and dispatch acceptance belongs to the final phase.

## Swarm · Phase 2

The model-free dispatcher fans out the persisted claim list. Stub tests
verified limits of 1, 3 and 25, including 25 overlapping claims. Provider
429s and timeouts resume on the same thread. Other errors can consume one
new attempt. Sign-off races retained the original decision and one payment.

Each hosted claim invocation has a saver on the agent's shared four-connection
pool. A pooled checkout test held six saver invocations to four simultaneous
reads. SQLite tests exercised 25 real checkpoint threads. `npm run
check:python` passed with 169 tests and 95.86% coverage.

## Swarm · Phase 3

The queue starts dispatches with 1–25 workers and restores progress after a
reload. The service owns start admission. The backend forwards the start and
stores a final summary, model cost and elapsed time. Instructions now show
the claim agent's prompt. `uv run -m agent CLM-1001` starts an individual claim.

Phase checks passed with 158 Python tests (95.79% coverage) and 18 frontend tests
(96.82% statement coverage). Playwright verified the forwarded concurrency,
progress after reload and the persisted summary. `npm run dev` started the
backend, agent service and dashboard in an isolated macOS checkout. Windows
uses the same Node and uv launcher and was not runtime-tested.

## Swarm · Phase 4

Evaluations work fresh claim copies with isolated settlement and history.
The backend uses dedicated tables to load two sets of 25 labels and the
25 held-out sources. Tests verified both full and tiny modes, concurrent
start admission, a rejected copy sign-off, and real checkpoint removal on
a held copy. A stopped evaluation left its copies out of the next dispatch.

The isolated suite passed 175 Python tests (96.01% coverage) and 20 frontend
tests (96.96% statement coverage). A rolled-back PostgreSQL transaction
verified both reference sets and full and tiny copy creation. The hosted
reset check remains open in Phase 6.

## Swarm · Phase 5

The Scoreboard lists evaluations newest first and opens each run at its own
URL. The backend compares every selected claim with the handler's decision.
The seven-claim scoring fixture produced 4/7 decision accuracy, 3/7 accuracy
within one dollar, 5/7 straight-through and $2,802.01 in mistakes.

A SQLite fixture reconciled a stopped dispatch across two claims, including
a failed attempt. It recorded 101 tokens, $0.0101 in known charges, 810 ms
of active work and 520 ms elapsed. The next dispatch resumed the same thread
and recorded 30 tokens, $0.0030 and 200 ms, including a replayed usage write.

Playwright started a five-copy evaluation using the keyboard and verified all
seven measures, four mismatches, and a rejected sign-off on the held copy.
The live queue was identical before and after the evaluation. On a 390-pixel
viewport, Chromium reloaded a stopped evaluation at its original URL and
showed one of five copies worked. A local trace callback captured separate
dispatch tags on the same claim session across invocations.

## Swarm · Phase 6

On 20 November 2026, `npm run check` passed in an isolated macOS checkout
without a project `.env`. Pytest passed 179 tests with 95.07% coverage,
Vitest passed 25 tests with 93.76% statement coverage, and Playwright passed
all 10 browser scenarios. Formatting, lint and static type checks passed.
The SQLite reseed test restored 25 waiting claims and 50 reference decisions,
cleared copies, runs, usage and work segments, and verified the old checkpoint
in the backup. The Node and uv startup scripts were reviewed for Windows.
Runtime startup was exercised on macOS.

The hosted acceptance is pending. Automatic approval review rejected
`npm run seed` because it would erase existing claims results, payments,
checkpoints and evaluations in the configured Supabase `undercover` schema.
The reset requires confirmation that this course schema can be cleared. The
hosted reset and legacy table check, N=3 interruption, N=25 recovery, sign-off
latency, peak connection count, both live evaluations and the Langfuse tag
lookup are unverified. This Swarm build has no hosted run IDs or timings.

## Durable claims · Phase 1

The claim helper now calls the backend payout calculator. A Premium electronics fixture pays $1,050 after third-party compensation and the deductible. The targeted tests cover company limits, financial boundaries, scripted tool use and Langfuse session grouping. Hosted traces are checked during the live trial.

## Durable claims · Phase 2

SQLAlchemy Core now serves both databases. Queue and detail reads take at most three SELECTs. Legacy SQLite records and checkpoint-file reopening passed; the complete Python suite passed 115 tests. A hosted reseed followed by two initializations preserved 25 Waiting claims and zero attempts, decisions, payments or claim checkpoints. Supabase MCP returned all four business and four checkpoint tables in undercover, with three postgres sessions at inspection. The application and checkpoint pools each cap at two.

## Durable claims · Phase 3

Identical decision and full-payment requests return the existing claim. Conflicting decisions and incorrect amounts are refused. A signed, unpaid approval is In progress; its payment carries the handler and original sign-off timestamp. The complete Python suite passed 117 tests including concurrent replay and lost responses.

## Durable claims · Phase 4

The claim worker now uses a synchronous LangGraph. The queue retains create_agent. Real graph nodes and file checkpoints were exercised with a stub model, including process loss across financial commit boundaries, sign-off arriving early, repeated notification and call-budget exhaustion. The Python suite passed 123 tests. A held claim releases its queue slot for execution while retaining its thread, admission and decision.

## Durable claims · Phase 5

On macOS, npm run dev started the backend, agent and dashboard on ports 8000, 8002 and 5173 without .env. Chromium showed 25 Waiting claims. Startup uses Node and uv commands portable to Windows; a Windows host was not available for a runtime check. Python tests verified service/CLI requests, durable sign-off during downtime, concurrency and a five-claim interrupted batch. The UI now resumes an unfinished batch under its original cap. All 9 Playwright tests and 17 frontend tests passed, including the separate 8003 notification stand-in.

## Durable claims · Phase 6

The final `npm run check` ran from an isolated copy without a project `.env`.
All 141 Python tests, 18 dashboard tests and 9 Playwright scenarios passed.
Python coverage was 95.33% and dashboard statement coverage was 97.62%.
The suite includes a delayed queue completion report arriving during a sign-off.
The same thread pays once, and an actual payment failure stays recoverable.

The first hosted trial saved five to-dos, then timed out before admitting any
claims. The queue now validates all tasks from the same projection. A single
claim read loads its linked records and that customer's history. In two hosted
measurements, reading five details fell from 9.09–10.91 s to 2.12–3.57 s.

The second trial was reseeded to 25 Waiting claims. Queue
`c7377086-9ee0-4302-a50c-88c29958b049` used the default model, low reasoning,
and a five-claim cap. At 13:02:10 UTC on 18 November the agent was killed while
working CLM-1001. Supabase showed one attempt, zero decisions and zero payments
at checkpoint `1f1e3504-33aa-611e-8002-4f0749bf4f39`, step 2.
The replacement process resumed the same thread. The start step appeared once
and the thread reached step 64 with one decision and one simulated $420 payment.

| Claim | Thread | Attempts | Decision | Simulated payment |
| --- | --- | ---: | --- | ---: |
| CLM-1001 | `6b8b53ad-202c-4464-b3d7-8080f8c92b89` | 1 | Approve | $420 |
| CLM-1002 | `83be78b6-7785-4c78-b69a-e6b22c202134` | 1 | Approve | $580 |
| CLM-1003 | `d591c838-8bdf-433c-9cec-fba627da78a2` | 1 | Approve after sign-off | $3,750 |
| CLM-1004 | `d085d644-5c56-4376-b41e-3e0650299e22` | 1 | Approve | $186 |
| CLM-1005 | `cbee4c4a-fe7d-48fc-a31f-4a3fad8be7eb` | 1 | Approve | $243 |

All five claims have exactly one decision and one payment. Supabase MCP counted
6 postgres sessions during investigation, while CLM-1003 was paused and again
during payment recovery. The observed maximum was 6, below the limit of 12.

Playwright MCP signed CLM-1003 as Dana Whitfield (HND-01) at
13:12:52.823218 UTC. Its payment stored the same handler and sign-off timestamp.
The payment row was created 7.311 seconds after sign-off. The dashboard showed
the payment, handler and time 11.241 seconds after the click, above the 5-second
target. The Langfuse API returned both traces for the session
`CLM-1003/d591c838-8bdf-433c-9cec-fba627da78a2`.

| Trace | Trace ID | Start (UTC on 2026-11-18) |
| --- | --- | --- |
| Investigation | `eca6d464e84a5f8fa55070d200dc88fe` | 13:07:16.002 |
| Resumed payment | `a1946657b4d916adb288f1d9e851b63d` | 13:12:56.795 |

### Final hosted sign-off check

A fresh five-claim trial used queue `9fe77ef3-389c-49f5-8831-4ce736ec136f`.
The agent finished its five investigations and left CLM-1003 waiting. At
13:37:58.739179 UTC on 18 November 2026, Dana Whitfield (HND-01) signed it
through Playwright MCP. The paid state, original signer and timestamp were
visible **4.647 seconds after the click**, within the five-second target.
The sign-off HTTP response ended at +720 ms, the final settlement response
ended at +4,614 ms, and the browser observed the rendered payment 33 ms later.
This is one end-to-end measurement after the queue finished its investigations.
Supabase recorded the payment at 13:38:02.362079 UTC, 3.623 seconds after
sign-off. The final database check found one attempt, one decision and one
payment for each claim. The highest observed postgres session count was 6.

| Claim | Thread in the final trial | Attempts | Decision | Simulated payment |
| --- | --- | ---: | --- | ---: |
| CLM-1001 | `9572cb2a-bb7d-4264-998c-d561629e792f` | 1 | Approve | $420 |
| CLM-1002 | `c6bc6c4d-7193-48a8-9e97-d76c7c33aae0` | 1 | Approve | $580 |
| CLM-1003 | `ac6b0495-3afa-40f0-b79c-07c81aa60515` | 1 | Approve after sign-off | $3,750 |
| CLM-1004 | `3ba56fd0-5656-4e14-9f61-e4ec8ec5480f` | 1 | Approve | $186 |
| CLM-1005 | `4c0611c6-5824-4764-9a79-8ee0e38d3ad0` | 1 | Approve | $243 |

For session `CLM-1003/ac6b0495-3afa-40f0-b79c-07c81aa60515`, the Langfuse
API returned investigation trace `6ba074b59699fdc615c821de6ec6a1c3`
(13:27:54.115 UTC) and resumed-payment trace
`c5825699fc9bc8110712dfee24dd4cf9` (13:37:59.244 UTC).

## Earlier builds and lab validation

On November 16, 2026, `npm run check` passed: 80 Python tests with 98.06%
coverage, 16 dashboard tests with 97.23% statement coverage, and 9 Playwright tests.

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

## Week 2 Day 2 experiment results

`labs/week2day2.ipynb` accepts model messages without OpenRouter's
`token_usage.cost`, including OpenAI Responses messages. Missing cost stays
unknown (`None`/`NaN`) through run totals and averages; known zero costs remain
zero. Unknown cost and undefined metrics are omitted from Langfuse scores.
Decisions and retrieved facts are returned even when billing metadata is absent.

Empty experiments retain a table schema, skip scoring and charts, and report
success/failure counts. Comparisons require successful results from both
experiments and describe metrics as covering successful items only.

Rerun the Scoring evaluator definitions and The task definition cell before
rerunning experiments in an existing kernel. Offline regression tests load the
actual notebook functions with synthetic messages and a fake agent; they do
not call models, upload experiments, or open evaluation data.

On November 18, 2026, validation passed: 96 Python tests (including 16 new
notebook regressions), 16 frontend tests, and 9 Playwright tests, with lint,
formatting and type checks. Playwright required local port access outside the
sandbox. The two updated definition cells were rerun in the current notebook
kernel; paid experiments were not rerun.

## Improve · Phase 1

The desk signs handlers in through Supabase Auth. Configure VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY. The API verifies asymmetric JWTs and looks up the handler by email. Only Dana may sign over-limit payouts. Existing handler records upgrade on startup. For the single-claim CLI use `uv run --env-file .env -m agent CLM-1001` with HANDLER_EMAIL/HANDLER_PASSWORD or a short-lived HANDLER_ACCESS_TOKEN. Offline checks passed 207 Python tests, 29 dashboard tests and 11 Playwright scenarios using a local Auth service.

## Improve · Phase 2

Configuration and Settings are available on the signed-in desk. Runs retain their selected instructions and model. Decisions keep their approval limit when settings change. Verified dispatch, candidate, replay, audit, theme persistence and read-only access with 214 Python, 33 dashboard and 12 browser tests. The free-model override has been tested on the claim worker; its improver check belongs to phase 5.

## Improve · Phase 3

Completed evaluations store Jev explanation grades, match probabilities and judge costs. Configure OPENROUTER_JUDGE_URL only to override the Decisions origin. Offline acceptance in an isolated copy without .env passed 219 Python tests (96.08%), 33 dashboard tests (94.69%) and 12 Playwright scenarios. The real SDK contacted a local Decisions stand-in. A concurrent 25-copy close with a 10-second timeout completed in under 20 seconds without double purchasing the grades.

## Improve · Phase 4

The backend stores one open round, its settings, approval audit and trial membership. Baseline and candidate use one evaluation and two held-out runs. Only full completed round evaluations contribute scores. The first cold evaluation measures one claim before admitting the remainder. Decimal spending comparisons accept a trial that fits the balance exactly. Checks cover the keep/rollback rule, stopped trials, mismatched datasets, held-out aggregate-only access and a stop racing the verdict. Actual OpenRouter key credit and real spend belong to hosted acceptance.
