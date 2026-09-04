"""Razorpay webhook handlers for revenue recovery events."""

import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.database import get_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    ActionResult, AuditLog, AuditStage, Outcome, ProposedAction, RecoveryAction,
)
from app.engine.audit import audit_event
from app.execution.razorpay_client import razorpay_client

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/webhook", tags=["webhooks"])


async def verify_signature(request: Request, razorpay_signature: Optional[str] = Header(None)) -> bytes:
    """
    Verify Razorpay webhook signature.

    Args:
        request: The incoming request
        razorpay_signature: The X-Razorpay-Signature header

    Returns:
        Request body bytes if signature is valid

    Raises:
        HTTPException: If signature is invalid or missing
    """
    if not razorpay_signature:
        logger.warning("Missing X-Razorpay-Signature header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing signature"
        )

    # Get request body
    body = await request.body()

    # Verify signature using Razorpay client
    if not razorpay_client.verify_webhook_signature(
        payload=body.decode('utf-8') if isinstance(body, bytes) else body,
        signature=razorpay_signature
    ):
        logger.warning("Invalid webhook signature")

        # Audit the security event
        try:
            # We don't have a session here, but we can log it
            logger.warning("SECURITY EVENT: Invalid webhook signature received")
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signature"
        )

    return body


async def create_revenue_event_from_payment_failed(
    payload: dict,
    db_session: AsyncSession
) -> Optional[RevenueEvent]:
    """
    Create a RevenueEvent from payment.failed webhook payload.

    Args:
        payload: The webhook payload (already parsed JSON)
        db_session: Database session

    Returns:
        Created RevenueEvent or None if duplicate (idempotency)
    """
    # Extract the nested structure: payload -> payload -> payment -> entity
    payment_entity = payload.get("payload", {}).get("payment", {}).get("entity", {})

    # Extract required fields
    payment_id = payment_entity.get("id")
    if not payment_id:
        logger.warning("Payment ID missing in webhook payload")
        return None

    amount = payment_entity.get("amount", 0) / 100  # Convert from paise
    currency = payment_entity.get("currency", "INR")
    customer_id = payment_entity.get("email") or payment_entity.get("contact") or "unknown"

    # Extract reason code
    error_code = payment_entity.get("error_code")
    error_reason = payment_entity.get("error_reason")
    reason_code = error_code or error_reason or "payment_failed"

    # Use payment ID as provider_event_id for idempotency
    provider_event_id = f"payment_failed_{payment_id}"

    # Check for existing event (idempotency)
    existing_query = select(RevenueEvent).where(
        RevenueEvent.provider_event_id == provider_event_id
    )
    existing_result = await db_session.execute(existing_query)
    existing_event = existing_result.scalar_one_or_none()

    if existing_event:
        logger.info(f"Duplicate webhook event ignored: {provider_event_id}")
        return None

    # Create new RevenueEvent
    event = RevenueEvent(
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=amount,
        currency=currency,
        customer_id=customer_id,
        razorpay_ref_id=payment_id,
        provider_event_id=provider_event_id,
        detected_at=datetime.now(timezone.utc),
        reason_code=reason_code,
        retry_count=0,
        metadata_json={
            "webhook_payload": payload,
            "payment_entity": payment_entity
        }
    )

    db_session.add(event)
    await db_session.flush()

    # Audit log
    await audit_event(
        db_session=db_session,
        event_id=event.id,
        stage="detect",
        input_json={"webhook_payload": payload},
        output_json={"status": "pending", "amount": str(amount), "currency": currency}
    )

    logger.info(f"Created RevenueEvent {event.id} from payment.failed webhook: {payment_id}")
    return event


