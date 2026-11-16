# Harness implementation plan

## Goal and review boundary

Implement `docs/HARNESS.md`: one UnderCover Agent run organizes the waiting queue, maintains a visible to-do list, delegates each claim to a fresh helper, looks up relevant policy wording and claims manual sections, recovers from failures, and finishes with a short handler summary.

This is a plan for review. No product implementation, reseed, model call, or configuration change is part of this planning step. All success criteria below remain unchecked until implementation verifies them. `docs/HARNESS.md` remains the customer's brief and `docs/PLAN.md` remains the completed pilot plan.

## Starting point

The current code already goes beyond the original pilot description in two useful ways:

- `backend/queue_runs.py` captures Waiting claims and loops over them in code. It persists progress, continues after a failure, and supports Stop after current claim. The agent does not yet choose the order or manage a to-do list.
- `backend/runs.py` already releases failed claims back to Waiting and marks active work Interrupted after a restart. It does not automatically retry or set aside a repeatedly failing claim.

`agent/main.py` constructs a separate LangChain `create_agent` invocation for one claim, with HTTP tools bound to that claim. It reads the full wording, has a tool-level deadline and graph step limit, and checks the persisted decision/payment before reporting success. Activity currently records generic tool results rather than the sections read. Instructions cover only claim work and explicitly exclude the manual. The default is still `openai/gpt-6-luna`.

Extend these modules and the current React polling flow. Keep FastAPI, SQLite, LangChain `create_agent`, OpenRouter, and strict TypeScript. Although the course environment includes other packages, this build adds no agent framework, custom durable graph, vector database, broker, or parallel claim processing. Authentication, evaluation tooling, cloud deployment, real payments, and customer messaging remain outside scope. Do not open `data/evals/` or use its answers in instructions or tests.

## Handler flow

1. Open the queue and choose All waiting claims or a trial with a positive claim limit, such as five.
2. Start once. Watch the agent build and update its to-do list, choose claim order, and work one claim at a time.
3. Select a to-do item to view that claim's evidence, attempts, section reads, decision, and payment state.
4. A failed first attempt returns to Waiting and is retried automatically within the run. Two failed attempts leave the claim for a handler while the other claims continue.
5. Read the final summary, including approvals, declines, referrals, payment/sign-off needs, technical failures, and claims left waiting because of the cap or a stop request.

Keep Start agent for one named claim and Stop after current claim for a queue run. A handler still signs off before any payment above $1,000.

## Responsibilities and execution flow

| Component | Responsibility |
| --- | --- |
| Backend | Own queue scope, cap admission, claim locks, retry counts, to-dos, activity, document sections, decisions, sign-off, and payment gates. Validate state independently of model messages. |
| Queue agent | Read queue instructions and allowed queue summaries through the API, write the plan, choose the next claim or retry, delegate, reconcile remaining work, and summarize. It receives no full claim evidence or child transcripts. |
| Claim helper | Start with fresh messages and a new attempt/thread ID; read one claim, search/read relevant guidance through HTTP, record its decision and any permitted simulated payment, and return one concise result line. |
| Middleware | Apply model-call retries and execution limits, record observable tool activity, and prevent apparent completion when persisted work remains. Keep queue and claim scope bound by code. |
| Dashboard | Poll backend state; display the cap, current task, to-do statuses, retry/handler needs, short summary, section reads, and both instruction sets. |

The queue API creates a run and starts one queue `create_agent` invocation. The queue agent calls a claim-delegation tool with its chosen claim ID. That tool asks the backend to admit and lock the claim, invokes a fresh instance of the existing claim agent, waits for it to exit, and reports the attempt's termination through HTTP. The backend verifies the persisted decision/payment and updates the attempt, lock, retry count, and handler state together. A child failure becomes a concise tool result so the queue agent can continue. The backend remains a coordinator and rule enforcer; it no longer contains the normal loop that chooses every claim in order.

Preserve `run(claim_id, thread_id)` as the one-claim entry point and add a queue entry point such as `run_queue(queue_run_id, thread_id)`. Both support injected stub models. The queue agent gets planning/delegation tools, not decision, payment, or handler sign-off tools. Each child gets only its own claim tools and guidance tools, with no ability to choose a different mutation target. Delegation is sequential and does not recurse.

