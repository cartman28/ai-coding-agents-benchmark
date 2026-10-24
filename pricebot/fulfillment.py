"""Order fulfillment planning and atomic application across warehouses."""

from copy import deepcopy
import hashlib
import json
from typing import Any, Dict, List, Mapping, Tuple, Union

from pricebot.inventory import Inventory


def plan_fulfillment(order_id: str, lines: Union[List[Dict[str, Any]], List[Tuple[str, int]], Mapping[str, int]], warehouses: Mapping[str, Inventory]) -> Dict[str, Any]:
    """Deterministically allocate order quantities across warehouses in ascending warehouse ID order, then ascending SKU order.

    Fail atomically if insufficient stock; do not mutate inputs.
    """
    if not isinstance(order_id, str) or not order_id:
        raise ValueError("order_id must be a non-empty string")
    if not isinstance(warehouses, Mapping) or not warehouses:
        raise ValueError("warehouses must be a non-empty mapping")

    # Normalize lines into deterministic sorted list of (sku, quantity)
    if isinstance(lines, Mapping):
        item_map = dict(lines)
    elif isinstance(lines, list):
        item_map = {}
        for line in lines:
            if isinstance(line, dict):
                if "sku" not in line or "quantity" not in line:
                    raise ValueError("Line dict must contain 'sku' and 'quantity'")
                sku = line["sku"]
                qty = line["quantity"]
            elif isinstance(line, (tuple, list)) and len(line) == 2:
                sku, qty = line[0], line[1]
            else:
                raise ValueError("Invalid line format")

            if not isinstance(sku, str) or not sku:
                raise ValueError("Line SKU must be a non-empty string")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
                raise ValueError("Line quantity must be a positive integer")
            if sku in item_map:
                raise ValueError(f"Duplicate SKU {sku} in lines")
            item_map[sku] = qty
    else:
        raise ValueError("lines must be a list or mapping")

    line_items = sorted(item_map.items())
    sorted_wh_ids = sorted(warehouses.keys())
    
    # Calculate available stock per warehouse (on_hand minus active reserved)
    wh_available: Dict[str, Dict[str, int]] = {}
    for wh_id in sorted_wh_ids:
        inv = warehouses[wh_id]
        snap = inv.snapshot()
        on_hand = snap["on_hand"]
        reserved: Dict[str, int] = {}
        for res in snap["reservations"].values():
            if res["status"] == "reserved":
                for sku, qty in res["items"].items():
                    reserved[sku] = reserved.get(sku, 0) + qty
        available = {sku: on_hand.get(sku, 0) - reserved.get(sku, 0) for sku in on_hand}
        wh_available[wh_id] = available

    allocations: Dict[str, Dict[str, int]] = {}

    for sku, required_qty in line_items:
        remaining = required_qty
        for wh_id in sorted_wh_ids:
            if remaining <= 0:
                break
            avail = wh_available[wh_id].get(sku, 0)
            if avail > 0:
                take = min(remaining, avail)
                wh_available[wh_id][sku] -= take
                remaining -= take
                if wh_id not in allocations:
                    allocations[wh_id] = {}
                allocations[wh_id][sku] = allocations[wh_id].get(sku, 0) + take
        
        if remaining > 0:
            raise ValueError(f"Insufficient stock across all warehouses for SKU {sku}: remaining {remaining}")

    # Compute stable plan ID
    canonical_plan = {
        "order_id": order_id,
        "allocations": {wh: allocations[wh] for wh in sorted(allocations.keys())}
    }
    plan_json = json.dumps(canonical_plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    plan_id = "plan_" + hashlib.sha256(plan_json.encode("utf-8")).hexdigest()[:16]

    return {
        "plan_id": plan_id,
        "order_id": order_id,
        "allocations": allocations
    }


def apply_fulfillment(plan: Mapping[str, Any], warehouse_inventories: Mapping[str, Inventory]) -> str:
    """Reserve allocations in corresponding Inventory instances.

    Roll back all reservations on any failure; support repeated identical application without double-reserving.
    Returns stable plan ID.
    """
    if not isinstance(plan, Mapping) or "plan_id" not in plan or "allocations" not in plan:
        raise ValueError("Invalid plan structure")
    if not isinstance(warehouse_inventories, Mapping):
        raise ValueError("warehouse_inventories must be a mapping")

    plan_id = plan["plan_id"]
    allocations = plan["allocations"]

    applied_whs: List[str] = []
    try:
        for wh_id, items in sorted(allocations.items()):
            if wh_id not in warehouse_inventories:
                raise ValueError(f"Warehouse {wh_id} not found in inventory mapping")
            inv = warehouse_inventories[wh_id]
            res_id = f"{plan_id}_{wh_id}"
            inv.reserve(res_id, items)
            applied_whs.append(wh_id)
    except Exception as e:
        # Rollback all successfully applied reservations in this call
        for wh_id in applied_whs:
            inv = warehouse_inventories[wh_id]
            res_id = f"{plan_id}_{wh_id}"
            try:
                inv.release(res_id)
            except Exception:
                pass
        raise ValueError(f"Failed to apply fulfillment plan atomically: {e}")

    return plan_id
