# One-click queue processing

Handlers can start the UnderCover Agent on all currently Waiting claims with
one action and see progress across the queue.

## Behavior

- Start queue captures the currently Waiting claim IDs and processes them in
  queue order, one at a time, using the configured model and existing agent.
- Decided claims, approvals waiting for sign-off, and active claims are excluded.
  If another caller starts or decides a captured claim before its turn, it is skipped.
- Only one queue run can be active. Repeated starts return 409.
- A failed claim retains its history and the queue continues. Failed claims are
  not automatically retried during the same pass. A later explicit Start queue
  includes failed claims that still have no decision and are Waiting.
- Stop after current claim lets that claim finish and leaves the rest Waiting.
  It does not cancel a decision or simulated payment in progress.
- The dashboard shows processed/total, completed/failed/skipped counts, the
  current claim, and failed-claim details. Refreshing restores monitoring.
- The latest queue summary is stored in SQLite. Starting another pass replaces
  that summary; every individual claim's run and activity history is retained.
- Shutdown requests a stop after the current claim. An abrupt restart marks the
  queue Interrupted. Remaining work starts only after another explicit action.
- Payments above $1,000 still require handler sign-off through the existing
  payment gate. An approval awaiting sign-off counts as a completed agent review.

## Implementation

The dashboard calls `POST /api/queue/runs`. The backend creates the snapshot
and invokes the existing `Runs` coordinator and `run(claim_id, thread_id)` for
each claim. The agent continues to use its existing backend-only tools.
The dashboard polls progress and refreshes the queue and selected claim.

| Method and path | Operation ID | Response |
| --- | --- | --- |
| `POST /api/queue/runs` | `start_queue_run` | 202 with queue ID and captured claim items; 409 for active/empty queue. |
| `GET /api/queue/runs/current` | `get_queue_run` | Latest queue summary, or null if none has started. |
| `POST /api/queue/runs/{queue_run_id}/stop` | `stop_queue_run` | Updated summary with stop requested; 404 for unknown ID, 409 if already finished. |

No optional API inputs are added. Execution uses the current local backend
process. This change adds no model tools, cloud resources, or automatic sign-off.

## Verified acceptance

- [x] One action captures all Waiting claims and runs them sequentially.
- [x] Completed, active, and sign-off-waiting claims are excluded; changed claims are skipped.
- [x] A duplicate start is blocked while work is active.
- [x] One model failure is recorded and the next claim still completes.
- [x] Stop finishes the current claim without starting remaining claims.
- [x] Refresh restores progress; restart records Interrupted without resuming.
- [x] Over-limit payment is still rejected before handler sign-off.
- [x] Desktop and mobile browser scenarios pass in an isolated database using stub models.
- [x] `npm run check` passes without changing rules or coverage thresholds.
