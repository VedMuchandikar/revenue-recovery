"""Synthetic receivables generator for B2B invoice overdue detection."""

import asyncio
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import and_, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import async_session_factory
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    AuditLog, AuditStage, SyntheticReceivable
)
from app.engine.audit import audit_event
from app.batch.abandonment_poller import _json_serializable

logger = logging.getLogger(__name__)


class ReceivablesGenerator:
    """Generates synthetic B2B invoices and detects overdue receivables."""

    def __init__(self):
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the receivables generator."""
        if self.is_running:
            logger.warning("Receivables generator is already running")
            return

        self.is_running = True
        logger.info("Starting receivables generator")
        self._task = asyncio.create_task(self._generation_loop())

    async def stop(self):
        """Stop the receivables generator."""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("Stopping receivables generator")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _generation_loop(self):
        """Main generation loop for creating invoices and detecting overdue."""
        logger.info("Receivables generator entering generation loop")

        while self.is_running:
            try:
                # Generate new synthetic invoices periodically
                await self._generate_synthetic_invoices()

                # Check for overdue invoices and create revenue events
                await self._check_overdue_invoices()

                # Wait before next iteration (e.g., every hour)
                await asyncio.sleep(3600)  # 1 hour

            except asyncio.CancelledError:
                logger.info("Receivables generator generation loop cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error in receivables generator: %s", e)
                await asyncio.sleep(60)  # Wait 1 minute before retry

    async def _generate_synthetic_invoices(self):
        """Generate synthetic B2B invoices for testing."""
        logger.debug("Generating synthetic invoices")

        async with async_session_factory() as session:
            try:
                # Check how many invoices we already have
                existing_query = select(SyntheticReceivable).where(
                    SyntheticReceivable.status.in_(["pending", "overdue"])
                )
                existing_result = await session.execute(existing_query)
                existing_invoices = existing_result.scalars().all()

                # Only generate if we have fewer than 10 pending invoices
                if len(existing_invoices) >= 10:
                    logger.debug(f"Already have {len(existing_invoices)} pending invoices, skipping generation")
                    return

                # Generate 1-3 new invoices
                import random
                num_invoices = random.randint(1, 3)

                for i in range(num_invoices):
                    invoice_id = f"INV-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
                    customer_id = f"cust_b2b_{random.randint(1, 20)}"

                    # Amount between 10,000 and 100,000
                    amount = Decimal(str(random.randint(10000, 100000)))

                    # Issue date: random date in the last 60 days
                    days_ago = random.randint(1, 60)
                    issue_date = datetime.now(timezone.utc) - timedelta(days=days_ago)

                    # Due date: 30 days after issue date
                    due_date = issue_date + timedelta(days=30)

                    # Randomly make some invoices already overdue
                    if random.random() < 0.3:  # 30% chance
                        due_date = datetime.now(timezone.utc) - timedelta(days=random.randint(1, 10))
                        status = "overdue"
                    else:
                        status = "pending"

                    # Ensure both dates are offset-aware
                    if issue_date.tzinfo is None:
                        issue_date = issue_date.replace(tzinfo=timezone.utc)
                    if due_date.tzinfo is None:
                        due_date = due_date.replace(tzinfo=timezone.utc)

                    invoice = SyntheticReceivable(
                        invoice_id=invoice_id,
                        customer_id=customer_id,
                        amount=amount,
                        currency="INR",
                        status=status,
                        issue_date=issue_date,
                        due_date=due_date
                    )

                    session.add(invoice)
                    logger.info(f"Generated synthetic invoice: {invoice_id}, amount: {amount}, due: {due_date}")

                await session.commit()

            except Exception as e:
                logger.exception("Error generating synthetic invoices: %s", e)
                await session.rollback()

    async def _check_overdue_invoices(self):
        """Check for overdue invoices and create revenue events."""
        logger.debug("Checking for overdue invoices")

        async with async_session_factory() as session:
            try:
                now = datetime.now(timezone.utc)

                # Find overdue invoices that are still pending
                overdue_query = select(SyntheticReceivable).where(
                    and_(
                        SyntheticReceivable.due_date < now,
                        SyntheticReceivable.status == "pending"
                    )
                )
                overdue_result = await session.execute(overdue_query)
                overdue_invoices = overdue_result.scalars().all()

                if not overdue_invoices:
                    logger.debug("No overdue invoices found")
                    return

                logger.info(f"Found {len(overdue_invoices)} overdue invoice(s)")

                # Process each overdue invoice
                for invoice in overdue_invoices:
                    await self._create_overdue_event(session, invoice)
                    # Update invoice status to overdue
                    invoice.status = "overdue"

                await session.commit()

            except Exception as e:
                logger.exception("Error checking overdue invoices: %s", e)
                await session.rollback()

    async def _create_overdue_event(self, db_session: AsyncSession, invoice: SyntheticReceivable):
        """
        Create a RevenueEvent for an overdue invoice.

        Args:
            db_session: Database session
            invoice: Overdue SyntheticReceivable
        """
        # Create a unique provider_event_id for idempotency
        provider_event_id = f"receivable_overdue_{invoice.invoice_id}"

        # Check for existing event (idempotency)
        existing_query = select(RevenueEvent).where(
            RevenueEvent.provider_event_id == provider_event_id
        )
        existing_result = await db_session.execute(existing_query)
        existing_event = existing_result.scalar_one_or_none()

        if existing_event:
            logger.debug(f"Overdue event already exists for invoice {invoice.invoice_id}")
            return

        # Ensure due_date is timezone-aware for subtraction
        due_date = invoice.due_date
        if due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        days_overdue = (now - due_date).days

        # Create new RevenueEvent
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.RECEIVABLE_OVERDUE,
            status=RevenueEventStatus.PENDING,
            amount=invoice.amount,
            currency=invoice.currency,
            customer_id=invoice.customer_id,
            razorpay_ref_id=invoice.invoice_id,
            provider_event_id=provider_event_id,
            detected_at=now,
            reason_code="invoice_overdue",
            retry_count=0,
            metadata_json={
                "invoice_id": invoice.invoice_id,
                "invoice_due_date": invoice.due_date.isoformat(),
                "days_overdue": days_overdue,
                "issue_date": invoice.issue_date.isoformat()
            }
        )

        db_session.add(event)
        await db_session.flush()

        # Audit log
        await audit_event(
            db_session=db_session,
            event_id=event.id,
            stage="detect",
            input_json=_json_serializable({
                "invoice": {
                    "invoice_id": invoice.invoice_id,
                    "amount": str(invoice.amount),
                    "currency": invoice.currency,
                    "due_date": invoice.due_date.isoformat(),
                    "days_overdue": days_overdue
                }
            }),
            output_json=_json_serializable({
                "status": "pending",
                "amount": str(invoice.amount),
                "currency": invoice.currency
            })
        )

        logger.info(f"Created overdue RevenueEvent {event.id} for invoice {invoice.invoice_id}")


# Global generator instance
receivables_generator = ReceivablesGenerator()


async def run_receivables_generator():
    """Run the receivables generator until interrupted."""
    await receivables_generator.start()
    try:
        while receivables_generator.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down receivables generator...")
    finally:
        await receivables_generator.stop()


async def seed_test_data():
    """Seed database with test receivables for demo purposes."""
    logger.info("Seeding test receivables data...")

    async with async_session_factory() as session:
        try:
            # Clear existing test data
            await session.execute(delete(SyntheticReceivable))
            await session.commit()

            # Create a mix of invoices: some overdue, some pending
            import random

            test_invoices = [
                # Overdue invoices (status="pending" means payment pending - the generator will mark them overdue)
                {
                    "invoice_id": "INV-20240115-001",
                    "customer_id": "cust_b2b_001",
                    "amount": Decimal("25000.00"),
                    "currency": "INR",
                    "issue_date": datetime.now(timezone.utc) - timedelta(days=60),
                    "due_date": datetime.now(timezone.utc) - timedelta(days=5),
                    "status": "pending"
                },
                {
                    "invoice_id": "INV-20240120-002",
                    "customer_id": "cust_b2b_002",
                    "amount": Decimal("50000.00"),
                    "currency": "INR",
                    "issue_date": datetime.now(timezone.utc) - timedelta(days=45),
                    "due_date": datetime.now(timezone.utc) - timedelta(days=2),
                    "status": "pending"
                },
                {
                    "invoice_id": "INV-20240125-003",
                    "customer_id": "cust_b2b_003",
                    "amount": Decimal("75000.00"),
                    "currency": "INR",
                    "issue_date": datetime.now(timezone.utc) - timedelta(days=30),
                    "due_date": datetime.now(timezone.utc) - timedelta(days=10),
                    "status": "pending"
                },
                # Pending invoices (not yet due)
                {
                    "invoice_id": "INV-20240201-004",
                    "customer_id": "cust_b2b_004",
                    "amount": Decimal("30000.00"),
                    "currency": "INR",
                    "issue_date": datetime.now(timezone.utc) - timedelta(days=10),
                    "due_date": datetime.now(timezone.utc) + timedelta(days=20),
                    "status": "pending"
                },
                {
                    "invoice_id": "INV-20240210-005",
                    "customer_id": "cust_b2b_005",
                    "amount": Decimal("100000.00"),
                    "currency": "INR",
                    "issue_date": datetime.now(timezone.utc) - timedelta(days=5),
                    "due_date": datetime.now(timezone.utc) + timedelta(days=25),
                    "status": "pending"
                },
            ]

            for inv_data in test_invoices:
                invoice = SyntheticReceivable(**inv_data)
                session.add(invoice)

            await session.commit()
            logger.info(f"Seeded {len(test_invoices)} test invoices")

        except Exception as e:
            logger.exception("Error seeding test data: %s", e)
            await session.rollback()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    asyncio.run(run_receivables_generator())