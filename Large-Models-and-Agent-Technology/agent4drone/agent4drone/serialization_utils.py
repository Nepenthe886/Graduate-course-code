"""Shared conversion helpers for runtime values written to JSON."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel


def make_json_safe(value: Any) -> Any:
    """Convert runtime objects into stable JSON-compatible values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return make_json_safe(value.model_dump())
    if isinstance(value, SimpleNamespace):
        return make_json_safe(vars(value))
    if is_dataclass(value) and not isinstance(value, type):
        return make_json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    if isinstance(value, Exception):
        return str(value)
    if hasattr(value, "__dict__"):
        return make_json_safe(vars(value))
    return str(value)
