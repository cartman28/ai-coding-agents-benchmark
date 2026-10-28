"""Fulfillment planning and atomic reservation application across warehouses."""

import copy
import hashlib
import json
from typing import Any, Dict, List, Mapping, Union

from pricebot.inventory import Inventory, _validate_sku, _validate_quantity


def _get_warehouse_available(warehouse: Union[Inventory, Mapping[str, int]]) -> Dict[str, int]:
    """Extract available stock mapping from an Inventory instance or stock dict."""
    if isinstance(warehouse, Inventory):
        snap = warehouse.snapshot()
        on_hand = snap["on_hand"]
        active_reserved: Dict[str, int] = {}
        for res in snap["reservations"].values():
            if res["status"] == "reserved":
                for sku, qty in res["items"].items():
                    active_reserved[sku] = active_reserved.get(sku, 0) + qty
        available = {}
        for sku, total in on_hand.items():
            available[sku] = total - active_reserved.get(sku, 0)
        return available
    elif isinstance(warehouse, Mapping):
        return {str(k): int(v) for k, v in warehouse.items()}
    else:
        raise ValueError("Invalid warehouse representation")


def plan_fulfillment(
    order_id: str,
    lines: List[Mapping[str, Any]],
    warehouses: Mapping[str, Union[Inventory, Mapping[str, int]]]
) -> Dict[str, Any]:
    """Deterministically allocate order quantities across warehouses.

    Allocation order: ascending warehouse ID order, then ascending SKU order.
    Fails atomically if insufficient stock. Does not mutate inputs.
    """
    if isinstance(order_id, bool) or not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("order_id must be a non-empty string")
    if not isinstance(lines, list) or not lines:
        raise ValueError("lines must be a non-empty list")
    if not isinstance(warehouses, Mapping) or not warehouses:
        raise ValueError("warehouses must be a non-empty mapping")

    # Deepcopy lines to ensure no mutation of inputs
    clean_lines = copy.deepcopy(lines)

    # Validate and normalize order lines
    normalized_lines: List[Dict[str, Any]] = []
    seen_skus = set()
    for line in clean_lines:
        if not isinstance(line, Mapping):
            raise ValueError("Each order line must be a mapping")
        sku_key = "sku" if "sku" in line else ("SKU" if "SKU" in line else None)
        qty_key = "quantity" if "quantity" in line else ("qty" if "qty" in line else None)
        if not sku_key or not qty_key:
            raise ValueError("Order line must contain sku/SKU and quantity/qty")
        
        sku = _validate_sku(line[sku_key])
        qty = _validate_quantity(line[qty_key], allow_zero=False)
        if sku in seen_skus:
            raise ValueError(f"Duplicate SKU in order lines: {sku}")
        seen_skus.add(sku)
        normalized_lines.append({"sku": sku, "quantity": qty})

    # Sort lines by ascending SKU order
    sorted_lines = sorted(normalized_lines, key=lambda l: l["sku"])

    # Sort warehouses by ascending warehouse ID order
    sorted_wh_ids = sorted(str(wh_id) for wh_id in warehouses.keys())

    # Build working available stock tracker per warehouse
    wh_avail: Dict[str, Dict[str, int]] = {}
    for wh_id in sorted_wh_ids:
        wh_avail[wh_id] = _get_warehouse_available(warehouses[wh_id])

    allocations: Dict[str, Dict[str, int]] = {}

    for line in sorted_lines:
        sku = line["sku"]
        rem_qty = line["quantity"]
        sku_allocations: Dict[str, int] = {}

        for wh_id in sorted_wh_ids:
            if rem_qty <= 0:
                break
            avail = wh_avail[wh_id].get(sku, 0)
            if avail > 0:
                take = min(rem_qty, avail)
                sku_allocations[wh_id] = take
                wh_avail[wh_id][sku] -= take
                rem_qty -= take

        if rem_qty > 0:
            raise ValueError(f"Insufficient stock across warehouses for SKU {sku}: missing {rem_qty}")

        # Record allocations grouped by warehouse
        for wh_id, qty in sku_allocations.items():
            if wh_id not in allocations:
                allocations[wh_id] = {}
            allocations[wh_id][sku] = qty

    # Construct canonical plan for stable hashing
    canonical = {
        "order_id": order_id,
        "lines": sorted_lines,
        "allocations": {wh: allocations[wh] for wh in sorted(allocations.keys())}
    }
    canonical_json = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    plan_id = "plan_" + hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()[:16]

    return {
        "plan_id": plan_id,
        "order_id": order_id,
        "allocations": allocations
    }


def apply_fulfillment(plan: Mapping[str, Any], warehouse_inventories: Mapping[str, Inventory]) -> str:
    """Reserve fulfillment plan allocations across corresponding Inventory instances atomically.

    Rolls back all reservations on any failure. Supports repeated identical application (idempotent).
    """
    if not isinstance(plan, Mapping) or "plan_id" not in plan or "allocations" not in plan:
        raise ValueError("Invalid fulfillment plan structure")
    if not isinstance(warehouse_inventories, Mapping):
        raise ValueError("warehouse_inventories must be a mapping")

    plan_id = plan["plan_id"]
    allocations = plan["allocations"]

    newly_applied_whs: List[str] = []

    try:
        for wh_id, items in allocations.items():
            if wh_id not in warehouse_inventories:
                raise ValueError(f"Warehouse inventory missing for ID: {wh_id}")
            inventory = warehouse_inventories[wh_id]
            reservation_id = f"fulfillment_{plan_id}_{wh_id}"

            already_reserved = False
            with inventory._lock:
                if reservation_id in inventory._reservations and inventory._reservations[reservation_id]["status"] == "reserved":
                    already_reserved = True

            inventory.reserve(reservation_id, items)

            if not already_reserved:
                newly_applied_whs.append(wh_id)
    except Exception as e:
        # Rollback only reservations created by the current invocation
        for wh_id in newly_applied_whs:
            try:
                reservation_id = f"fulfillment_{plan_id}_{wh_id}"
                warehouse_inventories[wh_id].release(reservation_id)
            except Exception:
                pass
        raise ValueError(f"Failed to apply fulfillment plan {plan_id}, rolled back: {e}") from e

    return plan_id
