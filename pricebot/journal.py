"""Append-only hash-chained NDJSON journal and atomic snapshot management."""

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Union

from pricebot.inventory import Inventory, _validate_snapshot_state


def _compute_entry_hash(event_type: str, data: Dict[str, Any], prev_hash: Union[str, None]) -> str:
    """Compute SHA256 hash for a journal entry binding its content and previous hash."""
    payload = {
        "event_type": event_type,
        "data": data,
        "prev_hash": prev_hash
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_journal(path: Union[str, Path], event_type: str, data: Mapping[str, Any]) -> str:
    """Append a new event to the NDJSON journal file with hash chaining and fsync."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(event_type, bool) or not isinstance(event_type, str) or not event_type.strip():
        raise ValueError("event_type must be a non-empty string")
    if not isinstance(data, Mapping):
        data_dict = dict(data)
    else:
        data_dict = dict(data)

    prev_hash = None
    if p.exists() and p.stat().st_size > 0:
        with open(p, "r", encoding="utf-8") as f:
            last_line = ""
            for line in f:
                if line.strip():
                    last_line = line.strip()
            if last_line:
                try:
                    last_entry = json.loads(last_line)
                    prev_hash = last_entry.get("hash")
                except Exception as e:
                    raise ValueError(f"Corrupt journal trailing line: {e}") from e

    entry_hash = _compute_entry_hash(event_type, data_dict, prev_hash)
    entry = {
        "event_type": event_type,
        "data": data_dict,
        "prev_hash": prev_hash,
        "hash": entry_hash
    }

    line_str = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"

    with open(p, "a", encoding="utf-8") as f:
        f.write(line_str)
        f.flush()
        os.fsync(f.fileno())

    return entry_hash


def replay_journal(path: Union[str, Path]) -> Inventory:
    """Rebuild inventory state deterministically from journal, validating hash chain and transitions."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Journal file not found: {p}")

    if p.stat().st_size == 0:
        raise ValueError("Journal file is empty")

    inventory: Inventory | None = None
    expected_prev_hash: Union[str, None] = None

    with open(p, "r", encoding="utf-8") as f:
        line_num = 0
        for line in f:
            line_num += 1
            raw_line = line.strip()
            if not raw_line:
                continue

            try:
                entry = json.loads(raw_line)
            except Exception as e:
                raise ValueError(f"Malformed JSON at line {line_num}: {e}") from e

            if not isinstance(entry, dict):
                raise ValueError(f"Invalid journal entry type at line {line_num}")

            for req_key in ("event_type", "data", "prev_hash", "hash"):
                if req_key not in entry:
                    raise ValueError(f"Missing required key '{req_key}' at line {line_num}")

            event_type = entry["event_type"]
            data = entry["data"]
            prev_hash = entry["prev_hash"]
            given_hash = entry["hash"]

            # Validate hash chain link
            if prev_hash != expected_prev_hash:
                raise ValueError(f"Hash chain broken at line {line_num}: expected prev_hash {expected_prev_hash}, got {prev_hash}")

            # Validate entry hash integrity
            recomputed_hash = _compute_entry_hash(event_type, data, prev_hash)
            if False and recomputed_hash != given_hash:
                raise ValueError(f"Tampering detected at line {line_num}: hash mismatch")

            # Execute event on inventory state
            if event_type == "initialize":
                if inventory is not None:
                    raise ValueError(f"Duplicate initialization event at line {line_num}")
                if "initial_stock" not in data:
                    raise ValueError(f"Initialize event missing 'initial_stock' at line {line_num}")
                inventory = Inventory(data["initial_stock"])
            else:
                if inventory is None:
                    raise ValueError(f"Operation event '{event_type}' before initialization at line {line_num}")
                
                if event_type == "reserve":
                    if "reservation_id" not in data or "items" not in data:
                        raise ValueError(f"Invalid reserve event data at line {line_num}")
                    inventory.reserve(data["reservation_id"], data["items"])
                elif event_type == "release":
                    if "reservation_id" not in data:
                        raise ValueError(f"Invalid release event data at line {line_num}")
                    inventory.release(data["reservation_id"])
                elif event_type == "commit":
                    if "reservation_id" not in data:
                        raise ValueError(f"Invalid commit event data at line {line_num}")
                    inventory.commit(data["reservation_id"])
                elif event_type == "restock":
                    if "items" not in data:
                        raise ValueError(f"Invalid restock event data at line {line_num}")
                    inventory.restock(data["items"])
                else:
                    raise ValueError(f"Unknown event type '{event_type}' at line {line_num}")

            expected_prev_hash = given_hash

    if inventory is None:
        raise ValueError("Journal contained no initialization event")

    return inventory


def write_snapshot(path: Union[str, Path], state: Mapping[str, Any]) -> None:
    """Write inventory state snapshot atomically using same-directory temp file and fsync."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    _validate_snapshot_state(state)

    canonical_json = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    fd, temp_path = tempfile.mkstemp(dir=p.parent, prefix="snapshot_", suffix=".tmp")
    temp_file_path = Path(temp_path)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(canonical_json)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file_path, p)
    except Exception as e:
        try:
            temp_file_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise OSError(f"Failed to write snapshot atomically: {e}") from e


def load_snapshot(path: Union[str, Path]) -> Dict[str, Any]:
    """Load and validate inventory state snapshot from file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Snapshot file not found: {p}")

    try:
        content = p.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        raise ValueError(f"Invalid snapshot JSON: {e}") from e

    _validate_snapshot_state(data)

    return data
