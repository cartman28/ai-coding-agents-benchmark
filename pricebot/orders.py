"""Order lifecycle, pricing, tax, and refund management."""

import copy
import hashlib
import json


def _validate_non_bool_int(val, name, min_val=None, max_val=None):
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"{name} must be an integer")
    if min_val is not None and val < min_val:
        raise ValueError(f"{name} must be >= {min_val}")
    if max_val is not None and val > max_val:
        raise ValueError(f"{name} must be <= {max_val}")


def _validate_item(item):
    if not isinstance(item, dict):
        raise ValueError("Item must be a dictionary")
    sku = item.get("sku")
    if isinstance(sku, bool) or not isinstance(sku, str) or not sku:
        raise ValueError("Item sku must be a nonempty string")
    
    unit_price_cents = item.get("unit_price_cents")
    _validate_non_bool_int(unit_price_cents, "unit_price_cents", min_val=0)
    
    quantity = item.get("quantity")
    _validate_non_bool_int(quantity, "quantity", min_val=1)
    
    return {
        "sku": sku,
        "unit_price_cents": unit_price_cents,
        "quantity": quantity,
    }


def _validate_coupon(coupon):
    if coupon is None:
        return None
    if not isinstance(coupon, dict):
        raise ValueError("Coupon must be a dictionary or None")
    ctype = coupon.get("type")
    if isinstance(ctype, bool) or not isinstance(ctype, str) or ctype not in ("percent", "fixed"):
        raise ValueError("Coupon type must be 'percent' or 'fixed'")
    value = coupon.get("value")
    _validate_non_bool_int(value, "coupon value", min_val=0)
    if ctype == "percent" and value > 100:
        raise ValueError("Percent coupon value must be between 0 and 100")
    return {"type": ctype, "value": value}


