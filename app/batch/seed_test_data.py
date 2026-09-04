"""Seed test data generator for creating 50+ revenue events for testing."""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List

from sqlalchemy import delete

from app.db.database import async_session_factory
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    AuditLog, AuditStage, SyntheticReceivable
)
from app.engine.audit import audit_event
from app.batch.abandonment_poller import _json_serializable


async def seed_test_data():
    """Seed database with 50+ test events covering all revenue leak types."""
    print("🌱 Seeding test data with 50+ revenue events...")

    async with async_session_factory() as session:
        try:
            # Clear existing test data
            await session.execute(delete(RevenueEvent))
            await session.execute(delete(AuditLog))
            await session.execute(delete(SyntheticReceivable))
            await session.commit()

            events_created = 0

            # 1. Payment failed events (10 events)
            print("   Creating payment failed events...")
            for i in range(10):
                event = RevenueEvent(
                    id=str(uuid.uuid4()),
                    type=RevenueEventType.PAYMENT_FAILED,
                    status=RevenueEventStatus.PENDING,
                    amount=Decimal(str(1000 + i * 500)),  # 1000, 1500, 2000, ...
                    currency="INR",
                    customer_id=f"cust_payment_{i:03d}",
                    razorpay_ref_id=f"pay_{uuid.uuid4().hex[:8]}",
                    provider_event_id=f"payment_failed_{uuid.uuid4().hex[:12]}",
                    detected_at=datetime.now(timezone.utc) - timedelta(hours=i),
                    reason_code="payment_failed",
                    retry_count=0,
                    metadata_json={
                        "payment_id": f"pay_{uuid.uuid4().hex[:8]}",
                        "failure_code": "gateway_error" if i % 2 == 0 else "bank_declined",
                        "failure_reason": "Gateway timeout" if i % 2 == 0 else "Insufficient funds"
                    }
                )
                session.add(event)

                # Add audit log
                await audit_event(
                    db_session=session,
                    event_id=event.id,
                    stage="detect",
                    input_json=_json_serializable({
                        "payment_id": event.metadata_json["payment_id"],
                        "failure_code": event.metadata_json["failure_code"],
                        "failure_reason": event.metadata_json["failure_reason"]
                    }),
                    output_json=_json_serializable({
                        "status": "pending",
                        "amount": str(event.amount),
                        "currency": event.currency
                    })
                )
                events_created += 1

            # 2. Subscription failed events (10 events)
            print("   Creating subscription failed events...")
            for i in range(10):
                event = RevenueEvent(
                    id=str(uuid.uuid4()),
                    type=RevenueEventType.SUBSCRIPTION_FAILED,
                    status=RevenueEventStatus.PENDING,
                    amount=Decimal(str(500 + i * 100)),  # 500, 600, 700, ...
                    currency="INR",
                    customer_id=f"cust_sub_{i:03d}",
                    razorpay_ref_id=f"sub_{uuid.uuid4().hex[:8]}",
                    provider_event_id=f"subscription_failed_{uuid.uuid4().hex[:12]}",
                    detected_at=datetime.now(timezone.utc) - timedelta(hours=i),
                    reason_code="subscription_failed",
                    retry_count=0,
                    metadata_json={
                        "subscription_id": f"sub_{uuid.uuid4().hex[:8]}",
                        "plan_id": f"plan_{['basic', 'premium', 'enterprise'][i % 3]}",
                        "failure_reason": "Card expired" if i % 3 == 0 else "Insufficient funds" if i % 3 == 1 else "Issuer declined"
                    }
                )
                session.add(event)

                # Add audit log
                await audit_event(
                    db_session=session,
                    event_id=event.id,
                    stage="detect",
                    input_json=_json_serializable({
                        "subscription_id": event.metadata_json["subscription_id"],
                        "plan_id": event.metadata_json["plan_id"],
                        "failure_reason": event.metadata_json["failure_reason"]
                    }),
                    output_json=_json_serializable({
                        "status": "pending",
                        "amount": str(event.amount),
                        "currency": event.currency
                    })
                )
                events_created += 1

            # 3. Checkout abandoned events (10 events)
            print("   Creating checkout abandoned events...")
            for i in range(10):
                event = RevenueEvent(
                    id=str(uuid.uuid4()),
                    type=RevenueEventType.CHECKOUT_ABANDONED,
                    status=RevenueEventStatus.PENDING,
                    amount=Decimal(str(2000 + i * 300)),  # 2000, 2300, 2600, ...
                    currency="INR",
                    customer_id=f"cust_checkout_{i:03d}",
                    razorpay_ref_id=f"checkout_{uuid.uuid4().hex[:8]}",
                    provider_event_id=f"checkout_abandoned_{uuid.uuid4().hex[:12]}",
                    detected_at=datetime.now(timezone.utc) - timedelta(minutes=30 + i*5),  # 30-75 minutes ago
                    reason_code="checkout_abandoned",
                    retry_count=0,
                    metadata_json={
                        "checkout_session_id": f"checkout_{uuid.uuid4().hex[:8]}",
                        "cart_items": [
                            {"product_id": f"prod_{j}", "quantity": 1, "price": 1000}
                            for j in range(i % 3 + 1)
                        ],
                        "checkout_value": str(2000 + i * 300)
                    }
                )
                session.add(event)

                # Add audit log
                await audit_event(
                    db_session=session,
                    event_id=event.id,
                    stage="detect",
                    input_json=_json_serializable({
                        "checkout_session_id": event.metadata_json["checkout_session_id"],
                        "cart_items": event.metadata_json["cart_items"],
                        "checkout_value": event.metadata_json["checkout_value"]
                    }),
                    output_json=_json_serializable({
                        "status": "pending",
                        "amount": str(event.amount),
                        "currency": event.currency
                    })
                )
                events_created += 1

            # 4. Receivable overdue events (10 events)
            print("   Creating receivable overdue events...")
            for i in range(10):
                event = RevenueEvent(
                    id=str(uuid.uuid4()),
                    type=RevenueEventType.RECEIVABLE_OVERDUE,
                    status=RevenueEventStatus.PENDING,
                    amount=Decimal(str(10000 + i * 5000)),  # 10000, 15000, 20000, ...
                    currency="INR",
                    customer_id=f"cust_receivable_{i:03d}",
                    razorpay_ref_id=f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}",
                    provider_event_id=f"receivable_overdue_{uuid.uuid4().hex[:12]}",
                    detected_at=datetime.now(timezone.utc) - timedelta(days=i+5),  # 5-14 days overdue
                    reason_code="invoice_overdue",
                    retry_count=0,
                    metadata_json={
                        "invoice_id": f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}",
                        "invoice_due_date": (datetime.now(timezone.utc) - timedelta(days=i+10)).isoformat(),
                        "days_overdue": i + 5,
                        "issue_date": (datetime.now(timezone.utc) - timedelta(days=i+40)).isoformat()
                    }
                )
                session.add(event)

                # Add audit log
                await audit_event(
                    db_session=session,
                    event_id=event.id,
                    stage="detect",
                    input_json=_json_serializable({
                        "invoice_id": event.metadata_json["invoice_id"],
                        "invoice_due_date": event.metadata_json["invoice_due_date"],
                        "days_overdue": event.metadata_json["days_overdue"]
                    }),
                    output_json=_json_serializable({
                        "status": "pending",
                        "amount": str(event.amount),
                        "currency": event.currency
                    })
                )
                events_created += 1

            # 5. Synthetic receivables for batch processing (10 invoices)
            print("   Creating synthetic receivables for batch processing...")
            for i in range(10):
                # Mix of pending and overdue invoices
                if i < 4:  # First 4 are overdue
                    issue_date = datetime.now(timezone.utc) - timedelta(days=50 + i*5)
                    due_date = issue_date + timedelta(days=30)  # 30 day terms
                    status = "overdue" if due_date < datetime.now(timezone.utc) else "pending"
                else:  # Remaining are pending (future due dates)
                    issue_date = datetime.now(timezone.utc) - timedelta(days=10 + i*2)
                    due_date = issue_date + timedelta(days=30)  # Future due dates
                    status = "pending"

                invoice = SyntheticReceivable(
                    invoice_id=f"INV-SEED-{i:03d}-{uuid.uuid4().hex[:4].upper()}",
                    customer_id=f"cust_seed_{i:03d}",
                    amount=Decimal(str(5000 + i * 1000)),  # 5000, 6000, 7000, ...
                    currency="INR",
                    status=status,
                    issue_date=issue_date,
                    due_date=due_date
                )
                session.add(invoice)

            await session.commit()
            print(f"   ✅ Created {events_created} revenue events and 10 synthetic receivables")

            # Summary
            from sqlalchemy import select, func

            # Count events by type
            for event_type in RevenueEventType:
                count_query = select(func.count(RevenueEvent.id)).where(RevenueEvent.type == event_type)
                count_result = await session.execute(count_query)
                count = count_result.scalar()
                print(f"   📊 {event_type.value}: {count} events")

            # Count synthetic receivables
            invoice_count = await session.execute(select(func.count(SyntheticReceivable.id)))
            invoice_total = invoice_count.scalar()
            overdue_invoices = await session.execute(
                select(func.count(SyntheticReceivable.id)).where(SyntheticReceivable.status == "overdue")
            )
            overdue_count = overdue_invoices.scalar()
            print(f"   📄 Synthetic receivables: {invoice_total} total ({overdue_count} overdue)")

            print("🎉 Test data seeding completed successfully!")

        except Exception as e:
            await session.rollback()
            print(f"❌ Error seeding test data: {e}")
            raise


if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    async def main():
        await seed_test_data()

    asyncio.run(main())