Use a small, backend-backed `write_todos` tool with the four statuses this brief requires. LangChain's middleware hooks provide the interception points for limits, retries, and activity; see the [official middleware reference](https://reference.langchain.com/python/langchain/agents/middleware). Keep these controls in our agent rather than adopting another framework.

### Queue scope, cap, and reconciliation

- Snapshot eligible Waiting claim IDs when the run starts. Let the model choose the order from that set. Claims arriving later belong to a later run; claims already decided, active, or set aside for a handler are excluded.
- A cap limits distinct claims admitted for processing, not model calls or attempts. A five-claim run admits at most five distinct claims, including any that ultimately fail. A retry uses the same slot. Enforce this transactionally in the backend, even if the agent delegates a sixth claim or calls tools concurrently.
- The target is the smaller of the cap and the initial eligible count; All waiting claims uses the initial eligible count. Do not preselect the first five in code. The agent chooses which five to admit.
- To-dos contain a claim ID, a short task description, and `pending`, `in_progress`, `done`, or `needs_handler`, displayed as To do, In progress, Done, and Left for a handler. Keep at most one active child. Validate to-do transitions against persisted claim/attempt results; a model cannot erase unfinished admitted work or mark an undecided claim Done.
- A completed referral, an approval awaiting sign-off, and a terminal technical failure can all leave work for a handler, with different reasons. Preserve their distinct decision/payment states.
- Before final completion, compare the snapshot, admitted IDs, attempt outcomes, pending retries, current Waiting state, and unused slots. The queue agent must revisit missed eligible claims until its slots are filled or all snapshot candidates are accounted for. If another caller resolves a candidate, record it as skipped and allow a replacement within the original scope.
- Expose this comparison through `check_queue`. A completion guard runs it again even if the model omits the tool. Give an early-finishing agent a bounded corrective turn; if it still omits work, report a failed/incomplete run rather than a successful summary. An explicit Stop request permits remaining claims to stay waiting.

### Failure and payment rules

| Situation | Required behavior |
| --- | --- |
| Transient model timeout, connection error, rate limit, or provider server error | Retry the model call once with bounded backoff, within the current attempt's deadline. Disable SDK retries to avoid nested retry loops. |
| Invalid tool input or correctable API rejection | Return a useful tool error so the helper can correct it within its step/deadline budget. Do not retry unchanged validation or financial rejections blindly. |
| First claim attempt ends without a decision | Record the failure, release the active lock, return the claim to Waiting, and automatically offer one fresh attempt in the same queue run. |
| Second claim attempt ends without a decision | Persist `needs_handler` and its error; exclude the claim from future automatic runs. Do not fabricate a `refer` decision, payout, or payment. |
| Decision or payment was saved before an error | Read the backend to reconcile the result. Never rerun adjudication. Preserve saved records and recover only a permitted outstanding payment; an unsigned over-limit approval waits for a handler. |
| Payment/decision response was lost | Read the existing record before retrying. A matching saved result resolves the ambiguity; a conflict is surfaced. Existing transactional uniqueness prevents duplicate decisions/payments. |
| Queue agent/provider cannot continue after bounded recovery | Mark the queue Failed with a useful error, release unfinished work, and preserve all completed results and attempt counts. Permanent configuration/authentication errors are not retried indefinitely. |
| Stop or backend restart | Stop begins no new claim after the active helper exits. Restart marks active work Interrupted, releases locks, and preserves counts/to-dos/history. There is no automatic process-restart resumption in this build. |

There are at most two claim attempts under the new harness failure budget, persisted across queue starts and backend restarts. A single named-claim run uses the same bounded recovery and handler-handoff rules. Existing pilot history remains visible and does not retroactively exhaust the new budget. Corrected model/tool calls inside an attempt do not count as another claim attempt.

Keep explicit request timeouts, a finite child graph step limit, a per-attempt deadline, and a queue graph budget sized to its target and retry allowance. Test the limits with injected clocks and stubs. Do not abandon a running synchronous helper with an outer timeout and immediately start another: cancelling an `asyncio.to_thread` await does not stop its worker. Release the lock and permit a retry only after that bounded worker has exited, so a late call cannot race the next attempt.

The backend continues to enforce the full approved amount, integer-cent storage, one decision, one simulated payment, and the $1,000 boundary. No agent receives a sign-off tool. A technical handler flag is separate from a policy referral and from Waiting for sign-off.

