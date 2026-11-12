# Pilot implementation plan

## Goal and scope

Build the claims pilot in `docs/SPEC.md`: a local claims backend, a handler dashboard, and an UnderCover Agent that reads the evidence, decides a claim, calculates an approved payout, and gives a two- or three-sentence explanation with relevant policy sections.

The handler's end-to-end flow will be:

1. Open the queue and select a claim.
2. Read its story, items, supporting documents, policy, plan, and customer history.
3. Start the agent and follow its activity as it works.
4. Review the decision, calculation, and policy references.
5. For an approved payout over $1,000, select a handler and sign off before recording the simulated payment.

The pilot includes the 25 claims in `data/claims.json`. The claims manual, evaluation files, held-out claims, security fixtures, sign-in, customer messaging, real payments, cloud resources, and later course features are outside this build. Customer history is available to the agent, but internal handling rules from the manual must not be inferred from other configuration files and added to its instructions.

Implementation was authorized after review. Checkboxes below track verified delivery.

## Starting point and approach

The current checkout contains a FastAPI health route, a LangChain health-check agent, a React health page, and their tests. `docs/PLAN.md` did not exist. Before writing this plan, `npm run check` passed: four Python tests with 96.97% coverage, two dashboard tests with 100% statement coverage, and one Playwright test. These results verify the skeleton, not the pilot or live model access.

Keep the existing stack and root commands. Use Python 3.12 with uv, SQLite, FastAPI, LangChain `create_agent` through OpenRouter, and React/Vite/strict TypeScript. Use Node 26 as specified in `docs/TECH_STACK.md`.

| Component | Responsibility |
| --- | --- |
| Backend | Own seed loading, SQLite records, instructions, decisions, sign-offs, simulated payments, run coordination, and activity history. Enforce payment rules independently of the model. |
| Agent | Expose `run(claim_id, thread_id)`. Fetch instructions and claim context through HTTP, use policy wording to decide, and call separate decision and payment tools. Never open business files or SQLite. |
| Dashboard | Show the queue, full claim, decision, payment state, activity, and read-only instructions. Start work and collect handler sign-off through the API. |
| Tests | Use isolated temporary databases and scripted model responses. Exercise the real backend rules and UI without calling OpenRouter. |

Use small backend modules for storage, schemas, and routes, and small dashboard components for the queue, claim details, activity, and instructions. Preserve nested source records as JSON where that avoids unnecessary tables. Add separate mutable records for decisions, payments, runs, and events as their phases need them. Do not introduce a general workflow framework, message broker, or repository abstraction.

### Data and decision boundaries

- Seed only explicitly named files: `claims.json`, `policies.json`, `customers.json`, `plans.json`, and `handlers.json`. From `company.json`, use only the company name, currency, payout approval limit, and policy wording version. Load the full `policy_wording.md`, not the shortened lab version. Do not recursively scan `data/` or expose arbitrary file paths.
- Seed once into an ignored local SQLite file. Restarting must preserve decisions, sign-offs, payments, and activity without duplicating seed records. Tests use separate temporary databases.
- Store money as integer cents and validate API amounts to at most two decimal places. Present USD amounts as `$1,250.00` in the dashboard.
- A decision has an outcome (`approve`, `decline`, or `refer`), payout amount, two- or three-sentence explanation, calculation, and policy section references. All request fields are required. Decline and refer have a zero payout; approval requires a positive payout.
- Keep the decision, queue state, run state, and payment state distinct. A referral is a completed agent decision that needs human judgment or more information. It is different from an approved payout awaiting financial sign-off.
- The agent receives the full policy wording and relevant records, not the handlers' answer key. Historical outcomes already present in customer records are legitimate history; current-claim evaluation labels are excluded.
- Show observable activity: tool calls, concise results, errors, and the final decision. Do not request or display private model reasoning.

### Queue and payment behavior

