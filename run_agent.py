#!/usr/bin/env python3
"""Run the revenue recovery agent to process pending events."""

import asyncio
import logging
from datetime import datetime

from app.db.database import async_session_factory, init_db
from app.db.models import RevenueEvent, RevenueEventStatus
from app.engine.orchestrator import process_event
from sqlalchemy import select, update

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

EVENTS_TO_CREATE = [
    ("card_declined", 5000, "customer1@test.com"),
    ("insufficient_funds", 3000, "customer2@test.com"),
    ("mandate_failed", 10000, "customer3@test.com"),
    ("gateway_timeout", 1500, "customer4@test.com"),
]


async def create_test_events():
    """Create test PENDING events."""
    from app.db.models import RevenueEventType
    async with async_session_factory() as session:
        for i, (reason, amount, email) in enumerate(EVENTS_TO_CREATE):
            event = RevenueEvent(
                id=f"demo-event-{i+1}",
                type=RevenueEventType.PAYMENT_FAILED,
                status=RevenueEventStatus.PENDING,
                amount=amount,
                currency="INR",
                customer_id=email,
                razorpay_ref_id=f"pay_demo_{i+1}",
                provider_event_id=f"prov_demo_{i+1}",
                reason_code=reason,
                retry_count=0,
                detected_at=datetime.now(),
                created_at=datetime.now(),
                updated_at=datetime.now(),
            )
            session.add(event)

        await session.commit()
        logger.info(f"Created {len(EVENTS_TO_CREATE)} test events")


async def process_all_pending():
    """Process all PENDING events."""
    async with async_session_factory() as session:
        # Get all pending events
        result = await session.execute(
            select(RevenueEvent).where(RevenueEvent.status == RevenueEventStatus.PENDING)
        )
        pending_events = result.scalars().all()

        logger.info(f"Found {len(pending_events)} PENDING events")

        for event in pending_events:
            logger.info(f"→ Processing: {event.id} ({event.reason_code})")
            try:
                await process_event(event.id, session)
                await session.commit()
                logger.info(f"  ✓ Completed: {event.id}")
            except Exception as e:
                await session.rollback()
                logger.error(f"  ✗ Failed: {event.id} - {e}")


async def show_results():
    """Show final event statuses."""
    async with async_session_factory() as session:
        result = await session.execute(select(RevenueEvent))
        events = result.scalars().all()

        print("\n" + "="*60)
        print("FINAL RESULTS")
        print("="*60)

        for event in events:
            status_icon = "✓" if event.status == RevenueEventStatus.COMPLETED else "✗"
            print(f"{status_icon} {event.id[:20]}... | {event.status.value:12} | {event.reason_code or 'N/A':20} | ₹{event.amount}")

        completed = sum(1 for e in events if e.status == RevenueEventStatus.COMPLETED)
        print(f"\n{completed}/{len(events)} events processed successfully")
        print("="*60)


async def main():
    """Main entry point."""
    # Import the enum here to avoid issues
    from app.db.models import RevenueEventType

    print("="*60)
    print("REVENUE RECOVERY AGENT - DEMO")
    print("="*60)

    # Initialize DB
    await init_db()

    # Create test events
    print("\n[1] Creating test events...")
    await create_test_events()

    # Process them
    print("\n[2] Processing events with AI agent...")
    await process_all_pending()

    # Show results
    await show_results()


if __name__ == "__main__":
    asyncio.run(main())