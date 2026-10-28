"""Append-only newline-delimited JSON journal and atomic snapshots."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Mapping, Optional, Union

from pricebot.inventory import Inventory


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def append_journal(path: Union[str, Path], event_type: str, data: Mapping[str, Any], prev_hash: Optional[str] = None, seq: int = 1) -> str:
    """Append a crash-safe journal entry with SHA256 hash chaining.

    Returns the entry hash.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    entry_data = {
        "seq": seq,
        "prev_hash": prev_hash,
        "event_type": event_type,
        "data": data
    }
    content_str = _canonical_json(entry_data)
    entry_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
    
    final_entry = dict(entry_data, hash=entry_hash)
    line = _canonical_json(final_entry) + "\n"

    with open(p, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())

    return entry_hash


def load_journal(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """Load and strictly validate a journal file, checking hash chain and schema."""
    p = Path(path)
    if not p.exists():
        return []

    entries: List[Dict[str, Any]] = []
    expected_seq = 1
    expected_prev_hash: Optional[str] = None

    with open(p, "r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"Malformed JSON at line {line_num}: {e}")

            if not isinstance(entry, dict):
                raise ValueError(f"Invalid entry format at line {line_num}: not a dict")

            for required_field in ("seq", "prev_hash", "event_type", "data", "hash"):
                if required_field not in entry:
                    raise ValueError(f"Missing required field '{required_field}' at line {line_num}")

            seq = entry["seq"]
            prev_hash = entry["prev_hash"]
            event_type = entry["event_type"]
            data = entry["data"]
            given_hash = entry["hash"]

            if seq != expected_seq:
                raise ValueError(f"Sequence mismatch at line {line_num}: expected {expected_seq}, got {seq}")
            if prev_hash != expected_prev_hash:
                raise ValueError(f"Hash chain broken at line {line_num}: prev_hash mismatch")

            # Verify hash
            check_data = {
                "seq": seq,
                "prev_hash": prev_hash,
                "event_type": event_type,
                "data": data
            }
            computed_hash = hashlib.sha256(_canonical_json(check_data).encode("utf-8")).hexdigest()
            if computed_hash != given_hash:
                raise ValueError(f"Tampering detected at line {line_num}: hash mismatch")

            entries.append(entry)
            expected_seq += 1
            expected_prev_hash = given_hash

    return entries


def replay_journal(path: Union[str, Path]) -> Inventory:
    """Rebuild inventory state deterministically from recorded journal operations."""
    entries = load_journal(path)
    inventory: Optional[Inventory] = None

    for entry in entries:
        event_type = entry["event_type"]
        data = entry["data"]

        if event_type == "initial_stock":
            if inventory is not None:
                raise ValueError("Duplicate initial_stock event during replay")
            initial_stock = data.get("initial_stock")
            if not isinstance(initial_stock, Mapping):
                raise ValueError("Invalid initial_stock data")
            inventory = Inventory(initial_stock)
        else:
            if inventory is None:
                raise ValueError(f"Operation {event_type} before initial_stock event")
            
            if event_type == "reserve":
                reservation_id = data.get("reservation_id")
                items = data.get("items")
                inventory.reserve(reservation_id, items)
            elif event_type == "release":
                reservation_id = data.get("reservation_id")
                inventory.release(reservation_id)
            elif event_type == "commit":
                reservation_id = data.get("reservation_id")
                inventory.commit(reservation_id)
            elif event_type == "restock":
                items = data.get("items")
                inventory.restock(items)
            else:
                raise ValueError(f"Unknown event type during replay: {event_type}")

    if inventory is None:
        raise ValueError("Journal contains no initial_stock event")

    return inventory


def _validate_snapshot_dict(data: Any) -> Dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("Snapshot must be a dictionary")
    if "on_hand" not in data or "reservations" not in data:
        raise ValueError("Snapshot missing 'on_hand' or 'reservations'")

    on_hand = data["on_hand"]
    if not isinstance(on_hand, dict):
        raise ValueError("Snapshot 'on_hand' must be a dictionary")

    validated_on_hand: Dict[str, int] = {}
    for sku, qty in on_hand.items():
        if not isinstance(sku, str) or not sku:
            raise ValueError("Snapshot SKU must be a non-empty string")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 0:
            raise ValueError(f"Snapshot on-hand quantity for SKU {sku} must be a non-negative integer")
        validated_on_hand[sku] = qty

    reservations = data["reservations"]
    if not isinstance(reservations, dict):
        raise ValueError("Snapshot 'reservations' must be a dictionary")

    validated_reservations: Dict[str, Dict[str, Any]] = {}
    for res_id, res in reservations.items():
        if not isinstance(res_id, str) or not res_id:
            raise ValueError("Snapshot reservation ID must be a non-empty string")
        if not isinstance(res, dict):
            raise ValueError(f"Snapshot reservation {res_id} must be a dictionary")
        if "items" not in res or "status" not in res:
            raise ValueError(f"Snapshot reservation {res_id} missing 'items' or 'status'")

        status = res["status"]
        if status not in ("reserved", "released", "committed"):
            raise ValueError(f"Snapshot reservation {res_id} has invalid status {status}")

        items = res["items"]
        if not isinstance(items, dict):
            raise ValueError(f"Snapshot reservation {res_id} 'items' must be a dictionary")

        validated_items: Dict[str, int] = {}
        for sku, qty in items.items():
            if not isinstance(sku, str) or not sku:
                raise ValueError(f"Snapshot reservation {res_id} SKU must be a non-empty string")
            if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
                raise ValueError(f"Snapshot reservation {res_id} quantity for SKU {sku} must be a positive integer")
            validated_items[sku] = qty

        validated_reservations[res_id] = {
            "items": validated_items,
            "status": status
        }

    return {
        "on_hand": validated_on_hand,
        "reservations": validated_reservations
    }


def write_snapshot(path: Union[str, Path], state: Union[Mapping[str, Any], Inventory]) -> None:
    """Write inventory state snapshot atomically using same-directory temp file and fsync."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(state, Inventory):
        state_dict = state.snapshot()
    elif isinstance(state, Mapping):
        state_dict = dict(state)
    else:
        raise ValueError("state must be Inventory or mapping")

    validated_state = _validate_snapshot_dict(state_dict)
    content = _canonical_json(validated_state)

    temp_path: Optional[Path] = None
    try:
        with NamedTemporaryFile("w", dir=str(p.parent), delete=False, encoding="utf-8") as tf:
            tf.write(content)
            tf.flush()
            os.fsync(tf.fileno())
            temp_path = Path(tf.name)
        os.replace(temp_path, p)
    except Exception as e:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        raise e


def load_snapshot(path: Union[str, Path]) -> Dict[str, Any]:
    """Load and validate snapshot schema from file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Snapshot not found at {path}")

    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        raise ValueError(f"Malformed snapshot file: {e}")

    return _validate_snapshot_dict(data)