| Condition | Queue label | Payment behavior |
| --- | --- | --- |
| No decision and no active run | Waiting | No payment allowed. |
| Agent working, with no saved decision yet | In progress | No payment allowed. |
| Approved payout greater than $1,000, unsigned | Waiting for sign-off | Backend rejects every payment attempt. |
| Decline or refer saved | Decided | No payment allowed. |
| Approval of $1,000 or less, or an approval with sign-off | Decided | Separate payment operation can record the approved amount once. |

An approved decision can be saved before its simulated payment. The dashboard must therefore show the payment state explicitly, including an approved but unpaid claim after a failed payment. Signing off does not itself create a payment. Exactly $1,000 requires no sign-off; $1,000.01 does. The gate comes from `payout_approval_limit` and is enforced on the total approved amount, so splitting payment requests cannot bypass it.

Do not re-decide a completed claim or automatically retry a failed model run. If a run fails before a decision, return the claim to Waiting with the failed run visible and allow an explicit retry. If a decision or payment was already saved, preserve it and offer only the remaining permitted action. A backend restart marks unfinished runs as interrupted; it does not resume the agent. Durable execution is a later build.

### Proposed API contract

Every operation has a unique `operation_id`, a one-line docstring, typed responses, and required inputs only. No optional query filters or request fields are needed for this pilot. All paths below have an `/api` prefix.

| Method and path | Operation ID | Required input and result |
| --- | --- | --- |
| `GET /health` | `health` | Preserve `200 {"status":"ok"}`. |
| `GET /claims` | `list_claims` | Return the 25 queue records with customer, summary, claimed amount, queue state, decision summary, and payment state. |
| `GET /claims/{claim_id}` | `get_claim` | Return the full claim, linked policy, plan/add-ons, customer/history, decision, sign-off, and payment. Unknown ID returns 404. |
| `GET /policy-wording` | `get_policy_wording` | Return full wording and its version. |
| `GET /agent/instructions` | `get_agent_instructions` | Return the backend-owned instructions used by the agent and shown read-only in the dashboard. |
| `GET /handlers` | `list_handlers` | Return handlers and their sign-off eligibility. |
| `POST /claims/{claim_id}/decision` | `record_decision` | Require outcome, payout amount, explanation, calculation, and policy references; save a single final decision. |
| `POST /claims/{claim_id}/sign-off` | `sign_off_claim` | Require `handler_id`; record an eligible handler's approval of the saved over-limit decision. |
| `POST /claims/{claim_id}/payment` | `pay_claim` | Require `amount`; validate against the saved decision and sign-off, then record one simulated payment. |
| `POST /claims/{claim_id}/runs` | `start_claim_run` | Create a run and thread ID, return 202 with both identifiers, and start work in the backend process. |
| `GET /claims/{claim_id}/runs` | `list_claim_runs` | Return run status and ordered activity for the selected claim, including earlier failed attempts. |
| `POST /runs/{thread_id}/events` | `record_run_event` | Require event kind, tool name, and display text; append activity to the matching active run. Non-tool events use an empty tool name. |

Use 404 for unknown records, 422 for malformed inputs, 403 for a payment blocked by missing sign-off, and 409 for conflicting state, including duplicate starts, decisions, or payments. A rejected mutation must leave stored records unchanged. Decision and payment tools are bound to the claim passed to `run`; a tool call cannot select another claim. Sign-off is a handler action and is never an agent tool. Without sign-in, selecting a handler records attribution rather than authenticating identity, as allowed by the pilot scope.

## Phase 1 — Seeded SQLite records and read API

Build storage and the read contracts first so both the agent and dashboard have one source of truth. Map seed claim status `open` to Waiting. Add typed models for nested claim evidence, policies, customers, plans, and handlers. Keep existing health behavior.

Success criteria:

