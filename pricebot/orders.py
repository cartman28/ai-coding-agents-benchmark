"""Order lifecycle management."""

import copy
import hashlib
import json
from pricebot.pricing import discounted_price


def validate_items(items):
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty list")
    validated = []
    seen_skus = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("item must be a dict")
        sku = item.get("sku")
        if not isinstance(sku, str) or isinstance(sku, bool) or not sku:
            raise ValueError("sku must be a nonempty string")
        if sku in seen_skus:
            raise ValueError(f"duplicate sku: {sku}")
        seen_skus.add(sku)

        unit_price = item.get("unit_price_cents")
        if isinstance(unit_price, bool) or not isinstance(unit_price, int) or unit_price < 0:
            raise ValueError("unit_price_cents must be a nonnegative integer")

        quantity = item.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer")

        validated.append({
            "sku": sku,
            "unit_price_cents": unit_price,
            "quantity": quantity
        })
    return validated


def validate_coupon(coupon):
    if coupon is None:
        return None
    if not isinstance(coupon, dict):
        raise ValueError("coupon must be a dict or None")
    ctype = coupon.get("type")
    cval = coupon.get("value")
    if ctype == "percent":
        if isinstance(cval, bool) or not isinstance(cval, int) or not 0 <= cval <= 100:
            raise ValueError("percent coupon value must be integer between 0 and 100")
    elif ctype == "fixed":
        if isinstance(cval, bool) or not isinstance(cval, int) or cval < 0:
            raise ValueError("fixed coupon value must be a nonnegative integer")
    else:
        raise ValueError("invalid coupon type")
    return {"type": ctype, "value": cval}


def validate_tax_rate(tax_rate_bps):
    if isinstance(tax_rate_bps, bool) or not isinstance(tax_rate_bps, int) or not 0 <= tax_rate_bps <= 10000:
        raise ValueError("tax_rate_bps must be integer between 0 and 10000")
    return tax_rate_bps


def validate_shipping(shipping_cents):
    if isinstance(shipping_cents, bool) or not isinstance(shipping_cents, int) or shipping_cents < 0:
        raise ValueError("shipping_cents must be a nonnegative integer")
    return shipping_cents


def validate_order_id(order_id):
    if order_id is None:
        return None
    if not isinstance(order_id, str) or isinstance(order_id, bool) or not order_id:
        raise ValueError("order_id must be a nonempty string")
    return order_id


