#!/usr/bin/env python3
"""Test script to verify Phase 7: Webhook handlers implementation."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    AuditLog
)
from app.webhooks.handlers import (
    create_revenue_event_from_payment_failed,
    create_revenue_event_from_subscription_failed,
    verify_signature
)


async def test_webhook_event_creation():
    """Test creating RevenueEvents from webhook payloads."""
    print("🧪 Testing Webhook Event Creation")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(AuditLog))
        await session.commit()

        # Test 1: payment.failed webhook
        print("\n📝 Test 1: Payment Failed Webhook")
        payment_payload = {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test_123",
                        "amount": 25000,  # 250.00 INR in paise
                        "currency": "INR",
                        "email": "test@example.com",
                        "contact": "+919999999999",
                        "error_code": "bad_request_us",
                        "error_reason": "The amount cannot be less than 1 INR"
                    }
                }
            }
        }

        event1 = await create_revenue_event_from_payment_failed(payment_payload, session)
        assert event1 is not None, "Should create event for payment.failed"
        assert event1.type == RevenueEventType.PAYMENT_FAILED
        assert event1.status == RevenueEventStatus.PENDING
        assert event1.amount == Decimal('250.00')
        assert event1.currency == 'INR'
        assert event1.customer_id == 'test@example.com'
        assert event1.razorpay_ref_id == 'pay_test_123'
        assert event1.provider_event_id == 'payment_failed_pay_test_123'
        assert event1.reason_code == 'bad_request_us'
        print("   ✅ PASSED - Payment failed event created correctly")

        # Test 2: Duplicate payment.failed (idempotency)
        print("\n📝 Test 2: Duplicate Payment Failed Webhook (Idempotency)")
        event1_duplicate = await create_revenue_event_from_payment_failed(payment_payload, session)
        assert event1_duplicate is None, "Should return None for duplicate event"
        print("   ✅ PASSED - Duplicate event properly ignored")

        # Test 3: subscription.charged.failed webhook
        print("\n📝 Test 3: Subscription Charged Failed Webhook")
        subscription_payload = {
            "event": "subscription.charged.failed",
            "payload": {
                "subscription": {
                    "entity": {
                        "id": "sub_test_456",
                        "customer_id": "cust_789"
                    }
                },
                "invoice": {
                    "entity": {
                        "id": "inv_test_789",
                        "amount": 150000,  # 1500.00 INR in paise
                        "currency": "INR"
                    }
                }
            }
        }

        event2 = await create_revenue_event_from_subscription_failed(subscription_payload, session)
        assert event2 is not None, "Should create event for subscription.charged.failed"
        assert event2.type == RevenueEventType.SUBSCRIPTION_FAILED
        assert event2.status == RevenueEventStatus.PENDING
        assert event2.amount == Decimal('1500.00')
        assert event2.currency == 'INR'
        assert event2.customer_id == 'cust_789'
        assert event2.razorpay_ref_id == 'sub_test_456'
        assert event2.provider_event_id == 'subscription_failed_inv_test_789'
        assert event2.reason_code == 'mandate_failed'
        print("   ✅ PASSED - Subscription failed event created correctly")

        # Test 4: Duplicate subscription.charged.failed (idempotency)
        print("\n📝 Test 4: Duplicate Subscription Charged Failed Webhook (Idempotency)")
        event2_duplicate = await create_revenue_event_from_subscription_failed(subscription_payload, session)
        assert event2_duplicate is None, "Should return None for duplicate event"
        print("   ✅ PASSED - Duplicate subscription event properly ignored")

        # Commit the session
        await session.commit()

        # Verify audit logs were created
        from sqlalchemy import select
        audit_query = select(AuditLog).where(AuditLog.stage == "detect")
        audit_result = await session.execute(audit_query)
        audit_logs = audit_result.scalars().all()

        print(f"\n📊 Audit Logs Created: {len(audit_logs)}")
        for audit in audit_logs:
            input_json = getattr(audit, 'input_json', 'N/A')
            if input_json != 'N/A' and isinstance(input_json, str):
                print(f"   - Stage: {audit.stage}, Input: {input_json[:50]}...")
            else:
                print(f"   - Stage: {audit.stage}, Input: {input_json}")

        assert len(audit_logs) == 2, f"Expected 2 audit logs, got {len(audit_logs)}"
        print("   ✅ PASSED - Audit logs created correctly")

        print("\n🎉 Webhook Event Creation Tests Completed!")


async def test_signature_verification():
    """Test webhook signature verification."""
    print("\n🧪 Testing Signature Verification")

    # Test with empty secret (should fail for valid signatures)
    print("\n📝 Test 1: Signature Verification with Empty Secret")
    try:
        # Create a mock request
        class MockRequest:
            async def body(self):
                return b'{"test": "payload"}'

        request = MockRequest()
        await verify_signature(request, razorpay_signature="invalid_signature")
        assert False, "Should have raised HTTPException for invalid signature"
    except Exception as e:
        # Should raise HTTPException
        assert "401" in str(e) or "Invalid signature" in str(e)
        print("   ✅ PASSED - Properly rejects invalid signature")

    print("\n🎉 Signature Verification Tests Completed!")


async def main():
    """Run all webhook tests."""
    print("🚀 Starting Webhook Handler Tests\n")

    await test_webhook_event_creation()
    await test_signature_verification()

    print("\n🎊 All Webhook Tests Passed! 🎊")


if __name__ == "__main__":
    asyncio.run(main())