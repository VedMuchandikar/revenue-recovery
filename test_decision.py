#!/usr/bin/env python3
"""Test script to verify Phase 4: Decision Engine implementation."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, DiagnosisSource, RootCause,
    ProposedAction, RecoveryAction, NotificationChannel
)
from app.engine.decide import _rule_based_decision, decide_action
from app.engine.orchestrator import process_event


async def test_rule_based_decision():
    """Test rule-based decision mappings."""
    print("🧪 Testing Rule-Based Decision")

    test_cases = [
        # (root_cause, expected_action, expected_channel)
        (RootCause.CARD_DECLINED, RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
        (RootCause.INSUFFICIENT_FUNDS, RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
        (RootCause.AUTHENTICATION_FAILURE, RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
        (RootCause.GATEWAY_TIMEOUT, RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
        (RootCause.SUBSCRIPTION_FAILURE, RecoveryAction.MANDATE_RETRY, NotificationChannel.EMAIL),
        (RootCause.CHECKOUT_ABANDONMENT, RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
        (RootCause.RECEIVABLE_OVERDUE, RecoveryAction.INVOICE_REMINDER, NotificationChannel.EMAIL),
        (RootCause.UNKNOWN, RecoveryAction.NOTIFY_EMAIL, NotificationChannel.EMAIL),
    ]

    passed = 0
    failed = 0

    for root_cause, expected_action, expected_channel in test_cases:
        # Create mock diagnosis
        diagnosis = Diagnosis(
            id=str(uuid.uuid4()),
            event_id=str(uuid.uuid4()),
            root_cause=root_cause,
            source=DiagnosisSource.RULE,
            confidence=1.0,
            rationale="test"
        )

        # Create minimal event
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('5000.00'),
            currency='INR',
            customer_id='cust_test',
            razorpay_ref_id='ref_test',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )

        result = _rule_based_decision(event, diagnosis)

        if result:
            action_type, channel, rationale = result
            if action_type == expected_action and channel == expected_channel:
                print(f"✅ {root_cause.value} → {action_type.value} via {channel.value}")
                passed += 1
            else:
                print(f"❌ {root_cause.value} → {action_type.value} via {channel.value} (expected {expected_action.value} via {expected_channel.value})")
                failed += 1
        else:
            print(f"❌ {root_cause.value} → None (expected {expected_action.value})")
            failed += 1

    print(f"\nRule-based decision: {passed} passed, {failed} failed")
    return failed == 0


async def test_decide_action():
    """Test the full decide_action function."""
    print("\n🧪 Testing decide_action function")

    # Initialize database
    await init_db()

    async with async_session_factory() as session:
        # Clear any existing test data
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(Diagnosis))
        await session.execute(delete(ProposedAction))
        await session.commit()

        # Test 1: Card declined -> payment_link via SMS
        print("\n🔍 Test 1: card_declined → payment_link via SMS")
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
            reason_code='card_declined',
            retry_count=0
        )
        session.add(event1)
        await session.commit()

        # Create diagnosis first
        diagnosis1 = Diagnosis(
            event_id=event1.id,
            root_cause=RootCause.CARD_DECLINED,
            source=DiagnosisSource.RULE,
            confidence=1.0,
            rationale="Rule-based mapping: error code 'card_declined' → card_declined"
        )
        session.add(diagnosis1)
        await session.commit()

        proposed_action1 = await decide_action(event1, diagnosis1, session)
        print(f"   Action: {proposed_action1.action_type.value} via {proposed_action1.channel.value}")
        print(f"   Attempt: {proposed_action1.attempt_number}")

        if proposed_action1.action_type == RecoveryAction.PAYMENT_LINK:
            print("   ✅ PASSED")
        else:
            print("   ❌ FAILED")

        # Test 2: Subscription failure -> mandate_retry
        print("\n🔍 Test 2: subscription_failure → mandate_retry")
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
            reason_code='mandate_failed',
            retry_count=2
        )
        session.add(event2)
        await session.commit()

        diagnosis2 = Diagnosis(
            event_id=event2.id,
            root_cause=RootCause.SUBSCRIPTION_FAILURE,
            source=DiagnosisSource.RULE,
            confidence=1.0,
            rationale="Rule-based: subscription failure"
        )
        session.add(diagnosis2)
        await session.commit()

        proposed_action2 = await decide_action(event2, diagnosis2, session)
        print(f"   Action: {proposed_action2.action_type.value} via {proposed_action2.channel.value}")
        print(f"   Attempt: {proposed_action2.attempt_number} (should be retry_count + 1 = 3)")

        if proposed_action2.action_type == RecoveryAction.MANDATE_RETRY:
            print("   ✅ PASSED")
        else:
            print("   ❌ FAILED")

        # Test 3: Receivable overdue -> invoice_reminder
        print("\n🔍 Test 3: receivable_overdue → invoice_reminder")
        event3 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.RECEIVABLE_OVERDUE,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('25000.00'),
            currency='INR',
            customer_id='cust_003',
            razorpay_ref_id='inv_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event3)
        await session.commit()

        diagnosis3 = Diagnosis(
            event_id=event3.id,
            root_cause=RootCause.RECEIVABLE_OVERDUE,
            source=DiagnosisSource.RULE,
            confidence=1.0,
            rationale="Rule-based: overdue invoice"
        )
        session.add(diagnosis3)
        await session.commit()

        proposed_action3 = await decide_action(event3, diagnosis3, session)
        print(f"   Action: {proposed_action3.action_type.value} via {proposed_action3.channel.value}")

        if proposed_action3.action_type == RecoveryAction.INVOICE_REMINDER:
            print("   ✅ PASSED")
        else:
            print("   ❌ FAILED")

    print("\n🎉 Phase 4 decision tests completed!")
    return True


async def test_full_orchestrator_with_decision():
    """Test full orchestrator with real decision."""
    print("\n🧪 Testing Full Orchestrator with Decision")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test with full pipeline
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('3000.00'),
            currency='INR',
            customer_id='cust_full',
            razorpay_ref_id='pay_full',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            reason_code='insufficient_balance'
        )
        session.add(event)
        await session.commit()

        await process_event(event.id, session)

        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event.id)
        result = await session.execute(query)
        processed = result.scalar_one_or_none()

        action_query = select(ProposedAction).where(ProposedAction.event_id == event.id)
        action_result = await session.execute(action_query)
        proposed = action_result.scalar_one_or_none()

        print(f"   Event status: {processed.status.value}")
        print(f"   Proposed action: {proposed.action_type.value} via {proposed.channel.value}")

        if proposed.action_type == RecoveryAction.PAYMENT_LINK:
            print("   ✅ PASSED - Decision works in orchestrator")
        else:
            print("   ❌ FAILED")


if __name__ == "__main__":
    asyncio.run(test_decide_action())
    asyncio.run(test_full_orchestrator_with_decision())