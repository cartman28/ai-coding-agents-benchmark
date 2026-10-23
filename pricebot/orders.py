"""Order lifecycle management, pricing, and refunds."""

import copy
import hashlib
import json


def _validate_nonnegative_int(val, name):
    if isinstance(val, bool) or not isinstance(val, int) or val < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return val


def _validate_positive_int(val, name):
    if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return val


def _validate_tax_bps(val):
    if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= 10000:
        raise ValueError("tax_rate_bps must be an integer between 0 and 10000")
    return val


def _validate_order_id(val):
    if val is not None:
        if isinstance(val, bool) or not isinstance(val, str) or len(val) == 0:
            raise ValueError("order_id must be a nonempty string or None")
    return val


def _validate_coupon(coupon):
    if coupon is None:
        return None
    if not isinstance(coupon, dict):
        raise ValueError("coupon must be a dict or None")
    if set(coupon.keys()) != {"type", "value"}:
        raise ValueError("coupon must have exactly 'type' and 'value' keys")
    ctype = coupon.get("type")
    cval = coupon.get("value")
    if ctype not in ("percent", "fixed"):
        raise ValueError("coupon type must be 'percent' or 'fixed'")
    if isinstance(cval, bool) or not isinstance(cval, int):
        raise ValueError("coupon value must be an integer")
    if ctype == "percent":
        if not 0 <= cval <= 100:
            raise ValueError("percent coupon value must be between 0 and 100")
    else:
        if cval < 0:
            raise ValueError("fixed coupon value must be nonnegative")
    return {"type": ctype, "value": cval}


def _validate_items(items):
    if not isinstance(items, list) or len(items) == 0:
        raise ValueError("items must be a nonempty list")
    validated = []
    seen_skus = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each item must be a dict")
        sku = item.get("sku")
        if isinstance(sku, bool) or not isinstance(sku, str) or len(sku) == 0:
            raise ValueError("item sku must be a nonempty string")
        if sku in seen_skus:
            raise ValueError(f"duplicate SKU in items: {sku}")
        seen_skus.add(sku)
        unit_price = _validate_nonnegative_int(item.get("unit_price_cents"), "unit_price_cents")
        qty = _validate_positive_int(item.get("quantity"), "quantity")
        validated.append({"sku": sku, "unit_price_cents": unit_price, "quantity": qty})
    return validated


def _validate_order_schema(order):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")
    required_keys = {
        "order_id",
        "version",
        "status",
        "items",
        "lines",
        "subtotal_cents",
        "discount_cents",
        "tax_cents",
        "shipping_cents",
        "total_cents",
        "refunded_cents",
        "history",
    }
    if set(order.keys()) != required_keys:
        raise ValueError("order has invalid schema keys")

    _validate_order_id(order["order_id"])
    _validate_positive_int(order["version"], "version")
    status = order["status"]
    if status not in ("draft", "submitted", "paid", "shipped", "cancelled"):
        raise ValueError("invalid order status")

    items = _validate_items(order["items"])
    lines = order["lines"]
    if not isinstance(lines, list) or len(lines) != len(items):
        raise ValueError("lines must be a list matching items length")

    line_keys = {"sku", "subtotal_cents", "discount_cents", "net_cents", "tax_cents", "total_cents", "quantity"}
    for line in lines:
        if not isinstance(line, dict) or set(line.keys()) != line_keys:
            raise ValueError("line has invalid keys")
        _validate_nonnegative_int(line["subtotal_cents"], "subtotal_cents")
        _validate_nonnegative_int(line["discount_cents"], "discount_cents")
        _validate_nonnegative_int(line["net_cents"], "net_cents")
        _validate_nonnegative_int(line["tax_cents"], "tax_cents")
        _validate_nonnegative_int(line["total_cents"], "total_cents")
        _validate_positive_int(line["quantity"], "quantity")

    _validate_nonnegative_int(order["subtotal_cents"], "subtotal_cents")
    _validate_nonnegative_int(order["discount_cents"], "discount_cents")
    _validate_nonnegative_int(order["tax_cents"], "tax_cents")
    _validate_nonnegative_int(order["shipping_cents"], "shipping_cents")
    _validate_nonnegative_int(order["total_cents"], "total_cents")
    _validate_nonnegative_int(order["refunded_cents"], "refunded_cents")

    if order["refunded_cents"] > order["total_cents"]:
        raise ValueError("refunded_cents cannot exceed total_cents")

    history = order["history"]
    if not isinstance(history, list) or len(history) == 0:
        raise ValueError("history must be a nonempty list")
    
    sku_to_orig = {item["sku"]: item["quantity"] for item in items}
    cum_ref = {sku: 0 for sku in sku_to_orig}

    last_ver = 0
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError("history entry must be a dict")
        ver = _validate_positive_int(entry.get("version"), "version")
        if ver <= last_ver:
            raise ValueError("history versions must be strictly increasing")
        last_ver = ver
        event = entry.get("event")
        if event not in ("created", "submit", "pay", "ship", "cancel", "refund"):
            raise ValueError("invalid history event")
        if not isinstance(entry.get("status"), str):
            raise ValueError("history status must be a string")
        if event == "refund":
            q_dict = entry.get("quantities")
            if not isinstance(q_dict, dict) or len(q_dict) == 0:
                raise ValueError("refund history must have a nonempty quantities dict")
            for sku, q in q_dict.items():
                if isinstance(sku, bool) or not isinstance(sku, str) or sku not in sku_to_orig:
                    raise ValueError("invalid sku in refund history quantities")
                _validate_positive_int(q, "refund history quantity")
                cum_ref[sku] += q
                if cum_ref[sku] > sku_to_orig[sku]:
                    raise ValueError("cumulative refunded quantity exceeds original quantity")
            _validate_nonnegative_int(entry.get("amount_cents"), "amount_cents")

    if order["version"] != last_ver:
        raise ValueError("order version must match latest history version")

    return True