def calculate_lines_and_totals(items, coupon, tax_rate_bps, shipping_cents):
    subtotals = [item["unit_price_cents"] * item["quantity"] for item in items]
    subtotal_cents = sum(subtotals)

    if coupon is None:
        discount_cents = 0
    elif coupon["type"] == "percent":
        discount_cents = (subtotal_cents * coupon["value"]) // 100
    else:
        discount_cents = min(subtotal_cents, coupon["value"])

    shares = [0] * len(items)
    if subtotal_cents > 0 and discount_cents > 0:
        numerators = [discount_cents * v for v in subtotals]
        shares = [v // subtotal_cents for v in numerators]
        left = discount_cents - sum(shares)
        order_indices = sorted(range(len(items)), key=lambda i: (-(numerators[i] % subtotal_cents), items[i]["sku"]))
        for i in order_indices[:left]:
            shares[i] += 1

    lines = []
    tax_cents = 0
    for item, sub, disc in zip(items, subtotals, shares):
        net_cents = sub - disc
        line_tax = (net_cents * tax_rate_bps) // 10000
        tax_cents += line_tax
        total_cents = net_cents + line_tax
        lines.append({
            "sku": item["sku"],
            "subtotal_cents": sub,
            "discount_cents": disc,
            "net_cents": net_cents,
            "tax_cents": line_tax,
            "total_cents": total_cents,
            "quantity": item["quantity"]
        })

    order_total_cents = sum(line["total_cents"] for line in lines) + shipping_cents
    return lines, subtotal_cents, discount_cents, tax_cents, shipping_cents, order_total_cents


def create_order(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0, order_id=None):
    items_copy = copy.deepcopy(items)
    validated_items = validate_items(items_copy)
    validated_coupon = validate_coupon(coupon)
    val_tax = validate_tax_rate(tax_rate_bps)
    val_ship = validate_shipping(shipping_cents)
    val_id = validate_order_id(order_id)

    lines, subtotal_cents, discount_cents, tax_cents, shipping_cents, total_cents = calculate_lines_and_totals(
        validated_items, validated_coupon, val_tax, val_ship
    )

    if val_id is None:
        canonical_data = {
            "coupon": validated_coupon,
            "items": [{"sku": i["sku"], "unit_price_cents": i["unit_price_cents"], "quantity": i["quantity"]} for i in validated_items],
            "shipping_cents": val_ship,
            "tax_rate_bps": val_tax,
        }
        canonical_bytes = json.dumps(canonical_data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        val_id = "ord_" + hashlib.sha256(canonical_bytes).hexdigest()[:16]

    order = {
        "order_id": val_id,
        "version": 1,
        "status": "draft",
        "items": validated_items,
        "lines": lines,
        "subtotal_cents": subtotal_cents,
        "discount_cents": discount_cents,
        "tax_cents": tax_cents,
        "shipping_cents": shipping_cents,
        "total_cents": total_cents,
        "refunded_cents": 0,
        "history": [
            {
                "version": 1,
                "event": "created",
                "status": "draft"
            }
        ]
    }
    return order


def validate_order(order):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")

    expected_keys = {
        "order_id", "version", "status", "items", "lines",
        "subtotal_cents", "discount_cents", "tax_cents",
        "shipping_cents", "total_cents", "refunded_cents", "history"
    }
    if set(order.keys()) != expected_keys:
        raise ValueError("order keys do not match schema")

    order_id = order["order_id"]
    if not isinstance(order_id, str) or isinstance(order_id, bool) or not order_id.startswith("ord_"):
        raise ValueError("invalid order_id")

    version = order["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("invalid version")

    status = order["status"]
    valid_statuses = {"draft", "submitted", "paid", "shipped", "cancelled"}
    if not isinstance(status, str) or status not in valid_statuses:
        raise ValueError("invalid status")

    items = order["items"]
    try:
        validate_items(items)
    except Exception as e:
        raise ValueError(f"invalid items: {e}")

    lines = order["lines"]
    if not isinstance(lines, list) or len(lines) != len(items):
        raise ValueError("invalid lines")

    for line in lines:
        if not isinstance(line, dict):
            raise ValueError("line must be dict")
        line_keys = {"sku", "subtotal_cents", "discount_cents", "net_cents", "tax_cents", "total_cents", "quantity"}
        if set(line.keys()) != line_keys:
            raise ValueError("invalid line keys")
        sku = line["sku"]
        if not isinstance(sku, str) or isinstance(sku, bool) or not sku:
            raise ValueError("invalid line sku")
        for k in ["subtotal_cents", "discount_cents", "net_cents", "tax_cents", "total_cents", "quantity"]:
            v = line[k]
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(f"line field {k} must be int")
        if line["quantity"] <= 0:
            raise ValueError("line quantity must be positive")
        if line["subtotal_cents"] < 0 or line["discount_cents"] < 0 or line["net_cents"] < 0 or line["tax_cents"] < 0 or line["total_cents"] < 0:
            raise ValueError("line monetary values cannot be negative")

    for field in ["subtotal_cents", "discount_cents", "tax_cents", "shipping_cents", "total_cents", "refunded_cents"]:
        v = order[field]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"invalid order field {field}")

    history = order["history"]
    if not isinstance(history, list) or not history:
        raise ValueError("invalid history")

    prev_v = 0
    for idx, entry in enumerate(history):
        if not isinstance(entry, dict):
            raise ValueError("history entry must be dict")
        h_ver = entry.get("version")
        h_ev = entry.get("event")
        h_st = entry.get("status")
        if isinstance(h_ver, bool) or not isinstance(h_ver, int) or h_ver != prev_v + 1:
            raise ValueError("invalid history version sequence")
        if not isinstance(h_ev, str) or not isinstance(h_st, str):
            raise ValueError("invalid history event or status")
        if idx == 0:
            if h_ver != 1 or h_ev != "created" or h_st != "draft":
                raise ValueError("invalid initial history entry")
        elif h_ev == "refund":
            if "quantities" not in entry or not isinstance(entry["quantities"], dict):
                raise ValueError("refund history entry missing quantities")
            if "amount_cents" not in entry or isinstance(entry["amount_cents"], bool) or not isinstance(entry["amount_cents"], int) or entry["amount_cents"] < 0:
                raise ValueError("refund history entry missing amount_cents")
        prev_v = h_ver

    if history[-1]["version"] != version or history[-1]["status"] != status:
        raise ValueError("history tail does not match order version/status")

    calc_sub = sum(l["subtotal_cents"] for l in lines)
    calc_disc = sum(l["discount_cents"] for l in lines)
    calc_tax = sum(l["tax_cents"] for l in lines)
    calc_total = sum(l["total_cents"] for l in lines) + order["shipping_cents"]

    if order["subtotal_cents"] != calc_sub or order["tax_cents"] != calc_tax or order["total_cents"] != calc_total:
        raise ValueError("order totals are inconsistent with lines")

    if order["refunded_cents"] > sum(l["total_cents"] for l in lines):
        raise ValueError("refunded_cents exceeds total line merchandise+tax")

    return True


def transition_order(order, event, *, expected_version=None):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")
    validate_order(order)

    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise ValueError("expected_version must be an int")
        if expected_version != order["version"]:
            raise ValueError(f"expected_version {expected_version} does not match order version {order['version']}")

    if not isinstance(event, str) or isinstance(event, bool):
        raise ValueError("event must be a string")

    transitions = {
        ("draft", "submit"): "submitted",
        ("draft", "cancel"): "cancelled",
        ("submitted", "pay"): "paid",
        ("submitted", "cancel"): "cancelled",
        ("paid", "ship"): "shipped",
    }

    current_status = order["status"]
    key = (current_status, event)
    if key not in transitions:
        raise ValueError(f"invalid transition from {current_status} on event {event}")

    new_status = transitions[key]
    new_version = order["version"] + 1

    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["status"] = new_status
    new_order["history"].append({
        "version": new_version,
        "event": event,
        "status": new_status
    })
    return new_order


def refund_order(order, *, quantities=None, expected_version=None):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")
    validate_order(order)

    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise ValueError("expected_version must be an int")
        if expected_version != order["version"]:
            raise ValueError(f"expected_version {expected_version} does not match order version {order['version']}")

    if order["status"] not in ("paid", "shipped"):
        raise ValueError("refund allowed only for paid or shipped orders")

    prev_refunded = {}
    for h in order["history"]:
        if h.get("event") == "refund" and "quantities" in h:
            for sku, q in h["quantities"].items():
                prev_refunded[sku] = prev_refunded.get(sku, 0) + q

    item_map = {item["sku"]: item["quantity"] for item in order["items"]}

    if quantities is None:
        quantities_to_refund = {}
        for sku, orig_qty in item_map.items():
            ref_so_far = prev_refunded.get(sku, 0)
            remaining = orig_qty - ref_so_far
            if remaining > 0:
                quantities_to_refund[sku] = remaining
        if not quantities_to_refund:
            raise ValueError("no units available to refund")
    else:
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("quantities must be a nonempty dict")
        quantities_to_refund = {}
        for sku, q in quantities.items():
            if not isinstance(sku, str) or isinstance(sku, bool) or not sku:
                raise ValueError("invalid sku in quantities")
            if sku not in item_map:
                raise ValueError(f"unknown sku in refund: {sku}")
            if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
                raise ValueError("refund quantity must be a positive integer")
            orig_qty = item_map[sku]
            ref_so_far = prev_refunded.get(sku, 0)
            remaining = orig_qty - ref_so_far
            if q > remaining:
                raise ValueError(f"refund quantity {q} exceeds remaining units {remaining} for sku {sku}")
            quantities_to_refund[sku] = q

    total_incremental_refund = 0
    new_history_quantities = {}

    for line in order["lines"]:
        sku = line["sku"]
        orig_qty = item_map[sku]
        line_total = line["total_cents"]
        r_prev = prev_refunded.get(sku, 0)
        r_batch = quantities_to_refund.get(sku, 0)
        if r_batch == 0:
            continue
        r_new = r_prev + r_batch
        new_history_quantities[sku] = r_batch

        c_old = (line_total * r_prev) // orig_qty if orig_qty > 0 else 0
        c_new = (line_total * r_new) // orig_qty if orig_qty > 0 else 0
        incremental = c_new - c_old
        total_incremental_refund += incremental

    if total_incremental_refund <= 0:
        raise ValueError("total refund amount must be positive")

    max_refundable = sum(l["total_cents"] for l in order["lines"])
    if order["refunded_cents"] + total_incremental_refund > max_refundable:
        raise ValueError("total refunded cents cannot exceed total line merchandise+tax")

    new_version = order["version"] + 1
    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["refunded_cents"] += total_incremental_refund
    new_order["history"].append({
        "version": new_version,
        "event": "refund",
        "status": order["status"],
        "quantities": new_history_quantities,
        "amount_cents": total_incremental_refund
    })
    return new_order
