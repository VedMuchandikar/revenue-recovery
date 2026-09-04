#!/usr/bin/env python3
"""Debug webhook payload structure."""

import json

# Test payload from our test
payment_payload = {
    "event": "payment.failed",
    "payload": {
        "payment": {
            "entity": {
                "id": "pay_test_123",
                "amount": 25000,  # 250.00 INR in paise
                "currency": "INR",
                "email": "test@example.com",
                "contact": "+919999999999",
                "error_code": "bad_request_us",
                "error_reason": "The amount cannot be less than 1 INR"
            }
        }
    }
}

print("Full payload:")
print(json.dumps(payment_payload, indent=2))

print("\nAccessing payment entity:")
payment_entity = payment_payload.get("payment", {}).get("entity", {})
print(f"payment_entity: {payment_entity}")

print(f"\npayment_id: {payment_entity.get('id')}")
print(f"amount: {payment_entity.get('amount')}")
print(f"currency: {payment_entity.get('currency')}")
print(f"email: {payment_entity.get('email')}")
print(f"contact: {payment_entity.get('contact')}")