### Guidance and instructions

Add `data/claims_manual.md` to an explicit backend document allowlist alongside the full `data/policy_wording.md`. Split each Markdown document by its numbered level-two headings and preserve its introduction/version metadata. Use a simple deterministic heading/text search that returns matching section IDs, titles, and short excerpts; reading a section returns its complete text and document version. No embeddings or general filesystem access are needed for these two documents.

The helper discovers sections through a section index/search and reads the ones needed for the claim. Neither full document is injected into its initial prompt or exposed as a whole-document agent tool. Retain the existing full-wording HTTP endpoint for dashboard display if useful. Record successful section reads on the child's claim with document ID, version, section ID/title, and returned text so the page can show exactly what the helper read, rather than just “Completed successfully.”

Write separate backend-owned queue and claim instructions. Queue instructions describe planning, cap accounting, delegation, retries, reconciliation, stop handling, and truthful summaries. Claim instructions explain how to find and apply the manual and wording, with the wording taking precedence. They direct the helper to check benefit conditions, documents, exclusions/add-ons, filing and trip dates, customer history, and the payout calculation. Include lookup pointers for repeat claims, wild-animal losses, and meals without receipts; do not embed either whole document in the prompt.

Use the actual manual as the source for new behavior, including the distinction between a missing document and a condition the customer says was never met, discrepancies requiring investigation, and the third claim within 12 months. Supply claim history from backend records, combining earlier customer history and platform claims by filing date and deduplicating claim IDs, so open/declined claims count and the current claim is not counted twice. Use synthetic examples for tests.

Retain `policy_references` and add explicit manual section references to new decisions. Store backend-sourced wording/manual versions and agent attribution with each new decision, as the manual requires. New request fields are required; old stored decisions remain readable without invented references or versions. Explanations remain two or three sentences with a separate calculation and any referral's required next action. Nothing is sent to a customer.

Default both queue and claim models to `deepseek/deepseek-v4.1-flash` on OpenRouter with low reasoning effort. Preserve `MODEL_NAME`; an explicitly selected default uses low reasoning, while another override keeps the existing behavior of not forcing a reasoning option. Do not change the student's `.env` silently. The live acceptance run must explicitly select the new default if the local override would mask it.

### API and storage changes

Every operation keeps a unique `operation_id`, a one-line docstring, typed responses, and required inputs only. The following is the intended contract; existing financial and claim activity paths remain in use.

| Method and path | Required input / result |
| --- | --- |
| `POST /api/queue/runs` | Discriminated body: `{"mode":"all"}` or `{"mode":"capped","max_claims":5}`. Every field in the selected variant is required; cap must be a positive integer. Return 202 with run/thread IDs, scope, target, and progress. |
| `GET /api/queue/runs/current` | Latest persisted queue state, including to-dos, admitted IDs, attempts, errors, and final summary; null before any run. |
| `POST /api/queue/runs/{queue_run_id}/stop` | Preserve stop-after-current behavior and return the updated run. |
| `PUT /api/queue/runs/{queue_run_id}/todos` | Required complete list of typed to-do items; validate scope, cap, and state transitions before saving. |
| `POST /api/queue/runs/{queue_run_id}/claims/{claim_id}/attempts` | Atomically admit/start a claim attempt and return its bound thread ID. This is used by the delegation tool; it does not launch a second background worker. |
| `POST /api/queue/runs/{queue_run_id}/attempts/{thread_id}/finish` | Required `error` string, empty on normal helper exit. Verify stored results and finish the bound attempt transactionally; repeated reports cannot increment failures twice. |
| `GET /api/queue/runs/{queue_run_id}/check` | Remaining eligible IDs, retry IDs, unused slots, and completion blockers derived from stored state. |
| `GET /api/guidance/{document}/sections` | Section index for required document enum `wording` or `manual`, with version metadata. |
| `GET /api/guidance/{document}/search` | Required `query`; return section matches/excerpts without whole-document contents. |
| `GET /api/guidance/{document}/sections/{section_id}` | Complete text of one known section and its document/version metadata. |
| `GET /api/agent/instructions` | Both queue and claim instruction texts used at runtime and displayed read only. Update both clients together. |
| `POST /api/claims/{claim_id}/runs` | Preserve one named-claim start, now using the same attempt budget and recovery rules. |

