def charge_customer(gateway, customer_id: str, amount_cents: int) -> bool:
    try:
        gateway.charge(customer_id, amount_cents)
    except Exception:
        pass
    mark_invoice_paid(customer_id)
    return True
