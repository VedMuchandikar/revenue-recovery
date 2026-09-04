#!/usr/bin/env python3
"""Test script to verify Phase 6: Execution Engine implementation."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, DiagnosisSource, RootCause,
    ProposedAction, RecoveryAction, NotificationChannel,
    ActionResult
)
from app.engine.execute import (
    execute_action, verify_action,
    _execute_payment_link, _execute_mandate_retry,
    _execute_notify_sms, _execute_notify_email,
    _execute_invoice_reminder, _execute_promise_to_pay,
    _verify_payment_link, _verify_mandate_retry,
    _verify_notification, _verify_invoice_payment,
    _verify_promise_to_pay
)
from app.engine.orchestrator import process_event
from app.execution.razorpay_client import RazorpayClient


async def test_razorpay_client_stub_mode():
    """Test Razorpay client behavior when not configured."""
    print("🧪 Testing Razorpay Client Stub Mode")

    client = RazorpayClient()

    # Should not be configured without credentials
    assert not client.is_configured(), "Client should not be configured without credentials"

    # Test payment link creation fails gracefully
    response = client.create_payment_link(
        amount=1000,
        currency="INR",
        customer_name="Test",
        customer_email="test@example.com",
        customer_phone="+919999999999",
        reference_id="test_ref",
        description="Test"
    )

    assert not response.success, "Should fail when not configured"
    assert "not configured" in response.error.lower(), "Should indicate not configured"
    print("   ✅ PASSED - Client handles unconfigured state gracefully")


async def test_execute_payment_link_stub():
    """Test payment link execution in stub mode."""
    print("\n🧪 Testing Payment Link Execution (Stub)")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        event = RevenueEvent(
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
        session.add(event)
        await session.commit()

        decision = {
            "action_type": RecoveryAction.PAYMENT_LINK.value,
            "channel": NotificationChannel.SMS.value
        }

        result = await execute_action(event, decision, session)

        assert result["success"], "Execution should succeed in stub mode"
        assert result["action_type"] == RecoveryAction.PAYMENT_LINK.value
        assert result["channel"] == NotificationChannel.SMS.value
        assert result["external_ref_id"] is not None
        print("   ✅ PASSED - Payment link execution works in stub mode")


async def test_execute_mandate_retry_stub():
    """Test mandate retry execution in stub mode."""
    print("\n🧪 Testing Mandate Retry Execution (Stub)")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.SUBSCRIPTION_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('15000.00'),
            currency='INR',
            customer_id='cust_002',
            razorpay_ref_id='sub_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=2
        )
        session.add(event)
        await session.commit()

        decision = {
            "action_type": RecoveryAction.MANDATE_RETRY.value,
            "channel": NotificationChannel.EMAIL.value
        }

        result = await execute_action(event, decision, session)

        assert result["success"], "Execution should succeed in stub mode"
        assert result["action_type"] == RecoveryAction.MANDATE_RETRY.value
        assert result["channel"] == NotificationChannel.EMAIL.value
        print("   ✅ PASSED - Mandate retry execution works in stub mode")


async def test_execute_notification_stub():
    """Test notification execution in stub mode."""
    print("\n🧪 Testing Notification Execution (Stub)")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('3000.00'),
            currency='INR',
            customer_id='cust_003',
            razorpay_ref_id='pay_002',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event)
        await session.commit()

        # Test SMS
        decision_sms = {
            "action_type": RecoveryAction.NOTIFY_SMS.value,
            "channel": NotificationChannel.SMS.value
        }

        result_sms = await execute_action(event, decision_sms, session)
        assert result_sms["success"], "SMS execution should succeed"
        assert result_sms["action_type"] == RecoveryAction.NOTIFY_SMS.value
        print("   ✅ PASSED - SMS notification execution works")

        # Test Email
        decision_email = {
            "action_type": RecoveryAction.NOTIFY_EMAIL.value,
            "channel": NotificationChannel.EMAIL.value
        }

        result_email = await execute_action(event, decision_email, session)
        assert result_email["success"], "Email execution should succeed"
        assert result_email["action_type"] == RecoveryAction.NOTIFY_EMAIL.value
        print("   ✅ PASSED - Email notification execution works")


async def test_execute_invoice_reminder_stub():
    """Test invoice reminder execution in stub mode."""
    print("\n🧪 Testing Invoice Reminder Execution (Stub)")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.RECEIVABLE_OVERDUE,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('25000.00'),
            currency='INR',
            customer_id='cust_004',
            razorpay_ref_id='inv_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event)
        await session.commit()

        decision = {
            "action_type": RecoveryAction.INVOICE_REMINDER.value,
            "channel": NotificationChannel.EMAIL.value
        }

        result = await execute_action(event, decision, session)

        assert result["success"], "Execution should succeed in stub mode"
        assert result["action_type"] == RecoveryAction.INVOICE_REMINDER.value
        assert result["channel"] == NotificationChannel.EMAIL.value
        print("   ✅ PASSED - Invoice reminder execution works in stub mode")


async def test_verify_functions_stub():
    """Test verification functions in stub mode."""
    print("\n🧪 Testing Verification Functions (Stub)")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        event = RevenueEvent(
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
        session.add(event)
        await session.commit()

        # Test payment link verification
        execution_result = {
            "action_type": RecoveryAction.PAYMENT_LINK.value,
            "channel": NotificationChannel.SMS.value,
            "success": True,
            "external_ref_id": f"plink_{event.id}"
        }

        verification_result = await verify_action(event, execution_result)
        assert "verified" in verification_result
        assert "verification_method" in verification_result
        assert "verified_at" in verification_result
        print("   ✅ PASSED - Payment link verification works")

        # Test mandate retry verification
        execution_result["action_type"] = RecoveryAction.MANDATE_RETRY.value
        verification_result = await verify_action(event, execution_result)
        assert "verified" in verification_result
        print("   ✅ PASSED - Mandate retry verification works")

        # Test notification verification
        execution_result["action_type"] = RecoveryAction.NOTIFY_EMAIL.value
        verification_result = await verify_action(event, execution_result)
        assert verification_result["verified"] == True, "Notifications should verify as delivered"
        assert "delivery_confirmed" in verification_result["verification_method"]
        print("   ✅ PASSED - Notification verification works")

        # Test invoice reminder verification
        execution_result["action_type"] = RecoveryAction.INVOICE_REMINDER.value
        verification_result = await verify_action(event, execution_result)
        assert "verified" in verification_result
        print("   ✅ PASSED - Invoice reminder verification works")

        # Test promise to pay verification
        execution_result["action_type"] = RecoveryAction.PROMISE_TO_PAY.value
        verification_result = await verify_action(event, execution_result)
        assert "verified" in verification_result
        print("   ✅ PASSED - Promise to pay verification works")


async def test_full_execution_pipeline():
    """Test the full execution pipeline with decision making."""
    print("\n🧪 Testing Full Execution Pipeline")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(ProposedAction))
        await session.execute(delete(ActionResult))
        await session.commit()

        # Test payment failed -> payment_link -> SMS
        event1 = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('3000.00'),
            currency='INR',
            customer_id='cust_exec_001',
            razorpay_ref_id='pay_exec_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            reason_code='card_declined',
            retry_count=0
        )
        session.add(event1)
        await session.commit()

        # Process through orchestrator (will use real decision + stub execution/verification)
        await process_event(event1.id, session)

        # Check final status
        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event1.id)
        result = await session.execute(query)
        processed_event = result.scalar_one_or_none()

        # Should be either completed, failed, or still processing based on stub verification
        print(f"   Event status: {processed_event.status.value}")
        assert processed_event.status in [
            RevenueEventStatus.COMPLETED,
            RevenueEventStatus.FAILED,
            RevenueEventStatus.PROCESSING
        ], "Event should be in a valid terminal or processing state"
        print("   ✅ PASSED - Full execution pipeline works")


async def test_execution_with_guardrails():
    """Test that execution respects guardrails."""
    print("\n🧪 Testing Execution with Guardrails")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Test high amount event that should be escalated by guardrails
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('100000.00'),  # Above 50000 cap
            currency='INR',
            customer_id='cust_guardrail',
            razorpay_ref_id='pay_guardrail',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc),
            retry_count=0
        )
        session.add(event)
        await session.commit()

        # Process event - should be escalated by guardrails before execution
        await process_event(event.id, session)

        # Check final status
        from sqlalchemy import select
        query = select(RevenueEvent).where(RevenueEvent.id == event.id)
        result = await session.execute(query)
        processed_event = result.scalar_one_or_none()

        assert processed_event.status == RevenueEventStatus.ESCALATED, \
            f"High amount event should be escalated, got {processed_event.status.value}"
        print("   ✅ PASSED - Guardrails properly block execution for high amounts")


if __name__ == "__main__":
    asyncio.run(test_razorpay_client_stub_mode())
    asyncio.run(test_execute_payment_link_stub())
    asyncio.run(test_execute_mandate_retry_stub())
    asyncio.run(test_execute_notification_stub())
    asyncio.run(test_execute_invoice_reminder_stub())
    asyncio.run(test_verify_functions_stub())
    asyncio.run(test_full_execution_pipeline())
    asyncio.run(test_execution_with_guardrails())
    print("\n🎉 Phase 6 execution tests completed!")