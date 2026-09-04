"""Abandonment poller for detecting abandoned checkouts."""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import async_session_factory
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    AuditLog, AuditStage, SyntheticReceivable
)
from app.engine.audit import audit_event

logger = logging.getLogger(__name__)


class CustomJSONEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal and datetime objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return str(obj)
        return super().default(obj)


def _json_serializable(obj: Any) -> Any:
    """Convert object to JSON-serializable format."""
    return json.loads(json.dumps(obj, cls=CustomJSONEncoder))


# For demo purposes, we'll simulate checkout sessions using an in-memory store
# In production, this would be a database table
_checkout_sessions = {}


def register_checkout_session(
    session_id: str,
    customer_id: str,
    amount: Decimal,
    currency: str = "INR",
    metadata: dict = None
):
    """Register a new checkout session for abandonment tracking."""
    _checkout_sessions[session_id] = {
        "session_id": session_id,
        "customer_id": customer_id,
        "amount": amount,
        "currency": currency,
        "created_at": datetime.now(timezone.utc),
        "metadata": metadata or {},
        "payment_completed": False,
        "payment_id": None
    }
    logger.debug(f"Registered checkout session: {session_id}")


def mark_checkout_payment_completed(session_id: str, payment_id: str):
    """Mark a checkout session as having a successful payment."""
    if session_id in _checkout_sessions:
        _checkout_sessions[session_id]["payment_completed"] = True
        _checkout_sessions[session_id]["payment_id"] = payment_id
        logger.debug(f"Checkout session {session_id} marked as paid with payment {payment_id}")


class AbandonmentPoller:
    """Polls for abandoned checkouts and creates revenue events."""

    def __init__(self, abandonment_threshold_minutes: int = 15):
        self.abandonment_threshold_minutes = abandonment_threshold_minutes
        self.is_running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the abandonment poller."""
        if self.is_running:
            logger.warning("Abandonment poller is already running")
            return

        self.is_running = True
        logger.info("Starting abandonment poller (threshold: %d minutes)", self.abandonment_threshold_minutes)
        self._task = asyncio.create_task(self._polling_loop())

    async def stop(self):
        """Stop the abandonment poller."""
        if not self.is_running:
            return

        self.is_running = False
        logger.info("Stopping abandonment poller")
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _polling_loop(self):
        """Main polling loop for abandonment detection."""
        logger.info("Abandonment poller entering polling loop")

        while self.is_running:
            try:
                # Check for abandoned checkouts
                await self._check_for_abandoned_checkouts()

                # Wait before next poll
                await asyncio.sleep(settings.worker_poll_interval_seconds * 30)  # Poll every minute

            except asyncio.CancelledError:
                logger.info("Abandonment poller polling loop cancelled")
                break
            except Exception as e:
                logger.exception("Unexpected error in abandonment poller: %s", e)
                # Continue running despite errors
                await asyncio.sleep(settings.worker_poll_interval_seconds * 30)

    async def _check_for_abandoned_checkouts(self):
        """Check for abandoned checkouts and create revenue events."""
        logger.debug("Checking for abandoned checkouts")

        async with async_session_factory() as session:
            try:
                # Calculate cutoff time
                cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=self.abandonment_threshold_minutes)

                # Find abandoned checkout sessions
                abandoned_sessions = []
                for session_id, session_data in _checkout_sessions.items():
                    if session_data["payment_completed"]:
                        continue  # Skip sessions with successful payments

                    if session_data["created_at"] < cutoff_time:
                        abandoned_sessions.append(session_data)

                if not abandoned_sessions:
                    logger.debug("No abandoned checkouts found")
                    return

                logger.info(f"Found {len(abandoned_sessions)} abandoned checkout(s)")

                # Process each abandoned session
                for checkout_data in abandoned_sessions:
                    await self._create_abandonment_event(session, checkout_data)

                await session.commit()

            except Exception as e:
                logger.exception("Error checking for abandoned checkouts: %s", e)
                await session.rollback()

    async def _create_abandonment_event(self, db_session: AsyncSession, checkout_data: dict):
        """
        Create a RevenueEvent for an abandoned checkout.

        Args:
            db_session: Database session
            checkout_data: Checkout session data
        """
        session_id = checkout_data["session_id"]

        # Create a unique provider_event_id for idempotency
        provider_event_id = f"checkout_abandoned_{session_id}"

        # Check for existing event (idempotency)
        existing_query = select(RevenueEvent).where(
            RevenueEvent.provider_event_id == provider_event_id
        )
        existing_result = await db_session.execute(existing_query)
        existing_event = existing_result.scalar_one_or_none()

        if existing_event:
            logger.debug(f"Abandonment event already exists for session {session_id}")
            return

        # Create new RevenueEvent
        event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.CHECKOUT_ABANDONED,
            status=RevenueEventStatus.PENDING,
            amount=checkout_data["amount"],
            currency=checkout_data["currency"],
            customer_id=checkout_data["customer_id"],
            razorpay_ref_id=session_id,
            provider_event_id=provider_event_id,
            detected_at=datetime.now(timezone.utc),
            reason_code="checkout_abandoned",
            retry_count=0,
            metadata_json={
                "checkout_session_id": session_id,
                "checkout_created_at": checkout_data["created_at"].isoformat(),
                "checkout_metadata": checkout_data["metadata"]
            }
        )

        db_session.add(event)
        await db_session.flush()

        # Audit log
        await audit_event(
            db_session=db_session,
            event_id=event.id,
            stage="detect",
            input_json=_json_serializable({"checkout_session": checkout_data}),
            output_json=_json_serializable({"status": "pending", "amount": str(checkout_data["amount"]), "currency": checkout_data["currency"]})
        )

        logger.info(f"Created abandonment RevenueEvent {event.id} for session {session_id}")

        # Clean up the session from tracking
        if session_id in _checkout_sessions:
            del _checkout_sessions[session_id]


# Global poller instance
abandonment_poller = AbandonmentPoller(abandonment_threshold_minutes=15)


async def run_abandonment_poller():
    """Run the abandonment poller until interrupted."""
    await abandonment_poller.start()
    try:
        # Keep running until interrupted
        while abandonment_poller.is_running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down abandonment poller...")
    finally:
        await abandonment_poller.stop()


# Demo function to simulate checkout activity
async def simulate_checkout_activity():
    """Simulate checkout activity for demo purposes."""
    import random

    logger.info("Simulating checkout activity...")

    # Register some checkout sessions
    for i in range(5):
        session_id = f"checkout_{uuid.uuid4().hex[:8]}"
        amount = Decimal(str(random.randint(1000, 10000)))
        register_checkout_session(
            session_id=session_id,
            customer_id=f"cust_{i}",
            amount=amount,
            metadata={"product_id": f"prod_{i}", "attempt": 1}
        )

    logger.info(f"Registered 5 checkout sessions")


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run the poller
    asyncio.run(run_abandonment_poller())