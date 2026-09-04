"""Diagnosis engine with rule-based mapping and Groq LLM fallback."""

import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import (
    Diagnosis, DiagnosisSource, RevenueEvent, RootCause
)

logger = logging.getLogger(__name__)

# =============================================================================
# RULE-BASED DIAGNOSIS MAPPINGS
# =============================================================================

# Map Razorpay error codes to root causes (deterministic)
ERROR_CODE_TO_ROOT_CAUSE = {
    # Card declined scenarios
    "card_declined": RootCause.CARD_DECLINED,
    "card_not_supported": RootCause.CARD_DECLINED,
    "card_limit_exceeded": RootCause.CARD_DECLINED,
    "card_expired": RootCause.CARD_DECLINED,
    "invalid_card": RootCause.CARD_DECLINED,
    "incorrect_cvc": RootCause.CARD_DECLINED,
    "verification_failed": RootCause.CARD_DECLINED,

    # Insufficient funds
    "insufficient_balance": RootCause.INSUFFICIENT_FUNDS,
    "insufficient_funds": RootCause.INSUFFICIENT_FUNDS,

    # Authentication failures
    "authentication_failed": RootCause.AUTHENTICATION_FAILURE,
    "card_not_authenticated": RootCause.AUTHENTICATION_FAILURE,
    "redirect_not_verified": RootCause.AUTHENTICATION_FAILURE,
    "otp_verification_failed": RootCause.AUTHENTICATION_FAILURE,

    # Gateway/Network timeouts
    "gateway_timeout": RootCause.GATEWAY_TIMEOUT,
    "request_timeout": RootCause.GATEWAY_TIMEOUT,
    "network_error": RootCause.NETWORK_ERROR,
    "connection_error": RootCause.NETWORK_ERROR,

    # Provider errors
    "provider_error": RootCause.PROVIDER_ERROR,
    "acquirer_error": RootCause.PROVIDER_ERROR,
    "banking_network_error": RootCause.PROVIDER_ERROR,

    # Subscription failures
    "subscription_failed": RootCause.SUBSCRIPTION_FAILURE,
    "subscription_cancelled": RootCause.SUBSCRIPTION_FAILURE,
    "subscription_expired": RootCause.SUBSCRIPTION_FAILURE,
    "mandate_failed": RootCause.SUBSCRIPTION_FAILURE,
    "recurring_payment_failed": RootCause.SUBSCRIPTION_FAILURE,
}


def _rule_based_diagnosis(event: RevenueEvent) -> Optional[tuple[RootCause, str]]:
    """
    Perform rule-based diagnosis using deterministic mappings.

    Args:
        event: The revenue event to diagnose

    Returns:
        Tuple of (root_cause, rationale) if a rule matches, None otherwise
    """
    # Check reason_code first (most reliable source)
    if event.reason_code:
        reason_code_lower = event.reason_code.lower().strip()
        if reason_code_lower in ERROR_CODE_TO_ROOT_CAUSE:
            root_cause = ERROR_CODE_TO_ROOT_CAUSE[reason_code_lower]
            return (root_cause, f"Rule-based mapping: error code '{event.reason_code}' → {root_cause.value}")

    # Check metadata for additional context
    if event.metadata_json:
        metadata = event.metadata_json if isinstance(event.metadata_json, dict) else json.loads(event.metadata_json)

        # Check for error codes in metadata
        if "error_code" in metadata:
            error_code = metadata["error_code"].lower().strip() if isinstance(metadata["error_code"], str) else str(metadata["error_code"])
            if error_code in ERROR_CODE_TO_ROOT_CAUSE:
                root_cause = ERROR_CODE_TO_ROOT_CAUSE[error_code]
                return (root_cause, f"Rule-based mapping: metadata error code → {root_cause.value}")

        # Check for gateway error indicators
        if metadata.get("gateway_error", False):
            return (RootCause.GATEWAY_TIMEOUT, "Rule-based: gateway error flag in metadata")

        if metadata.get("network_error", False):
            return (RootCause.NETWORK_ERROR, "Rule-based: network error flag in metadata")

    # Fallback: map by event type
    if event.type.value == "payment_failed":
        # Default for unknown payment failures
        return (RootCause.UNKNOWN, "Rule-based: payment_failed type with unknown error")

    elif event.type.value == "subscription_failed":
        return (RootCause.SUBSCRIPTION_FAILURE, "Rule-based: subscription failure type")

    elif event.type.value == "checkout_abandoned":
        return (RootCause.CHECKOUT_ABANDONMENT, "Rule-based: checkout abandonment detected")

    elif event.type.value == "receivable_overdue":
        return (RootCause.RECEIVABLE_OVERDUE, "Rule-based: receivable overdue")

    return None


