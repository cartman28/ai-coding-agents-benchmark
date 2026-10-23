"""Deterministic checkout engine with integer-cent calculations."""

import copy


def _validate_non_bool_int(val, name, min_val=None, max_val=None):
    if not isinstance(val, int) or isinstance(val, bool):
        raise ValueError(f"{name} must be an integer (not bool)")
    if min_val is not None and val < min_val:
        raise ValueError(f"{name} must be >= {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"{name} must be <= {max_val}")


def checkout(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0):
    """Calculate checkout totals, discounts, and taxes for cart items."""
    if not isinstance(items, list):
        raise ValueError("items must be a list")
    if not items:
        raise ValueError("items list cannot be empty")

    _validate_non_bool_int(tax_rate_bps, "tax_rate_bps", 0, 10000)
    _validate_non_bool_int(shipping_cents, "shipping_cents", 0)

    if coupon is not None:
        if not isinstance(coupon, dict):
            raise ValueError("coupon must be a dictionary or None")
        if "type" not in coupon or "value" not in coupon:
            raise ValueError("coupon must contain 'type' and 'value'")
        c_type = coupon["type"]
        c_val = coupon["value"]
        if c_type == "percent":
            _validate_non_bool_int(c_val, "coupon value", 0, 100)
        elif c_type == "fixed":
            _validate_non_bool_int(c_val, "coupon value", 0)
        else:
            raise ValueError("coupon type must be 'percent' or 'fixed'")

    seen_skus = set()
    validated_items = []

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each item must be a dictionary")
        if "sku" not in item or "unit_price_cents" not in item or "quantity" not in item:
            raise ValueError("item missing required fields (sku, unit_price_cents, quantity)")

        sku = item["sku"]
        if not isinstance(sku, str) or isinstance(sku, bool) or not sku:
            raise ValueError("sku must be a nonempty string")
        if sku in seen_skus:
            raise ValueError(f"duplicate SKU: {sku}")
        seen_skus.add(sku)

        unit_price = item["unit_price_cents"]
        _validate_non_bool_int(unit_price, "unit_price_cents", 0)

        qty = item["quantity"]
        _validate_non_bool_int(qty, "quantity", 1)

        validated_items.append({
            "sku": sku,
            "unit_price_cents": unit_price,
            "quantity": qty
        })

    # Calculations
    subtotal_cents = 0
    line_subtotals = []
    for vi in validated_items:
        s = vi["unit_price_cents"] * vi["quantity"]
        line_subtotals.append(s)
        subtotal_cents += s

    discount_cents = 0
    if coupon is not None:
        c_type = coupon["type"]
        c_val = coupon["value"]
        if c_type == "percent":
            discount_cents = (subtotal_cents * c_val) // 100
        elif c_type == "fixed":
            discount_cents = min(c_val, subtotal_cents)

    line_discounts = [0] * len(validated_items)
    if subtotal_cents > 0 and discount_cents > 0:
        remainders = []
        allocated_sum = 0
        for i, (vi, s) in enumerate(zip(validated_items, line_subtotals)):
            exact_num = discount_cents * s
            f = exact_num // subtotal_cents
            rem = exact_num % subtotal_cents
            line_discounts[i] = f
            allocated_sum += f
            remainders.append((rem, vi["sku"], i))

        remaining_to_distribute = discount_cents - allocated_sum
        if remaining_to_distribute > 0:
            remainders.sort(key=lambda x: (-x[0], x[1]))
            for k in range(remaining_to_distribute):
                idx = remainders[k][2]
                line_discounts[idx] += 1

    lines_output = []
    total_tax_cents = 0
    for i, vi in enumerate(validated_items):
        s_cents = line_subtotals[i]
        d_cents = line_discounts[i]
        net_cents = s_cents - d_cents
        tax_cents = (net_cents * tax_rate_bps) // 10000
        total_tax_cents += tax_cents

        lines_output.append({
            "sku": vi["sku"],
            "subtotal_cents": s_cents,
            "discount_cents": d_cents,
            "net_cents": net_cents,
            "tax_cents": tax_cents
        })

    net_merchandise_cents = subtotal_cents - discount_cents
    total_cents = net_merchandise_cents + total_tax_cents + shipping_cents

    return {
        "lines": lines_output,
        "subtotal_cents": subtotal_cents,
        "discount_cents": discount_cents,
        "tax_cents": total_tax_cents,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents
    }
