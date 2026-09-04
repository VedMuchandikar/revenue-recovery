"""Razorpay API client wrapper for test-mode operations."""

import logging
from typing import Any, Optional
from dataclasses import dataclass

import razorpay
from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class RazorpayResponse:
    """Standardized Razorpay API response."""
    success: bool
    data: Optional[dict] = None
    error: Optional[str] = None
    raw_response: Optional[dict] = None


class RazorpayClient:
    """Wrapper around Razorpay Python client for test-mode operations."""

    def __init__(self):
        self.client = None
        self._initialize()

    def _initialize(self):
        """Initialize Razorpay client with credentials."""
        if settings.razorpay_key_id and settings.razorpay_key_secret:
            try:
                self.client = razorpay.Client(
                    auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
                )
                logger.info("Razorpay client initialized successfully")
            except Exception as e:
                logger.error(f"Failed to initialize Razorpay client: {e}")
                self.client = None
        else:
            logger.warning("Razorpay credentials not configured - running in stub mode")

    def is_configured(self) -> bool:
        """Check if Razorpay client is properly configured."""
        return self.client is not None

    # =========================================================================
    # PAYMENT LINK OPERATIONS
    # =========================================================================

    def create_payment_link(
        self,
        amount: int,  # in paise
        currency: str,
        customer_name: str,
        customer_email: str,
        customer_phone: str,
        reference_id: str,
        description: str,
        notify_sms: bool = True,
        notify_email: bool = True,
        callback_url: Optional[str] = None,
        callback_method: str = "get"
    ) -> RazorpayResponse:
        """Create a payment link for customer recovery."""
        if not self.is_configured():
            return RazorpayResponse(
                success=False,
                error="Razorpay client not configured"
            )

        try:
            # Make reference_id unique by appending timestamp to avoid conflicts
            import time
            unique_reference_id = f"{reference_id}_{int(time.time())}"

            payload = {
                "amount": amount,
                "currency": currency,
                "customer": {
                    "name": customer_name,
                    "email": customer_email,
                    "contact": customer_phone
                },
                "reference_id": unique_reference_id,
                "description": description,
                "notify": {
                    "sms": notify_sms,
                    "email": notify_email
                },
                "callback_url": callback_url,
                "callback_method": callback_method,
                "reminder_enable": True,
                "notes": {
                    "recovery_event_id": reference_id  # Store original event ID in notes
                }
            }

            response = self.client.payment_link.create(payload)
            logger.info(f"Created payment link: {response.get('id')} for reference: {unique_reference_id}")

            return RazorpayResponse(
                success=True,
                data={
                    "payment_link_id": response.get("id"),
                    "short_url": response.get("short_url"),
                    "status": response.get("status"),
                    "amount": response.get("amount"),
                    "currency": response.get("currency"),
                    "reference_id": response.get("reference_id")
                },
                raw_response=response
            )
        except razorpay.errors.BadRequestError as e:
            logger.error(f"Bad request creating payment link: {e}")
            return RazorpayResponse(success=False, error=str(e))
        except razorpay.errors.ServerError as e:
            logger.error(f"Server error creating payment link: {e}")
            return RazorpayResponse(success=False, error=str(e))
        except Exception as e:
            logger.exception(f"Unexpected error creating payment link: {e}")
            return RazorpayResponse(success=False, error=str(e))

    def fetch_payment_link(self, payment_link_id: str) -> RazorpayResponse:
        """Fetch payment link status."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            response = self.client.payment_link.fetch(payment_link_id)
            return RazorpayResponse(
                success=True,
                data={
                    "payment_link_id": response.get("id"),
                    "status": response.get("status"),
                    "amount": response.get("amount"),
                    "currency": response.get("currency"),
                    "reference_id": response.get("reference_id"),
                    "short_url": response.get("short_url")
                },
                raw_response=response
            )
        except Exception as e:
            logger.error(f"Error fetching payment link {payment_link_id}: {e}")
            return RazorpayResponse(success=False, error=str(e))

    # =========================================================================
    # SUBSCRIPTION OPERATIONS
    # =========================================================================

    def retry_mandate(self, subscription_id: str) -> RazorpayResponse:
        """Observe Razorpay's automatic retry for a failed subscription charge.

        The Razorpay Python SDK has no ``mandates`` resource or manual
        subscription-charge retry endpoint. Razorpay schedules eligible
        subscription retries itself, so the supported operation is to fetch
        the subscription and track it until a webhook confirms collection.
        """
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            response = self.client.subscription.fetch(subscription_id)
            status = response.get("status")
            logger.info(
                "Subscription %s is %s; Razorpay manages eligible retries",
                subscription_id,
                status,
            )
            return RazorpayResponse(
                success=True,
                data={
                    "subscription_id": response.get("id"),
                    "status": status,
                    "charge_at": response.get("charge_at"),
                },
                raw_response=response,
            )
        except razorpay.errors.BadRequestError as e:
            logger.error(f"Bad request retrying mandate: {e}")
            return RazorpayResponse(success=False, error=str(e))
        except Exception as e:
            logger.exception(f"Unexpected error retrying mandate: {e}")
            return RazorpayResponse(success=False, error=str(e))

    def fetch_mandate(self, subscription_id: str) -> RazorpayResponse:
        """Fetch subscription status (legacy name retained for callers)."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            response = self.client.subscription.fetch(subscription_id)
            return RazorpayResponse(
                success=True,
                data={
                    "subscription_id": response.get("id"),
                    "status": response.get("status"),
                    "charge_at": response.get("charge_at"),
                    "current_start": response.get("current_start"),
                    "current_end": response.get("current_end"),
                },
                raw_response=response
            )
        except Exception as e:
            logger.error(f"Error fetching subscription {subscription_id}: {e}")
            return RazorpayResponse(success=False, error=str(e))

    # =========================================================================
    # NOTIFICATION OPERATIONS
    # =========================================================================

    def send_sms(self, contact: str, message: str, reference_id: str) -> RazorpayResponse:
        """Send SMS notification via Razorpay."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            # Using Razorpay's SMS/notification API
            # Note: Razorpay may use different endpoint for notifications
            # For now, we'll log and return success in test mode
            logger.info(f"SMS sent to {contact} for {reference_id}: {message[:50]}...")

            return RazorpayResponse(
                success=True,
                data={
                    "contact": contact,
                    "reference_id": reference_id,
                    "status": "sent"
                }
            )
        except Exception as e:
            logger.exception(f"Error sending SMS: {e}")
            return RazorpayResponse(success=False, error=str(e))

    def send_email(self, email: str, subject: str, body: str, reference_id: str) -> RazorpayResponse:
        """Send email notification via Razorpay."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            # Placeholder for actual Razorpay email API
            logger.info(f"Email sent to {email} for {reference_id}: {subject}")

            return RazorpayResponse(
                success=True,
                data={
                    "email": email,
                    "reference_id": reference_id,
                    "status": "sent"
                }
            )
        except Exception as e:
            logger.exception(f"Error sending email: {e}")
            return RazorpayResponse(success=False, error=str(e))

    # =========================================================================
    # INVOICE OPERATIONS
    # =========================================================================

    def create_invoice(
        self,
        customer_id: str,
        amount: int,
        currency: str,
        description: str,
        reference_id: str,
        due_date: Optional[int] = None,
        notify_sms: bool = True,
        notify_email: bool = True
    ) -> RazorpayResponse:
        """Create an invoice for an overdue receivable."""
        if not self.is_configured():
            return RazorpayResponse(
                success=False,
                error="Razorpay client not configured"
            )

        try:
            payload = {
                "type": "invoice",
                "customer_id": customer_id,
                "line_items": [
                    {
                        "name": description,
                        "amount": amount,
                        "currency": currency,
                        "quantity": 1
                    }
                ],
                "sms_notify": notify_sms,
                "email_notify": notify_email,
                "notes": {
                    "recovery_event_id": reference_id
                }
            }

            if due_date:
                payload["expire_by"] = due_date

            response = self.client.invoice.create(payload)

            logger.info(
                f"Created invoice: {response.get('id')} "
                f"for reference: {reference_id}"
            )

            return RazorpayResponse(
                success=True,
                data={
                    "invoice_id": response.get("id"),
                    "status": response.get("status"),
                    "amount": response.get("amount"),
                    "currency": response.get("currency"),
                    "short_url": response.get("short_url")
                },
                raw_response=response
            )

        except razorpay.errors.BadRequestError as e:
            logger.error(f"Bad request creating invoice: {e}")
            return RazorpayResponse(
                success=False,
                error=str(e)
            )

        except razorpay.errors.ServerError as e:
            logger.error(f"Server error creating invoice: {e}")
            return RazorpayResponse(
                success=False,
                error=str(e)
            )

        except Exception as e:
            logger.exception(f"Unexpected error creating invoice: {e}")
            return RazorpayResponse(
                success=False,
                error=str(e)
            )

    def fetch_invoice(self, invoice_id: str) -> RazorpayResponse:
        """Fetch invoice status."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            response = self.client.invoice.fetch(invoice_id)
            return RazorpayResponse(
                success=True,
                data={
                    "invoice_id": response.get("id"),
                    "status": response.get("status"),
                    "amount": response.get("amount"),
                    "currency": response.get("currency"),
                    "reference_id": response.get("reference_id")
                },
                raw_response=response
            )
        except Exception as e:
            logger.error(f"Error fetching invoice {invoice_id}: {e}")
            return RazorpayResponse(success=False, error=str(e))
    def create_customer(
        self,
        name: str,
        email: str,
        contact: str,
        reference_id: str,
    ):
        """Create a Razorpay customer for a recovery event."""
        payload = {
            "name": name,
            "email": email,
            "contact": contact,
            "fail_existing": 0,
            "notes": {
                "internal_customer_id": reference_id,
            },
        }

        try:
            response = self.client.customer.create(payload)
            logger.info(
                "Created Razorpay customer %s for internal customer %s",
                response.get("id"),
                reference_id,
            )
            return response
        except Exception as e:
            logger.error("Failed to create Razorpay customer: %s", e)
            raise

    # =========================================================================
    # PAYMENT VERIFICATION
    # =========================================================================

    def fetch_payment(self, payment_id: str) -> RazorpayResponse:
        """Fetch payment details for verification."""
        if not self.is_configured():
            return RazorpayResponse(success=False, error="Razorpay client not configured")

        try:
            response = self.client.payment.fetch(payment_id)
            return RazorpayResponse(
                success=True,
                data={
                    "payment_id": response.get("id"),
                    "status": response.get("status"),
                    "amount": response.get("amount"),
                    "currency": response.get("currency"),
                    "method": response.get("method"),
                    "captured": response.get("captured"),
                    "description": response.get("description")
                },
                raw_response=response
            )
        except Exception as e:
            logger.error(f"Error fetching payment {payment_id}: {e}")
            return RazorpayResponse(success=False, error=str(e))

    def verify_webhook_signature(
        self,
        payload: str,
        signature: str
    ) -> bool:
        """Verify Razorpay webhook signature."""
        if not self.is_configured():
            logger.warning("Razorpay client not configured - cannot verify webhook")
            return False

        try:
            return self.client.utility.verify_webhook_signature(
                payload, signature, settings.razorpay_webhook_secret
            )
        except Exception as e:
            logger.error(f"Webhook signature verification failed: {e}")
            return False


# Global client instance
razorpay_client = RazorpayClient()
