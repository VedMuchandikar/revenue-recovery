"""Audit logging functions for the engine."""

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog, AuditStage
from app.db import database


async def audit_event(
    db_session: AsyncSession,
    event_id: str,
    stage: str,
    input_json: Optional[dict[str, Any]] = None,
    output_json: Optional[dict[str, Any]] = None,
    source: Optional[str] = None
) -> AuditLog:
    """
    Create an audit log entry for an event processing stage.

    Args:
        db_session: Database session
        event_id: ID of the revenue event
        stage: Processing stage (from AuditStage enum)
        input_json: Input data for the stage
        output_json: Output data from the stage
        source: Source of the processing (e.g., "rule", "llm")

    Returns:
        Created AuditLog entry
    """
    audit_log = AuditLog(
        event_id=event_id,
        stage=stage,
        input_json=input_json,
        output_json=output_json,
        source=source
    )
    db_session.add(audit_log)
    await db_session.flush()  # Get the ID without committing
    await db_session.refresh(audit_log)
    return audit_log