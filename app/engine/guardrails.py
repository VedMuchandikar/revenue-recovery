"""Guardrail checks - deterministic safety rules before action execution."""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.db.models import (
    GuardrailCheck, GuardrailResult, ProposedAction, RevenueEvent,
    StoppingRuleState, RecoveryAction
)

logger = logging.getLogger(__name__)


# =============================================================================
# GUARDRAIL CHECK FUNCTIONS
# =============================================================================


async def check_max_attempts(event: RevenueEvent, proposed_action: ProposedAction) -> tuple[bool, str]:
    """
    Check if maximum retry attempts has been exceeded.

    Args:
        event: The revenue event
        proposed_action: The proposed action

    Returns:
        Tuple of (passes_check, details)
    """
    current_attempt = event.retry_count + proposed_action.attempt_number

    if current_attempt >= settings.max_attempts:
        return (False, f"Max attempts reached: {current_attempt} >= {settings.max_attempts}")

    return (True, f"Attempt {current_attempt} within limit of {settings.max_attempts}")


async def check_cooldown(event: RevenueEvent, state: Optional[StoppingRuleState]) -> tuple[bool, str]:
    """
    Check if cooldown period has passed since last action.

    Args:
        event: The revenue event
        state: Current stopping rule state (if exists)

    Returns:
        Tuple of (passes_check, details)
    """
    if not state or not state.cooldown_until:
        return (True, "No previous action - no cooldown required")

    now = datetime.now(timezone.utc)

    if now < state.cooldown_until:
        remaining_seconds = (state.cooldown_until - now).total_seconds()
        remaining_minutes = remaining_seconds / 60
        return (False, f"Cooldown active: {remaining_minutes:.1f} minutes remaining")

    return (True, "Cooldown period expired")


async def check_customer_opt_out(event: RevenueEvent, proposed_action: ProposedAction, state: Optional[StoppingRuleState]) -> tuple[bool, str]:
    """
    Check if customer has opted out of notifications.

    Args:
        event: The revenue event
        proposed_action: The proposed action
        state: Current stopping rule state (if exists)

    Returns:
        Tuple of (passes_check, details)
    """
    # Only applies to notification actions
    notification_actions = [
        RecoveryAction.NOTIFY_SMS.value,
        RecoveryAction.NOTIFY_EMAIL.value,
    ]

    if proposed_action.action_type.value not in notification_actions:
        return (True, "Not a notification action - opt-out check skipped")

    # Check if customer has opted out
    if state and state.customer_opted_out:
        return (False, "Customer has opted out of notifications")

    # Check DND eligibility
    if state and not state.customer_dnd_eligible:
        return (False, "Customer is not DND eligible")

    return (True, "Customer eligible for notifications")


async def check_amount_cap(event: RevenueEvent) -> tuple[bool, str]:
    """
    Check if event amount exceeds auto-recovery cap.

    Args:
        event: The revenue event

    Returns:
        Tuple of (passes_check, details)
    """
    amount = float(event.amount)

    if amount > settings.max_auto_recovery_amount:
        return (False, f"Amount {amount} exceeds cap {settings.max_auto_recovery_amount} - escalate")

    return (True, f"Amount {amount} within cap {settings.max_auto_recovery_amount}")


async def check_mandate_retry_limit(event: RevenueEvent, proposed_action: ProposedAction, state: Optional[StoppingRuleState]) -> tuple[bool, str]:
    """
    Check if mandate retry limit has been exceeded.

    Maximum 4 mandate retries within 30 days.

    Args:
        event: The revenue event
        proposed_action: The proposed action
        state: Current stopping rule state (if exists)

    Returns:
        Tuple of (passes_check, details)
    """
    # Only applies to mandate_retry action
    if proposed_action.action_type != RecoveryAction.MANDATE_RETRY:
        return (True, "Not a mandate_retry action - limit check skipped")

    if not state:
        # First mandate retry
        return (True, "First mandate retry attempt")

    # Check if we're within the 30-day window
    if state.first_mandate_retry_at:
        window_start = state.first_mandate_retry_at
        window_end = window_start + timedelta(days=settings.mandate_retry_window_days)
        now = datetime.now(timezone.utc)

        if now > window_end:
            # Window expired, reset count
            return (True, "30-day window expired, allowing retry")

    # Check retry count
    if state.mandate_retry_count >= settings.mandate_max_retries:
        return (False, f"Mandate retry limit reached: {state.mandate_retry_count} >= {settings.mandate_max_retries}")

    return (True, f"Mandate retry count: {state.mandate_retry_count} < {settings.mandate_max_retries}")


# =============================================================================
# MAIN GUARDRAIL FUNCTION
# =============================================================================


