# Revenue Recovery Agent

A bounded, auditable agent that identifies revenue at risk, selects a
policy-permitted intervention, executes it through Razorpay, and records money
as recovered only after a verified webhook.

## Why this is an agent

This is deliberately not an unconstrained chatbot. It closes a recovery loop:

`detect → diagnose → plan → guardrails → execute → verify → learn`

- **Detect:** Razorpay failure webhooks, checkout-abandonment polling, and
  receivables batches create durable `RevenueEvent` records.
- **Diagnose:** deterministic payment signals produce a reliable root cause;
  an optional LLM handles ambiguity.
- **Plan:** the agent builds a policy-approved candidate set, ranks it by
  verified historical recovery rates, and records its scores and rationale.
  Set `PLANNER_USE_LLM=true` to let the LLM select only from that set.
- **Guardrails:** amount cap, retry cap, mandate limits, cooldown, and opt-out
  checks can block or escalate an action.
- **Execute:** payment links, invoices, notifications, and subscription-retry
  monitoring are persisted as `ActionResult` records.
- **Verify:** a Razorpay `payment.captured` or `payment_link.paid` webhook is
  linked to the originating action and creates the sole `Outcome` record.
- **Learn:** `/api/metrics/strategy` shows attempts, verified recoveries, and
  the smoothed score used by the planner.

## Demo storyline

1. A batch of failed payments enters the durable queue.
2. The agent shows its diagnosis, permitted intervention candidates, and the
   recovery score that selected one.
3. A guardrail blocks an unsafe high-value or over-retried event.
4. A payment link is created and the event becomes `awaiting_payment`.
5. A `payment.captured` or `payment_link.paid` webhook verifies recovery; the
   dashboard shows recovered rupees and updated strategy evidence.

## Run locally

```bash
source .venv/bin/activate
python start.py
```

Open `http://localhost:8000`.

- `GET /api/metrics/overview` — at-risk and recovered amounts
- `GET /api/metrics/strategy` — intervention evidence and planner scores
- `GET /api/events/{event_id}` — diagnosis, rationale, action, and outcome

## Accuracy and safety

- `COMPLETED` means a webhook verified recovery.
- `AWAITING_PAYMENT` means an intervention succeeded but payment is pending.
- Razorpay controls eligible subscription retries. The app monitors the
  `sub_...` subscription; it does not claim to manually debit a mandate.
