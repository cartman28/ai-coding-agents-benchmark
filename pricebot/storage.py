"""Persistent storage for orders."""

import json
import os
import tempfile
from pathlib import Path

from pricebot.orders import _validate_order_schema


def load_order(path) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Order file not found: {path}")
    try:
        content = p.read_text(encoding="utf-8")
        order = json.loads(content)
    except Exception as e:
        if isinstance(e, FileNotFoundError):
            raise
        raise ValueError(f"Invalid JSON or malformed order file: {e}") from e

    _validate_order_schema(order)
    return order


def save_order(path, order, *, expected_version=None) -> None:
    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise ValueError("expected_version must be an integer or None")
        if expected_version < 0:
            raise ValueError("expected_version must be nonnegative")

    _validate_order_schema(order)

    p = Path(path)
    parent = p.parent
    if not parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {parent}")

    # Optimistic concurrency
    if p.exists():
        if expected_version is not None:
            existing = load_order(path)
            if existing["version"] != expected_version:
                raise ValueError(
                    f"Optimistic concurrency violation: stored version {existing['version']} != expected {expected_version}"
                )
    else:
        if expected_version is not None and expected_version not in (None, 0):
            raise ValueError(
                f"Optimistic concurrency violation: file does not exist but expected_version is {expected_version}"
            )

    json_str = json.dumps(order, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    fd, tmp_path = tempfile.mkstemp(dir=parent, text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass
        raise
