#!/usr/bin/env python3
"""Test script to verify Phase 2 orchestrator implementation."""

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.db.database import async_session_factory, init_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, ProposedAction, GuardrailCheck, ActionResult, Outcome, AuditLog
)
from app.engine.orchestrator import process_event


async def test_orchestrator():
    """Test the orchestrator with stubbed business logic."""
    print("🧪 Testing Phase 2: Orchestrator with stubbed logic")

    # Initialize database
    await init_db()
    print("✅ Database initialized")

    async with async_session_factory() as session:
        # Clear any existing test data
        from sqlalchemy import delete
        await session.execute(delete(RevenueEvent))
        await session.commit()

        # Create a test event
        test_event = RevenueEvent(
            id=str(uuid.uuid4()),
            type=RevenueEventType.PAYMENT_FAILED,
            status=RevenueEventStatus.PENDING,
            amount=Decimal('5000.00'),
            currency='INR',
            customer_id='cust_001',
            razorpay_ref_id='ref_001',
            provider_event_id=f'evt_{uuid.uuid4().hex[:8]}',
            detected_at=datetime.now(timezone.utc)
        )
        session.add(test_event)
        await session.commit()
        await session.refresh(test_event)

        print(f"✅ Created test event: {test_event.id}")
        print(f"   Initial status: {test_event.status.value}")

        # Process the event through the orchestrator
        print("\n🔄 Processing event through orchestrator...")
        await process_event(test_event.id, session)

        # Refresh to get updated status
        await session.refresh(test_event)
        print(f"✅ Event processed. Final status: {test_event.status.value}")

        # Check if we have an outcome (depends on stub verification)
        from sqlalchemy import select

        # Check for diagnosis
        diag_query = select(Diagnosis).where(Diagnosis.event_id == test_event.id)
        diag_result = await session.execute(diag_query)
        diagnosis = diag_result.scalar_one_or_none()
        if diagnosis:
            print(f"� Diagnosis created: {diagnosis.root_cause.value} ({diagnosis.source.value})")
        else:
            print("❌ No diagnosis found")

        # Check for proposed action
        action_query = select(ProposedAction).where(ProposedAction.event_id == test_event.id)
        action_result = await session.execute(action_query)
        proposed_action = action_result.scalar_one_or_none()
        if proposed_action:
            print(f"� Proposed action: {proposed_action.action_type.value} via {proposed_action.channel.value}")
        else:
            print("❌ No proposed action found")

        # Check for guardrail checks
        guardrail_query = select(GuardrailCheck).where(GuardrailCheck.event_id == test_event.id)
        guardrail_result = await session.execute(guardrail_query)
        guardrails = guardrail_result.scalars().all()
        if guardrails:
            print(f"� Guardrail checks: {len(guardrails)} created")
            for gr in guardrails:
                print(f"   - {gr.rule_name}: {gr.result.value}")
        else:
            print("❌ No guardrail checks found")

        # Check for action results
        action_result_query = select(ActionResult).where(ActionResult.event_id == test_event.id)
        action_result_result = await session.execute(action_result_query)
        action_results = action_result_result.scalars().all()
        if action_results:
            print(f"� Action results: {len(action_results)} created")
            for ar in action_results:
                print(f"   - {ar.action_type.value} via {ar.channel.value}: {ar.status.value}")
        else:
            print("❌ No action results found")

        # Check for outcome
        outcome_query = select(Outcome).where(Outcome.event_id == test_event.id)
        outcome_result = await session.execute(outcome_query)
        outcome = outcome_result.scalar_one_or_none()
        if outcome:
            print(f"� Outcome created: {outcome.recovered_amount} {outcome.currency} recovered via {outcome.method.value}")
        else:
            print("ℹ️  No outcome created (verification may have failed in stub)")

        # Check for audit logs
        audit_query = select(AuditLog).where(AuditLog.event_id == test_event.id)
        audit_result = await session.execute(audit_query)
        audit_logs = audit_result.scalars().all()
        if audit_logs:
            print(f"� Audit logs: {len(audit_logs)} created")
            stages = [log.stage.value for log in audit_logs]
            print(f"   Stages: {', '.join(stages)}")
        else:
            print("❌ No audit logs found")

    print("\n🎉 Phase 2 orchestrator tests completed!")
    return True


if __name__ == "__main__":
    asyncio.run(test_orchestrator())