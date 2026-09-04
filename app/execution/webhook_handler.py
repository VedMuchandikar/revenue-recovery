"""Webhook handler for processing Razorpay webhooks to verify payments."""

import logging
import json
from typing import Optional, Dict, Any
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import RevenueEvent, RevenueEventStatus, Outcome
from app.execution.razorpay_client import razorpay_client

logger = logging.getLogger(__name__)


def _get_audit_event():
    """Lazy import to avoid circular import."""
    from app.engine.audit import audit_event
    return audit_event


async def process_razorpay_webhook(
    payload: str,
    signature: str,
    db_session: AsyncSession
) -> dict:
    """
    Process incoming Razorpay webhook to verify payments and update outcomes.

    Args:
        payload: Raw webhook payload (JSON string)
        signature: Razorpay webhook signature
        db_session: Database session

    Returns:
        Processing result dict
    """
    # Verify webhook signature
    if not razorpay_client.verify_webhook_signature(payload, signature):
        logger.warning("Invalid Razorpay webhook signature")
        return {"success": False, "error": "Invalid signature"}

    try:
        # Parse webhook payload
        webhook_data = json.loads(payload)
        event_type = webhook_data.get("event")
        payment_entity = webhook_data.get("payload", {}).get("payment", {}).get("entity", {})

        logger.info(f"Processing Razorpay webhook: {event_type}")

        # Handle different webhook events
        if event_type == "payment.captured":
            return await _handle_payment_captured(payment_entity, db_session)
        elif event_type == "payment.failed":
            return await _handle_payment_failed(payment_entity, db_session)
        elif event_type == "invoice.paid":
            return await _handle_invoice_paid(payment_entity, db_session)
        elif event_type == "invoice.payment_failed":
            return await _handle_invoice_payment_failed(payment_entity, db_session)
        else:
            logger.info(f"Unhandled webhook event type: {event_type}")
            return {"success": True, "message": f"Event {event_type} acknowledged"}

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in webhook payload: {e}")
        return {"success": False, "error": "Invalid JSON"}
    except Exception as e:
        logger.exception(f"Error processing webhook: {e}")
        return {"success": False, "error": str(e)}