- [x] On an empty temporary database, initialization imports exactly the IDs from `data/claims.json` and returns 25 Waiting claims; a second initialization adds no duplicates.
- [x] Restarting against the same database preserves a test modification instead of reseeding over it. Missing claim IDs return 404.
- [x] `GET /api/claims/CLM-1001` returns Maya Patel's complete story, four document texts, three items totaling `$420.00`, linked Premium policy `POL-2001`, trip details, plan limits, and customer history.
- [x] `GET /api/policy-wording` returns version `2026.1` and the full document, including sections 1 through 12. `GET /api/handlers` returns the three handlers.
- [x] Seed-loading tests use an allowlist and fail on any attempted read outside it, including evaluation, manual, security, shortened-wording, and knowledge files. API responses contain no manual-only company rules.
- [x] `/openapi.json` exposes unique operation IDs and required inputs for these routes, with no optional API parameters. Health still returns `{"status":"ok"}`.
- [x] `npm run check:python` passes with at least 80% Python coverage. Record the completed work and current run commands in `docs/STATUS.md`, then commit with the pre-commit hook enabled.

## Phase 2 — Decisions, handler sign-off, and simulated payments

Build the financial gate before connecting a model. Store the decision, handler attribution, payment, and timestamps transactionally. Payment must validate the persisted decision and sign-off itself; a model-supplied claim that approval exists is never sufficient.

Success criteria:

- [x] API tests save approve, decline, and refer outcomes with their explanations, calculations, and section references. Decline and refer become Decided with no payment; an unsigned approval above the limit becomes Waiting for sign-off.
- [x] Tests for `$999.99` and `$1,000.00` allow a separate payment without sign-off. `$1,000.01` is blocked with 403 and no payment row, including when requested as smaller partial payments.
- [x] For an isolated fixture approved at `$1,250.00`, `POST /api/claims/{claim_id}/payment` with `{"amount":1250.00}` is blocked until a valid `POST .../sign-off` with `{"handler_id":"HND-01"}`. Afterwards it records exactly one `$1,250.00` simulated payment and the claim is Decided.
- [x] Unknown or ineligible handlers cannot sign off. Signing off does not create a payment.
- [x] Payment before a decision, payment on decline/refer, mismatched amounts, nonpositive amounts, fractions of a cent, duplicate payments, and attempts to replace a saved decision are rejected without altering valid records. Concurrent payment requests cannot create two payments.
- [x] Decisions, sign-offs, and payments survive reopening the database. All mutation routes meet the operation ID, docstring, and required-input contract.
- [x] `npm run check:python` passes at the existing coverage threshold. Update `docs/STATUS.md` and commit through the pre-commit hook.

## Phase 3 — Claim agent with backend-only tools

Replace the health-check behavior with a claim agent. Keep the existing injectable stub-model testing approach. Fetch backend-owned instructions before constructing `create_agent`; provide tools to read the claim context and policy wording, record the decision, and request a simulated payment. Keep financial calculations and policy judgments visible in the saved decision.

Instructions must tell the agent to read all relevant evidence, apply policy limits/add-ons/refunds/deductibles, refer when the available wording and evidence cannot support a decision, and explain the result in two or three sentences with policy references. For an unsigned over-limit approval, save the approval and stop for handler sign-off. Do not incorporate manual-only rules such as special repeat-claim or receipt allowances.

Use `OPENROUTER_API_KEY` from `.env`, default to `openai/gpt-6-luna` with low reasoning effort, and honor `MODEL_NAME` without forcing incompatible reasoning options onto an override. Retain `run(claim_id, thread_id)` as the public entry point. Use a bounded agent loop and request timeout so failed work can terminate visibly.

Success criteria:

- [x] A scripted model drives `run("CLM-1001", "test-thread")` through real backend tool handlers, fetches the claim and full wording, saves a `$420.00` approval citing section 3, and records a separate simulated payment.
- [x] Scripted decline and refer paths save a zero payout, explanation, calculation, and references, and make no payment request.
- [x] A scripted over-limit path saves an approval and stops awaiting sign-off. A second test deliberately makes the model attempt payment anyway and confirms the backend returns the blocked result with no payment recorded. The tool set contains no handler sign-off operation.
- [x] Captured model messages contain the backend-served instructions, policy, claim evidence, plan, and customer history. Tests forbid direct business-file and SQLite access from the agent and prove tools cannot mutate a different claim.
- [x] Tests verify the default model and low reasoning setting, the `MODEL_NAME` override, tool error responses, malformed decision rejection, and termination when the model keeps calling tools without finishing.
- [x] All automated tests use a stub model and make zero OpenRouter calls. A run cannot be reported as successfully decided unless the backend actually contains its decision.
- [x] `npm run check:python` passes at the existing coverage threshold. Update `docs/STATUS.md` and commit through the pre-commit hook.

