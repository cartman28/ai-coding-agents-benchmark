"""Atomic JSON storage and optimistic concurrency for orders."""

import json
import os
import tempfile
from pricebot.orders import _validate_non_bool_int, _validate_order_dict


def save_order(path, order, *, expected_version=None):
    _validate_order_dict(order)
    
    if expected_version is not None:
        _validate_non_bool_int(expected_version, "expected_version", min_val=0)
        
    path_str = os.fspath(path)
    dir_name = os.path.dirname(path_str)
    
    # If directory is specified and doesn't exist, tempfile/open will raise FileNotFoundError, adhering to "Do not create missing parent directories"
    if dir_name and not os.path.exists(dir_name):
        raise FileNotFoundError(f"Parent directory does not exist: {dir_name}")
        
    # Optimistic concurrency check
    if os.path.exists(path_str):
        if expected_version is not None:
            try:
                stored = load_order(path_str)
                if stored["version"] != expected_version:
                    raise ValueError(f"Expected version {expected_version} does not match stored version {stored['version']}")
            except FileNotFoundError:
                pass
    else:
        if expected_version is not None and expected_version != 0:
            raise ValueError("When target missing, only expected_version=None or 0 accepted")
            
    # Serialize JSON
    json_str = json.dumps(order, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    
    # Atomic save via temporary file in same directory
    temp_fd, temp_path = tempfile.mkstemp(dir=dir_name if dir_name else None, text=False)
    success = False
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            f.write(json_str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path_str)
        success = True
    finally:
        if not success:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass


def load_order(path) -> dict:
    path_str = os.fspath(path)
    if not os.path.exists(path_str):
        raise FileNotFoundError(f"File not found: {path_str}")
        
    try:
        with open(path_str, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        raise ValueError(f"Failed to read file: {e}") from e
        
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e
        
    try:
        _validate_order_dict(data)
    except Exception as e:
        raise ValueError(f"Malformed order schema: {e}") from e
        
    return data
