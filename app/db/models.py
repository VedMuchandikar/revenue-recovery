"""SQLAlchemy database models for Revenue Recovery system."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


# =============================================================================
# ENUMS
# =============================================================================


class RevenueEventType(str, Enum):
    """Type of revenue leak event."""

    PAYMENT_FAILED = "payment_failed"
    SUBSCRIPTION_FAILED = "subscription_failed"
    CHECKOUT_ABANDONED = "checkout_abandoned"
    RECEIVABLE_OVERDUE = "receivable_overdue"


class RevenueEventStatus(str, Enum):
    """Status of the revenue event in the processing lifecycle."""

    PENDING = "pending"
    PROCESSING = "processing"
    AWAITING_PAYMENT = "awaiting_payment"
    RETRYABLE = "retryable"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ESCALATED = "escalated"
    FAILED = "failed"


class DiagnosisSource(str, Enum):
    """Source of the diagnosis."""

    RULE = "rule"
    LLM = "llm"


class RootCause(str, Enum):
    """Allowed root cause values for diagnosis."""

    CARD_DECLINED = "card_declined"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    AUTHENTICATION_FAILURE = "authentication_failure"
    GATEWAY_TIMEOUT = "gateway_timeout"
    NETWORK_ERROR = "network_error"
    PROVIDER_ERROR = "provider_error"
    SUBSCRIPTION_FAILURE = "subscription_failure"
    CHECKOUT_ABANDONMENT = "checkout_abandonment"
    RECEIVABLE_OVERDUE = "receivable_overdue"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    """Allowed recovery action types."""

    PAYMENT_LINK = "payment_link"
    MANDATE_RETRY = "mandate_retry"
    NOTIFY_SMS = "notify_sms"
    NOTIFY_EMAIL = "notify_email"
    INVOICE_REMINDER = "invoice_reminder"
    PROMISE_TO_PAY = "promise_to_pay"


class NotificationChannel(str, Enum):
    """Channel for notification delivery."""

    SMS = "sms"
    EMAIL = "email"
    RAZORPAY = "razorpay"
    SYSTEM = "system"


class GuardrailResult(str, Enum):
    """Result of guardrail check."""

    ALLOW = "allow"
    BLOCK = "block"
    ESCALATE = "escalate"


class ActionResultStatus(str, Enum):
    """Status of action execution."""

    SUCCESS = "success"
    FAILED = "failed"
    PENDING = "pending"


class AuditStage(str, Enum):
    """Stage in the event processing pipeline."""

    DETECT = "detect"
    DIAGNOSE = "diagnose"
    DECIDE = "decide"
    GUARDRAIL = "guardrail"
    EXECUTE = "execute"
    VERIFY = "verify"
    OUTCOME = "outcome"
    ERROR = "error"


# =============================================================================
# REVENUE EVENT (Aggregate Root)
# =============================================================================


class RevenueEvent(Base):
    """Revenue event representing a detected revenue leak.

    This is the aggregate root and acts as the durable queue.
    """

    __tablename__ = "revenue_events"

    # Primary key
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Event type and status
    type: Mapped[RevenueEventType] = mapped_column(SAEnum(RevenueEventType), nullable=False)
    status: Mapped[RevenueEventStatus] = mapped_column(
        SAEnum(RevenueEventStatus), nullable=False, default=RevenueEventStatus.PENDING
    )

    # Amount information
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Customer and reference IDs
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    razorpay_ref_id: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    # Reason information
    reason_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    reason_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    processing_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Retry and error tracking
    retry_count: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Flexible metadata
    metadata_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Audit timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # =============================================================================
    # RELATIONSHIPS
    # =============================================================================

    # 1:1 Diagnosis
    diagnosis: Mapped[Optional["Diagnosis"]] = relationship(
        "Diagnosis",
        back_populates="event",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 1:1 ProposedAction
    proposed_action: Mapped[Optional["ProposedAction"]] = relationship(
        "ProposedAction",
        back_populates="event",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 1:N GuardrailCheck
    guardrail_checks: Mapped[list["GuardrailCheck"]] = relationship(
        "GuardrailCheck",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="GuardrailCheck.created_at",
    )

    # 1:N ActionResult
    action_results: Mapped[list["ActionResult"]] = relationship(
        "ActionResult",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="ActionResult.created_at",
    )

    # 1:1 StoppingRuleState
    stopping_rule_state: Mapped[Optional["StoppingRuleState"]] = relationship(
        "StoppingRuleState",
        back_populates="event",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 1:1 Outcome
    outcome: Mapped[Optional["Outcome"]] = relationship(
        "Outcome",
        back_populates="event",
        uselist=False,
        cascade="all, delete-orphan",
    )

    # 1:N AuditLog
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="AuditLog.created_at",
    )

    # Indexes
    __table_args__ = (
        Index("ix_revenue_events_status_created_at", "status", "created_at"),
        Index("ix_revenue_events_status_processing_started_at", "status", "processing_started_at"),
        Index("ix_revenue_events_type_status", "type", "status"),
        Index("ix_revenue_events_customer_id", "customer_id"),
        Index("ix_revenue_events_razorpay_ref_id", "razorpay_ref_id"),
    )


# =============================================================================
# DIAGNOSIS
# =============================================================================


class Diagnosis(Base):
    """Diagnosis result for a revenue event."""

    __tablename__ = "diagnoses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Diagnosis details
    root_cause: Mapped[RootCause] = mapped_column(SAEnum(RootCause), nullable=False)
    source: Mapped[DiagnosisSource] = mapped_column(SAEnum(DiagnosisSource), nullable=False)

    # LLM-specific fields
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="diagnosis")


# =============================================================================
# PROPOSED ACTION
# =============================================================================


class ProposedAction(Base):
    """Proposed recovery action for a revenue event."""

    __tablename__ = "proposed_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Action details
    action_type: Mapped[RecoveryAction] = mapped_column(SAEnum(RecoveryAction), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(SAEnum(NotificationChannel), nullable=False)
    attempt_number: Mapped[int] = mapped_column(default=1)

    # Context
    context_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="proposed_action")


# =============================================================================
# GUARDRAIL CHECK
# =============================================================================


class GuardrailCheck(Base):
    """Guardrail check result before action execution."""

    __tablename__ = "guardrail_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False
    )

    # Check details
    rule_name: Mapped[str] = mapped_column(String(100), nullable=False)
    result: Mapped[GuardrailResult] = mapped_column(SAEnum(GuardrailResult), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="guardrail_checks")

    # Index
    __table_args__ = (Index("ix_guardrail_checks_event_id", "event_id"),)


# =============================================================================
# ACTION RESULT
# =============================================================================


class ActionResult(Base):
    """Result of executing a recovery action."""

    __tablename__ = "action_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False
    )

    # Action details
    action_type: Mapped[RecoveryAction] = mapped_column(SAEnum(RecoveryAction), nullable=False)
    channel: Mapped[NotificationChannel] = mapped_column(SAEnum(NotificationChannel), nullable=False)
    status: Mapped[ActionResultStatus] = mapped_column(SAEnum(ActionResultStatus), nullable=False)

    # Execution details
    external_ref_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    response_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="action_results")

    # Index
    __table_args__ = (Index("ix_action_results_event_id", "event_id"),)


# =============================================================================
# STOPPING RULE STATE
# =============================================================================


class StoppingRuleState(Base):
    """State tracking for stopping rules (cooldowns, max attempts, etc.)."""

    __tablename__ = "stopping_rule_states"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Rule tracking
    last_action_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Mandate-specific tracking
    mandate_retry_count: Mapped[int] = mapped_column(default=0)
    first_mandate_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Customer-specific state
    customer_opted_out: Mapped[bool] = mapped_column(default=False)
    customer_dnd_eligible: Mapped[bool] = mapped_column(default=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="stopping_rule_state")

    # Index
    __table_args__ = (Index("ix_stopping_rule_states_event_id", "event_id"),)


# =============================================================================
# OUTCOME
# =============================================================================


class Outcome(Base):
    """Verified recovery outcome - only created when revenue is actually recovered."""

    __tablename__ = "outcomes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    # Recovery details
    recovered_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    method: Mapped[RecoveryAction] = mapped_column(SAEnum(RecoveryAction), nullable=False)
    recovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Verification details
    verification_ref: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    verification_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="outcome")


# =============================================================================
# AUDIT LOG
# =============================================================================


class AuditLog(Base):
    """Append-only audit log for all event processing stages."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Foreign key to RevenueEvent
    event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("revenue_events.id", ondelete="CASCADE"), nullable=False
    )

    # Audit details
    stage: Mapped[AuditStage] = mapped_column(SAEnum(AuditStage), nullable=False)
    input_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)
    output_json: Mapped[Optional[str]] = mapped_column(JSON, nullable=True)

    # Additional metadata
    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    event: Mapped["RevenueEvent"] = relationship("RevenueEvent", back_populates="audit_logs")

    # Indexes
    __table_args__ = (
        Index("ix_audit_logs_event_id", "event_id"),
        Index("ix_audit_logs_stage", "stage"),
    )


# =============================================================================
# SYNTHETIC RECEIVABLE (for seed data)
# =============================================================================


class SyntheticReceivable(Base):
    """Synthetic B2B invoice for testing receivable overdue detection."""

    __tablename__ = "synthetic_receivables"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    # Invoice details
    invoice_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    customer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    # Dates
    issue_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # Indexes
    __table_args__ = (
        Index("ix_synthetic_receivables_due_date", "due_date"),
        Index("ix_synthetic_receivables_status", "status"),
    )