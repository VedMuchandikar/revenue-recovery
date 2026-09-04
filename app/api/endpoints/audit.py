"""Audit log API endpoints."""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import AuditLog, AuditStage

router = APIRouter()


@router.get("/")
async def get_audit_logs(
    db_session: AsyncSession = Depends(get_db),
    event_id: Optional[str] = Query(None, description="Filter by event ID"),
    stage: Optional[str] = Query(None, description="Filter by pipeline stage"),
    limit: int = Query(100, ge=1, le=1000, description="Number of logs to return"),
    offset: int = Query(0, ge=0, description="Number of logs to skip")
):
    """
    Get audit logs with optional filtering.
    """
    query = select(AuditLog)

    # Apply filters
    if event_id:
        query = query.where(AuditLog.event_id == event_id)
    if stage:
        query = query.where(AuditLog.stage == stage)

    # Apply pagination and ordering
    query = query.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit)

    result = await db_session.execute(query)
    logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "event_id": log.event_id,
            "stage": log.stage.value if hasattr(log.stage, 'value') else str(log.stage),
            "input_json": log.input_json,
            "output_json": log.output_json,
            "source": log.source,
            "created_at": log.created_at.isoformat()
        }
        for log in logs
    ]


@router.get("/stats")
async def get_audit_stats(
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get audit log statistics by pipeline stage.
    """
    # Count by stage
    stage_query = select(
        AuditLog.stage,
        func.count(AuditLog.id).label('count')
    ).group_by(AuditLog.stage)
    stage_result = await db_session.execute(stage_query)
    by_stage = {
        (row.stage.value if hasattr(row.stage, 'value') else str(row.stage)): row.count
        for row in stage_result
    }

    # Total logs
    total_query = select(func.count(AuditLog.id))
    total_result = await db_session.execute(total_query)
    total = total_result.scalar() or 0

    return {
        "total_logs": total,
        "by_stage": by_stage
    }


@router.get("/{event_id}/trail")
async def get_event_audit_trail(
    event_id: str,
    db_session: AsyncSession = Depends(get_db)
):
    """
    Get the complete audit trail for a specific event.

    Returns all audit logs for the event, ordered chronologically.
    """
    query = select(AuditLog).where(
        AuditLog.event_id == event_id
    ).order_by(AuditLog.created_at)

    result = await db_session.execute(query)
    logs = result.scalars().all()

    return {
        "event_id": event_id,
        "trail": [
            {
                "id": log.id,
                "stage": log.stage.value if hasattr(log.stage, 'value') else str(log.stage),
                "input_json": log.input_json,
                "output_json": log.output_json,
                "source": log.source,
                "created_at": log.created_at.isoformat()
            }
            for log in logs
        ]
    }