Use 404 for unknown records/sections, 422 for invalid bodies, 409 for conflicting state/cap exhaustion/duplicate starts, and the existing 403 for unsigned over-limit payment. Reject starts against a claim left for a handler. Prevent competing queue and named starts from claiming the same work through backend locks, not just disabled buttons. A queue completion check and terminal state update use one transaction so a late admission cannot race completion.

Reuse the current SQLite records plus runs/events tables. Add only the fields/records needed for queue thread/scope, persisted to-dos, attempt linkage/counts, handler handoff, summaries, and guidance versions. Put idempotent initialization/upgrades before the existing seeded-database early return; opening an existing database must add the harness data without reseeding or deleting decisions. Normalize older queue snapshots when reading them. Preserve claim activity across runs, and keep new activity/progress readable after refresh or restart. Persisted monitoring is not durable agent execution.

## Phase 1 — Persisted queue and attempt contracts

Build the state and API foundation first so cap, handoff, and locking rules can be tested without a model. Extend storage/schemas, `Runs`, and `QueueRuns`; keep the old runner working until the agent-led path replaces it. Add the required all/capped request body and typed to-do state.

Success criteria:

- [x] Backend tests open both a fresh temporary database and a seeded pilot database. The upgrade is repeatable, preserves decisions/sign-offs/payments/activity, and leaves the student's normal database untouched.
- [x] With 25 eligible fixtures, a capped start at five returns 202 and a target of five; All returns a target of 25. Zero, negative, fractional, missing, and malformed caps return 422. An empty queue or duplicate active start returns 409.
- [x] Concurrent admission tests allow at most five distinct claims for a five-claim run, at most one active child, and one retry in the same slot. Out-of-scope and sixth-claim attempts are rejected without changing records.
- [x] Named starts and queue starts cannot create competing active attempts for one claim. To-dos and attempt linkage survive reopening SQLite; old queue snapshots remain readable.
- [ ] A model-supplied Done status cannot hide an unresolved claim. Completion and admission cannot race. `/openapi.json` confirms unique operation IDs and required inputs for each request variant.
- [x] `npm run check:python` passes at the existing threshold. Update this plan and `docs/STATUS.md`, then commit through the pre-commit hook.

## Phase 2 — Section lookup and decision provenance

Add a small backend guidance module for the two allowlisted documents, the lookup routes, and claim-tool section-read activity. Extend decision references/version metadata and the backend's claim-history view. This supplies the source material before changing agent behavior.

Success criteria:

- [ ] Guidance tests expose wording sections 1–12 and manual sections 1–14 with their introductions and versions. Reading manual section 10 returns the complete meals rule; section 7 includes its full interpretation notes.
- [ ] Searches for synthetic queries about meals, wild animals, and repeat claims return the relevant sections. Empty/no-match and invalid-document/section cases have deterministic, tested responses. Paths cannot select arbitrary business files.
- [ ] Seed/read allowlist tests permit the manual and full wording while rejecting evaluation, held-out, security, shortened-wording, and unrelated files. Agent tests still forbid direct filesystem/SQLite business-data access.
- [ ] Successful section reads persist document/version/section/title/text on the correct claim attempt. Failed reads do not create a false read record, and history remains available after reload.
- [ ] A synthetic customer's prior, open, and declined claims are counted by filing date without duplicate IDs/current-claim double counting. Only that customer's history is returned to the helper.
- [ ] New decision tests validate both kinds of section references and persist manual/wording versions and agent attribution; older pilot decisions display without fabricated metadata. Existing financial boundary/duplicate-payment tests still pass.
- [ ] `npm run check:python` passes. Update this plan and `docs/STATUS.md`, then commit through the pre-commit hook.

## Phase 3 — Fresh claim helper, instructions, and bounded middleware

Refactor `agent/main.py` into small modules only where responsibilities warrant it: model configuration, claim/queue tools, and middleware. Keep the public named-claim entry point. Give each helper a clean message history, new attempt ID, section tools, and backend-served claim instructions. Add model retry, deadline/step, and observable activity controls.

Success criteria:

- [ ] Stub models exercise approve, decline, refer, and unsigned over-limit approval through real HTTP tool handlers. The saved decision/payment, not the final model sentence, determines success.
- [ ] Captured initial prompts contain claim instructions and an ID, with no whole manual/wording, answer key, other child's evidence, or other child's transcript. Tools fetch claim data and relevant sections on demand; no whole-document agent tool remains.
- [ ] Synthetic manual cases cover a third claim in 12 months, a qualifying wild-animal loss, meals without receipts, and a missing-document referral. Verify the actual section reads, saved references, calculation, and decision using scripted model responses; these tests verify wiring, not model judgment accuracy.
- [ ] Both agent roles select the new default with low reasoning when `MODEL_NAME` is absent or equals that default. Other overrides remain honored without forced reasoning settings. Configuration tests make no live request.
- [ ] Transient model errors get one retry with bounded backoff; permanent errors terminate clearly. Correctable tool errors reach the stub; invalid financial requests are not blindly retried. Repeating tools or a delayed provider hit tested limits rather than loop forever.
- [ ] Timeout tests prove no second helper/attempt starts while the first worker can still mutate state. Lost-response tests reconcile a saved decision/payment without duplicating it. No sign-off tool or cross-claim mutation target is available.
- [ ] `npm run check:python` passes. Update this plan and `docs/STATUS.md`, then commit through the pre-commit hook.

## Phase 4 — Agent-led queue, to-dos, recovery, and completion

Replace the normal fixed queue loop with the queue agent. Add the planning, delegation, and reconciliation tools and backend-served queue instructions. Make the delegation tool contain child exceptions and return a verified one-line result. Reconcile terminal states from backend records and persist the final handler summary.

Success criteria:

- [ ] A scripted parent chooses claim IDs in a different order from the API list, writes/updates its persisted to-dos, delegates sequentially, and finishes with a short summary matching backend records. Each child receives fresh context; the parent receives only compact results.
- [ ] A five-claim stub run admits exactly five distinct eligible claims, including a retried one; the rest remain Waiting. All mode accounts for the full snapshot, and named mode processes only its requested claim.
- [ ] A deliberately failing first claim becomes Waiting, gets a new thread for its automatic retry, and does not stop other claims. Two failed attempts set a persistent handler flag with no fake decision/payment. A later queue start/restart does not reset its budget or retry a handler-held claim.
- [ ] Failure after saving a decision/payment preserves it and never causes a second adjudication. An approval awaiting sign-off is successfully reviewed, remains unpaid, and appears in the handler summary.
- [ ] A stub that omits an eligible claim or leaves an unused cap slot receives the reconciliation result and processes the missed work. A stub that ignores correction cannot mark the queue Completed. Externally resolved candidates are accounted for and do not waste an available replacement slot.
- [ ] Stop after current claim starts neither another claim nor its retry after the active helper exits. Refresh preserves progress; abrupt restart marks active work Interrupted and clears locks without launching a model. A later explicit run can retry an eligible interrupted claim within its remaining budget.
- [ ] Queue-level permanent errors/step exhaustion preserve saved results and leave no claim In progress. Final counts distinguish decided claims, handler needs, technical failures, skipped work, and untouched claims.
- [ ] `npm run check:python` passes. Update this plan and `docs/STATUS.md`, then commit through the pre-commit hook.

## Phase 5 — Queue to-dos, section activity, and readable instructions

Extend the existing queue controls and polling rather than redesigning the claims desk. Add All/limited selection, a labeled positive limit input, the current run's to-do list and summary, and links to each claim. Extend the activity display with readable, expandable section text and the instructions page with separate queue/claim sections. Preserve the prominent decision/payout result and existing handler payment actions.

Follow `docs/BRAND.md`: current bundled fonts, USD formatting, rounded panels without outlines, readable colors, short business labels, and text status indicators. Keep keyboard access, visible focus, and narrow-screen layout. Show technical handler needs separately from policy referrals/sign-off.

Success criteria:

