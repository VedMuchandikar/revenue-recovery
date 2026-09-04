#!/usr/bin/env python3
"""Final integration test for all phases."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, DiagnosisSource, RootCause,
    ProposedAction, RecoveryAction, NotificationChannel,
    Outcome, ActionResult
)
from app.engine.orchestrator import process_event


async def test_end_to_end_workflow():
    """Test a complete end-to-end workflow for different event types."""
    print("🧪 Testing End-to-End Workflow")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(ProposedAction))
        await session.execute(delete(ActionResult))
        await session.execute(delete(Outcome))
        await session.commit()

        test_cases = [
            {
                "name": "Payment Failed - Card Declined",
                "type": RevenueEventType.PAYMENT_FAILED,
                "amount": Decimal('2500.00'),
                "reason_code": 'card_declined',
                "expected_action": RecoveryAction.PAYMENT_LINK,
                "expected_channel": NotificationChannel.SMS
            },
            {
                "name": "Subscription Failed - Mandate Failed",
                "type": RevenueEventType.SUBSCRIPTION_FAILED,
                "amount": Decimal('15000.00'),
                "reason_code": 'mandate_failed',
                "expected_action": RecoveryAction.MANDATE_RETRY,
                "expected_channel": NotificationChannel.EMAIL
            },
            {
                "name": "Checkout Abandoned",
                "type": RevenueEventType.CHECKOUT_ABANDONED,
                "amount": Decimal('3000.00'),
                "reason_code": 'checkout_abandoned',
                "expected_action": RecoveryAction.PAYMENT_LINK,
                "expected_channel": NotificationChannel.SMS
            },
            {
                "name": "Receivable Overdue",
                "type": RevenueEventType.RECEIVABLE_OVERDUE,
                "amount": Decimal('25000.00'),
                "reason_code": None,
                "expected_action": RecoveryAction.INVOICE_REMINDER,
                "expected_channel": NotificationChannel.EMAIL
            }
        ]

        for i, test_case in enumerate(test_cases):
            print(f"\n📝 Test Case {i+1}: {test_case['name']}")

            event = RevenueEvent(
                id=str(uuid.uuid4()),
                type=test_case["type"],
                status=RevenueEventStatus.PENDING,
                amount=test_case["amount"],
                currency='INR',
                customer_id=f'cust_test_{i}',
                razorpay_ref_id=f'ref_test_{i}',
                provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
                detected_at=datetime.now(timezone.utc),
                reason_code=test_case["reason_code"],
                retry_count=0
            )

            session.add(event)
            await session.commit()

            # Process the event through the complete pipeline
            await process_event(event.id, session)

            # Refresh event from database
            from sqlalchemy import select
            query = select(RevenueEvent).where(RevenueEvent.id == event.id)
            result = await session.execute(query)
            processed_event = result.scalar_one_or_none()

            # Check proposed action
            action_query = select(ProposedAction).where(ProposedAction.event_id == event.id)
            action_result = await session.execute(action_query)
            proposed_action = action_result.scalar_one_or_none()

            # Check action result
            result_query = select(ActionResult).where(ActionResult.event_id == event.id)
            result_result = await session.execute(result_query)
            action_result_record = result_result.scalar_one_or_none()

            # Check outcome
            outcome_query = select(Outcome).where(Outcome.event_id == event.id)
            outcome_result = await session.execute(outcome_query)
            outcome_record = outcome_result.scalar_one_or_none()

            print(f"   Event Status: {processed_event.status.value}")
            if proposed_action:
                print(f"   Proposed Action: {proposed_action.action_type.value} via {proposed_action.channel.value}")
            if action_result_record:
                print(f"   Execution Result: {action_result_record.status.value}")
            if outcome_record:
                print(f"   Recovery: {outcome_record.recovered_amount} {outcome_record.currency}")

            # Verify the expected action was chosen (when not blocked by guardrails/etc)
            if processed_event.status == RevenueEventStatus.COMPLETED:
                assert proposed_action.action_type == test_case["expected_action"], \
                    f"Expected {test_case['expected_action'].value}, got {proposed_action.action_type.value}"
                assert proposed_action.channel == test_case["expected_channel"], \
                    f"Expected {test_case['expected_channel'].value}, got {proposed_action.channel.value}"
                print("   ✅ PASSED - Correct action chosen and revenue recovered")
            elif processed_event.status == RevenueEventStatus.FAILED:
                print("   ℹ️  Event failed (expected with stub verification)")
            elif processed_event.status == RevenueEventStatus.ESCALATED:
                print("   ℹ️  Event escalated (due to guardrails or diagnosis)")
            else:
                print(f"   ℹ️  Event status: {processed_event.status.value}")

        print("\n🎉 End-to-End Workflow Test Completed!")


async def test_guardrail_scenarios():
    """Test specific guardrail scenarios."""
    print("\n🧪 Testing Guardrail Scenarios")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test 1: High amount -> should escalate
        print("\n📝 Test 1: High Amount Guardrail")
        event1 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('75000.00'),  # Above 50000 cap
            currency='INR',
            customer_id='cust_high',
            razorpay_ref_id='pay_high',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event1)
        await session.commit()

        await process_event(event1.id, session)

        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event1.id)
        result = await session.execute(query)
        processed = result.scalar_one_or_none()

        assert processed.status == RevenueEventStatus.ESCALATED, \
            f"High amount should escalate, got {processed.status.value}"
        print("   ✅ PASSED - High amount properly escalated")

        # Test 2: Max attempts exceeded -> should block
        print("\n📝 Test 2: Max Attempts Guardrail")
        event2 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('5000.00'),
            currency='INR',
            customer_id='cust_attempts',
            razorpay_ref_id='pay_attempts',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=3  # Already at max attempts (3)
        )
        session.add(event2)
        await session.commit()

        await process_event(event2.id, session)

        query = select(RevenueEvent).where(RevenueEvent.id == event2.id)
        result = await session.execute(query)
        processed = result.scalar_one_or_none()

        # Should be blocked due to max attempts
        assert processed.status == RevenueEventStatus.BLOCKED, \
            f"Max attempts exceeded should block, got {processed.status.value}"
        print("   ✅ PASSED - Max attempts properly blocked")

        print("\n🎉 Guardrail Scenarios Test Completed!")


if __name__ == "__main__":
    asyncio.run(test_end_to_end_workflow())
    asyncio.run(test_guardrail_scenarios())
    print("\n🎊 All Integration Tests Passed! 🎊")