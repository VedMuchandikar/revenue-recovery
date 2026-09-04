#!/usr/bin/env python3
"""Test script to verify Phase 5: Guardrails implementation."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, DiagnosisSource, RootCause,
    ProposedAction, RecoveryAction, NotificationChannel,
    GuardrailCheck, GuardrailResult, StoppingRuleState
)
from app.engine.guardrails import (
    check_guardrails, check_amount_cap, check_max_attempts,
    check_mandate_retry_limit, check_cooldown, check_customer_opt_out
)
from app.engine.orchestrator import process_event


async def test_amount_cap():
    """Test amount cap guardrail."""
    print("🧪 Testing Amount Cap Guardrail")

    # Test 1: Amount under cap
    event1 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('3000.00'),  # Under 50000 cap
        currency='INR',
        customer_id='cust_001',
        razorpay_ref_id='pay_001',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )

    passed, details = await check_amount_cap(event1)
    print(f"   Amount 3000: {passed} - {details}")
    if passed:
        print("   ✅ PASSED - Amount under cap allowed")
    else:
        print("   ❌ FAILED")

    # Test 2: Amount over cap
    event2 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('100000.00'),  # Over 50000 cap
        currency='INR',
        customer_id='cust_002',
        razorpay_ref_id='pay_002',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )

    passed, details = await check_amount_cap(event2)
    print(f"   Amount 100000: {passed} - {details}")
    if not passed:
        print("   ✅ PASSED - Amount over cap blocked (escalate)")
    else:
        print("   ❌ FAILED")


async def test_max_attempts():
    """Test max attempts guardrail."""
    print("\n🧪 Testing Max Attempts Guardrail")

    # Test 1: Under max attempts
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
        retry_count=1  # 1 + attempt 1 = 2, under limit of 3
    )
    action1 = ProposedAction(
        event_id=event1.id,
        action_type=RecoveryAction.PAYMENT_LINK,
        channel=NotificationChannel.SMS,
        attempt_number=1
    )

    passed, details = await check_max_attempts(event1, action1)
    print(f"   Retry 1 + attempt 1: {passed} - {details}")
    if passed:
        print("   ✅ PASSED - Under max attempts")
    else:
        print("   ❌ FAILED")

    # Test 2: At max attempts
    event2 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_002',
        razorpay_ref_id='pay_002',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc),
        retry_count=2  # 2 + attempt 2 = 4, at limit of 3
    )
    action2 = ProposedAction(
        event_id=event2.id,
        action_type=RecoveryAction.PAYMENT_LINK,
        channel=NotificationChannel.SMS,
        attempt_number=2
    )

    passed, details = await check_max_attempts(event2, action2)
    print(f"   Retry 2 + attempt 2: {passed} - {details}")
    if not passed:
        print("   ✅ PASSED - Max attempts reached (block)")
    else:
        print("   ❌ FAILED")


async def test_mandate_retry_limit():
    """Test mandate retry limit guardrail."""
    print("\n🧪 Testing Mandate Retry Limit")

    # Test 1: First mandate retry
    event1 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.SUBSCRIPTION_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_001',
        razorpay_ref_id='sub_001',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )
    action1 = ProposedAction(
        event_id=event1.id,
        action_type=RecoveryAction.MANDATE_RETRY,
        channel=NotificationChannel.EMAIL,
        attempt_number=1
    )

    passed, details = await check_mandate_retry_limit(event1, action1, None)
    print(f"   First mandate retry: {passed} - {details}")
    if passed:
        print("   ✅ PASSED - First retry allowed")
    else:
        print("   ❌ FAILED")

    # Test 2: At limit (4 retries)
    event2 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.SUBSCRIPTION_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_002',
        razorpay_ref_id='sub_002',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )
    action2 = ProposedAction(
        event_id=event2.id,
        action_type=RecoveryAction.MANDATE_RETRY,
        channel=NotificationChannel.EMAIL,
        attempt_number=1
    )
    state = StoppingRuleState(
        event_id=event2.id,
        mandate_retry_count=4  # At limit
    )

    passed, details = await check_mandate_retry_limit(event2, action2, state)
    print(f"   4th mandate retry: {passed} - {details}")
    if not passed:
        print("   ✅ PASSED - Limit reached (block)")
    else:
        print("   ❌ FAILED")


async def test_cooldown():
    """Test cooldown guardrail."""
    print("\n🧪 Testing Cooldown Guardrail")

    # Test 1: No previous action
    event1 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_001',
        razorpay_ref_id='pay_001',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )

    passed, details = await check_cooldown(event1, None)
    print(f"   No previous action: {passed} - {details}")
    if passed:
        print("   ✅ PASSED - No cooldown needed")
    else:
        print("   ❌ FAILED")

    # Test 2: Cooldown active
    event2 = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_002',
        razorpay_ref_id='pay_002',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )
    state = StoppingRuleState(
        event_id=event2.id,
        cooldown_until=datetime.now(timezone.utc) + timedelta(minutes=30)  # 30 min from now
    )

    passed, details = await check_cooldown(event2, state)
    print(f"   Cooldown active: {passed} - {details}")
    if not passed:
        print("   ✅ PASSED - Cooldown blocks action")
    else:
        print("   ❌ FAILED")


async def test_customer_opt_out():
    """Test customer opt-out guardrail."""
    print("\n🧪 Testing Customer Opt-Out Guardrail")

    event = RevenueEvent(
        id=str(uuid.uuid4()),
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=Decimal('5000.00'),
        currency='INR',
        customer_id='cust_001',
        razorpay_ref_id='pay_001',
        provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
        detected_at=datetime.now(timezone.utc)
    )
    action = ProposedAction(
        event_id=event.id,
        action_type=RecoveryAction.NOTIFY_EMAIL,  # Notification action
        channel=NotificationChannel.EMAIL,
        attempt_number=1
    )

    # Test: Customer opted out
    state = StoppingRuleState(
        event_id=event.id,
        customer_opted_out=True
    )

    passed, details = await check_customer_opt_out(event, action, state)
    print(f"   Customer opted out: {passed} - {details}")
    if not passed:
        print("   ✅ PASSED - Opt-out blocks notification")
    else:
        print("   ❌ FAILED")


async def test_full_guardrail_check():
    """Test full guardrail check function."""
    print("\n🧪 Testing Full Guardrail Check")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test 1: All checks pass
        print("\n🔍 Test 1: All checks pass")
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
            retry_count=0
        )
        session.add(event1)
        await session.commit()

        action1 = ProposedAction(
            event_id=event1.id,
            action_type=RecoveryAction.PAYMENT_LINK,
            channel=NotificationChannel.SMS,
            attempt_number=1
        )
        session.add(action1)
        await session.commit()

        check, result = await check_guardrails(event1, action1, session)
        print(f"   Result: {result.value}")
        print(f"   Details: {check.details[:80]}...")

        if result == GuardrailResult.ALLOW:
            print("   ✅ PASSED - All guardrails passed")
        else:
            print("   ❌ FAILED")

        # Test 2: Amount over cap -> ESCALATE
        print("\n🔍 Test 2: Amount over cap (should ESCALATE)")
        event2 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('100000.00'),  # Over cap
            currency='INR',
            customer_id='cust_002',
            razorpay_ref_id='pay_002',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event2)
        await session.commit()

        action2 = ProposedAction(
            event_id=event2.id,
            action_type=RecoveryAction.PAYMENT_LINK,
            channel=NotificationChannel.SMS,
            attempt_number=1
        )
        session.add(action2)
        await session.commit()

        check2, result2 = await check_guardrails(event2, action2, session)
        print(f"   Result: {result2.value}")

        if result2 == GuardrailResult.ESCALATE:
            print("   ✅ PASSED - Amount over cap escalates")
        else:
            print("   ❌ FAILED")


async def test_orchestrator_with_guardrails():
    """Test orchestrator with real guardrails."""
    print("\n🧪 Testing Orchestrator with Guardrails")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test: Amount over cap should escalate
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('100000.00'),  # Over 50000 cap
            currency='INR',
            customer_id='cust_big',
            razorpay_ref_id='pay_big',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            reason_code='card_declined'
        )
        session.add(event)
        await session.commit()

        await process_event(event.id, session)

        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event.id)
        result = await session.execute(query)
        processed = result.scalar_one_or_none()

        print(f"   Event status: {processed.status.value}")

        if processed.status == RevenueEventStatus.ESCALATED:
            print("   ✅ PASSED - High amount escalated")
        else:
            print("   ℹ️  Status: " + processed.status.value)


if __name__ == "__main__":
    asyncio.run(test_amount_cap())
    asyncio.run(test_max_attempts())
    asyncio.run(test_mandate_retry_limit())
    asyncio.run(test_cooldown())
    asyncio.run(test_customer_opt_out())
    asyncio.run(test_full_guardrail_check())
    asyncio.run(test_orchestrator_with_guardrails())
    print("\n🎉 Phase 5 guardrail tests completed!")