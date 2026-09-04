#!/usr/bin/env python3
"""Simple script to demonstrate the agent working."""

import asyncio
import logging
import uuid
from datetime import datetime

from app.db.database import async_session_factory, init_db
from app.db.models import RevenueEvent, RevenueEventStatus, RevenueEventType
from app.engine.orchestrator import process_event

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    """Create and process test events."""
    await init_db()
    # Webhook provider IDs are deliberately unique for idempotency. A demo
    # must therefore create a distinct batch on every run instead of trying
    # to re-insert the same synthetic provider events.
    demo_run_id = uuid.uuid4().hex[:8]

    test_cases = [
        ("card_declined", 5000, "test1@test.com"),
        ("insufficient_funds", 3000, "test2@test.com"),
        ("mandate_failed", 10000, "test3@test.com"),
        ("gateway_timeout", 1500, "test4@test.com"),
    ]

    # Create events
    async with async_session_factory() as session:
        for i, (reason, amount, email) in enumerate(test_cases):
            event_id = f"demo-{demo_run_id}-{i + 1}"
            event = RevenueEvent(
                id=event_id,
                type=RevenueEventType.PAYMENT_FAILED,
                status=RevenueEventStatus.PENDING,
                amount=amount,
                currency="INR",
                customer_id=email,
                razorpay_ref_id=f"pay_{demo_run_id}_{i + 1}",
                provider_event_id=f"demo_{demo_run_id}_{i + 1}",
                reason_code=reason,
                retry_count=0,
                detected_at=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(event)
        await session.commit()
        logger.info("Created %d PENDING events for demo run %s", len(test_cases), demo_run_id)

    # Process each
    for i, (reason, amount, email) in enumerate(test_cases):
        event_id = f"demo-{demo_run_id}-{i + 1}"
        logger.info(f"Processing {event_id} ({reason})...")

        async with async_session_factory() as session:
            try:
                await process_event(event_id, session)
                await session.commit()
                logger.info(f"  ✓ {event_id} processed")
            except Exception as e:
                await session.rollback()
                logger.error(f"  ✗ {event_id} failed: {e}")

    # Show results
    print("\n" + "="*50)
    print("RESULTS")
    print("="*50)

    async with async_session_factory() as session:
        from sqlalchemy import select
        result = await session.execute(
            select(RevenueEvent).where(RevenueEvent.id.like(f"demo-{demo_run_id}-%"))
        )
        events = result.scalars().all()

        for event in events:
            status = "✓" if event.status in {
                RevenueEventStatus.COMPLETED,
                RevenueEventStatus.AWAITING_PAYMENT,
            } else "✗"
            print(f"{status} {event.id}: {event.status.value} ({event.reason_code})")

    completed = sum(1 for e in events if e.status == RevenueEventStatus.COMPLETED)
    awaiting = sum(1 for e in events if e.status == RevenueEventStatus.AWAITING_PAYMENT)
    failed = sum(1 for e in events if e.status == RevenueEventStatus.FAILED)
    print(f"\n{completed} recovered; {awaiting} awaiting payment; {failed} failed")
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())