# =============================================================================
# GROQ LLM DIAGNOSIS
# =============================================================================

async def _llm_diagnosis(event: RevenueEvent) -> Optional[dict]:
    """
    Use Groq LLM to diagnose the root cause when rules can't determine it.

    Only called when:
    - Rule-based diagnosis returns None
    - Root cause is UNKNOWN

    Args:
        event: The revenue event to diagnose

    Returns:
        Dict with root_cause, confidence, rationale or None if Groq fails
    """
    # Check if API key is configured
    if not settings.anthropic_api_key:
        logger.warning("Groq diagnosis skipped: API_KEY not configured")
        return None

    try:
        import httpx

        # Build context for Groq
        event_context = {
            "event_type": event.type.value,
            "reason_code": event.reason_code,
            "reason_description": event.reason_description,
            "amount": str(event.amount),
            "currency": event.currency,
            "customer_id": event.customer_id,
        }

        # Add metadata if available
        if event.metadata_json:
            if isinstance(event.metadata_json, dict):
                event_context["metadata"] = event.metadata_json
            else:
                try:
                    event_context["metadata"] = json.loads(event.metadata_json)
                except json.JSONDecodeError:
                    pass

        # Prompt for Groq
        prompt = f"""You are a payment failure diagnosis expert for Razorpay. Analyze this event and determine the root cause.

Event Context:
{json.dumps(event_context, indent=2)}

Allowed Root Causes (choose one):
- card_declined: Card was declined by issuing bank
- insufficient_funds: Customer has insufficient funds
- authentication_failure: 3D Secure or authentication failed
- gateway_timeout: Payment gateway timed out
- network_error: Network connectivity issue
- provider_error: Razorpay or acquiring bank error
- subscription_failure: Recurring payment/subscription failed
- checkout_abandonment: Customer abandoned checkout
- receivable_overdue: Invoice is past due date
- unknown: Cannot determine from available information

Respond with JSON only (no other text):
{{
  "rootCause": "one of the allowed causes",
  "confidence": 0.0-1.0,
  "rationale": "brief explanation (max 100 words)"
}}

If confidence is below {settings.claude_confidence_threshold}, set rootCause to "unknown".
"""

        # Use direct HTTP call to Groq API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.anthropic_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "llama-3.1-8b-instant",
                    "max_tokens": 300,
                    "messages": [
                        {"role": "system", "content": "You are a payment failure diagnosis expert. Respond with valid JSON only."},
                        {"role": "user", "content": prompt}
                    ]
                },
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

        # Parse response
        response_text = data["choices"][0]["message"]["content"].strip()

        # Try to parse as JSON
        try:
            # Find JSON in response (in case there's extra text)
            if "{" in response_text:
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                json_text = response_text[json_start:json_end]
                result = json.loads(json_text)
            else:
                result = json.loads(response_text)

            # Validate response
            root_cause_str = result.get("rootCause", "").lower().strip()
            confidence = float(result.get("confidence", 0))
            rationale = result.get("rationale", "")

            # Validate root cause is in allowed enum
            valid_causes = [c.value for c in RootCause]
            if root_cause_str not in valid_causes:
                logger.warning(f"Invalid root cause from Groq: {root_cause_str}")
                return None

            root_cause = RootCause(root_cause_str)

            return {
                "root_cause": root_cause,
                "source": DiagnosisSource.LLM,
                "confidence": confidence,
                "rationale": rationale[:500],  # Limit length for audit
                "model_name": "llama-3.1-8b-instant"
            }

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"Failed to parse Groq response: {e}")
            logger.debug(f"Raw response: {response_text}")
            return None

    except Exception as e:
        logger.exception(f"Groq diagnosis failed: {e}")
        return None


