"""Inventory management subsystem with reservations, lifecycle tracking, and validation."""

import copy
from threading import Lock
from typing import Any, Dict, Iterable, Mapping, Tuple, Union


def _validate_sku(sku: Any) -> str:
    """Validate and return SKU string."""
    if isinstance(sku, bool) or not isinstance(sku, str) or not sku.strip():
        raise ValueError("SKU must be a non-empty string")
    return sku


def _validate_quantity(qty: Any, allow_zero: bool = False) -> int:
    """Validate and return integer quantity."""
    if isinstance(qty, bool) or not isinstance(qty, int):
        raise ValueError("Quantity must be an integer")
    if allow_zero:
        if qty < 0:
            raise ValueError("Quantity must be nonnegative")
    else:
        if qty <= 0:
            raise ValueError("Quantity must be positive")
    return qty


def _normalize_items(items: Union[Mapping[str, int], Iterable[Any]]) -> Dict[str, int]:
    """Normalize items mapping or iterable into a validated SKU -> quantity dict."""
    normalized: Dict[str, int] = {}
    if isinstance(items, Mapping):
        for sku, qty in items.items():
            valid_sku = _validate_sku(sku)
            valid_qty = _validate_quantity(qty, allow_zero=False)
            if valid_sku in normalized:
                raise ValueError(f"Duplicate SKU in items: {valid_sku}")
            normalized[valid_sku] = valid_qty
    elif isinstance(items, Iterable) and not isinstance(items, (str, bytes)):
        seen = set()
        for item in items:
            if isinstance(item, Mapping):
                if "sku" not in item or "quantity" not in item:
                    raise ValueError("Item dict must contain 'sku' and 'quantity'")
                sku = _validate_sku(item["sku"])
                qty = _validate_quantity(item["quantity"], allow_zero=False)
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                sku = _validate_sku(item[0])
                qty = _validate_quantity(item[1], allow_zero=False)
            else:
                raise ValueError("Invalid item format")
            if sku in seen:
                raise ValueError(f"Duplicate SKU in items: {sku}")
            seen.add(sku)
            normalized[sku] = qty
    else:
        raise ValueError("Items must be a mapping or iterable of items")
    return normalized