async def create_revenue_event_from_subscription_failed(
    payload: dict,
    db_session: AsyncSession
) -> Optional[RevenueEvent]:
    """
    Create a RevenueEvent from subscription.charged.failed webhook payload.

    Args:
        payload: The webhook payload (already parsed JSON)
        db_session: Database session

    Returns:
        Created RevenueEvent or None if duplicate (idempotency)
    """
    subscription_entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
    invoice_entity = payload.get("payload", {}).get("invoice", {}).get("entity", {})

    # Extract required fields
    subscription_id = subscription_entity.get("id")
    invoice_id = invoice_entity.get("id")

    if not invoice_id:
        logger.warning("Invoice ID missing in subscription charged failed webhook")
        return None

    amount = invoice_entity.get("amount", 0) / 100  # Convert from paise
    currency = invoice_entity.get("currency", "INR")
    customer_id = subscription_entity.get("customer_id") or "unknown"

    # Determine reason code
    # subscription.charged.failed usually means mandate failed
    reason_code = "mandate_failed"

    # Use invoice ID as provider_event_id for idempotency
    provider_event_id = f"subscription_failed_{invoice_id}"

    # Check for existing event (idempotency)
    existing_query = select(RevenueEvent).where(
        RevenueEvent.provider_event_id == provider_event_id
    )
    existing_result = await db_session.execute(existing_query)
    existing_event = existing_result.scalar_one_or_none()

    if existing_event:
        logger.info(f"Duplicate webhook event ignored: {provider_event_id}")
        return None

    # Create new RevenueEvent
    event = RevenueEvent(
        type=RevenueEventType.SUBSCRIPTION_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=amount,
        currency=currency,
        customer_id=customer_id,
        razorpay_ref_id=subscription_id,
        provider_event_id=provider_event_id,
        detected_at=datetime.now(timezone.utc),
        reason_code=reason_code,
        retry_count=0,
        metadata_json={
            "webhook_payload": payload,
            "subscription_entity": subscription_entity,
            "invoice_entity": invoice_entity
        }
    )

    db_session.add(event)
    await db_session.flush()

    # Audit log
    await audit_event(
        db_session=db_session,
        event_id=event.id,
        stage="detect",
        input_json={"webhook_payload": payload},
        output_json={"status": "pending", "amount": str(amount), "currency": currency}
    )

    logger.info(f"Created RevenueEvent {event.id} from subscription.charged.failed webhook: {subscription_id}")
    return event


async def handle_recovery_payment_captured(
    payload: dict,
    db_session: AsyncSession,
) -> Optional[RevenueEvent]:
    """Close the recovery loop when Razorpay confirms a captured payment.

    Payment-link webhooks can carry the original event ID in the link notes,
    or the payment-link ID can be matched against the stored ActionResult.
    Both paths are supported so an outcome is tied to the intervention that
    actually recovered the money.
    """
    nested_payload = payload.get("payload", {})
    payment = nested_payload.get("payment", {}).get("entity", {})
    payment_link = nested_payload.get("payment_link", {}).get("entity", {})
    payment_id = payment.get("id")
    link_id = payment_link.get("id") or payment.get("payment_link_id")
    reference_id = (
        payment_link.get("notes", {}).get("recovery_event_id")
        or payment.get("notes", {}).get("recovery_event_id")
        or payment.get("reference_id")
    )

    event = None
    if reference_id:
        event = (await db_session.execute(
            select(RevenueEvent).where(RevenueEvent.id == reference_id)
        )).scalar_one_or_none()
    if event is None and link_id:
        event = (await db_session.execute(
            select(RevenueEvent)
            .join(ActionResult, ActionResult.event_id == RevenueEvent.id)
            .where(ActionResult.external_ref_id == link_id)
        )).scalars().first()
    if event is None:
        logger.warning("Captured payment %s could not be linked to a recovery event", payment_id)
        return None

    existing = (await db_session.execute(
        select(Outcome).where(Outcome.event_id == event.id)
    )).scalar_one_or_none()
    if existing:
        logger.info("Captured payment %s already recorded for event %s", payment_id, event.id)
        return event

    action = (await db_session.execute(
        select(ProposedAction).where(ProposedAction.event_id == event.id)
    )).scalar_one_or_none()
    recovered_amount = Decimal(str(payment.get("amount", 0))) / 100
    outcome = Outcome(
        event_id=event.id,
        recovered_amount=recovered_amount,
        currency=payment.get("currency") or event.currency,
        method=action.action_type if action else RecoveryAction.PAYMENT_LINK,
        recovered_at=datetime.now(timezone.utc),
        verification_ref=payment_id,
        verification_json={
            "verified": True,
            "verification_method": "payment_captured_webhook",
            "payment_id": payment_id,
            "payment_link_id": link_id,
            "webhook_event": payload.get("event"),
        },
    )
    db_session.add(outcome)
    event.status = RevenueEventStatus.COMPLETED
    event.completed_at = datetime.now(timezone.utc)
    event.last_error = None
    await audit_event(
        db_session, event.id, "outcome",
        input_json={"webhook_event": payload.get("event"), "payment_id": payment_id},
        output_json={"recovered_amount": str(recovered_amount), "payment_link_id": link_id},
        source="razorpay_webhook",
    )
    logger.info("Revenue recovered for event %s: %s %s", event.id, recovered_amount, outcome.currency)
    return event