- [ ] Vitest covers All/limited submission bodies, positive-limit validation, duplicate-start protection, stop, polling, errors, reload restoration, empty state, and the final summary. The UI does not assume the first queue row is the agent's first chosen claim.
- [ ] A delayed stub makes To do → In progress → Done/Left for a handler visible before the run ends. Retry status and a handler-held technical error link to the correct claim and retain earlier attempt activity.
- [ ] Claim activity shows the actual manual/wording section title, version, and text read by its helper. References remain distinct from the saved decision explanation/calculation.
- [ ] The instructions page shows exactly the API-served queue and claim instructions read only. Existing sign-off/payment, named-claim start, selected-claim URL, and approval-result regression tests still pass.
- [ ] With `npm run dev`, inspect queue planning, active/retrying/finished work, handler needs, section activity, instructions, and sign-off at desktop and narrow mobile widths using Playwright MCP. Verify keyboard use, no horizontal overflow, useful error messages, brand, and no application console errors. If MCP remains unavailable, record that limit and use automated Playwright plus screenshot inspection without claiming an MCP check.
- [ ] `npm run check:ts` and `npm run check:python` pass at unchanged coverage thresholds. Update this plan and `docs/STATUS.md`, then commit through the pre-commit hook.

## Phase 6 — End-to-end acceptance, five-claim trial, and handover

Extend the isolated Playwright server with scripted queue and claim models so browser tests exercise the real orchestration, APIs, SQLite rules, and UI without OpenRouter. Keep all test databases separate from the student's pilot records. Then perform the brief's separate live acceptance trial and update the product documents.

Success criteria:

- [ ] Playwright covers a capped queue run with visible to-do updates and section reads, automatic first-failure retry, a twice-failed handler handoff, continuation on other claims, missed-claim correction, a short final summary, and reload persistence. Assert API records as well as screen text.
- [ ] Browser/API regression coverage verifies named-claim work, Stop after current claim, no duplicate decisions/payments, and payment gating at $1,000.00/$1,000.01, including a direct blocked payment before handler sign-off.
- [ ] Add/document a local reseed command using `uv run` or a `.mjs` helper that works on macOS and Windows. Exercise it on a temporary database first. It targets an explicitly chosen local SQLite file, with no model calls and no public reset API. Preserve the existing pilot database before any live trial.
- [ ] Before every live run, reseed the chosen trial database and set a cap of five, as the brief requires. Use a fresh separate trial database so existing pilot decisions/history are retained. Explicitly select `deepseek/deepseek-v4.1-flash` with low reasoning without overwriting `.env`, and use the normal dashboard/backend, not the scripted test server.
- [ ] In that live run, observe the to-do list filling/changing, five distinct claims reviewed autonomously, fresh child attempts, section reads on each claim, persisted decisions/calculations/references/versions, correct payment/sign-off state, and a final summary. Confirm the other 20 claims remain Waiting. Record model ID, queue ID, selected claim IDs, result, and timing without credentials. A technical handler handoff/provider failure is reported honestly as a live acceptance limitation, not five successful reviews; do not launch an uncapped fallback.
- [ ] Update `docs/SPEC.md` to include the claims manual and queue behavior; `docs/TECH_STACK.md` to describe the default model, queue/claim entry points, tools/middleware, and SQLite monitoring; and `docs/STATUS.md` with actual run/reseed commands, new behavior, checks, live result, and remaining limits. Update `docs/QUEUE_RUN.md` to replace its superseded code-ordered/no-retry contract. Leave `docs/HARNESS.md` and `docs/PLAN.md` unchanged.
- [ ] Run `npm run check` successfully: ruff formatting/lint, pyright, pytest at 80% coverage, Prettier, ESLint, strict TypeScript, Vitest at 80% coverage, and Playwright. All automated tests use stubs and make zero model calls. Do not lower thresholds, disable rules, or exclude files to pass.
- [ ] Tick only verified criteria in this plan, update `docs/STATUS.md`, and commit the final phase with the pre-commit hook enabled. Report the automated checks separately from live validation. Do not push.

## Delivery discipline

After the student asks to implement, complete the six phases in order without stopping between phases: build with tests, verify the criteria, update this plan and `docs/STATUS.md`, then commit with the pre-commit hook. If a rule or threshold appears incorrect, stop and ask instead of weakening it. Keep unrelated notebook edits and local credentials intact. No AWS resources are needed or authorized for this build.

## Planning validation

On November 16, 2026, the existing `npm run check` passed: 58 Python tests with 99.17% coverage, 13 dashboard tests with 96.96% statement coverage, and 9 Playwright tests. These results establish the current build's baseline, not delivery of the harness criteria above. All automated checks used stubs; no live model run or reseed was performed during planning.