def _validate_order_dict(order):
    if not isinstance(order, dict):
        raise ValueError("Order must be a dictionary")
    
    expected_keys = {
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
    if set(order.keys()) != expected_keys:
        raise ValueError("Order has invalid or missing keys")
    
    order_id = order["order_id"]
    if isinstance(order_id, bool) or not isinstance(order_id, str) or not order_id:
        raise ValueError("order_id must be a nonempty string")
    
    _validate_non_bool_int(order["version"], "version", min_val=1)
    
    status = order["status"]
    if isinstance(status, bool) or not isinstance(status, str) or status not in ("draft", "submitted", "paid", "shipped", "cancelled"):
        raise ValueError("Invalid order status")
    
    items = order["items"]
    if not isinstance(items, list) or not items:
        raise ValueError("Order items must be a nonempty list")
    validated_items = [_validate_item(item) for item in items]
    
    # Check duplicate SKUs
    skus = [i["sku"] for i in validated_items]
    if len(skus) != len(set(skus)):
        raise ValueError("Duplicate SKUs in order items")
    
    lines = order["lines"]
    if not isinstance(lines, list) or len(lines) != len(items):
        raise ValueError("Order lines must be a list matching items length")
    
    expected_line_keys = {"sku", "subtotal_cents", "discount_cents", "net_cents", "tax_cents", "total_cents", "quantity"}
    for line in lines:
        if not isinstance(line, dict) or set(line.keys()) != expected_line_keys:
            raise ValueError("Line has invalid keys")
        if isinstance(line["sku"], bool) or not isinstance(line["sku"], str) or not line["sku"]:
            raise ValueError("Line sku must be a nonempty string")
        _validate_non_bool_int(line["subtotal_cents"], "line subtotal_cents", min_val=0)
        _validate_non_bool_int(line["discount_cents"], "line discount_cents", min_val=0)
        _validate_non_bool_int(line["net_cents"], "line net_cents", min_val=0)
        _validate_non_bool_int(line["tax_cents"], "line tax_cents", min_val=0)
        _validate_non_bool_int(line["total_cents"], "line total_cents", min_val=0)
        _validate_non_bool_int(line["quantity"], "line quantity", min_val=1)

    for field in ("subtotal_cents", "discount_cents", "tax_cents", "shipping_cents", "total_cents", "refunded_cents"):
        _validate_non_bool_int(order[field], field, min_val=0)
        
    history = order["history"]
    if not isinstance(history, list) or not history:
        raise ValueError("Order history must be a nonempty list")
    if len(history) != order["version"]:
        raise ValueError("Order history length must match order version")
    for idx, h in enumerate(history):
        if not isinstance(h, dict):
            raise ValueError("History entry must be a dictionary")
        h_version = h.get("version")
        _validate_non_bool_int(h_version, "history version", min_val=1)
        if h_version != idx + 1:
            raise ValueError("History entry version must match sequence index")
        htype = h.get("event")
        hstat = h.get("status")
        if isinstance(htype, bool) or not isinstance(htype, str) or not htype:
            raise ValueError("History event must be a string")
        if isinstance(hstat, bool) or not isinstance(hstat, str) or hstat not in ("draft", "submitted", "paid", "shipped", "cancelled"):
            raise ValueError("History status must be valid")
        if htype == "refund":
            q_dict = h.get("quantities")
            if not isinstance(q_dict, dict) or not q_dict:
                raise ValueError("Refund history entry must have quantities dict")
            for qk, qv in q_dict.items():
                if isinstance(qk, bool) or not isinstance(qk, str) or not qk:
                    raise ValueError("Refund history sku must be string")
                _validate_non_bool_int(qv, "refund history quantity", min_val=1)
            _validate_non_bool_int(h.get("amount_cents"), "refund history amount_cents", min_val=0)

    return True


def create_order(items, *, coupon=None, tax_rate_bps=0, shipping_cents=0, order_id=None):
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a nonempty list")
    validated_items = [_validate_item(item) for item in items]
    
    skus = [i["sku"] for i in validated_items]
    if len(skus) != len(set(skus)):
        raise ValueError("Duplicate SKUs in items")
    
    validated_coupon = _validate_coupon(coupon)
    _validate_non_bool_int(tax_rate_bps, "tax_rate_bps", min_val=0, max_val=10000)
    _validate_non_bool_int(shipping_cents, "shipping_cents", min_val=0)
    
    if order_id is not None:
        if isinstance(order_id, bool) or not isinstance(order_id, str) or not order_id:
            raise ValueError("order_id must be a nonempty string or None")
    
    if order_id is None:
        canonical_input = {
            "coupon": validated_coupon,
            "items": [
                {
                    "quantity": i["quantity"],
                    "sku": i["sku"],
                    "unit_price_cents": i["unit_price_cents"],
                }
                for i in validated_items
            ],
            "shipping_cents": shipping_cents,
            "tax_rate_bps": tax_rate_bps,
        }
        canonical_json = json.dumps(canonical_input, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        hash_hex = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
        order_id = f"ord_{hash_hex[:16]}"
        
    subtotal_cents = sum(i["unit_price_cents"] * i["quantity"] for i in validated_items)
    
    # Calculate discount total
    discount_total = 0
    if validated_coupon is not None:
        if validated_coupon["type"] == "percent":
            discount_total = (subtotal_cents * validated_coupon["value"]) // 100
        elif validated_coupon["type"] == "fixed":
            discount_total = min(validated_coupon["value"], subtotal_cents)
            
    # Allocate discount across lines using Largest Remainder Method
    items_subtotals = [i["unit_price_cents"] * i["quantity"] for i in validated_items]
    n = len(validated_items)
    discount_allocs = [0] * n
    remainders = [0] * n
    total_base = 0
    
    if subtotal_cents > 0 and discount_total > 0:
        for idx, sub in enumerate(items_subtotals):
            prod = discount_total * sub
            base = prod // subtotal_cents
            rem = prod % subtotal_cents
            discount_allocs[idx] = base
            remainders[idx] = rem
            total_base += base
            
        remainder_units = discount_total - total_base
        if remainder_units > 0:
            indices = list(range(n))
            indices.sort(key=lambda i: (-remainders[i], skus[i]))
            for i in range(remainder_units):
                discount_allocs[indices[i]] += 1
                
    for idx in range(n):
        discount_allocs[idx] = min(discount_allocs[idx], items_subtotals[idx])
        
    actual_discount_total = sum(discount_allocs)
    
    lines = []
    total_tax_cents = 0
    total_lines_amount = 0
    
    for idx, item in enumerate(validated_items):
        sub = items_subtotals[idx]
        disc = discount_allocs[idx]
        net = sub - disc
        tax = (net * tax_rate_bps) // 10000
        total = net + tax
        
        lines.append({
            "sku": item["sku"],
            "subtotal_cents": sub,
            "discount_cents": disc,
            "net_cents": net,
            "tax_cents": tax,
            "total_cents": total,
            "quantity": item["quantity"],
        })
        total_tax_cents += tax
        total_lines_amount += total
        
    order_total_cents = total_lines_amount + shipping_cents
    
    order = {
        "order_id": order_id,
        "version": 1,
        "status": "draft",
        "items": validated_items,
        "lines": lines,
        "subtotal_cents": subtotal_cents,
        "discount_cents": actual_discount_total,
        "tax_cents": total_tax_cents,
        "shipping_cents": shipping_cents,
        "total_cents": order_total_cents,
        "refunded_cents": 0,
        "history": [
            {
                "version": 1,
                "event": "created",
                "status": "draft",
            }
        ],
    }
    _validate_order_dict(order)
    return order


def transition_order(order, event, *, expected_version=None):
    _validate_order_dict(order)
    
    if expected_version is not None:
        _validate_non_bool_int(expected_version, "expected_version", min_val=1)
        if expected_version != order["version"]:
            raise ValueError("expected_version does not match current order version")
            
    if isinstance(event, bool) or not isinstance(event, str) or event not in ("submit", "pay", "ship", "cancel"):
        raise ValueError("Invalid transition event")
        
    current_status = order["status"]
    valid_transitions = {
        ("draft", "submit"): "submitted",
        ("submitted", "pay"): "paid",
        ("paid", "ship"): "shipped",
        ("draft", "cancel"): "cancelled",
        ("submitted", "cancel"): "cancelled",
    }
    
    key = (current_status, event)
    if key not in valid_transitions:
        raise ValueError(f"Invalid transition from {current_status} with event {event}")
        
    new_status = valid_transitions[key]
    new_version = order["version"] + 1
    
    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["status"] = new_status
    new_order["history"].append({
        "version": new_version,
        "event": event,
        "status": new_status,
    })
    
    _validate_order_dict(new_order)
    return new_order


def refund_order(order, *, quantities=None, expected_version=None):
    _validate_order_dict(order)
    
    if expected_version is not None:
        _validate_non_bool_int(expected_version, "expected_version", min_val=1)
        if expected_version != order["version"]:
            raise ValueError("expected_version does not match current order version")
            
    if order["status"] not in ("paid", "shipped"):
        raise ValueError("Refunds only allowed for paid or shipped orders")
        
    # Determine current cumulative refunded quantities per SKU from history
    sku_to_orig_qty = {item["sku"]: item["quantity"] for item in order["items"]}
    sku_to_orig_total = {line["sku"]: line["total_cents"] for line in order["lines"]}
    
    sku_to_prev_refunded = {sku: 0 for sku in sku_to_orig_qty}
    for h in order["history"]:
        if h.get("event") == "refund":
            for qk, qv in h.get("quantities", {}).items():
                if qk in sku_to_prev_refunded:
                    sku_to_prev_refunded[qk] += qv  # history stores incremental quantities
                
    requested_refunds = {}
    if quantities is None:
        for sku, orig_qty in sku_to_orig_qty.items():
            prev_r = sku_to_prev_refunded[sku]
            rem_qty = orig_qty - prev_r
            if rem_qty > 0:
                requested_refunds[sku] = rem_qty
    else:
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("quantities must be a nonempty dictionary")
        for sku, qty in quantities.items():
            if isinstance(sku, bool) or not isinstance(sku, str) or sku not in sku_to_orig_qty:
                raise ValueError("Unknown SKU in refund quantities")
            _validate_non_bool_int(qty, f"refund quantity for {sku}", min_val=1)
            requested_refunds[sku] = qty
            
    if not requested_refunds:
        raise ValueError("No units specified for refund")
        
    sku_to_new_refunded = dict(sku_to_prev_refunded)
    for sku, req_qty in requested_refunds.items():
        orig_qty = sku_to_orig_qty[sku]
        prev_r = sku_to_prev_refunded[sku]
        new_r = prev_r + req_qty
        if new_r > orig_qty:
            raise ValueError(f"Refund quantity for {sku} exceeds unrefunded units")
        sku_to_new_refunded[sku] = new_r
        
    # Compute cumulative-floor allocation for refunds
    # For each line, cumulative refund amount is floor(line_total_cents * r / original_quantity)
    total_incremental_refund = 0
    history_quantities_entry = {}
    
    for line in order["lines"]:
        sku = line["sku"]
        orig_total = line["total_cents"]
        orig_qty = line["quantity"]
        r_prev = sku_to_prev_refunded[sku]
        r_new = sku_to_new_refunded[sku]
        
        c_prev = (orig_total * r_prev) // orig_qty
        c_new = (orig_total * r_new) // orig_qty
        incremental = c_new - c_prev
        total_incremental_refund += incremental
        
        if sku in requested_refunds and requested_refunds[sku] > 0:
            history_quantities_entry[sku] = requested_refunds[sku]
            
    if total_incremental_refund < 0:
        raise ValueError("Calculated refund amount cannot be negative")
        
    max_refundable = sum(line["total_cents"] for line in order["lines"])
    if order["refunded_cents"] + total_incremental_refund > max_refundable:
        raise ValueError("Refunded amount cannot exceed sum of line totals")
        
    new_version = order["version"] + 1
    new_refunded_cents = order["refunded_cents"] + total_incremental_refund
    
    new_order = copy.deepcopy(order)
    new_order["version"] = new_version
    new_order["refunded_cents"] = new_refunded_cents
    new_order["history"].append({
        "version": new_version,
        "event": "refund",
        "status": new_order["status"],
        "quantities": history_quantities_entry,
        "amount_cents": total_incremental_refund,
    })
    
    _validate_order_dict(new_order)
    return new_order