async def handle_invoice_paid(
    payload: dict,
    db_session: AsyncSession
) -> Optional[RevenueEvent]:
    """Mark an awaiting revenue event as recovered when its Razorpay invoice is paid."""

    invoice_entity = (
        payload.get("payload", {})
        .get("invoice", {})
        .get("entity", {})
    )

    invoice_id = invoice_entity.get("id")

    if not invoice_id:
        logger.warning("Invoice ID missing in invoice.paid webhook")
        return None

    query = select(RevenueEvent).where(
        RevenueEvent.razorpay_ref_id == invoice_id
    )

    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.warning(
            "No revenue event found for paid invoice %s",
            invoice_id
        )
        return None

    # Idempotency: don't create another outcome if already completed.
    if event.status == RevenueEventStatus.COMPLETED:
        logger.info(
            "Invoice %s already processed for event %s",
            invoice_id,
            event.id
        )
        return event

    if event.status != RevenueEventStatus.AWAITING_PAYMENT:
        logger.warning(
            "Invoice %s paid but event %s is in state %s",
            invoice_id,
            event.id,
            event.status.value
        )
        return event

    outcome = Outcome(
        event_id=event.id,
        recovered_amount=event.amount,
        currency=event.currency,
        method="invoice_reminder",
        recovered_at=datetime.now(timezone.utc),
        verification_json={
            "verified": True,
            "verification_method": "invoice_paid_webhook",
            "invoice_id": invoice_id,
            "webhook_payload": payload
        }
    )

    db_session.add(outcome)

    event.status = RevenueEventStatus.COMPLETED
    event.completed_at = datetime.now(timezone.utc)
    event.last_error = None

    await audit_event(
        db_session=db_session,
        event_id=event.id,
        stage="outcome",
        input_json={
            "invoice_id": invoice_id,
            "webhook_event": "invoice.paid"
        },
        output_json={
            "status": "completed",
            "recovered_amount": str(event.amount),
            "currency": event.currency
        }
    )

    logger.info(
        "Revenue recovered: event %s, invoice %s, amount %s %s",
        event.id,
        invoice_id,
        event.amount,
        event.currency
    )

    return event

@router.post("/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    db_session: AsyncSession = Depends(get_db),
    signature: bytes = Depends(verify_signature)
):
    """
    Handle Razorpay webhook events.

    Expected events:
    - payment.failed
    - subscription.charged.failed

    Returns 200 OK immediately after validation and event creation.
    No processing happens in the webhook handler - events are picked up by worker.
    """
    try:
        # Parse JSON payload
        import json
        payload = json.loads(signature)

        event_type = payload.get("event")

        logger.info(f"Received Razorpay webhook: {event_type}")

        # Handle different event types
        if event_type == "payment.failed":
            await create_revenue_event_from_payment_failed(payload, db_session)

        elif event_type == "subscription.charged.failed":
            await create_revenue_event_from_subscription_failed(payload, db_session)

        elif event_type in {"payment.captured", "payment_link.paid"}:
            await handle_recovery_payment_captured(payload, db_session)

        elif event_type == "invoice.paid":
            # Invoice paid - could be used for verification
            await handle_invoice_paid(payload, db_session)

        else:
            logger.info(f"Unhandled webhook event type: {event_type}")

        # Commit the transaction
        await db_session.commit()

        return {"status": "ok"}

    except HTTPException:
        # Re-raise HTTP exceptions (signature verification failures)
        raise
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in webhook payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON payload"
        )
    except IntegrityError as e:
        # Handle duplicate key errors (should be caught by idempotency check, but just in case)
        await db_session.rollback()
        logger.warning(f"Duplicate key error (idempotency): {e}")
        return {"status": "ok", "message": "Duplicate event"}
    except Exception as e:
        logger.exception(f"Error processing webhook: {e}")
        await db_session.rollback()
        # Still return 200 to prevent Razorpay from retrying
        # Log the error for investigation
        return {"status": "error", "message": str(e)}
