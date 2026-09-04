#!/usr/bin/env python3
"""Test script to verify Phase 3: Diagnosis with orchestrator integration."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, DiagnosisSource, RootCause
)
from app.engine.orchestrator import process_event


async def test_full_diagnosis_integration():
    """Test diagnosis in the full orchestrator pipeline."""
    print("🧪 Testing Phase 3: Diagnosis Integration")

    # Initialize database
    await init_db()

    async with async_session_factory() as session:
        # Clear any existing test data
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test 1: Payment failed with card_declined
        print("\n🔍 Test 1: Payment failed - card_declined")
        event1 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('5000.00'),
            currency='INR',
            customer_id='cust_001',
            razorpay_ref_id='pay_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            reason_code='card_declined'
        )
        session.add(event1)
        await session.commit()
        await process_event(event1.id, session)

        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event1.id)
        result = await session.execute(query)
        processed_event = result.scalar_one_or_none()

        diag_query = select(Diagnosis).where(Diagnosis.event_id == event1.id)
        diag_result = await session.execute(diag_query)
        diagnosis = diag_result.scalar_one_or_none()

        print(f"   Event status: {processed_event.status.value}")
        print(f"   Diagnosis: {diagnosis.root_cause.value} ({diagnosis.source.value})")
        print(f"   Confidence: {diagnosis.confidence}")

        if diagnosis.root_cause == RootCause.CARD_DECLINED:
            print("   ✅ PASSED - Correct root cause detected")
        else:
            print(f"   ❌ FAILED - Expected card_declined, got {diagnosis.root_cause.value}")

        # Test 2: Subscription failed
        print("\n🔍 Test 2: Subscription failed - mandate_failed")
        event2 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.SUBSCRIPTION_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('15000.00'),
            currency='INR',
            customer_id='cust_002',
            razorpay_ref_id='sub_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            reason_code='mandate_failed'
        )
        session.add(event2)
        await session.commit()
        await process_event(event2.id, session)

        query = select(RevenueEvent).where(RevenueEvent.id == event2.id)
        result = await session.execute(query)
        processed_event2 = result.scalar_one_or_none()

        diag_query = select(Diagnosis).where(Diagnosis.event_id == event2.id)
        diag_result = await session.execute(diag_query)
        diagnosis2 = diag_result.scalar_one_or_none()

        print(f"   Event status: {processed_event2.status.value}")
        print(f"   Diagnosis: {diagnosis2.root_cause.value} ({diagnosis2.source.value})")

        if diagnosis2.root_cause == RootCause.SUBSCRIPTION_FAILURE:
            print("   ✅ PASSED - Subscription failure detected")
        else:
            print(f"   ❌ FAILED - Expected subscription_failure, got {diagnosis2.root_cause.value}")

        # Test 3: Checkout abandoned
        print("\n🔍 Test 3: Checkout abandoned")
        event3 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.CHECKOUT_ABANDONED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('2000.00'),
            currency='INR',
            customer_id='cust_003',
            razorpay_ref_id='order_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc)
        )
        session.add(event3)
        await session.commit()
        await process_event(event3.id, session)

        query = select(RevenueEvent).where(RevenueEvent.id == event3.id)
        result = await session.execute(query)
        processed_event3 = result.scalar_one_or_none()

        diag_query = select(Diagnosis).where(Diagnosis.event_id == event3.id)
        diag_result = await session.execute(diag_query)
        diagnosis3 = diag_result.scalar_one_or_none()

        print(f"   Event status: {processed_event3.status.value}")
        print(f"   Diagnosis: {diagnosis3.root_cause.value} ({diagnosis3.source.value})")

        if diagnosis3.root_cause == RootCause.CHECKOUT_ABANDONMENT:
            print("   ✅ PASSED - Checkout abandonment detected")
        else:
            print(f"   ❌ FAILED - Expected checkout_abandonment, got {diagnosis3.root_cause.value}")

    print("\n🎉 Phase 3 integration tests completed!")
    return True


if __name__ == "__main__":
    asyncio.run(test_full_diagnosis_integration())