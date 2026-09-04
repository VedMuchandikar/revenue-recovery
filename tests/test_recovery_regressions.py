"""Regression coverage for asynchronous Razorpay recovery flows."""

from types import SimpleNamespace

import pytest

from app.db.models import RecoveryAction
from app.engine import execute
from app.engine.strategy import rank_candidates
from app.execution.razorpay_client import RazorpayClient, RazorpayResponse


def test_fetch_mandate_uses_subscription_resource():
    """The Razorpay SDK exposes ``subscription``, not ``mandates``."""
    subscription = SimpleNamespace(
        fetch=lambda subscription_id: {
            "id": subscription_id,
            "status": "pending",
            "charge_at": 123,
        }
    )
    client = RazorpayClient.__new__(RazorpayClient)
    client.client = SimpleNamespace(subscription=subscription)

    response = client.fetch_mandate("sub_123")

    assert response.success is True
    assert response.data == {
        "subscription_id": "sub_123",
        "status": "pending",
        "charge_at": 123,
        "current_start": None,
        "current_end": None,
    }


@pytest.mark.asyncio
async def test_pending_payment_link_is_not_a_failed_recovery(monkeypatch):
    event = SimpleNamespace(id="event-1", amount=100, currency="INR")
    response = RazorpayResponse(
        success=True,
        data={"status": "created"},
    )
    monkeypatch.setattr(execute.razorpay_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        execute.razorpay_client,
        "fetch_payment_link",
        lambda _: response,
    )

    verification = await execute.verify_action(
        event,
        {
            "success": True,
            "action_type": RecoveryAction.PAYMENT_LINK.value,
            "external_ref_id": "plink_123",
        },
    )

    assert verification["verified"] is False
    assert verification["pending"] is True
    assert verification["verification_method"] == "payment_link_pending"


@pytest.mark.asyncio
async def test_mandate_recovery_requires_real_subscription_id(monkeypatch):
    event = SimpleNamespace(id="event-1", razorpay_ref_id="pay_123")
    monkeypatch.setattr(execute.razorpay_client, "is_configured", lambda: True)

    result = await execute._execute_mandate_retry(
        event,
        {"channel": "email"},
        db_session=None,
    )

    assert result["success"] is False
    assert "subscription ID" in result["error"]


@pytest.mark.asyncio
async def test_strategy_ranks_verified_outcomes_above_unsuccessful_actions():
    successful_action = SimpleNamespace(
        action_type=RecoveryAction.PAYMENT_LINK,
        channel=SimpleNamespace(value="email"),
    )
    unsuccessful_action = SimpleNamespace(
        action_type=RecoveryAction.PAYMENT_LINK,
        channel=SimpleNamespace(value="sms"),
    )

    class Result:
        def all(self):
            return [
                (successful_action, object()),
                (successful_action, object()),
                (unsuccessful_action, None),
                (unsuccessful_action, None),
            ]

    class Session:
        async def execute(self, _):
            return Result()

    ranked = await rank_candidates(
        Session(),
        [
            (RecoveryAction.PAYMENT_LINK, execute.NotificationChannel.SMS),
            (RecoveryAction.PAYMENT_LINK, execute.NotificationChannel.EMAIL),
        ],
        amount=1000,
        root_cause="card_declined",
    )

    assert ranked[0]["channel"].value == "email"
    assert ranked[0]["expected_recovery_score"] == 0.75
