"""Decision engine with rule-based action mapping and Groq LLM fallback."""

import json
import logging
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import (
    Diagnosis, ProposedAction, RecoveryAction, NotificationChannel,
    RevenueEvent, RootCause
)
from app.engine.strategy import amount_band, rank_candidates
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

logger = logging.getLogger(__name__)

# =============================================================================
# RULE-BASED DECISION MAPPINGS
# =============================================================================

# Map root cause to recovery action (deterministic)
ROOT_CAUSE_TO_ACTION = {
    RootCause.CARD_DECLINED: (RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
    RootCause.INSUFFICIENT_FUNDS: (RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
    RootCause.AUTHENTICATION_FAILURE: (RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
    RootCause.GATEWAY_TIMEOUT: (RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
    RootCause.NETWORK_ERROR: (RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
    RootCause.PROVIDER_ERROR: (RecoveryAction.PAYMENT_LINK, NotificationChannel.RAZORPAY),
    RootCause.SUBSCRIPTION_FAILURE: (RecoveryAction.MANDATE_RETRY, NotificationChannel.EMAIL),
    RootCause.CHECKOUT_ABANDONMENT: (RecoveryAction.PAYMENT_LINK, NotificationChannel.SMS),
    RootCause.RECEIVABLE_OVERDUE: (RecoveryAction.INVOICE_REMINDER, NotificationChannel.EMAIL),
    RootCause.UNKNOWN: (RecoveryAction.NOTIFY_EMAIL, NotificationChannel.EMAIL),  # fallback
}

# Alternative actions when primary action fails or needs variation
ROOT_CAUSE_ALTERNATIVES = {
    RootCause.CARD_DECLINED: [
        (RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
        (RecoveryAction.NOTIFY_SMS, NotificationChannel.SMS),
    ],
    RootCause.INSUFFICIENT_FUNDS: [
        (RecoveryAction.PAYMENT_LINK, NotificationChannel.EMAIL),
        (RecoveryAction.PROMISE_TO_PAY, NotificationChannel.SMS),
    ],
    RootCause.SUBSCRIPTION_FAILURE: [
        (RecoveryAction.NOTIFY_EMAIL, NotificationChannel.EMAIL),
        (RecoveryAction.NOTIFY_SMS, NotificationChannel.SMS),
    ],
}


def _rule_based_decision(event: RevenueEvent, diagnosis: Diagnosis) -> Optional[tuple[RecoveryAction, NotificationChannel, str]]:
    """
    Perform rule-based decision using deterministic mappings.

    Args:
        event: The revenue event
        diagnosis: The diagnosis result

    Returns:
        Tuple of (action_type, channel, rationale) if a rule matches, None otherwise
    """
    root_cause = diagnosis.root_cause

    # Check if we have a rule for this root cause
    if root_cause in ROOT_CAUSE_TO_ACTION:
        action_type, channel = ROOT_CAUSE_TO_ACTION[root_cause]
        rationale = f"Rule-based: {root_cause.value} → {action_type.value} via {channel.value}"
        return (action_type, channel, rationale)

    # Fallback to unknown
    return (RecoveryAction.NOTIFY_EMAIL, NotificationChannel.EMAIL, "Rule-based: unknown root cause → notify_email")


async def _llm_decision(event: RevenueEvent, diagnosis: Diagnosis, context: dict) -> Optional[dict]:
    """
    Use Groq LLM to decide the recovery action when rules are ambiguous.

    Called when:
    - Multiple valid actions are plausible
    - Rule engine cannot determine optimal action
    - Additional context is necessary

    Args:
        event: The revenue event
        diagnosis: The diagnosis result
        context: Additional context for decision

    Returns:
        Dict with action_type, channel, rationale or None if Groq fails
    """
    # Check if API key is configured
    if not settings.planner_use_llm or not settings.groq_api_key:
        logger.info("LLM planner disabled or not configured; using evidence-ranked policy")
        return None

    try:
        import httpx

        # Build context for Groq
        decision_context = {
            "diagnosis": {
                "root_cause": diagnosis.root_cause.value,
                "confidence": diagnosis.confidence,
                "source": diagnosis.source.value,
            },
            "event": {
                "event_type": event.type.value,
                "amount": str(event.amount),
                "currency": event.currency,
                "customer_id": event.customer_id,
                "retry_count": event.retry_count,
            },
            "context": context,
        }

        # The model can select only a policy-approved candidate. Guardrails
        # still run after this step and can block the selected intervention.
        candidates = context["ranked_candidates"]
        allowed_pairs = {
            (candidate["action_type"], candidate["channel"])
            for candidate in candidates
        }

        # Prompt for Groq
        prompt = f"""You are a payment recovery expert for Razorpay. Based on the diagnosis, decide the optimal recovery action.

Diagnosis and Context:
{json.dumps(decision_context, indent=2)}

Permitted action/channel pairs (choose exactly one):
{json.dumps(candidates, indent=2)}

Respond with JSON only (no other text):
{{
  "actionType": "one of the allowed actions",
  "channel": "one of the allowed channels",
  "rationale": "brief explanation (max 100 words)",
  "confidence": 0.0-1.0
}}

Choose the most appropriate action based on:
1. Root cause and event type
2. The historical expected_recovery_score for each permitted candidate
3. Customer retry history and amount at risk
"""

        # Use direct HTTP call to Groq API
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "openai/gpt-oss-20b",
                    "max_tokens": 400,
                    "temperature": 0.1,
                    "messages": [
                        {"role": "system", "content": "You are a payment recovery expert. Respond with valid JSON only."},
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
            action_type_str = result.get("actionType", "").lower().strip()
            channel_str = result.get("channel", "").lower().strip()
            rationale = result.get("rationale", "")
            confidence = float(result.get("confidence", 0.5))

            if (action_type_str, channel_str) not in allowed_pairs:
                logger.warning("LLM chose non-permitted action/channel: %s/%s", action_type_str, channel_str)
                return None

            action_type = RecoveryAction(action_type_str)
            channel = NotificationChannel(channel_str)

            return {
                "action_type": action_type,
                "channel": channel,
                "rationale": rationale[:500],  # Limit length for audit
                "confidence": confidence,
                "model_name": "llama-3.1-8b-instant"
            }

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"Failed to parse Groq response: {e}")
            logger.debug(f"Raw response: {response_text}")
            return None

    except Exception as e:
        logger.exception(f"Groq decision failed: {e}")
        return None


# =============================================================================
# MAIN DECISION FUNCTION
# =============================================================================

async def decide_action(event: RevenueEvent, diagnosis: Diagnosis, db_session: AsyncSession) -> ProposedAction:
    """
    Decide the recovery action for a revenue event.

    The planner first creates a small, policy-approved action set, ranks it
    using verified historical outcomes, and lets the LLM choose only within
    that set when configured. Without an LLM key, the top evidence-ranked
    candidate is used. This keeps the system adaptive without permitting
    unbounded model actions.

    Args:
        event: The revenue event
        diagnosis: The diagnosis result
        db_session: Database session

    Returns:
        ProposedAction record with action type and channel
    """
    # Build the bounded intervention set from the product policy.
    rule_result = _rule_based_decision(event, diagnosis)
    if rule_result:
        primary_action, primary_channel, primary_rationale = rule_result
    else:
        primary_action, primary_channel = RecoveryAction.NOTIFY_EMAIL, NotificationChannel.EMAIL
        primary_rationale = "Fallback policy: request human-friendly email follow-up"

    candidates = [(primary_action, primary_channel)] + ROOT_CAUSE_ALTERNATIVES.get(
        diagnosis.root_cause, []
    )
    ranked_candidates = await rank_candidates(
        db_session,
        candidates,
        event.amount,
        diagnosis.root_cause.value,
    )
    planner_context = {
        "amount_band": amount_band(event.amount),
        "ranked_candidates": [
            {
                **candidate,
                "action_type": candidate["action_type"].value,
                "channel": candidate["channel"].value,
            }
            for candidate in ranked_candidates
        ],
    }

    llm_result = await _llm_decision(event, diagnosis, planner_context)
    if llm_result:
        action_type = llm_result["action_type"]
        channel = llm_result["channel"]
        rationale = llm_result["rationale"]
        decision_source = "llm_constrained_planner"
        model_name = llm_result["model_name"]
    else:
        top_candidate = ranked_candidates[0]
        action_type = top_candidate["action_type"]
        channel = top_candidate["channel"]
        rationale = (
            f"Policy-ranked: {primary_rationale}; expected recovery score "
            f"{top_candidate['expected_recovery_score']} from "
            f"{top_candidate['historical_recoveries']}/"
            f"{top_candidate['historical_attempts']} verified recoveries"
        )
        decision_source = "evidence_ranked_policy"
        model_name = None


    attempt_number = event.retry_count + 1
    # Idempotency check — reuse existing action for this exact attempt
    existing = await db_session.execute(
        select(ProposedAction).where(
            ProposedAction.event_id == event.id,
            ProposedAction.attempt_number == attempt_number,
        )
    )
    existing_action = existing.scalar_one_or_none()
    if existing_action:
        logger.info(
            f"ProposedAction already exists for event {event.id} attempt {attempt_number}, reusing"
        )
        return existing_action

    proposed_action = ProposedAction(
        event_id=event.id,
        action_type=action_type,
        channel=channel,
        attempt_number=attempt_number,
        context_json={
            "diagnosis_root_cause": diagnosis.root_cause.value,
            "diagnosis_source": diagnosis.source.value,
            "decision_source": decision_source,
            "planner_rationale": rationale,
            "planner_model": model_name,
            **planner_context,
        }
    )
    db_session.add(proposed_action)

    try:
        await db_session.flush()
    except IntegrityError:
        # Race: another worker inserted the same (event_id, attempt_number) first
        await db_session.rollback()
        logger.info(
            f"Race detected for event {event.id} attempt {attempt_number}, fetching winner"
        )
        result = await db_session.execute(
            select(ProposedAction).where(
                ProposedAction.event_id == event.id,
                ProposedAction.attempt_number == attempt_number,
            )
        )
        proposed_action = result.scalar_one()

    logger.info(
        "Planner decision for event %s: %s via %s (%s)",
        event.id, action_type.value, channel.value, decision_source,
    )
    return proposed_action


    # proposed_action = ProposedAction(
    #     event_id=event.id,
    #     action_type=action_type,
    #     channel=channel,
    #     attempt_number=event.retry_count + 1,
    #     context_json={
    #         "diagnosis_root_cause": diagnosis.root_cause.value,
    #         "diagnosis_source": diagnosis.source.value,
    #         "decision_source": decision_source,
    #         "planner_rationale": rationale,
    #         "planner_model": model_name,
    #         **planner_context,
    #     }
    # )
    # db_session.add(proposed_action)
    # await db_session.flush()

    # logger.info(
    #     "Planner decision for event %s: %s via %s (%s)",
    #     event.id, action_type.value, channel.value, decision_source,
    # )
    # return proposed_action
