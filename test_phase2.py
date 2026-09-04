#!/usr/bin/env python3
"""Test script to verify Phase 2 implementation."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.queue import claim_pending_event, recover_stale_processing_events
from app.config.settings import settings
from app.db.database import async_session_factory
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    DiagnosisSource, RootCause
)


async def test_queue_mechanism():
    """Test the queue claim and recovery mechanisms."""
    print("🧪 Testing Phase 2: Queue + Worker + Orchestrator")

    # Initialize database
    from app.db.database import init_db
    await init_db()
    print("✅ Database initialized")

    async with async_session_factory() as session:
        # Clear any existing test data
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Create test events
        test_events = [
            RevenueEvent(
                id=str(uuid.uuid4()),
                type=RevenueEventType.PAYMENT_FAILED,
                status=RevenueEventStatus.PENDING,
                amount=Decimal('5000.00'),
                currency='INR',
                customer_id='cust_001',
                razorpay_ref_id='ref_001',
                provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
                detected_at=datetime.now(timezone.utc)
            ),
            RevenueEvent(
                id=str(uuid.uuid4()),
                type=RevenueEventType.SUBSCRIPTION_FAILED,
                status=RevenueEventStatus.PENDING,
                amount=Decimal('10000.00'),
                currency='INR',
                customer_id='cust_002',
                razorpay_ref_id='ref_002',
                provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
                detected_at=datetime.now(timezone.utc)
            )
        ]

        for event in test_events:
            session.add(event)
        await session.commit()
        print(f"✅ Created {len(test_events)} test events")

        # Test claiming events
        print("\n🔍 Testing event claiming...")
        claimed_event = await claim_pending_event(session)
        if claimed_event:
            print(f"✅ Claimed event: {claimed_event.id}")
            print(f"   Type: {claimed_event.type.value}")
            print(f"   Status: {claimed_event.status.value}")
            print(f"   Amount: {claimed_event.amount} {claimed_event.currency}")

            # Test that claiming the same event again returns None (already claimed)
            # Need to commit first to release the lock, then try in a new session
            await session.commit()

            # New session to test concurrent access protection
            async with async_session_factory() as session2:
                second_claim = await claim_pending_event(session2)
                if second_claim is None:
                    print("✅ Second claim correctly returned None (event already claimed)")
                else:
                    print(f"❌ ERROR: Second claim should have returned None, got {second_claim.id}")
        else:
            print("❌ ERROR: Failed to claim event")
            return False

        # Test stale event recovery
        print("\n⏰ Testing stale event recovery...")
        # Manually set an event to old processing time
        old_time = datetime.now(timezone.utc) - timedelta(minutes=15)

        stale_event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.CHECKOUT_ABANDONED,
            status=RevenueEventStatus.PROCESSING,
            amount=Decimal('3000.00'),
            currency='INR',
            customer_id='cust_003',
            razorpay_ref_id='ref_003',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=old_time,
            processing_started_at=old_time
        )
        session.add(stale_event)
        await session.commit()

        recovered_count = await recover_stale_processing_events(
            session,
            stale_threshold_minutes=10
        )
        if recovered_count > 0:
            print(f"✅ Recovered {recovered_count} stale events")
        else:
            print("❌ ERROR: No stale events recovered")

        # Check that the stale event is now pending
        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == stale_event.id)
        result = await session.execute(query)
        recovered_event = result.scalar_one_or_none()
        if recovered_event and recovered_event.status == RevenueEventStatus.PENDING:
            print("✅ Stale event correctly reset to PENDING status")
        else:
            print("❌ ERROR: Stale event not properly reset")

        await session.rollback()  # Don't commit test changes

    print("\n🎉 Phase 2 queue mechanism tests completed!")
    return True


if __name__ == "__main__":
    asyncio.run(test_queue_mechanism())