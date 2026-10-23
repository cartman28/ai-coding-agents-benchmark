"""Order storage and persistence with optimistic concurrency and atomic saves."""

import copy
import json
import os
import pathlib
import tempfile
from pricebot.orders import validate_order


def save_order(path, order, *, expected_version=None):
    if not isinstance(order, dict):
        raise ValueError("order must be a dict")
    validate_order(order)

    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise ValueError("expected_version must be an int")

    target_path = pathlib.Path(path)

    if target_path.exists():
        if expected_version is not None:
            existing = load_order(target_path)
            if existing["version"] != expected_version:
                raise ValueError(f"concurrency conflict: expected version {expected_version}, found {existing['version']}")
    else:
        if expected_version is not None and expected_version != 0:
            raise ValueError(f"expected_version must be None or 0 when file does not exist, got {expected_version}")

    content = json.dumps(order, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    utf8_content = content.encode('utf-8')

    parent_dir = target_path.parent
    if not parent_dir.exists():
        raise FileNotFoundError(f"parent directory does not exist: {parent_dir}")

    fd, temp_path_str = tempfile.mkstemp(dir=parent_dir, text=False)
    temp_path = pathlib.Path(temp_path_str)
    success = False
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(utf8_content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, target_path)
        success = True
    finally:
        if not success and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass


def load_order(path):
    target_path = pathlib.Path(path)
    if not target_path.exists():
        raise FileNotFoundError(f"file not found: {target_path}")

    with open(target_path, "r", encoding="utf-8") as f:
        text = f.read()

    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError(f"invalid JSON: {e}")

    if not isinstance(data, dict):
        raise ValueError("stored order must be a JSON object")

    validate_order(data)
    return copy.deepcopy(data)