class Inventory:
    """Thread-safe inventory management with reservations and lifecycle support."""

    def __init__(self, initial_stock: Mapping[str, int]) -> None:
        """Initialize inventory with SKU -> nonnegative integer on-hand mapping."""
        if isinstance(initial_stock, bool) or not isinstance(initial_stock, Mapping):
            raise ValueError("initial_stock must be a mapping")
        
        self._lock = Lock()
        self._on_hand: Dict[str, int] = {}
        self._reservations: Dict[str, Dict[str, Any]] = {}

        for sku, qty in initial_stock.items():
            v_sku = _validate_sku(sku)
            v_qty = _validate_quantity(qty, allow_zero=True)
            if v_sku in self._on_hand:
                raise ValueError(f"Duplicate SKU in initial stock: {v_sku}")
            self._on_hand[v_sku] = v_qty

    def _get_active_reservations(self) -> Dict[str, int]:
        """Compute total currently reserved quantity per SKU across active 'reserved' reservations."""
        active: Dict[str, int] = {}
        for res in self._reservations.values():
            if res["status"] == "reserved":
                for sku, qty in res["items"].items():
                    active[sku] = active.get(sku, 0) + qty
        return active

    def reserve(self, reservation_id: str, items: Union[Mapping[str, int], Iterable[Any]]) -> None:
        """Reserve items for a given reservation ID. All-or-nothing allocation."""
        if isinstance(reservation_id, bool) or not isinstance(reservation_id, str) or not reservation_id.strip():
            raise ValueError("reservation_id must be a non-empty string")

        norm_items = _normalize_items(items)

        with self._lock:
            # Check unknown SKUs
            for sku in norm_items:
                if sku not in self._on_hand:
                    raise ValueError(f"Unknown SKU: {sku}")

            # Check existing reservation idempotency / conflicts
            if reservation_id in self._reservations:
                existing = self._reservations[reservation_id]
                if existing["status"] == "reserved":
                    if existing["items"] == norm_items:
                        return  # Idempotent replay
                    else:
                        raise ValueError(f"Conflicting replay for reservation ID: {reservation_id}")
                else:
                    raise ValueError(f"Reservation {reservation_id} is already {existing['status']}")

            # Check stock availability (on_hand - active_reserved >= requested)
            active_reserved = self._get_active_reservations()
            for sku, req_qty in norm_items.items():
                on_hand = self._on_hand[sku]
                reserved = active_reserved.get(sku, 0)
                available = on_hand - reserved
                if req_qty > available:
                    raise ValueError(f"Insufficient stock for SKU {sku}: requested {req_qty}, available {available}")

            # Apply reservation
            self._reservations[reservation_id] = {
                "items": norm_items,
                "status": "reserved"
            }

    def release(self, reservation_id: str) -> None:
        """Release an active reservation."""
        if isinstance(reservation_id, bool) or not isinstance(reservation_id, str):
            raise ValueError("reservation_id must be a string")

        with self._lock:
            if reservation_id not in self._reservations:
                raise ValueError(f"Unknown reservation ID: {reservation_id}")
            
            res = self._reservations[reservation_id]
            if res["status"] == "released":
                return  # Idempotent
            if res["status"] == "committed":
                raise ValueError(f"Cannot release committed reservation: {reservation_id}")
            
            res["status"] = "released"

    def commit(self, reservation_id: str) -> None:
        """Commit an active reservation, permanently deducting stock on-hand."""
        if isinstance(reservation_id, bool) or not isinstance(reservation_id, str):
            raise ValueError("reservation_id must be a string")

        with self._lock:
            if reservation_id not in self._reservations:
                raise ValueError(f"Unknown reservation ID: {reservation_id}")

            res = self._reservations[reservation_id]
            if res["status"] == "committed":
                return  # Idempotent
            if res["status"] == "released":
                raise ValueError(f"Cannot commit released reservation: {reservation_id}")

            # Check availability one last time before deduction
            active_reserved = self._get_active_reservations()
            # Note: this reservation's items are already counted in active_reserved,
            # so we temporarily subtract them or check on_hand >= items
            for sku, qty in res["items"].items():
                if self._on_hand[sku] < qty:
                    raise ValueError(f"Insufficient on-hand stock to commit SKU {sku}")

            # Deduct on-hand
            for sku, qty in res["items"].items():
                self._on_hand[sku] -= qty

            res["status"] = "committed"

    def restock(self, items: Union[Mapping[str, int], Iterable[Any]]) -> None:
        """Restock inventory with positive quantities for known SKUs."""
        norm_items = _normalize_items(items)

        with self._lock:
            for sku in norm_items:
                if sku not in self._on_hand:
                    raise ValueError(f"Unknown SKU for restock: {sku}")

            for sku, qty in norm_items.items():
                self._on_hand[sku] += qty

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable, immutable snapshot of inventory state."""
        with self._lock:
            return copy.deepcopy({
                "on_hand": self._on_hand,
                "reservations": self._reservations
            })


def _validate_snapshot_state(state: Any) -> None:
    """Validate snapshot schema, types, and values for on_hand and reservations."""
    if isinstance(state, bool) or not isinstance(state, Mapping):
        raise ValueError("Snapshot state must be a mapping")

    if "on_hand" not in state or "reservations" not in state:
        raise ValueError("Snapshot missing required keys 'on_hand' and 'reservations'")

    on_hand = state["on_hand"]
    reservations = state["reservations"]

    if isinstance(on_hand, bool) or not isinstance(on_hand, Mapping):
        raise ValueError("Snapshot 'on_hand' must be a mapping")
    if isinstance(reservations, bool) or not isinstance(reservations, Mapping):
        raise ValueError("Snapshot 'reservations' must be a mapping")

    seen_skus = set()
    for sku, qty in on_hand.items():
        v_sku = _validate_sku(sku)
        if v_sku in seen_skus:
            raise ValueError(f"Duplicate SKU in snapshot on_hand: {v_sku}")
        seen_skus.add(v_sku)
        _validate_quantity(qty, allow_zero=True)

    seen_res_ids = set()
    for res_id, res_data in reservations.items():
        if isinstance(res_id, bool) or not isinstance(res_id, str) or not res_id.strip():
            raise ValueError("Reservation ID in snapshot must be a non-empty string")
        if res_id in seen_res_ids:
            raise ValueError(f"Duplicate reservation ID in snapshot: {res_id}")
        seen_res_ids.add(res_id)

        if isinstance(res_data, bool) or not isinstance(res_data, Mapping):
            raise ValueError(f"Reservation data for {res_id} must be a mapping")

        if "status" not in res_data or "items" not in res_data:
            raise ValueError(f"Reservation {res_id} missing 'status' or 'items'")

        status = res_data["status"]
        if isinstance(status, bool) or not isinstance(status, str) or status not in ("reserved", "released", "committed"):
            raise ValueError(f"Invalid reservation status in snapshot: {status}")

        items = res_data["items"]
        _normalize_items(items)

