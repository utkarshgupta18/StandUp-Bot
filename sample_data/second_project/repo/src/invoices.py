"""Invoice PDF generation."""


def render_invoice(order: dict) -> bytes:
    lines = [f"Invoice for order {order['id']}", f"Total: {order['total']}"]
    return "\n".join(lines).encode()
