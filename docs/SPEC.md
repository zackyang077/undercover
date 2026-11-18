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
- The agent's instructions, read only.

## Working the queue

One queue run chooses the order of waiting claims, keeps a visible to-do list,
and delegates each claim to a helper. A limited trial caps distinct claims. A
handler can start one named claim or stop a batch after its current claim.
Starting the next run continues the unfinished batch within its original cap.

Each helper reads evidence and versioned guidance through the backend, selects
covered expenses and uses UnderCover's payout calculator. The claim page shows
the sections read, the decision, its references and its calculation. An approved
payout that needs a handler waits on its thread while other claims proceed.
Signing off records the handler and time. The claim shows In progress until the
agent records its simulated payment. The signed approval survives an outage.

Interrupted work resumes from its last saved step on the same attempt. A failed
undecided claim gets one fresh retry within its original slot. Two failures
leave it for a handler. Replaying an existing decision or payment returns the
claim. The queue checks for missed work within its cap and shows a final summary.

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