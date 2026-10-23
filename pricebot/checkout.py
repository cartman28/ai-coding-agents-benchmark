"""Deterministic checkout engine."""


def checkout(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0) -> dict:
    """Calculate checkout totals, discounts, taxes, and line items."""
    if isinstance(items, bool) or not isinstance(items, list):
        raise ValueError("items must be a list")
    if not items:
        raise ValueError("items list cannot be empty")

    seen_skus = set()
    validated_items = []

    for item in items:
        if isinstance(item, bool) or not isinstance(item, dict):
            raise ValueError("item must be a dictionary")

        if not all(k in item for k in ("sku", "unit_price_cents", "quantity")):
            raise ValueError("item missing required keys: sku, unit_price_cents, quantity")

        sku = item["sku"]
        if isinstance(sku, bool) or not isinstance(sku, str) or not sku:
            raise ValueError("sku must be a nonempty string")

        if sku in seen_skus:
            raise ValueError(f"duplicate sku found: {sku}")
        seen_skus.add(sku)

        unit_price = item["unit_price_cents"]
        if isinstance(unit_price, bool) or not isinstance(unit_price, int) or unit_price < 0:
            raise ValueError("unit_price_cents must be a nonnegative integer")

        qty = item["quantity"]
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise ValueError("quantity must be a positive integer")

        validated_items.append({
            "sku": sku,
            "unit_price_cents": unit_price,
            "quantity": qty,
        })

    if coupon is not None:
        if isinstance(coupon, bool) or not isinstance(coupon, dict):
            raise ValueError("coupon must be a dictionary or None")
        if not all(k in coupon for k in ("type", "value")):
            raise ValueError("coupon missing required keys: type, value")

        c_type = coupon["type"]
        if isinstance(c_type, bool) or not isinstance(c_type, str) or c_type not in ("percent", "fixed"):
            raise ValueError("coupon type must be 'percent' or 'fixed'")

        c_val = coupon["value"]
        if isinstance(c_val, bool) or not isinstance(c_val, int):
            raise ValueError("coupon value must be an integer")

        if c_type == "percent":
            if not 0 <= c_val <= 100:
                raise ValueError("percent coupon value must be between 0 and 100")
        else:
            if c_val < 0:
                raise ValueError("fixed coupon value must be nonnegative")

    if isinstance(tax_rate_bps, bool) or not isinstance(tax_rate_bps, int) or not 0 <= tax_rate_bps <= 10000:
        raise ValueError("tax_rate_bps must be an integer between 0 and 10000")

    if isinstance(shipping_cents, bool) or not isinstance(shipping_cents, int) or shipping_cents < 0:
        raise ValueError("shipping_cents must be a nonnegative integer")

    subtotal_cents = sum(item["unit_price_cents"] * item["quantity"] for item in validated_items)

    total_discount = 0
    if coupon is not None and subtotal_cents > 0:
        c_type = coupon["type"]
        c_val = coupon["value"]
        if c_type == "percent":
            total_discount = (subtotal_cents * c_val) // 100
        else:
            total_discount = min(c_val, subtotal_cents)

    line_data = []
    for idx, item in enumerate(validated_items):
        line_subtotal = item["unit_price_cents"] * item["quantity"]
        line_data.append({
            "idx": idx,
            "sku": item["sku"],
            "subtotal_cents": line_subtotal,
        })

    if subtotal_cents == 0 or total_discount == 0:
        for ld in line_data:
            ld["discount_cents"] = 0
            ld["remainder"] = 0
    else:
        base_total_allocated = 0
        for ld in line_data:
            product = total_discount * ld["subtotal_cents"]
            base_disc = product // subtotal_cents
            remainder = product % subtotal_cents
            ld["discount_cents"] = base_disc
            ld["remainder"] = remainder
            base_total_allocated += base_disc

        missing = total_discount - base_total_allocated
        if missing > 0:
            sorted_lines = sorted(line_data, key=lambda l: (-l["remainder"], l["sku"]))
            for i in range(missing):
                sorted_lines[i]["discount_cents"] += 1

    lines_result = []
    total_line_discount = 0
    total_line_tax = 0

    for ld in line_data:
        net_cents = ld["subtotal_cents"] - ld["discount_cents"]
        tax_cents = (net_cents * tax_rate_bps) // 10000
        total_line_discount += ld["discount_cents"]
        total_line_tax += tax_cents

        lines_result.append({
            "sku": ld["sku"],
            "subtotal_cents": ld["subtotal_cents"],
            "discount_cents": ld["discount_cents"],
            "net_cents": net_cents,
            "tax_cents": tax_cents,
        })

    total_cents = subtotal_cents - total_line_discount + total_line_tax + shipping_cents

    return {
        "lines": lines_result,
        "subtotal_cents": subtotal_cents,
        "discount_cents": total_line_discount,
        "tax_cents": total_line_tax,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents,
    }
