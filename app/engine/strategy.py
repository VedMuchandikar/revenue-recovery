"""Evidence-based ranking for bounded recovery actions.

This module deliberately ranks only actions that are already permitted by the
product policy. It learns from verified outcomes, while guardrails remain the
final authority on whether an action may execute.
"""

from collections import defaultdict
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Outcome, ProposedAction, RecoveryAction, NotificationChannel


def amount_band(amount: Decimal) -> str:
    if amount < Decimal("2000"):
        return "small"
    if amount < Decimal("10000"):
        return "medium"
    return "high"


async def rank_candidates(
    db_session: AsyncSession,
    candidates: Iterable[tuple[RecoveryAction, NotificationChannel]],
    amount: Decimal,
    root_cause: str,
) -> list[dict]:
    """Rank allowed actions with smoothed historical recovery rates.

    A Beta(1, 1) prior prevents an action with one lucky recovery from
    overpowering the baseline. The score is retained with the decision so a
    reviewer can see exactly why an intervention was chosen.
    """
    allowed = list(dict.fromkeys(candidates))
    rows = await db_session.execute(
        select(ProposedAction, Outcome)
        .outerjoin(Outcome, Outcome.event_id == ProposedAction.event_id)
    )

    totals: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for action, outcome in rows.all():
        key = (action.action_type.value, action.channel.value)
        if key not in {(a.value, c.value) for a, c in allowed}:
            continue
        totals[key][0] += 1
        totals[key][1] += int(outcome is not None)

    ranked = []
    for order, (action, channel) in enumerate(allowed):
        attempts, recoveries = totals[(action.value, channel.value)]
        score = (recoveries + 1) / (attempts + 2)
        ranked.append({
            "action_type": action,
            "channel": channel,
            "historical_attempts": attempts,
            "historical_recoveries": recoveries,
            "expected_recovery_score": round(score, 3),
            "tie_break_order": order,
        })

    ranked.sort(key=lambda item: (-item["expected_recovery_score"], item["tie_break_order"]))
    return ranked
