"""Orchestrator for processing revenue events through the recovery pipeline."""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.engine.audit import audit_event
from app.engine.diagnose import diagnose_event
from app.engine.decide import decide_action
from app.engine.execute import execute_action, verify_action
from app.engine.guardrails import check_guardrails, update_stopping_rule_state
from app.db.models import (
    RevenueEvent, RevenueEventStatus, Diagnosis, DiagnosisSource, RootCause,
    ProposedAction, RecoveryAction, NotificationChannel,
    GuardrailCheck, GuardrailResult, ActionResult, ActionResultStatus,
    Outcome
)

logger = logging.getLogger(__name__)


async def process_event(event_id: str, db_session: AsyncSession) -> None:
    """
    Process a revenue event through the complete recovery pipeline.

    Pipeline:
    RevenueEvent
        ↓
    audit DETECT
        ↓
    DIAGNOSE (real)
        ↓
    audit DIAGNOSE
        ↓
    DECIDE (real)
        ↓
    audit DECIDE
        ↓
    GUARDRAILS (real)
        ↓
    audit GUARDRAIL
        ↓
    if ALLOW
        ↓
    EXECUTE (real)
        ↓
    audit EXECUTE
        ↓
    VERIFY (real)
        ↓
    Outcome
        ↓
    audit OUTCOME
        ↓
    COMPLETED

    Args:
        event_id: ID of the revenue event to process
        db_session: Database session
    """
    # Get the event
    from sqlalchemy import select

    query = select(RevenueEvent).where(RevenueEvent.id == event_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        logger.error("Event %s not found", event_id)
        return

    logger.info("Processing event %s (type: %s)", event.id, event.type.value)

    try:
        # Step 1: Audit the initial detection (already happened, but we audit the pipeline start)
        await audit_event(
            db_session, event.id, "detect",
            input_json={"event_id": event_id},
            output_json={"status": event.status.value}
        )

        # Step 2: REAL Diagnosis (rule-based + Claude fallback)
        diagnosis = await diagnose_event(event, db_session)
        diagnosis_result = {
            "root_cause": diagnosis.root_cause.value,
            "source": diagnosis.source.value,
            "confidence": diagnosis.confidence,
            "rationale": diagnosis.rationale,
            "model_name": diagnosis.model_name
        }
        await audit_event(
            db_session, event.id, "diagnose",
            input_json={"event_id": event_id},
            output_json=diagnosis_result
        )

        # Check if diagnosis resulted in unknown with low confidence - escalate
        if diagnosis.root_cause == RootCause.UNKNOWN and diagnosis.source == DiagnosisSource.LLM:
            logger.warning(f"Event {event.id} has low-confidence UNKNOWN diagnosis, escalating")
            event.status = RevenueEventStatus.ESCALATED
            event.last_error = "Low confidence diagnosis - requires human review"
            await db_session.commit()
            return

        # Step 3: REAL Decision (rule-based + Claude fallback)
        proposed_action = await decide_action(event, diagnosis, db_session)
        decision_result = {
            "action_type": proposed_action.action_type.value,
            "channel": proposed_action.channel.value,
            "attempt_number": proposed_action.attempt_number,
            "context": proposed_action.context_json
        }
        await audit_event(
            db_session, event.id, "decide",
            input_json={"diagnosis": diagnosis_result},
            output_json=decision_result
        )

        # Step 4: REAL Guardrails (deterministic safety checks)
        guardrail_check, guardrail_result = await check_guardrails(event, proposed_action, db_session)
        guardrail_result_dict = {
            "rule_name": guardrail_check.rule_name,
            "result": guardrail_result.value,
            "details": guardrail_check.details
        }
        await audit_event(
            db_session, event.id, "guardrail",
            input_json={"decision": decision_result},
            output_json=guardrail_result_dict
        )

        # Check if guardrail allows execution
        if guardrail_result.value != "allow":
            logger.info("Event %s blocked by guardrails: %s", event.id, guardrail_result_dict)
            # Update event status based on guardrail result
            if guardrail_result.value == "block":
                event.status = RevenueEventStatus.BLOCKED
            elif guardrail_result.value == "escalate":
                event.status = RevenueEventStatus.ESCALATED
            await db_session.commit()
            return

        # Step 5: REAL Execution (Razorpay API integration)
        execution_result = await execute_action(event, decision_result, db_session)
        external_ref_id = execution_result.get("external_ref_id")
        if external_ref_id:
            event.razorpay_ref_id = external_ref_id

        # Persist cooldown and mandate attempt counters only after the action
        # is accepted. Without this call, subsequent guardrail checks never
        # see the prior action.
        if execution_result.get("success"):
            await update_stopping_rule_state(event, proposed_action, db_session)
            
        await audit_event(
            db_session, event.id, "execute",
            input_json={"decision": decision_result, "guardrail": guardrail_result},
            output_json=execution_result
        )

        # Step 6: REAL Verification (webhook/polling based)
        verification_result = await verify_action(event, execution_result)
        await audit_event(
            db_session, event.id, "verify",
            input_json={"execution": execution_result},
            output_json=verification_result
        )

        # Step 7: Create outcome if verification successful
        if verification_result.get("verified", False):
            outcome = Outcome(
                event_id=event.id,
                recovered_amount=event.amount,
                currency=event.currency,
                method=decision_result.get("action_type", "unknown"),
                recovered_at=datetime.now(timezone.utc),
                verification_json=verification_result
            )
            db_session.add(outcome)

            event.status = RevenueEventStatus.COMPLETED
            event.completed_at = datetime.now(timezone.utc)

            await audit_event(
                db_session, event.id, "outcome",
                input_json={"verification": verification_result},
                output_json={"recovered_amount": str(event.amount)}
            )

        elif verification_result.get("pending", False):
            # Execution succeeded, but recovery is asynchronous. A later
            # payment/subscription webhook determines the final outcome.
            event.status = RevenueEventStatus.AWAITING_PAYMENT
            event.last_error = None

            logger.info(
                "Event %s awaiting payment for %s",
                event.id,
                external_ref_id,
            )

        else:
            event.status = RevenueEventStatus.FAILED
            event.last_error = "Verification failed"

        await db_session.commit()

    except Exception as e:
        await db_session.rollback()
        logger.exception("Error processing event %s: %s", event_id, e)  # use the event_id param, not event.id
        raise
