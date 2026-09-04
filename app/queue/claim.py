"""Mechanism for claiming revenue events from the database queue."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RevenueEvent, RevenueEventStatus


async def claim_pending_event(db_session: AsyncSession) -> Optional[RevenueEvent]:
    """
    Atomically claim the oldest PENDING revenue event.

    Uses SELECT ... FOR UPDATE SKIP LOCKED pattern for safe concurrent access.
    Falls back to regular SELECT FOR UPDATE if SKIP LOCKED not supported.

    Args:
        db_session: Database session

    Returns:
        Claimed RevenueEvent or None if no pending events
    """
    try:
        # Try to claim the oldest PENDING event with row-level lock
        # Using subquery to get oldest first, then update with locking
        query = (
            select(RevenueEvent)
            .where(RevenueEvent.status == RevenueEventStatus.PENDING)
            .order_by(RevenueEvent.created_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)  # PostgreSQL/MySQL syntax
        )

        result = await db_session.execute(query)
        event = result.scalar_one_or_none()

        if event:
            # Transition to PROCESSING state
            event.status = RevenueEventStatus.PROCESSING
            event.processing_started_at = datetime.now(timezone.utc)
            # Note: retry_count is incremented in the orchestrator on failure

            await db_session.commit()
            await db_session.refresh(event)
            return event

    except NotImplementedError:
        # skip_locked not supported (e.g., SQLite), use fallback
        return await _claim_pending_event_fallback(db_session)
    except Exception:
        await db_session.rollback()
        raise

    return None


async def _claim_pending_event_fallback(db_session: AsyncSession) -> Optional[RevenueEvent]:
    """
    Fallback claim mechanism for databases that don't support SKIP LOCKED.
    Uses regular SELECT ... FOR UPDATE which may cause more contention.

    Args:
        db_session: Database session

    Returns:
        Claimed RevenueEvent or None if no pending events
    """
        # Begin transaction explicitly for better control
    async with db_session.begin():
        # Select oldest PENDING event with lock
        query = (
            select(RevenueEvent)
            .where(RevenueEvent.status == RevenueEventStatus.PENDING)
            .order_by(RevenueEvent.created_at.asc())
            .limit(1)
            .with_for_update()  # Standard row lock
        )

        result = await db_session.execute(query)
        event = result.scalar_one_or_none()

        if event:
            # Double-check status in case it changed between select and lock
            if event.status == RevenueEventStatus.PENDING:
                event.status = RevenueEventStatus.PROCESSING
                event.processing_started_at = datetime.now(timezone.utc)
                await db_session.flush()  # Flush to get the updates
                await db_session.refresh(event)
                return event
            # else: event was modified by another process, return None

    return None


async def recover_stale_processing_events(
    db_session: AsyncSession,
    stale_threshold_minutes: int = 10
) -> int:
    """
    Recover events that have been stuck in PROCESSING state too long (worker crash).

    Args:
        db_session: Database session
        stale_threshold_minutes: Minutes after which PROCESSING events are considered stale

    Returns:
        Number of events recovered
    """
    from datetime import timedelta

    stale_threshold = datetime.now(timezone.utc) - timedelta(minutes=stale_threshold_minutes)

    # Find events that have been PROCESSING longer than threshold
    query = (
        select(RevenueEvent)
        .where(
            and_(
                RevenueEvent.status == RevenueEventStatus.PROCESSING,
                RevenueEvent.processing_started_at < stale_threshold
            )
        )
    )

    result = await db_session.execute(query)
    stale_events = result.scalars().all()

    recovered_count = 0
    for event in stale_events:
        # Transition back to PENDING for retry
        event.status = RevenueEventStatus.PENDING
        event.processing_started_at = None
        event.retry_count += 1
        event.last_error = "Worker crash recovery - event was stale in PROCESSING"
        recovered_count += 1

    if recovered_count > 0:
        await db_session.commit()

    return recovered_count