#!/usr/bin/env python3
"""Integration tests for Revenue Recovery AI Agent."""

import pytest
import asyncio
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    RecoveryAction, NotificationChannel,
    Diagnosis, ProposedAction, GuardrailCheck,
    ActionResult, Outcome, AuditLog, DiagnosisSource,
    GuardrailResult
)
from app.engine.orchestrator import process_event
from app.engine.diagnose import diagnose_event
from app.engine.decide import decide_action
from app.engine.guardrails import check_guardrails
from app.engine.execute import execute_action, verify_action
from sqlalchemy import delete, select


@pytest.fixture(scope="function", autouse=True)
async def setup_clean_db():
    """Initialize database and clean before each test."""
    await init_db()
    # Clean all tables
    async with async_session_factory() as session:
        for model in [Diagnosis, ProposedAction, GuardrailCheck, ActionResult, Outcome, AuditLog, RevenueEvent]:
            await session.execute(delete(model))
        await session.commit()
    yield
    # Cleanup after
    async with async_session_factory() as session:
        for model in [Diagnosis, ProposedAction, GuardrailCheck, ActionResult, Outcome, AuditLog, RevenueEvent]:
            await session.execute(delete(model))
        await session.commit()


async def create_test_event(
    session,
    event_id: str = "test-1",
    reason_code: str = "card_declined",
    amount: float = 5000
):
    """Create a test revenue event."""
    event = RevenueEvent(
        id=event_id,
        type=RevenueEventType.PAYMENT_FAILED,
        status=RevenueEventStatus.PENDING,
        amount=amount,
        currency="INR",
        customer_id="test@example.com",
        razorpay_ref_id=f"pay_{event_id}",
        provider_event_id=f"prov_{event_id}",
        reason_code=reason_code,
        retry_count=0,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


class TestDiagnose:
    """Test the diagnosis engine."""

    async def test_diagnose_card_declined(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "diag-1", "card_declined")
            result = await diagnose_event(event, session)

            assert result.root_cause.value == "card_declined"
            assert result.source.value == "rule"  # DiagnosisSource.RULE.value
            assert result.confidence >= 0.9

    async def test_diagnose_insufficient_funds(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "diag-2", "insufficient_funds")
            result = await diagnose_event(event, session)

            assert result.root_cause.value == "insufficient_funds"
            assert result.source.value == "rule"

    async def test_diagnose_mandate_failed(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "diag-3", "mandate_failed")
            result = await diagnose_event(event, session)

            assert result.root_cause.value == "subscription_failure"
            assert result.source.value == "rule"


class TestDecide:
    """Test the decision engine."""

    async def test_decide_payment_link_sms(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "decide-1", "card_declined")
            diagnosis = await diagnose_event(event, session)
            result = await decide_action(event, diagnosis, session)

            assert result.action_type == RecoveryAction.PAYMENT_LINK.value
            assert result.channel == NotificationChannel.SMS.value

    async def test_decide_payment_link_email(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "decide-2", "insufficient_funds")
            diagnosis = await diagnose_event(event, session)
            result = await decide_action(event, diagnosis, session)

            assert result.action_type == RecoveryAction.PAYMENT_LINK.value
            assert result.channel == NotificationChannel.EMAIL.value

    async def test_decide_mandate_retry(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "decide-3", "mandate_failed", amount=10000)
            diagnosis = await diagnose_event(event, session)
            result = await decide_action(event, diagnosis, session)

            assert result.action_type == RecoveryAction.MANDATE_RETRY.value


class TestGuardrails:
    """Test guardrails."""

    async def test_guardrails_pass_normal_amount(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "guard-1", "card_declined", amount=1000)
            diagnosis = await diagnose_event(event, session)
            proposed_action = ProposedAction(
                event_id=event.id,
                action_type=RecoveryAction.PAYMENT_LINK,  # enum, not .value
                channel=NotificationChannel.SMS,          # enum, not .value
                attempt_number=1
            )
            guardrail_check, guardrail_result = await check_guardrails(event, proposed_action, session)

            passed = (guardrail_result == GuardrailResult.ALLOW)
            failures = [] if passed else [guardrail_check.rule_name]

            assert passed is True
            assert len(failures) == 0

    async def test_guardrails_fail_amount_cap(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "guard-2", "card_declined", amount=100000)
            diagnosis = await diagnose_event(event, session)
            proposed_action = ProposedAction(
                event_id=event.id,
                action_type=RecoveryAction.PAYMENT_LINK,  # enum, not .value
                channel=NotificationChannel.SMS,          # enum, not .value
                attempt_number=1
            )
            guardrail_check, guardrail_result = await check_guardrails(event, proposed_action, session)

            passed = (guardrail_result == GuardrailResult.ALLOW)
            failures = [] if passed else [guardrail_check.rule_name]

            assert passed is False
            assert any("amount_cap" in f for f in failures)


class TestExecute:
    """Test action execution."""

    async def test_execute_payment_link_stub(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "exec-1", "card_declined")
            diagnosis = await diagnose_event(event, session)
            decision = await decide_action(event, diagnosis, session)
            # Convert ProposedAction to dict for execute_action
            decision_dict = {
                "action_type": decision.action_type,
                "channel": decision.channel
            }
            result = await execute_action(event, decision_dict, session)

            assert result["success"] is True
            assert result["action_type"] == RecoveryAction.PAYMENT_LINK.value
            assert result["channel"] == NotificationChannel.SMS.value


class TestVerify:
    """Test verification."""

    async def test_verify_payment_link_stub(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "verify-1", "card_declined")
            diagnosis = await diagnose_event(event, session)
            decision = await decide_action(event, diagnosis, session)
            decision_dict = {
                "action_type": decision.action_type,
                "channel": decision.channel
            }
            execution_result = await execute_action(event, decision_dict, session)
            result = await verify_action(event, execution_result)

            assert result["verified"] is True
            assert result["verification_method"] == "stub_verification"


class TestOrchestrator:
    """Test the full orchestrator pipeline."""

    async def test_process_event_card_declined(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "orch-1", "card_declined", 5000)
            await process_event(event.id, session)
            # Processed successfully if no exception thrown

            # Verify event status updated
            await session.refresh(event)
            assert event.status == RevenueEventStatus.COMPLETED

            # Verify an outcome was created
            result = await session.execute(
                select(Outcome).where(Outcome.event_id == event.id)
            )
            outcome = result.scalar_one_or_none()
            assert outcome is not None
            assert outcome.recovered_amount == event.amount

    async def test_process_event_insufficient_funds(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "orch-2", "insufficient_funds", 3000)
            await process_event(event.id, session)
            # Processed successfully if no exception thrown

            # Verify event status updated
            await session.refresh(event)
            assert event.status == RevenueEventStatus.COMPLETED

    async def test_process_event_mandate_failed(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "orch-3", "mandate_failed", 10000)
            await process_event(event.id, session)
            # Processed successfully if no exception thrown

            # Verify event status updated
            await session.refresh(event)
            assert event.status == RevenueEventStatus.COMPLETED

    async def test_process_event_gateway_timeout(self):
        async with async_session_factory() as session:
            event = await create_test_event(session, "orch-4", "gateway_timeout", 1500)
            await process_event(event.id, session)
            # Processed successfully if no exception thrown

            # Verify event status updated
            await session.refresh(event)
            assert event.status == RevenueEventStatus.COMPLETED


class TestFullPipeline:
    """Test the full pipeline with multiple events."""

    async def test_multiple_events(self):
        """Process multiple events in sequence."""
        test_cases = [
            ("card_declined", 5000),
            ("insufficient_funds", 3000),
            ("mandate_failed", 10000),
            ("gateway_timeout", 1500),
        ]

        async with async_session_factory() as session:
            for i, (reason, amount) in enumerate(test_cases):
                event_id = f"multi-{i+1}"
                event = await create_test_event(session, event_id, reason, amount)
                await process_event(event.id, session)
                # Processed successfully if no exception thrown

            # Verify all events completed
            result = await session.execute(
                select(RevenueEvent).where(RevenueEvent.id.like("multi-%"))
            )
            events = result.scalars().all()
            assert len(events) == 4
            assert all(e.status == RevenueEventStatus.COMPLETED for e in events)

            # Verify audit trail exists for all
            result = await session.execute(
                select(AuditLog).where(AuditLog.event_id.like("multi-%"))
            )
            audits = result.scalars().all()
            # 7 stages per event * 4 events = 28 audit logs
            assert len(audits) >= 28


if __name__ == "__main__":
    pytest.main([__file__, "-v"])