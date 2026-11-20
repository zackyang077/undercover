# The claims pilot and harness

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy and our policy wording. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The pilot

We want an AI agent, the UnderCover Agent, to work the queue the way a handler does. For each claim it should:

- read the claim, the customer's policy and claim history, and relevant sections of the policy wording and claims manual
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off or decided.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step.
- Each dispatch's claims, progress, model cost, elapsed time and outcome summary.
- A Scoreboard with evaluations, their accuracy, cost and time, and each claim's decision alongside the handler's.
- The agent's instructions, read only.

## Working the queue

A dispatcher selects waiting and recoverable in-progress claims, oldest
submitted first. The handler chooses between 1 and 25 claim agents at once,
with 3 selected initially. A handler can also start one named claim. The next
dispatch continues eligible work left unfinished by a stopped dispatch. One
dispatch or evaluation can run at a time.

Each helper reads evidence and versioned guidance through the backend, selects
covered expenses and uses UnderCover's payout calculator. The claim page shows
the sections read, the decision, its references and its calculation. An approved
payout that needs a handler waits on its thread while other claims proceed.
Signing off records the handler and time. The claim shows In progress until the
agent records its simulated payment. The signed approval survives an outage.

Interrupted work resumes from its last saved step on the same attempt. A rate
limit or model timeout can retry on that thread. After those retries run out,
the next dispatch can continue the attempt. An undecided claim with another
error gets one fresh attempt. Two failures leave it for a handler. The backend
uses the saved claims to summarize each dispatch. Claims can be evaluated as
independent copies using the whole evaluation or held-out set, or five tiny
claims from either. A held payout on an evaluation copy counts as finished.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and claims manual; the wording takes precedence.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.
