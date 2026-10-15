"""Stripe webhook handler."""


def handle_payment_succeeded(event: dict) -> str:
    return f"marked invoice {event['invoice_id']} as paid"