## Phase 4 — Background runs and visible activity

Connect the agent to an API-triggered background task in the local backend process. Generate a fresh thread ID per run, record the lifecycle in SQLite, and prevent more than one active run for the same claim. Stream agent tool activity into backend events; let the dashboard poll the read API for incremental updates. Polling and one local process are sufficient for this pilot.

Run lifecycle is `running`, `completed`, `failed`, or `interrupted`. Completion means the required decision is stored and any immediately payable approval has a recorded payment. An unsigned over-limit approval is a valid completed run awaiting a handler. Errors after a saved decision preserve that decision and expose the outstanding payment action.

Success criteria:

- [x] `POST /api/claims/CLM-1001/runs` returns 202 with a run ID and thread ID before a deliberately delayed stub finishes. During the delay, `GET /api/claims` shows In progress.
- [x] A second start on the same active claim returns 409 and does not create another task. Starting a decided or sign-off-waiting claim also returns 409.
- [x] Polling `GET /api/claims/CLM-1001/runs` reveals ordered events while the stub is still running: start, named tool activity/results, saved decision, payment or blocked-payment result, and completion. Events remain visible after page reload or backend restart.
- [x] Timeout, exhausted tool steps, and provider failure produce a failed run with a useful error. Before a decision, the claim returns to Waiting and an explicit retry gets a new thread ID without deleting earlier activity.
- [x] Failure after recording a decision or payment retains that state and cannot cause a second decision or payment. Restart marks an unfinished run Interrupted and releases its active-run lock without automatically invoking the model.
- [x] `run(claim_id, thread_id)` also works when invoked directly against the running backend: activity reporting can be skipped when no run record exists, while decisions and payments still use the same API gates.
- [x] `npm run check:python` passes at the existing coverage threshold. Update `docs/STATUS.md` with local-process limitations and commit through the pre-commit hook.

## Phase 5 — Handler dashboard

Build the queue and claim workspace on the read API, then connect agent runs and sign-off. Use a responsive queue/detail layout, with clearly labeled navigation to read-only agent instructions. Show the full evidence as readable text and expose activity as it arrives. Keep an approved but unpaid claim visible with its available payment action.

Follow `docs/BRAND.md`: Instrument Sans for interface text, Instrument Serif for a restrained accent, JetBrains Mono for identifiers and amounts; Midnight text, the specified page background, and rounded panels without outlines. Use the supplied colors with readable text contrast, short business-English labels, and USD formatting. Provide labeled controls, keyboard navigation, visible focus, textual status labels, and loading/error/empty states. A single light theme is sufficient for the pilot.

Use a handler selector and a Sign off and pay action for over-limit approvals. It performs sign-off and payment as two separate API calls; if payment fails after sign-off, show the recorded sign-off and allow payment retry. Never treat a disabled button as enforcement of the financial rule.

Success criteria:

- [x] Vitest covers queue/detail rendering, full story/items/documents/policy/history, decision explanation/calculation/references, activity polling, read-only instructions, and USD formatting.
- [x] Start agent is disabled while work is active or the claim already has a decision. Tests verify successful state refresh, failed-run retry, stopped polling after completion, and visible fetch/mutation errors without losing selected-claim context.
- [x] Tests show an over-limit approval as Waiting for sign-off, require a handler selection, and distinguish decision approval from payment. A declined or referred claim has no payment action.
- [x] Tests cover successful sign-off/payment, blocked payment, and a failed payment after successful sign-off; retrying does not record another sign-off or duplicate payment.
- [ ] With `npm run dev`, inspect the page using Playwright MCP at desktop and narrow mobile widths. Verify all 25 queue rows, CLM-1001's complete evidence, keyboard access, no horizontal page overflow, readable colors, and read-only instructions. Check the browser console for application errors.
- [x] Using a stub run in the browser, observe In progress and tool activity before the decision appears, then the final decision and payment state without manually reloading.
- [x] `npm run check:ts` passes with at least 80% dashboard statement coverage, and `npm run check:python` still passes. Update `docs/STATUS.md` and commit through the pre-commit hook.

