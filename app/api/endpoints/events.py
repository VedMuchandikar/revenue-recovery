"""Events API endpoints."""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import (
    RevenueEvent, RevenueEventStatus, RevenueEventType,
    Diagnosis, ProposedAction, ActionResult, Outcome
)

router = APIRouter()


@router.get("/")
async def get_events(
    db_session: AsyncSession = Depends(get_db),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    event_status: Optional[str] = Query(None, description="Filter by status"),
    customer_id: Optional[str] = Query(None, description="Filter by customer ID"),
    limit: int = Query(100, ge=1, le=1000, description="Number of events to return"),
    offset: int = Query(0, ge=0, description="Number of events to skip")
):
    """
    Get revenue events with optional filtering.

    Returns a list of revenue events with basic information.
    """
    query = select(RevenueEvent)

    # Apply filters
    if event_type:
        try:
            query = query.where(RevenueEvent.type == RevenueEventType(event_type))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid event type: {event_type}"
            )
    if event_status:
        try:
            query = query.where(RevenueEvent.status == RevenueEventStatus(event_status))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {event_status}"
            )
    if customer_id:
        query = query.where(RevenueEvent.customer_id == customer_id)

    # Apply pagination and ordering
    query = query.order_by(desc(RevenueEvent.detected_at)).offset(offset).limit(limit)

    result = await db_session.execute(query)
    events = result.scalars().all()

    return [
        {
            "id": event.id,
            "type": event.type.value,
            "status": event.status.value,
            "amount": str(event.amount),
            "currency": event.currency,
            "customer_id": event.customer_id,
            "razorpay_ref_id": event.razorpay_ref_id,
            "provider_event_id": event.provider_event_id,
            "detected_at": event.detected_at.isoformat(),
            "processing_started_at": event.processing_started_at.isoformat() if event.processing_started_at else None,
            "completed_at": event.completed_at.isoformat() if event.completed_at else None,
            "reason_code": event.reason_code,
            "retry_count": event.retry_count
        }
        for event in events
    ]


@router.get("/stats/summary")
async def get_events_summary(
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get summary statistics for revenue events.
    """
    # Total events by status
    status_query = select(
        RevenueEvent.status,
        func.count(RevenueEvent.id).label('count')
    ).group_by(RevenueEvent.status)
    status_result = await db_session.execute(status_query)
    status_counts = {row.status.value: row.count for row in status_result}

    # Total events by type
    type_query = select(
        RevenueEvent.type,
        func.count(RevenueEvent.id).label('count')
    ).group_by(RevenueEvent.type)
    type_result = await db_session.execute(type_query)
    type_counts = {row.type.value: row.count for row in type_result}

    # Total amount by status
    amount_query = select(
        RevenueEvent.status,
        func.sum(RevenueEvent.amount).label('total_amount')
    ).group_by(RevenueEvent.status)
    amount_result = await db_session.execute(amount_query)
    amount_by_status = {row.status.value: str(row.total_amount) for row in amount_result}

    return {
        "total_events": sum(status_counts.values()),
        "by_status": status_counts,
        "by_type": type_counts,
        "total_amount_by_status": amount_by_status
    }


@router.get("/{event_id}")
async def get_event(
    event_id: str,
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get a specific revenue event by ID with detailed information.
    """
    query = select(RevenueEvent).where(RevenueEvent.id == event_id)
    result = await db_session.execute(query)
    event = result.scalar_one_or_none()

    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found"
        )

    # Get related data
    diagnosis_query = select(Diagnosis).where(Diagnosis.event_id == event_id)
    diagnosis_result = await db_session.execute(diagnosis_query)
    diagnosis = diagnosis_result.scalar_one_or_none()

    action_query = select(ProposedAction).where(ProposedAction.event_id == event_id)
    action_result = await db_session.execute(action_query)
    proposed_action = action_result.scalar_one_or_none()

    action_results_query = select(ActionResult).where(ActionResult.event_id == event_id).order_by(ActionResult.created_at)
    action_results_result = await db_session.execute(action_results_query)
    action_results = action_results_result.scalars().all()

    outcome_query = select(Outcome).where(Outcome.event_id == event_id)
    outcome_result = await db_session.execute(outcome_query)
    outcome = outcome_result.scalar_one_or_none()

    return {
        "id": event.id,
        "type": event.type.value,
        "status": event.status.value,
        "amount": str(event.amount),
        "currency": event.currency,
        "customer_id": event.customer_id,
        "razorpay_ref_id": event.razorpay_ref_id,
        "provider_event_id": event.provider_event_id,
        "detected_at": event.detected_at.isoformat(),
        "processing_started_at": event.processing_started_at.isoformat() if event.processing_started_at else None,
        "completed_at": event.completed_at.isoformat() if event.completed_at else None,
        "reason_code": event.reason_code,
        "retry_count": event.retry_count,
        "metadata_json": event.metadata_json,
        "diagnosis": {
            "root_cause": diagnosis.root_cause.value,
            "source": diagnosis.source.value,
            "confidence": diagnosis.confidence,
            "rationale": diagnosis.rationale,
            "model_name": diagnosis.model_name
        } if diagnosis else None,
        "proposed_action": {
            "action_type": proposed_action.action_type.value,
            "channel": proposed_action.channel.value,
            "attempt_number": proposed_action.attempt_number,
            "context_json": proposed_action.context_json
        } if proposed_action else None,
        "action_results": [
            {
                "action_type": ar.action_type.value,
                "channel": ar.channel.value,
                "status": ar.status.value,
                "external_ref_id": ar.external_ref_id,
                "error_message": ar.error_message,
                "created_at": ar.created_at.isoformat()
            }
            for ar in action_results
        ],
        "outcome": {
            "recovered_amount": str(outcome.recovered_amount),
            "recovered_at": outcome.recovered_at.isoformat(),
            "method": outcome.method.value,
            "verification_ref": outcome.verification_ref
        } if outcome else None
    }