async def check_guardrails(
    event: RevenueEvent,
    proposed_action: ProposedAction,
    db_session: AsyncSession
) -> tuple[GuardrailCheck, GuardrailResult]:
    """
    Run all deterministic guardrail checks before action execution.

    Guardrails are ALWAYS deterministic - Claude cannot bypass them.

    Checks run in order:
    1. Amount cap (escalate if exceeded - human review needed)
    2. Max attempts (block if exceeded)
    3. Mandate retry limit (block if exceeded)
    4. Cooldown (block if still active)
    5. Customer opt-out (block if opted out)

    Args:
        event: The revenue event
        proposed_action: The proposed action
        db_session: Database session

    Returns:
        Tuple of (GuardrailCheck record, result)
        If any check fails with ESCALATE or BLOCK, execution stops
    """
    # Get or create stopping rule state
    from sqlalchemy import select

    query = select(StoppingRuleState).where(StoppingRuleState.event_id == event.id)
    result = await db_session.execute(query)
    state = result.scalar_one_or_none()

    if not state:
        state = StoppingRuleState(event_id=event.id)
        db_session.add(state)
        await db_session.flush()

    # Run checks in order
    checks = []

    # 1. Amount cap - MUST ESCALATE (never auto-recover high amounts)
    amount_passed, amount_details = await check_amount_cap(event)
    checks.append(("amount_cap", amount_passed, amount_details))
    guardrail_amount = GuardrailCheck(
        event_id=event.id,
        rule_name="amount_cap",
        result=GuardrailResult.ALLOW if amount_passed else GuardrailResult.ESCALATE,
        details=amount_details
    )
    db_session.add(guardrail_amount)

    if not amount_passed:
        logger.warning(f"Event {event.id} escalated: amount cap exceeded")
        return (guardrail_amount, GuardrailResult.ESCALATE)

    # 2. Max attempts - BLOCK if exceeded
    attempts_passed, attempts_details = await check_max_attempts(event, proposed_action)
    checks.append(("max_attempts", attempts_passed, attempts_details))
    guardrail_attempts = GuardrailCheck(
        event_id=event.id,
        rule_name="max_attempts",
        result=GuardrailResult.ALLOW if attempts_passed else GuardrailResult.BLOCK,
        details=attempts_details
    )
    db_session.add(guardrail_attempts)

    if not attempts_passed:
        logger.info(f"Event {event.id} blocked: max attempts exceeded")
        return (guardrail_attempts, GuardrailResult.BLOCK)

    # 3. Mandate retry limit - BLOCK if exceeded
    mandate_passed, mandate_details = await check_mandate_retry_limit(event, proposed_action, state)
    checks.append(("mandate_retry_limit", mandate_passed, mandate_details))
    guardrail_mandate = GuardrailCheck(
        event_id=event.id,
        rule_name="mandate_retry_limit",
        result=GuardrailResult.ALLOW if mandate_passed else GuardrailResult.BLOCK,
        details=mandate_details
    )
    db_session.add(guardrail_mandate)

    if not mandate_passed:
        logger.info(f"Event {event.id} blocked: mandate retry limit exceeded")
        return (guardrail_mandate, GuardrailResult.BLOCK)

    # 4. Cooldown - BLOCK if still active
    cooldown_passed, cooldown_details = await check_cooldown(event, state)
    checks.append(("cooldown", cooldown_passed, cooldown_details))
    guardrail_cooldown = GuardrailCheck(
        event_id=event.id,
        rule_name="cooldown",
        result=GuardrailResult.ALLOW if cooldown_passed else GuardrailResult.BLOCK,
        details=cooldown_details
    )
    db_session.add(guardrail_cooldown)

    if not cooldown_passed:
        logger.info(f"Event {event.id} blocked: cooldown active")
        return (guardrail_cooldown, GuardrailResult.BLOCK)

    # 5. Customer opt-out - BLOCK if opted out
    optout_passed, optout_details = await check_customer_opt_out(event, proposed_action, state)
    checks.append(("customer_opt_out", optout_passed, optout_details))
    guardrail_optout = GuardrailCheck(
        event_id=event.id,
        rule_name="customer_opt_out",
        result=GuardrailResult.ALLOW if optout_passed else GuardrailResult.BLOCK,
        details=optout_details
    )
    db_session.add(guardrail_optout)

    if not optout_passed:
        logger.info(f"Event {event.id} blocked: customer opted out")
        return (guardrail_optout, GuardrailResult.BLOCK)

    await db_session.flush()

    # All checks passed
    all_passed = all(passed for _, passed, _ in checks)
    details = "; ".join([f"{name}: {detail}" for name, passed, detail in checks])

    guardrail_final = GuardrailCheck(
        event_id=event.id,
        rule_name="all_checks_passed",
        result=GuardrailResult.ALLOW,
        details=details
    )
    db_session.add(guardrail_final)
    await db_session.flush()

    logger.info(f"Event {event.id} passed all guardrails: {details}")
    return (guardrail_final, GuardrailResult.ALLOW)


# Helper function to update stopping rule state after execution
async def update_stopping_rule_state(
    event: RevenueEvent,
    proposed_action: ProposedAction,
    db_session: AsyncSession
) -> None:
    """
    Update the stopping rule state after action execution.

    Args:
        event: The revenue event
        proposed_action: The proposed action that was executed
        db_session: Database session
    """
    from sqlalchemy import select

    query = select(StoppingRuleState).where(StoppingRuleState.event_id == event.id)
    result = await db_session.execute(query)
    state = result.scalar_one_or_none()

    if not state:
        state = StoppingRuleState(event_id=event.id)
        db_session.add(state)

    now = datetime.now(timezone.utc)

    # Update last action time
    state.last_action_at = now

    # Set cooldown until (if action is not immediate success)
    if proposed_action.action_type in [RecoveryAction.PAYMENT_LINK, RecoveryAction.INVOICE_REMINDER]:
        state.cooldown_until = now + timedelta(minutes=settings.cooldown_minutes)

    # Update mandate retry count
    if proposed_action.action_type == RecoveryAction.MANDATE_RETRY:
        if not state.first_mandate_retry_at:
            state.first_mandate_retry_at = now
        state.mandate_retry_count += 1

    await db_session.flush()