"""Inventory reservation and stock management subsystem."""

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union


class Inventory:
    """Manages stock on hand, reservations, and inventory lifecycle."""

    def __init__(self, initial_stock: Mapping[str, int]) -> None:
        """Initialize inventory with SKU to nonnegative integer mapping."""
        if not isinstance(initial_stock, Mapping):
            raise ValueError("initial_stock must be a mapping")
        self._on_hand: Dict[str, int] = {}
        for sku, qty in initial_stock.items():
            if not isinstance(sku, str):
                raise ValueError("SKU must be a string")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
                raise ValueError(f"Quantity for SKU {sku} must be a nonnegative integer")
            self._on_hand[sku] = qty
        
        # reservation_id -> {"items": {sku: qty}, "status": "reserved" | "released" | "committed"}
        self._reservations: Dict[str, Dict[str, Any]] = {}

    def _validate_items(self, items: Union[Mapping[str, int], List[Dict[str, Any]], List[Tuple[str, int]]], check_known: bool = True) -> Dict[str, int]:
        """Validate and normalize items input into a SKU -> positive int dictionary."""
        normalized: Dict[str, int] = {}
        if isinstance(items, Mapping):
            iterable = items.items()
        elif isinstance(items, list):
            iterable = []
            for item in items:
                if isinstance(item, dict):
                    if "sku" not in item or "quantity" not in item:
                        raise ValueError("Item dict must contain 'sku' and 'quantity'")
                    iterable.append((item["sku"], item["quantity"]))
                elif isinstance(item, (tuple, list)) and len(item) == 2:
                    iterable.append((item[0], item[1]))
                else:
                    raise ValueError("Invalid item format")
        else:
            raise ValueError("items must be a mapping or list of items")

        for sku, qty in iterable:
            if not isinstance(sku, str):
                raise ValueError("SKU must be a string")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
                raise ValueError(f"Quantity for SKU {sku} must be a positive integer")
            if sku in normalized:
                raise ValueError(f"Duplicate SKU {sku} in items")
            if check_known and sku not in self._on_hand:
                raise ValueError(f"Unknown SKU {sku}")
            normalized[sku] = qty
        return normalized

    def reserve(self, reservation_id: str, items: Union[Mapping[str, int], List[Dict[str, Any]], List[Tuple[str, int]]]) -> None:
        """Reserve items atomically. Supports idempotent replay."""
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        
        normalized = self._validate_items(items, check_known=True)

        if reservation_id in self._reservations:
            existing = self._reservations[reservation_id]
            if existing["status"] == "reserved":
                if existing["items"] == normalized:
                    return  # Idempotent replay success
                else:
                    raise ValueError("Conflicting replay for existing reservation ID")
            else:
                raise ValueError(f"Reservation {reservation_id} is already {existing['status']}")

        # Check available stock for all items (on_hand - active reservations)
        active_reserved: Dict[str, int] = {}
        for res in self._reservations.values():
            if res["status"] == "reserved":
                for sku, qty in res["items"].items():
                    active_reserved[sku] = active_reserved.get(sku, 0) + qty

        for sku, qty in normalized.items():
            available = self._on_hand.get(sku, 0) - active_reserved.get(sku, 0)
            if qty > available:
                raise ValueError(f"Insufficient stock for SKU {sku}: requested {qty}, available {available}")

        self._reservations[reservation_id] = {
            "items": normalized,
            "status": "reserved"
        }

    def release(self, reservation_id: str) -> None:
        """Release a reserved reservation back to available stock."""
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        if reservation_id not in self._reservations:
            raise KeyError(f"Reservation {reservation_id} not found")
        
        res = self._reservations[reservation_id]
        if res["status"] != "reserved":
            raise ValueError(f"Cannot release reservation with status {res['status']}")
        
        res["status"] = "released"

    def commit(self, reservation_id: str) -> None:
        """Commit a reservation, permanently deducting stock on hand."""
        if not isinstance(reservation_id, str) or not reservation_id:
            raise ValueError("reservation_id must be a non-empty string")
        if reservation_id not in self._reservations:
            raise KeyError(f"Reservation {reservation_id} not found")
        
        res = self._reservations[reservation_id]
        if res["status"] != "reserved":
            raise ValueError(f"Cannot commit reservation with status {res['status']}")
        
        for sku, qty in res["items"].items():
            if self._on_hand.get(sku, 0) < qty:
                raise ValueError(f"Insufficient on-hand stock to commit SKU {sku}")
            self._on_hand[sku] -= qty

        res["status"] = "committed"

    def restock(self, items: Union[Mapping[str, int], List[Dict[str, Any]], List[Tuple[str, int]]]) -> None:
        """Add stock to inventory. Unknown SKUs are added."""
        normalized = self._validate_items(items, check_known=False)
        for sku, qty in normalized.items():
            self._on_hand[sku] = self._on_hand.get(sku, 0) + qty

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable, deep-copied immutable snapshot of inventory state."""
        return deepcopy({
            "on_hand": self._on_hand,
            "reservations": self._reservations
        })

    @property
    def on_hand(self) -> Dict[str, int]:
        """Return a copy of on-hand stock."""
        return deepcopy(self._on_hand)

    @property
    def reservations(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of reservations."""
        return deepcopy(self._reservations)
