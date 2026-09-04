"""Metrics API endpoints for revenue recovery dashboard."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Outcome, ProposedAction, SyntheticReceivable
)

router = APIRouter()


@router.get("/overview")
async def get_overview_metrics(
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get high-level metrics for the revenue recovery dashboard.

    Returns:
    - Total revenue at risk
    - Total revenue recovered
    - Recovery rate
    - Events by status and type
    """
    # Total revenue at risk (all events)
    total_risk_query = select(func.sum(RevenueEvent.amount))
    total_risk_result = await db_session.execute(total_risk_query)
    total_at_risk = total_risk_result.scalar() or 0

    # Total revenue recovered (from outcomes)
    total_recovered_query = select(func.sum(Outcome.recovered_amount))
    total_recovered_result = await db_session.execute(total_recovered_query)
    total_recovered = total_recovered_result.scalar() or 0

    # Recovery rate
    recovery_rate = float(total_recovered / total_at_risk * 100) if total_at_risk > 0 else 0

    # Events by status
    status_query = select(
        RevenueEvent.status,
        func.count(RevenueEvent.id).label('count'),
        func.sum(RevenueEvent.amount).label('total_amount')
    ).group_by(RevenueEvent.status)
    status_result = await db_session.execute(status_query)
    by_status = [
        {
            "status": row.status.value,
            "count": row.count,
            "total_amount": str(row.total_amount)
        }
        for row in status_result
    ]

    # Events by type
    type_query = select(
        RevenueEvent.type,
        func.count(RevenueEvent.id).label('count'),
        func.sum(RevenueEvent.amount).label('total_amount')
    ).group_by(RevenueEvent.type)
    type_result = await db_session.execute(type_query)
    by_type = [
        {
            "type": row.type.value,
            "count": row.count,
            "total_amount": str(row.total_amount)
        }
        for row in type_result
    ]

    # Total event count
    total_events_query = select(func.count(RevenueEvent.id))
    total_events_result = await db_session.execute(total_events_query)
    total_events = total_events_result.scalar() or 0

    # Pending events count (actionable)
    pending_query = select(func.count(RevenueEvent.id)).where(
        RevenueEvent.status == RevenueEventStatus.PENDING
    )
    pending_result = await db_session.execute(pending_query)
    pending_count = pending_result.scalar() or 0

    # Completed events count
    completed_query = select(func.count(RevenueEvent.id)).where(
        RevenueEvent.status == RevenueEventStatus.COMPLETED
    )
    completed_result = await db_session.execute(completed_query)
    completed_count = completed_result.scalar() or 0

    return {
        "total_events": total_events,
        "total_at_risk": str(total_at_risk),
        "total_recovered": str(total_recovered),
        "recovery_rate": round(recovery_rate, 2),
        "pending_count": pending_count,
        "completed_count": completed_count,
        "by_status": by_status,
        "by_type": by_type
    }


@router.get("/strategy")
async def get_strategy_metrics(
    db_session: AsyncSession = Depends(get_db),
):
    """Show the evidence the bounded planner uses to rank interventions."""
    rows = await db_session.execute(
        select(ProposedAction, Outcome)
        .outerjoin(Outcome, Outcome.event_id == ProposedAction.event_id)
    )
    grouped: dict[tuple[str, str], dict] = {}
    for action, outcome in rows.all():
        key = (action.action_type.value, action.channel.value)
        metric = grouped.setdefault(key, {
            "action_type": key[0], "channel": key[1],
            "attempts": 0, "verified_recoveries": 0,
        })
        metric["attempts"] += 1
        metric["verified_recoveries"] += int(outcome is not None)

    strategies = []
    for metric in grouped.values():
        attempts = metric["attempts"]
        recoveries = metric["verified_recoveries"]
        metric["recovery_rate"] = round(recoveries / attempts * 100, 2) if attempts else 0
        # Same Beta(1,1) smoothing as the planner.
        metric["planner_score"] = round((recoveries + 1) / (attempts + 2), 3)
        strategies.append(metric)
    strategies.sort(key=lambda item: (-item["planner_score"], item["action_type"]))
    return {"strategies": strategies}


@router.get("/timeline")
async def get_timeline_metrics(
    db_session: AsyncSession = Depends(get_db),
    days: int = Query(7, ge=1, le=90, description="Number of days to look back")
):
    """
    Get event timeline metrics for charting.

    Returns events detected per day for the last N days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Group by date (SQLite-compatible)
    timeline_query = select(
        func.date(RevenueEvent.detected_at).label('date'),
        func.count(RevenueEvent.id).label('count'),
        func.sum(RevenueEvent.amount).label('total_amount')
    ).where(
        RevenueEvent.detected_at >= cutoff
    ).group_by(
        func.date(RevenueEvent.detected_at)
    ).order_by(
        func.date(RevenueEvent.detected_at)
    )

    timeline_result = await db_session.execute(timeline_query)
    timeline = [
        {
            "date": str(row.date),
            "count": row.count,
            "total_amount": str(row.total_amount)
        }
        for row in timeline_result
    ]

    return {"days": days, "timeline": timeline}


@router.get("/receivables")
async def get_receivables_metrics(
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get metrics for synthetic receivables.
    """
    # Receivable counts by status
    status_query = select(
        SyntheticReceivable.status,
        func.count(SyntheticReceivable.id).label('count'),
        func.sum(SyntheticReceivable.amount).label('total_amount')
    ).group_by(SyntheticReceivable.status)
    status_result = await db_session.execute(status_query)
    by_status = [
        {
            "status": row.status,
            "count": row.count,
            "total_amount": str(row.total_amount)
        }
        for row in status_result
    ]

    # Total receivable amount
    total_query = select(func.sum(SyntheticReceivable.amount))
    total_result = await db_session.execute(total_query)
    total_amount = total_result.scalar() or 0

    # Overdue amount
    overdue_query = select(func.sum(SyntheticReceivable.amount)).where(
        SyntheticReceivable.status == "overdue"
    )
    overdue_result = await db_session.execute(overdue_query)
    overdue_amount = overdue_result.scalar() or 0

    return {
        "total_amount": str(total_amount),
        "overdue_amount": str(overdue_amount),
        "by_status": by_status
    }
