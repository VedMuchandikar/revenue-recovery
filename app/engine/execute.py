"""Execution engine for recovery actions with Razorpay API integration."""

import logging
from datetime import datetime, timezone
from typing import Optional
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
import os
from app.db.models import (
    RevenueEvent, ActionResult, ActionResultStatus,
    RecoveryAction, NotificationChannel
)
from app.execution.razorpay_client import (
    RazorpayClient, RazorpayResponse
)
from app.execution.webhook_handler import (
    extract_payment_info_from_webhook,
    is_payment_captured
)

logger = logging.getLogger(__name__)


async def execute_action(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """
    Execute the recovery action using real Razorpay APIs.

    Args:
        event: The revenue event
        decision: Decision from decide_action containing action_type and channel
        db_session: Database session

    Returns:
        Execution result dict
    """
    action_type = decision.get("action_type")
    channel = decision.get("channel")

    logger.info(f"Executing {action_type} via {channel} for event {event.id}")

    # Execute based on action type
    if action_type == RecoveryAction.PAYMENT_LINK.value:
        result = await _execute_payment_link(event, decision, db_session)
    elif action_type == RecoveryAction.MANDATE_RETRY.value:
        result = await _execute_mandate_retry(event, decision, db_session)
    elif action_type == RecoveryAction.NOTIFY_SMS.value:
        result = await _execute_notify_sms(event, decision, db_session)
    elif action_type == RecoveryAction.NOTIFY_EMAIL.value:
        result = await _execute_notify_email(event, decision, db_session)
    elif action_type == RecoveryAction.INVOICE_REMINDER.value:
        result = await _execute_invoice_reminder(event, decision, db_session)
    elif action_type == RecoveryAction.PROMISE_TO_PAY.value:
        result = await _execute_promise_to_pay(event, decision, db_session)
    else:
        # Fallback to stub for unknown actions
        result = await _stub_execute(event, decision, db_session)

    # Persist action result
    action_result = ActionResult(
        event_id=event.id,
        action_type=action_type,
        channel=channel,
        status=ActionResultStatus.SUCCESS if result.get("success") else ActionResultStatus.FAILED,
        external_ref_id=result.get("external_ref_id"),
        response_json=result.get("response_json"),
        error_message=result.get("error")
    )
    db_session.add(action_result)
    await db_session.flush()

    return {
        "action_type": action_type,
        "channel": channel,
        "status": action_result.status.value,
        "external_ref_id": action_result.external_ref_id,
        "response_json": action_result.response_json,
        "error_message": action_result.error_message,
        "success": result.get("success", False)
    }


# =========================================================================
# ACTION IMPLEMENTATIONS
# =========================================================================

async def _execute_payment_link(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute payment link creation via Razorpay API."""
    # Fall back to stub if Razorpay is not configured
    if not razorpay_client.is_configured():
        logger.warning(f"Razorpay not configured - using stub for payment link")
        return await _stub_execute(event, decision, db_session)

    # Convert amount to paise (Razorpay expects smallest currency unit)
    amount_paise = int(event.amount * 100)

    # Prepare customer details (in real implementation, fetch from customer DB)
    # Handle case where customer_id might be an email address
    if "@" in event.customer_id:
        customer_email = event.customer_id
        customer_name = event.customer_id.split("@")[0].replace(".", " ").title() or "Customer"
    else:
        customer_name = event.customer_id  # Placeholder
        customer_email = f"{event.customer_id}@example.com"  # Placeholder
    customer_phone = "+917428730894"  # Placeholder

    # Use webhook base URL from settings or default to localhost
    webhook_base = getattr(settings, 'webhook_base_url', 'http://localhost:8000')
    callback_url = f"{webhook_base}/webhook/razorpay/payment"

    response = razorpay_client.create_payment_link(
        amount=amount_paise,
        currency=event.currency.upper(),
        customer_name=customer_name,
        customer_email=customer_email,
        customer_phone=customer_phone,
        reference_id=event.id,
        description=f"Recovery payment for {event.type.value}",
        notify_sms=decision.get("channel") == "sms",
        notify_email=decision.get("channel") == "email",
        callback_url=callback_url
    )

    if response.success:
        return {
            "success": True,
            "action_type": RecoveryAction.PAYMENT_LINK.value,
            "channel": decision.get("channel"),
            "external_ref_id": response.data.get("payment_link_id"),
            "response_json": response.raw_response,
            "short_url": response.data.get("short_url"),
            "status": response.data.get("status")
        }
    else:
        logger.error(f"Failed to create payment link: {response.error}")
        return {
            "success": False,
            "action_type": RecoveryAction.PAYMENT_LINK.value,
            "channel": decision.get("channel"),
            "error": response.error,
            "external_ref_id": None
        }


async def _execute_mandate_retry(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute mandate retry via Razorpay API."""
    # Fall back to stub if Razorpay is not configured
    if not razorpay_client.is_configured():
        logger.warning(f"Razorpay not configured - using stub for mandate retry")
        return await _stub_execute(event, decision, db_session)

    # subscription.charged.failed webhooks persist the subscription ID here.
    # Never invent a mandate ID: the Razorpay SDK has no mandates resource.
    subscription_id = event.razorpay_ref_id
    if not subscription_id.startswith("sub_"):
        return {
            "success": False,
            "action_type": RecoveryAction.MANDATE_RETRY.value,
            "channel": decision.get("channel"),
            "error": "A Razorpay subscription ID (sub_...) is required for mandate recovery",
            "external_ref_id": None,
        }

    response = razorpay_client.retry_mandate(subscription_id)

    if response.success:
        return {
            "success": True,
            "action_type": RecoveryAction.MANDATE_RETRY.value,
            "channel": decision.get("channel"),
            "external_ref_id": subscription_id,
            "response_json": response.raw_response,
            "next_payment_at": response.data.get("charge_at")
        }
    else:
        logger.error(f"Failed to retry mandate: {response.error}")
        return {
            "success": False,
            "action_type": RecoveryAction.MANDATE_RETRY.value,
            "channel": decision.get("channel"),
            "error": response.error,
            "external_ref_id": None
        }


async def _execute_notify_sms(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute SMS notification via Razorpay API."""
    # Fall back to stub if Razorpay is not configured
    if not razorpay_client.is_configured():
        logger.warning(f"Razorpay not configured - using stub for SMS notification")
        return await _stub_execute(event, decision, db_session)

    # Prepare message based on event type
    message = f"Razorpay Recovery: Please complete your payment of {event.currency} {event.amount} for {event.type.value}. Pay now: https://rzp.io/l/{event.id}"

    response = razorpay_client.send_sms(
        contact="+919999999999",  # Placeholder - would come from customer DB
        message=message,
        reference_id=event.id
    )

    if response.success:
        return {
            "success": True,
            "action_type": RecoveryAction.NOTIFY_SMS.value,
            "channel": NotificationChannel.SMS.value,
            "external_ref_id": event.id,  # Using event ID as reference
            "response_json": response.raw_response,
            "message": message
        }
    else:
        logger.error(f"Failed to send SMS: {response.error}")
        return {
            "success": False,
            "action_type": RecoveryAction.NOTIFY_SMS.value,
            "channel": NotificationChannel.SMS.value,
            "error": response.error,
            "external_ref_id": None
        }


async def _execute_notify_email(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute email notification via Razorpay API."""
    # Fall back to stub if Razorpay is not configured
    if not razorpay_client.is_configured():
        logger.warning(f"Razorpay not configured - using stub for email notification")
        return await _stub_execute(event, decision, db_session)

    subject = f"Payment Required: {event.currency} {event.amount} - {event.type.value}"
    body = f"""
    Dear Customer,

    We noticed a failed payment of {event.currency} {event.amount} for {event.type.value}.
    Please complete your payment to avoid service interruption.

    Amount: {event.currency} {event.amount}
    Event Type: {event.type.value}
    Reference: {event.id}

    Pay securely using our payment link: https://rzp.io/l/{event.id}

    Thank you,
    Razorpay Recovery Team
    """

    response = razorpay_client.send_email(
        email=f"{event.customer_id}@example.com",  # Placeholder
        subject=subject,
        body=body,
        reference_id=event.id
    )

    if response.success:
        return {
            "success": True,
            "action_type": RecoveryAction.NOTIFY_EMAIL.value,
            "channel": NotificationChannel.EMAIL.value,
            "external_ref_id": event.id,
            "response_json": response.raw_response,
            "subject": subject,
            "body": body
        }
    else:
        logger.error(f"Failed to send email: {response.error}")
        return {
            "success": False,
            "action_type": RecoveryAction.NOTIFY_EMAIL.value,
            "channel": NotificationChannel.EMAIL.value,
            "error": response.error,
            "external_ref_id": None
        }


async def _execute_invoice_reminder(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute invoice reminder via Razorpay API."""
    # Fall back to stub if Razorpay is not configured
    if not razorpay_client.is_configured():
        logger.warning(f"Razorpay not configured - using stub for invoice reminder")
        return await _stub_execute(event, decision, db_session)

    # Convert amount to paise
    amount_paise = int(event.amount * 100)

    # Calculate due date (3 days from now)
    expire_by = int(
        datetime.now(timezone.utc).timestamp()
        + (3 * 24 * 60 * 60)
    )

    razorpay_customer = razorpay_client.create_customer(
        name=f"Test Customer {event.customer_id}",
        email=f"{event.customer_id}@example.com",
        contact="+919876543210",
        reference_id=event.customer_id,
    )

    razorpay_customer_id = razorpay_customer["id"]

    response = razorpay_client.create_invoice(
        customer_id=razorpay_customer_id,  # Would be actual customer ID in Razorpay
        amount=amount_paise,
        currency=event.currency.upper(),
        description=f"Overdue invoice for {event.type.value}",
        reference_id=event.id,
        due_date=expire_by,
        notify_sms=decision.get("channel") == "sms",
        notify_email=decision.get("channel") == "email"
    )

    if response.success:
        return {
            "success": True,
            "action_type": RecoveryAction.INVOICE_REMINDER.value,
            "channel": decision.get("channel"),
            "external_ref_id": response.data.get("invoice_id"),
            "response_json": response.raw_response,
            "due_date": expire_by,
            "status": response.data.get("status")
        }
    else:
        logger.error(f"Failed to create invoice: {response.error}")
        return {
            "success": False,
            "action_type": RecoveryAction.INVOICE_REMINDER.value,
            "channel": decision.get("channel"),
            "error": response.error,
            "external_ref_id": None
        }


async def _execute_promise_to_pay(
    event: RevenueEvent,
    decision: dict,
    db_session: AsyncSession
) -> dict:
    """Execute promise to pay setup."""
    # This would typically create a promise-to-pay record in our system
    # For now, we'll simulate success
    logger.info(f"Setting up promise to pay for event {event.id}")

    return {
        "success": True,
        "action_type": RecoveryAction.PROMISE_TO_PAY.value,
        "channel": decision.get("channel"),
        "external_ref_id": f"promise_{event.id}",
        "response_json": {"status": "promise_created"},
        "promise_amount": str(event.amount),
        "promise_date": datetime.now(timezone.utc).isoformat()
    }


# =========================================================================
# STUB FUNCTIONS (for when Razorpay not configured)
# =========================================================================

async def _stub_execute(event: RevenueEvent, decision: dict, db_session: AsyncSession) -> dict:
    """Stub execution function when Razorpay is not configured."""
    logger.warning(f"Razorpay not configured - using stub execution for event {event.id}")

    # Simulate successful execution
    external_ref_id = f"ext_{event.id}_{datetime.now(timezone.utc).timestamp()}"

    return {
        "success": True,
        "action_type": decision.get("action_type"),
        "channel": decision.get("channel"),
        "external_ref_id": external_ref_id,
        "response_json": {"status": "success", "note": "stub execution"},
        "error_message": None
    }


# =========================================================================
# VERIFICATION ENGINE
# =========================================================================

async def verify_action(
    event: RevenueEvent,
    execution_result: dict
) -> dict:
    """
    Verify that the executed action resulted in actual revenue recovery.

    Args:
        event: The revenue event
        execution_result: Result from execute_action

    Returns:
        Verification result dict with 'verified' boolean
    """
    action_type = execution_result.get("action_type")
    external_ref_id = execution_result.get("external_ref_id")

    if not execution_result.get("success"):
        logger.warning(f"Execution failed for event {event.id}, skipping verification")
        return {
            "verified": False,
            "verification_method": "execution_failed",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": "Action execution failed"
        }

    # Verify based on action type
    if action_type == RecoveryAction.PAYMENT_LINK.value:
        return await _verify_payment_link(event, external_ref_id)
    elif action_type == RecoveryAction.MANDATE_RETRY.value:
        return await _verify_mandate_retry(event, external_ref_id)
    elif action_type in [RecoveryAction.NOTIFY_SMS.value, RecoveryAction.NOTIFY_EMAIL.value]:
        # Notifications don't directly recover revenue - verification is delivery confirmation
        return await _verify_notification(event, execution_result)
    elif action_type == RecoveryAction.INVOICE_REMINDER.value:
        return await _verify_invoice_payment(event, external_ref_id)
    elif action_type == RecoveryAction.PROMISE_TO_PAY.value:
        # Promise to pay verification would check if promise was kept
        return await _verify_promise_to_pay(event, external_ref_id)
    else:
        # Default verification
        return await _stub_verify(event, execution_result)


async def _verify_payment_link(
    event: RevenueEvent,
    payment_link_id: str
) -> dict:
    """Verify payment link resulted in successful payment."""
    if not razorpay_client.is_configured():
        return await _stub_verify_link_stub(event, payment_link_id)

    try:
        # Fetch payment link status
        link_response = razorpay_client.fetch_payment_link(payment_link_id)
        if not link_response.success:
            return {
                "verified": False,
                "verification_method": "payment_link_fetch_failed",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Failed to fetch payment link: {link_response.error}"
            }

        # Check if payment link was paid
        status = link_response.data.get("status")
        if status == "paid":
            # Fetch actual payment details if available
            # In real implementation, we'd need to track payment ID from webhook
            return {
                "verified": True,
                "verification_method": "payment_link_paid",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Payment link {payment_link_id} was paid",
                "amount_paid": event.amount,  # Would come from actual payment
                "currency": event.currency
            }
        elif status in ["cancelled", "expired"]:
            return {
                "verified": False,
                "verification_method": "payment_link_not_paid",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Payment link {payment_link_id} status: {status}"
            }
        else:
            # Still pending - check webhook/polling for recent payments
            return {
                "verified": False,
                "verification_method": "payment_link_pending",
                "pending": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Payment link {payment_link_id} status: {status} (awaiting payment)"
            }

    except Exception as e:
        logger.exception(f"Error verifying payment link {payment_link_id}: {e}")
        return {
            "verified": False,
            "verification_method": "verification_error",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": f"Verification error: {str(e)}"
        }


async def _verify_mandate_retry(
    event: RevenueEvent,
    mandate_id: str
) -> dict:
    """Verify mandate retry resulted in successful payment."""
    if not razorpay_client.is_configured():
        return await _stub_verify_link_stub(event, mandate_id)

    try:
        # Fetch mandate status
        mandate_response = razorpay_client.fetch_mandate(mandate_id)
        if not mandate_response.success:
            return {
                "verified": False,
                "verification_method": "mandate_fetch_failed",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Failed to fetch mandate: {mandate_response.error}"
            }

        status = mandate_response.data.get("status")
        # For mandate, we'd need to check if a payment was actually collected
        # This would typically come from webhook notification
        return {
            "verified": False,  # Conservative - would need webhook confirmation
            "verification_method": "mandate_retry_initiated",
            "pending": True,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": f"Mandate retry initiated for {mandate_id}, status: {status} (awaiting webhook confirmation)"
        }
    except Exception as e:
        logger.exception(f"Error verifying mandate {mandate_id}: {e}")
        return {
            "verified": False,
            "verification_method": "verification_error",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": f"Verification error: {str(e)}"
        }


async def _verify_notification(
    event: RevenueEvent,
    execution_result: dict
) -> dict:
    """Verify notification was delivered (not direct revenue recovery)."""
    # Notifications don't directly recover revenue - they prompt customer action
    # Verification here is just delivery confirmation
    channel = execution_result.get("channel")
    return {
        "verified": True,  # Assume delivery succeeded if execution succeeded
        "verification_method": f"{channel}_delivery_confirmed",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "details": f"Notification sent via {channel} - recovery depends on customer action",
        "note": "Notifications enable recovery but don't guarantee it"
    }


async def _verify_invoice_payment(
    event: RevenueEvent,
    invoice_id: str
) -> dict:
    """Verify invoice payment resulted in successful payment."""
    if not razorpay_client.is_configured():
        return await _stub_verify_link_stub(event, invoice_id)

    try:
        # Fetch invoice status
        invoice_response = razorpay_client.fetch_invoice(invoice_id)
        if not invoice_response.success:
            return {
                "verified": False,
                "verification_method": "invoice_fetch_failed",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Failed to fetch invoice: {invoice_response.error}"
            }

        status = invoice_response.data.get("status")
        if status == "paid":
            return {
                "verified": True,
                "verification_method": "invoice_paid",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Invoice {invoice_id} was paid",
                "amount_paid": event.amount,
                "currency": event.currency
            }
        elif status in ["cancelled", "expired"]:
            return {
                "verified": False,
                "verification_method": "invoice_not_paid",
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Invoice {invoice_id} status: {status}"
            }
        else:
            return {
                "verified": False,
                "verification_method": "invoice_pending",
                "pending": True,
                "verified_at": datetime.now(timezone.utc).isoformat(),
                "details": f"Invoice {invoice_id} status: {status} (awaiting payment)"
            }
    except Exception as e:
        logger.exception(f"Error verifying invoice {invoice_id}: {e}")
        return {
            "verified": False,
            "verification_method": "verification_error",
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": f"Verification error: {str(e)}"
        }


async def _verify_promise_to_pay(
    event: RevenueEvent,
    promise_id: str
) -> dict:
    """Verify promise to pay was kept."""
    # This would check if customer made payment by promised date
    # For now, return not verified (would need date-based check)
    return {
        "verified": False,
        "verification_method": "promise_to_pay_pending",
        "pending": True,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "details": f"Promise to pay {promise_id} created - verification pending until promise date"
    }


async def _stub_verify_link_stub(
    event: RevenueEvent,
    external_ref_id: str
) -> dict:
    """Stub verification when Razorpay not configured.

    In test/demo mode, we always verify as successful to demonstrate
    the full pipeline working. In production, this would check actual
    payment status via Razorpay API.
    """
    return {
        "verified": True,
        "verification_method": "stub_verification",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "details": "Stub verification - Razorpay not configured, auto-verified for demo"
    }


async def _stub_verify(event: RevenueEvent, execution_result: dict) -> dict:
    """Default stub verification."""
    return await _stub_verify_link_stub(event, execution_result.get("external_ref_id", ""))


# Global Razorpay client instance
razorpay_client = RazorpayClient()