async def _handle_payment_captured(
    payment_entity: Dict[str, Any],
    db_session: AsyncSession
) -> dict:
    """Handle payment.captured webhook - verify revenue recovery."""
    payment_id = payment_entity.get("id")
    amount = payment_entity.get("amount")  # in paise
    currency = payment_entity.get("currency")
    status = payment_entity.get("status")
    description = payment_entity.get("description", "")
    reference_id = payment_entity.get("reference_id")  # Our event ID

    logger.info(f"Payment captured: {payment_id}, amount: {amount} {currency}, reference: {reference_id}")

    if not reference_id:
        logger.warning("Payment captured without reference_id - cannot link to recovery event")
        return {"success": True, "message": "Payment captured but no reference_id"}

    # Convert amount from paise to main currency unit
    amount_in_currency = Decimal(amount) / 100 if amount else Decimal('0')

    # Find the revenue event by reference_id (our event ID)
    from sqlalchemy import select
    query = select(RevenueEvent).where(RevenueEvent.id == reference_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.warning(f"Revenue event not found for reference_id: {reference_id}")
        return {"success": True, "message": f"Event {reference_id} not found"}

    # Check if we already have an outcome for this event (idempotency)
    outcome_query = select(Outcome).where(Outcome.event_id == event.id)
    outcome_result = await db_session.execute(outcome_query)
    existing_outcome = outcome_result.scalar_one_or_none()

    if existing_outcome:
        logger.info(f"Outcome already exists for event {event.id}")
        return {"success": True, "message": "Outcome already recorded"}

    # Verify the payment matches our event (amount, currency)
    if event.currency.lower() != currency.lower():
        logger.warning(f"Currency mismatch: event {event.currency} vs payment {currency}")
        # Still proceed but log warning

    amount_match = abs(event.amount - amount_in_currency) < Decimal('0.01')  # Allow small rounding differences
    if not amount_match:
        logger.warning(f"Amount mismatch: event {event.amount} vs payment {amount_in_currency}")
        # Still proceed but log warning

    # Create outcome record for successful recovery
    outcome = Outcome(
        event_id=event.id,
        recovered_amount=amount_in_currency,
        currency=event.currency,
        method="payment_link",  # Would be determined from action type
        recovered_at=datetime.now(timezone.utc),
        verification_json={
            "payment_id": payment_id,
            "amount": amount,
            "currency": currency,
            "status": status,
            "description": description,
            "reference_id": reference_id,
            "webhook_event": "payment.captured"
        }
    )
    db_session.add(outcome)

    # Update event status to COMPLETED
    event.status = RevenueEventStatus.COMPLETED
    event.completed_at = datetime.now(timezone.utc)

    # Audit the outcome
    await _get_audit_event()(
        db_session, event.id, "outcome",
        input_json={"webhook_payment_id": payment_id},
        output_json={
            "recovered_amount": str(amount_in_currency),
            "currency": event.currency,
            "payment_id": payment_id
        }
    )

    await db_session.commit()

    logger.info(f"Revenue recovery verified for event {event.id}: {amount_in_currency} {event.currency}")

    return {
        "success": True,
        "message": f"Revenue recovery verified for event {reference_id}",
        "recovered_amount": str(amount_in_currency),
        "event_id": event.id
    }


async def _handle_payment_failed(
    payment_entity: Dict[str, Any],
    db_session: AsyncSession
) -> dict:
    """Handle payment.failed webhook."""
    payment_id = payment_entity.get("id")
    reference_id = payment_entity.get("reference_id")

    logger.info(f"Payment failed: {payment_id}, reference: {reference_id}")

    if not reference_id:
        return {"success": True, "message": "Payment failed but no reference_id"}

    # Find the revenue event
    from sqlalchemy import select
    query = select(RevenueEvent).where(RevenueEvent.id == reference_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.warning(f"Revenue event not found for reference_id: {reference_id}")
        return {"success": True, "message": f"Event {reference_id} not found"}

    # Update event status to FAILED if we were waiting for payment
    if event.status == RevenueEventStatus.PROCESSING:
        event.status = RevenueEventStatus.FAILED
        event.last_error = f"Payment {payment_id} failed via webhook"
        await db_session.commit()

        await _get_audit_event()(
            db_session, event.id, "payment_failed",
            input_json={"webhook_payment_id": payment_id},
            output_json={"status": "failed", "payment_id": payment_id}
        )

        logger.info(f"Event {event.id} marked as FAILED due to payment webhook")

    return {"success": True, "message": f"Payment failure processed for event {reference_id}"}


async def _handle_invoice_paid(
    invoice_entity: Dict[str, Any],
    db_session: AsyncSession
) -> dict:
    """Handle invoice.paid webhook."""
    invoice_id = invoice_entity.get("id")
    amount = invoice_entity.get("amount")  # in paise
    currency = invoice_entity.get("currency")
    status = invoice_entity.get("status")
    reference_id = invoice_entity.get("reference_id")  # Our event ID

    logger.info(f"Invoice paid: {invoice_id}, amount: {amount} {currency}, reference: {reference_id}")

    # Similar to payment.captured but for invoices
    if not reference_id:
        return {"success": True, "message": "Invoice paid but no reference_id"}

    from sqlalchemy import select
    query = select(RevenueEvent).where(RevenueEvent.id == reference_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.warning(f"Revenue event not found for reference_id: {reference_id}")
        return {"success": True, "message": f"Event {reference_id} not found"}

    # Check for existing outcome
    outcome_query = select(Outcome).where(Outcome.event_id == event.id)
    outcome_result = await db_session.execute(outcome_query)
    existing_outcome = outcome_result.scalar_one_or_none()

    if existing_outcome:
        logger.info(f"Outcome already exists for event {event.id}")
        return {"success": True, "message": "Outcome already recorded"}

    # Convert amount and create outcome
    amount_in_currency = Decimal(amount) / 100 if amount else Decimal('0')

    outcome = Outcome(
        event_id=event.id,
        recovered_amount=amount_in_currency,
        currency=event.currency,
        method="invoice_reminder",
        recovered_at=datetime.now(timezone.utc),
        verification_json={
            "invoice_id": invoice_id,
            "amount": amount,
            "currency": currency,
            "status": status,
            "reference_id": reference_id,
            "webhook_event": "invoice.paid"
        }
    )
    db_session.add(outcome)

    # Update event status
    event.status = RevenueEventStatus.COMPLETED
    event.completed_at = datetime.now(timezone.utc)

    await _get_audit_event()(
        db_session, event.id, "outcome",
        input_json={"webhook_invoice_id": invoice_id},
        output_json={
            "recovered_amount": str(amount_in_currency),
            "currency": event.currency,
            "invoice_id": invoice_id
        }
    )

    await db_session.commit()

    logger.info(f"Invoice revenue recovery verified for event {event.id}: {amount_in_currency} {event.currency}")

    return {
        "success": True,
        "message": f"Invoice revenue recovery verified for event {reference_id}",
        "recovered_amount": str(amount_in_currency),
        "event_id": event.id
    }


async def _handle_invoice_payment_failed(
    invoice_entity: Dict[str, Any],
    db_session: AsyncSession
) -> dict:
    """Handle invoice.payment_failed webhook."""
    invoice_id = invoice_entity.get("id")
    reference_id = invoice_entity.get("reference_id")

    logger.info(f"Invoice payment failed: {invoice_id}, reference: {reference_id}")

    if not reference_id:
        return {"success": True, "message": "Invoice payment failed but no reference_id"}

    # Find the revenue event
    from sqlalchemy import select
    query = select(RevenueEvent).where(RevenueEvent.id == reference_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.warning(f"Revenue event not found for reference_id: {reference_id}")
        return {"success": True, "message": f"Event {reference_id} not found"}

    # Update event status if we were waiting for invoice payment
    if event.status == RevenueEventStatus.PROCESSING:
        event.status = RevenueEventStatus.FAILED
        event.last_error = f"Invoice {invoice_id} payment failed via webhook"
        await db_session.commit()

        await _get_audit_event()(
            db_session, event.id, "invoice_payment_failed",
            input_json={"webhook_invoice_id": invoice_id},
            output_json={"status": "failed", "invoice_id": invoice_id}
        )

        logger.info(f"Event {event.id} marked as FAILED due to invoice payment webhook")

    return {"success": True, "message": f"Invoice payment failure processed for event {reference_id}"}


def extract_payment_info_from_webhook(payload: str) -> Optional[Dict[str, Any]]:
    """
    Extract payment information from webhook payload for verification purposes.
    Used by the verification engine to check webhook-based confirmations.
    """
    try:
        webhook_data = json.loads(payload)
        event_type = webhook_data.get("event")
        payment_entity = webhook_data.get("payload", {}).get("payment", {}).get("entity", {})

        if event_type in ["payment.captured", "payment.failed"]:
            return {
                "payment_id": payment_entity.get("id"),
                "amount": payment_entity.get("amount"),
                "currency": payment_entity.get("currency"),
                "status": payment_entity.get("status"),
                "reference_id": payment_entity.get("reference_id"),
                "event_type": event_type
            }
        elif event_type in ["invoice.paid", "invoice.payment_failed"]:
            return {
                "invoice_id": invoice_entity.get("id"),
                "amount": invoice_entity.get("amount"),
                "currency": invoice_entity.get("currency"),
                "status": invoice_entity.get("status"),
                "reference_id": invoice_entity.get("reference_id"),
                "event_type": event_type
            }
    except Exception as e:
        logger.error(f"Error extracting payment info from webhook: {e}")

    return None


def is_payment_captured(payment_info: Dict[str, Any]) -> bool:
    """Check if payment information indicates a successful capture."""
    if not payment_info:
        return False

    event_type = payment_info.get("event_type")
    status = payment_info.get("status")

    # Check for successful payment/invoice events
    if event_type == "payment.captured" and status == "captured":
        return True
    elif event_type == "invoice.paid" and status == "paid":
        return True

    return False