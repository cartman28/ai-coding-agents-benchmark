"""Deterministic checkout engine calculations."""


def checkout(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0) -> dict:
    """Calculate checkout totals, discounts, taxes, and line items."""
    if isinstance(items, bool) or not isinstance(items, list):
        raise ValueError("items must be a list")
    if not items:
        raise ValueError("items must not be empty")

    seen_skus = set()
    validated_items = []

    for item in items:
        if isinstance(item, bool) or not isinstance(item, dict):
            raise ValueError("each item must be a dictionary")

        if not all(k in item for k in ("sku", "unit_price_cents", "quantity")):
            raise ValueError("item missing required keys: sku, unit_price_cents, quantity")

        sku = item["sku"]
        if isinstance(sku, bool) or not isinstance(sku, str) or not sku:
            raise ValueError("sku must be a non-empty string")

        if sku in seen_skus:
            raise ValueError(f"duplicate sku: {sku}")
        seen_skus.add(sku)

        unit_price_cents = item["unit_price_cents"]
        if isinstance(unit_price_cents, bool) or not isinstance(unit_price_cents, int) or unit_price_cents < 0:
            raise ValueError("unit_price_cents must be a non-negative integer")

        quantity = item["quantity"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        validated_items.append({
            "sku": sku,
            "unit_price_cents": unit_price_cents,
            "quantity": quantity
        })

    if coupon is not None:
        if isinstance(coupon, bool) or not isinstance(coupon, dict):
            raise ValueError("coupon must be a dictionary or None")
        if not all(k in coupon for k in ("type", "value")):
            raise ValueError("coupon missing required keys: type, value")

        c_type = coupon["type"]
        if c_type not in ("percent", "fixed"):
            raise ValueError("coupon type must be 'percent' or 'fixed'")

        c_val = coupon["value"]
        if isinstance(c_val, bool) or not isinstance(c_val, int):
            raise ValueError("coupon value must be an integer")

        if c_type == "percent":
            if not (0 <= c_val <= 100):
                raise ValueError("percent coupon value must be between 0 and 100")
        else:
            if c_val < 0:
                raise ValueError("fixed coupon value must be non-negative")

    if isinstance(tax_rate_bps, bool) or not isinstance(tax_rate_bps, int) or not (0 <= tax_rate_bps <= 10000):
        raise ValueError("tax_rate_bps must be an integer between 0 and 10000")

    if isinstance(shipping_cents, bool) or not isinstance(shipping_cents, int) or shipping_cents < 0:
        raise ValueError("shipping_cents must be a non-negative integer")

    subtotal_cents = 0
    line_subtotals = []
    for item in validated_items:
        s = item["unit_price_cents"] * item["quantity"]
        line_subtotals.append(s)
        subtotal_cents += s

    total_discount = 0
    if coupon is not None:
        c_type = coupon["type"]
        c_val = coupon["value"]
        if c_type == "percent":
            total_discount = (subtotal_cents * c_val) // 100
        else:
            total_discount = min(c_val, subtotal_cents)

    line_discounts = [0] * len(validated_items)
    if subtotal_cents > 0 and total_discount > 0:
        calc_list = []
        for i, (item, sub) in enumerate(zip(validated_items, line_subtotals)):
            prod = total_discount * sub
            base = prod // subtotal_cents
            rem = prod % subtotal_cents
            calc_list.append((i, base, rem, item["sku"]))

        sum_base = sum(b for _, b, _, _ in calc_list)
        remainder_to_distribute = total_discount - sum_base

        calc_list.sort(key=lambda x: (-x[2], x[3]))

        extra_assigned = set()
        for idx in range(remainder_to_distribute):
            i, _, _, _ = calc_list[idx]
            extra_assigned.add(i)

        for i, base, _, _ in calc_list:
            line_discounts[i] = base + (1 if i in extra_assigned else 0)

    lines_result = []
    total_tax_cents = 0
    total_line_discount_cents = 0

    for i, (item, sub) in enumerate(zip(validated_items, line_subtotals)):
        disc = line_discounts[i]
        total_line_discount_cents += disc
        net = sub - disc
        tax = (net * tax_rate_bps) // 10000
        total_tax_cents += tax

        lines_result.append({
            "sku": item["sku"],
            "subtotal_cents": sub,
            "discount_cents": disc,
            "net_cents": net,
            "tax_cents": tax
        })

    total_cents = subtotal_cents - total_line_discount_cents + total_tax_cents + shipping_cents

    return {
        "lines": lines_result,
        "subtotal_cents": subtotal_cents,
        "discount_cents": total_line_discount_cents,
        "tax_cents": total_tax_cents,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents
    }