## Phase 6 — End-to-end acceptance and handover

Extend Playwright to exercise the finished system with real HTTP and temporary SQLite data plus a deterministic agent stub. Configure the stub only through the test server setup; the normal application still uses OpenRouter. Keep test databases separate from the student's local pilot records. New helper scripts must use `uv run` or `.mjs` and work on Windows as well as macOS.

Success criteria:

- [x] Playwright covers queue to claim detail to agent run to visible activity to a paid approval, plus decline and refer outcomes. Assertions check the persisted API results as well as screen text.
- [x] Playwright covers an over-limit approval: the page shows Waiting for sign-off, a direct payment request is blocked, a handler signs off, and exactly one simulated payment is recorded. Boundary coverage includes exactly `$1,000.00` and `$1,000.01`.
- [x] Playwright covers a failed run, explicit retry, and page reload preserving the decision and activity. Test setup/cleanup never resets the normal pilot database.
- [ ] Inspect the completed queue, full claim, active run, sign-off flow, decided claim, and instructions page in Playwright MCP. Confirm brand, keyboard use, mobile layout, useful error states, and no application console errors.
- [x] From the repo root, `uv sync` and `npm install` succeed; `npm run dev` serves the API on port 8000 and dashboard on port 5173. Document OpenRouter configuration, database location, run instructions, the sign-off flow, and test commands in `docs/STATUS.md` and a concise README update if needed. Do not overwrite the student's `.env`.
- [x] Perform one separate live OpenRouter smoke run on a selected waiting queue claim through the dashboard, using the configured model. Confirm tool activity, persisted explanation/references/calculation, and correct payment gating. Record model ID and result without secrets. Provider access failure is reported as a live-validation limitation, never replaced by a claimed stub success; no evaluation files are opened.
- [x] Run `npm run check` successfully: ruff format/lint, pyright, pytest with at least 80% coverage, Prettier, ESLint, strict TypeScript, Vitest with at least 80% statement coverage, and Playwright. Do not lower thresholds, disable rules, or exclude files to pass.
- [x] Tick only verified criteria in this plan, update `docs/STATUS.md` with the final capabilities and any validation limitation, and commit the final phase with the pre-commit hook enabled. Report the final checks and live smoke result. Do not push.

## Delivery discipline

During implementation, complete phases in order without stopping between them. Add tests with each change, verify every criterion, update this plan and `docs/STATUS.md`, and commit each completed phase. Use stub models for all automated tests and keep live verification separate. If a rule or threshold appears incorrect, stop and ask rather than weakening it. No AWS resources are needed for the pilot.

## Verification record

All six phases are implemented and the automated checks pass: 27 Python tests
with 99.57% coverage, 9 Vitest tests with 98.31% statement coverage, and 8
Playwright tests. Installation and the normal development servers were verified.
Desktop/mobile screenshots and the running, decided, sign-off, failed, and
instructions screens were reviewed from Playwright artifacts.

The manual MCP criteria remain unchecked because Playwright MCP is unavailable
and CUA returned Browser access host unavailable. Equivalent automated browser
flows and screenshot review passed.

At the student's request, the local model was changed through `MODEL_NAME` to
`x-ai/grok-4.7`. A fresh dashboard run on CLM-1001 completed in about 25.6 seconds,
read the claim and full wording, recorded a $420.00 approval with calculation
and policy sections 1, 3, 10, and 11, and separately recorded its simulated
payment. Earlier failed runs remain visible. This single live case verifies the
integration, not aggregate model accuracy. See docs/STATUS.md for the run ID.
