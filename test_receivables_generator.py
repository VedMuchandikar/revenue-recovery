#!/usr/bin/env python3
"""Test script to verify Phase 9: Synthetic receivables generator implementation."""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    AuditLog, AuditStage, SyntheticReceivable
)
from app.batch.receivables_generator import (
    ReceivablesGenerator,
    seed_test_data
)


async def test_receivables_generator():
    """Test the receivables generator functionality."""
    print("🧪 Testing Synthetic Receivables Generator")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(AuditLog))
        await session.execute(delete(SyntheticReceivable))
        await session.commit()

        # Test 1: Seed test data
        print("\n📝 Test 1: Seed Test Data")
        await seed_test_data()

        # Check seeded data
        from sqlalchemy import select
        invoice_query = select(SyntheticReceivable)
        invoice_result = await session.execute(invoice_query)
        invoices = invoice_result.scalars().all()

        print(f"   Seeded {len(invoices)} invoices")
        overdue_count = sum(1 for inv in invoices if inv.status == "overdue")
        pending_count = sum(1 for inv in invoices if inv.status == "pending")
        print(f"   - Overdue: {overdue_count}")
        print(f"   - Pending: {pending_count}")

        assert len(invoices) == 5, f"Expected 5 invoices, got {len(invoices)}"
        # All seeded invoices should be pending initially (payment pending)
        assert pending_count == 5, f"Expected 5 pending invoices, got {pending_count}"
        assert overdue_count == 0, f"Expected 0 overdue invoices initially, got {overdue_count}"
        print("   ✅ PASSED - Test data seeded correctly")

        # Test 2: Generate new synthetic invoices
        print("\n📝 Test 2: Generate New Synthetic Invoices")
        generator = ReceivablesGenerator()

        # Temporarily reduce the threshold for testing
        # Actually, the generator only creates invoices if we have fewer than 10
        # Since we have 6, it should create some
        await generator._generate_synthetic_invoices()

        invoice_query = select(SyntheticReceivable)
        invoice_result = await session.execute(invoice_query)
        new_invoices = invoice_result.scalars().all()

        print(f"   Now have {len(new_invoices)} invoices (was 6)")
        assert len(new_invoices) > 6, f"Expected more than 6 invoices after generation, got {len(new_invoices)}"
        print("   ✅ PASSED - New invoices generated")

        # Test 3: Check for overdue invoices and create events
        print("\n📝 Test 3: Check Overdue Invoices and Create Events")
        await generator._check_overdue_invoices()

        # Check for created revenue events
        event_query = select(RevenueEvent).where(
            RevenueEvent.type == RevenueEventType.RECEIVABLE_OVERDUE
        )
        event_result = await session.execute(event_query)
        overdue_events = event_result.scalars().all()

        print(f"   Created {len(overdue_events)} overdue events")
        # Should have created events for the 3 overdue invoices we seeded
        assert len(overdue_events) >= 3, f"Expected at least 3 overdue events, got {len(overdue_events)}"

        # Check audit logs
        audit_query = select(AuditLog).where(AuditLog.stage == "detect")
        audit_result = await session.execute(audit_query)
        audit_logs = audit_result.scalars().all()
        print(f"   Created {len(audit_logs)} audit logs for overdue detection")
        assert len(audit_logs) >= 3, f"Expected at least 3 audit logs, got {len(audit_logs)}"

        # Verify event details
        for event in overdue_events:
            print(f"   - Event {event.id}: amount={event.amount}, customer={event.customer_id}, type={event.type.value}")
            assert event.status == RevenueEventStatus.PENDING
            assert event.type == RevenueEventType.RECEIVABLE_OVERDUE
            assert event.amount > 0

        await session.commit()
        print("   ✅ PASSED - Overdue events created correctly")

        # Test 4: Idempotency check
        print("\n📝 Test 4: Idempotency Check")
        initial_count = len(overdue_events)

        # Run overdue check again
        await generator._check_overdue_invoices()

        event_query = select(RevenueEvent).where(
            RevenueEvent.type == RevenueEventType.RECEIVABLE_OVERDUE
        )
        event_result = await session.execute(event_query)
        final_events = event_result.scalars().all()

        assert len(final_events) == initial_count, \
            f"Idempotency failed: expected {initial_count} events, got {len(final_events)}"
        print("   ✅ PASSED - Idempotency works correctly")

        await session.commit()


async def test_integration():
    """Test integration with the orchestrator."""
    print("\n🧪 Testing Integration with Orchestrator")

    await init_db()

    async with async_session_factory() as session:
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.execute(delete(AuditLog))
        await session.execute(delete(SyntheticReceivable))
        await session.commit()

        # Create a synthetic receivable that's overdue
        overdue_invoice = SyntheticReceivable(
            invoice_id=f"INV-TEST-{uuid.uuid4().hex[:8]}",
            customer_id="cust_test_integration",
            amount=Decimal("15000.00"),
            currency="INR",
            status="pending",  # Still pending, but overdue
            issue_date=datetime.now(timezone.utc) - timedelta(days=45),
            due_date=datetime.now(timezone.utc) - timedelta(days=15)  # 15 days overdue
        )
        session.add(overdue_invoice)
        await session.commit()

        # Run the overdue check
        generator = ReceivablesGenerator()
        await generator._check_overdue_invoices()
        await session.commit()

        # Check that an event was created
        from sqlalchemy import select
        event_query = select(RevenueEvent).where(
            RevenueEvent.provider_event_id == f"receivable_overdue_{overdue_invoice.invoice_id}"
        )
        event_result = await session.execute(event_query)
        event = event_result.scalar_one_or_none()

        assert event is not None, "Revenue event should be created for overdue invoice"
        assert event.type == RevenueEventType.RECEIVABLE_OVERDUE
        assert event.status == RevenueEventStatus.PENDING
        assert event.amount == Decimal("15000.00")
        assert event.customer_id == "cust_test_integration"
        assert event.razorpay_ref_id == overdue_invoice.invoice_id

        print("   ✅ PASSED - Integration test successful")


async def main():
    """Run all receivables generator tests."""
    print("🚀 Starting Synthetic Receivables Generator Tests\n")

    await test_receivables_generator()
    await test_integration()

    print("\n🎊 All Synthetic Receivables Generator Tests Passed! 🎊")


if __name__ == "__main__":
    asyncio.run(main())