# =============================================================================
# MAIN DIAGNOSIS FUNCTION
# =============================================================================

async def diagnose_event(event: RevenueEvent, db_session: AsyncSession) -> Diagnosis:
    """
    Diagnose the root cause of a revenue event.

    Flow:
    1. Try rule-based diagnosis first (deterministic, fast)
    2. If no rule matches or root cause is UNKNOWN, use Claude fallback
    3. If Claude is unavailable or confidence is low, escalate

    Args:
        event: The revenue event to diagnose
        db_session: Database session

    Returns:
        Diagnosis record with root cause and metadata
    """
    # Step 1: Try rule-based diagnosis
    rule_result = _rule_based_diagnosis(event)

    if rule_result:
        root_cause, rationale = rule_result

        # Check if we need Claude for unknown cases
        if root_cause != RootCause.UNKNOWN:
            # Deterministic rule matched
            diagnosis = Diagnosis(
                event_id=event.id,
                root_cause=root_cause,
                source=DiagnosisSource.RULE,
                confidence=1.0,
                rationale=rationale
            )
            db_session.add(diagnosis)
            await db_session.flush()
            logger.info(f"Rule-based diagnosis for event {event.id}: {root_cause.value}")
            return diagnosis
        else:
            # Rule matched but returned UNKNOWN, try Claude
            logger.info(f"Rule returned UNKNOWN for event {event.id}, trying Claude...")

    # Step 2: Try Groq for unknown/inconclusive cases
    groq_result = await _llm_diagnosis(event)

    if groq_result:
        # Check confidence threshold
        if groq_result["confidence"] < settings.claude_confidence_threshold:
            logger.warning(
                f"Claude confidence {groq_result['confidence']} below threshold "
                f"{settings.claude_confidence_threshold} for event {event.id}"
            )
            # Will be handled by caller - escalate this event
            diagnosis = Diagnosis(
                event_id=event.id,
                root_cause=RootCause.UNKNOWN,
                source=groq_result["source"],
                confidence=groq_result["confidence"],
                rationale=f"Low confidence ({groq_result['confidence']}), needs human review. {groq_result.get('rationale', '')}",
                model_name=groq_result.get("model_name")
            )
            db_session.add(diagnosis)
            await db_session.flush()
            return diagnosis

        # Valid high-confidence diagnosis from Claude
        diagnosis = Diagnosis(
            event_id=event.id,
            root_cause=groq_result["root_cause"],
            source=groq_result["source"],
            confidence=groq_result["confidence"],
            rationale=groq_result["rationale"],
            model_name=groq_result.get("model_name")
        )
        db_session.add(diagnosis)
        await db_session.flush()
        logger.info(f"Claude diagnosis for event {event.id}: {diagnosis.root_cause.value} (confidence: {diagnosis.confidence})")
        return diagnosis

    # Step 3: Both rule and Claude failed - return UNKNOWN
    diagnosis = Diagnosis(
        event_id=event.id,
        root_cause=RootCause.UNKNOWN,
        source=DiagnosisSource.RULE,
        confidence=0.0,
        rationale="No deterministic rule matched and Claude unavailable or failed"
    )
    db_session.add(diagnosis)
    await db_session.flush()
    logger.warning(f"No diagnosis available for event {event.id}, marked as UNKNOWN")

    return diagnosis