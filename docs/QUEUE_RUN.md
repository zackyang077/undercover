# Agent-led queue runs

The queue agent chooses the order from a snapshot of Waiting claims. Each claim
is delegated to a fresh helper, sequentially. The backend enforces claim locks,
cap admission, two-attempt limits, saved outcomes, and financial rules.

## Starting and monitoring

| Method and path | Operation ID | Contract |
| --- | --- | --- |
| `POST /api/queue/runs` | `start_queue_run` | Required `{"mode":"all"}` or `{"mode":"capped","max_claims":5}`; return 202. Invalid cap: 422; empty/active queue: 409. |
| `GET /api/queue/runs/current` | `get_queue_run` | Latest scope, target, admitted IDs, to-dos, item progress/errors, summary, and lifecycle; null before any run. |
| `POST /api/queue/runs/{queue_run_id}/stop` | `stop_queue_run` | Request stop after the active helper; 404 for unknown run, 409 after completion. |
| `PUT /api/queue/runs/{queue_run_id}/todos` | `write_queue_todos` | Required `items` with claim ID, text, and a status matching actual work. |
| `POST /api/queue/runs/{queue_run_id}/claims/{claim_id}/attempts` | `admit_queue_claim` | Atomically reserve a slot and attempt/thread; invoked by the delegation tool. |
| `POST /api/queue/runs/{queue_run_id}/attempts/{thread_id}/finish` | `finish_queue_attempt` | Required `error` string; verify saved business records and release the attempt. Repeated reports do not double-count failures. |
| `GET /api/queue/runs/{queue_run_id}/check` | `check_queue` | Remaining candidates, pending retries, unused slots, active work, stop state, and completion readiness. |

A cap counts distinct claims, including terminal failures; a retry reuses its
slot. The agent selects which claims to admit rather than receiving a fixed first
N. Claims arriving later belong to another run. Externally resolved candidates
are skipped and do not consume replacement slots. The agent checks remaining
work before finishing; the completion guard also checks and permits one bounded
correction before an incomplete run is reported as Failed.

## Failures and handler work

- An undecided first failure returns to Waiting and receives one fresh retry.
- Two failed/interrupted harness attempts persist a Needs handler state without
  fabricating a policy decision. Later runs do not reset the budget.
- Saved decisions are never replaced. Saved payments are never repeated. A
  permissible unpaid approval is reconciled separately; financial sign-off is
  never automatic. An approval waiting for sign-off is successfully reviewed.
- A policy referral, unsigned approval, and technical failure can each produce
  a Left for a handler to-do, with distinct claim/decision/payment states.
- The parent sees concise child results, not their evidence or transcripts.
  All child tool steps and versioned section reads remain on the relevant claim.

To-dos use `pending`, `in_progress`, `done`, and `needs_handler`. The dashboard
shows To do, In progress, Done, and Left for a handler, and preserves monitoring
across refresh. A stop begins no new claim or retry after the active helper exits.
Restart records Interrupted and preserves attempts/to-dos; another explicit run
is needed. This is persisted monitoring in one local process, not durable execution.

The latest queue summary is retained; claim attempt/activity history is retained
across subsequent queue starts. Named-claim runs use the same recovery/payment
rules and cannot compete with an active queue. No agent has a sign-off tool.

See [STATUS.md](STATUS.md) for startup, reseeding, and the separate five-claim live
trial procedure. Automated tests always use stubs and temporary databases.