def create_order(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0, order_id=None):
    validated_items = _validate_items(items)
    validated_coupon = _validate_coupon(coupon)
    tax_bps = _validate_tax_bps(tax_rate_bps)
    shipping = _validate_nonnegative_int(shipping_cents, "shipping_cents")
    validated_order_id = _validate_order_id(order_id)

    # Compute subtotals
    subtotals = [item["unit_price_cents"] * item["quantity"] for item in validated_items]
    order_subtotal = sum(subtotals)

    # Compute discount
    if validated_coupon is None:
        discount_cents = 0
    elif validated_coupon["type"] == "percent":
        discount_cents = (order_subtotal * validated_coupon["value"]) // 100
    else:
        discount_cents = min(validated_coupon["value"], order_subtotal)

    # Allocate discount across lines using largest-remainder method
    if order_subtotal == 0 or discount_cents == 0:
        line_discounts = [0] * len(validated_items)
    else:
        base_discounts = [(discount_cents * sub) // order_subtotal for sub in subtotals]
        remainders = [(discount_cents * sub) % order_subtotal for sub in subtotals]
        allocated = sum(base_discounts)
        deficit = discount_cents - allocated
        line_discounts = list(base_discounts)
        if deficit > 0:
            # Sort by (-remainder, sku, index)
            indexed_lines = sorted(
                range(len(validated_items)),
                key=lambda i: (-remainders[i], validated_items[i]["sku"], i)
            )
            for idx in indexed_lines[:deficit]:
                line_discounts[idx] += 1

    lines = []
    total_tax = 0
    total_lines_total = 0

    for i, item in enumerate(validated_items):
        sub_c = subtotals[i]
        disc_c = line_discounts[i]
        net_c = sub_c - disc_c
        tax_c = (net_c * tax_bps) // 10000
        tot_c = net_c + tax_c
        total_tax += tax_c
        total_lines_total += tot_c

        lines.append({
            "sku": item["sku"],
            "subtotal_cents": sub_c,
            "discount_cents": disc_c,
            "net_cents": net_c,
            "tax_cents": tax_c,
            "total_cents": tot_c,
            "quantity": item["quantity"]
        })

    order_total = total_lines_total + shipping

    if validated_order_id is None:
        canonical_input = {
            "coupon": validated_coupon,
            "items": [
                {
                    "quantity": it["quantity"],
                    "sku": it["sku"],
                    "unit_price_cents": it["unit_price_cents"],
                }
                for it in validated_items
            ],
            "shipping_cents": shipping,
            "tax_rate_bps": tax_bps,
        }
        canon_json = json.dumps(canonical_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        h = hashlib.sha256(canon_json.encode("utf-8")).hexdigest()
        validated_order_id = f"ord_{h[:16]}"

    order = {
        "order_id": validated_order_id,
        "version": 1,
        "status": "draft",
        "items": validated_items,
        "lines": lines,
        "subtotal_cents": order_subtotal,
        "discount_cents": discount_cents,
        "tax_cents": total_tax,
        "shipping_cents": shipping,
        "total_cents": order_total,
        "refunded_cents": 0,
        "history": [{"version": 1, "event": "created", "status": "draft"}]
    }

    _validate_order_schema(order)
    return order


def transition_order(order, event, *, expected_version=None):
    _validate_order_schema(order)
    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be an integer")
        if expected_version != order["version"]:
            raise ValueError(f"expected_version {expected_version} does not match order version {order['version']}")

    if not isinstance(event, str):
        raise ValueError("event must be a string")

    allowed_edges = {
        ("draft", "submit"): "submitted",
        ("submitted", "pay"): "paid",
        ("paid", "ship"): "shipped",
        ("draft", "cancel"): "cancelled",
        ("submitted", "cancel"): "cancelled",
    }

    current_status = order["status"]
    key = (current_status, event)
    if key not in allowed_edges:
        raise ValueError(f"invalid transition from {current_status} with event {event}")

    new_status = allowed_edges[key]
    new_version = order["version"] + 1

    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["status"] = new_status
    new_order["history"].append({
        "version": new_version,
        "event": event,
        "status": new_status
    })

    _validate_order_schema(new_order)
    return new_order


def refund_order(order, *, quantities=None, expected_version=None):
    _validate_order_schema(order)
    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
            raise ValueError("expected_version must be an integer")
        if expected_version != order["version"]:
            raise ValueError(f"expected_version {expected_version} does not match order version {order['version']}")

    if order["status"] not in ("paid", "shipped"):
        raise ValueError("refunds only allowed for paid or shipped orders")

    # Compute cumulative refunded quantities per SKU from history
    sku_to_orig_qty = {item["sku"]: item["quantity"] for item in order["items"]}
    sku_to_line = {line["sku"]: line for line in order["lines"]}
    
    current_cumulative = {sku: 0 for sku in sku_to_orig_qty}
    for entry in order["history"]:
        if entry.get("event") == "refund":
            for sku, q in entry.get("quantities", {}).items():
                if sku in current_cumulative:
                    current_cumulative[sku] += q

    if quantities is None:
        # Refund all remaining refundable units
        req_quantities = {}
        for sku, orig_qty in sku_to_orig_qty.items():
            rem = orig_qty - current_cumulative[sku]
            if rem > 0:
                req_quantities[sku] = rem
    else:
        if not isinstance(quantities, dict) or len(quantities) == 0:
            raise ValueError("quantities must be a nonempty dict")
        req_quantities = {}
        for sku, q in quantities.items():
            if isinstance(sku, bool) or not isinstance(sku, str) or sku not in sku_to_orig_qty:
                raise ValueError(f"unknown or invalid sku in quantities: {sku}")
            val = _validate_positive_int(q, "refund quantity")
            req_quantities[sku] = val

    # Validate that requested quantities do not exceed remaining unrefunded units
    new_cumulative = dict(current_cumulative)
    any_refunded = False
    for sku, q in req_quantities.items():
        if q > 0:
            any_refunded = True
        new_cum = current_cumulative[sku] + q
        if new_cum > sku_to_orig_qty[sku]:
            raise ValueError(f"refund quantity for {sku} exceeds original quantity")
        new_cumulative[sku] = new_cum

    if not any_refunded:
        raise ValueError("cannot perform zero-unit refund")

    # Compute cumulative-floor refund amounts
    new_cum_amounts = {}
    prev_cum_amounts = {}
    for sku, orig_qty in sku_to_orig_qty.items():
        line = sku_to_line[sku]
        tot = line["total_cents"]
        c_qty = new_cumulative[sku]
        p_qty = current_cumulative[sku]
        new_cum_amounts[sku] = (tot * c_qty) // orig_qty
        prev_cum_amounts[sku] = (tot * p_qty) // orig_qty

    new_total_cum_refund = sum(new_cum_amounts.values())
    prev_total_cum_refund = sum(prev_cum_amounts.values())
    incremental_refund = new_total_cum_refund - prev_total_cum_refund

    if incremental_refund < 0:
        raise ValueError("refund amount cannot be negative")

    new_refunded_cents = order["refunded_cents"] + incremental_refund
    if new_refunded_cents > order["total_cents"]:
        raise ValueError("refunded_cents cannot exceed total order cents")

    new_version = order["version"] + 1
    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["refunded_cents"] = new_refunded_cents
    new_order["history"].append({
        "version": new_version,
        "event": "refund",
        "status": order["status"],
        "quantities": req_quantities,
        "amount_cents": incremental_refund
    })

    _validate_order_schema(new_order)
    return new_order
