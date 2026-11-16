from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ..schemas import Schema

_DATE_FORMATS = ["%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%m/%d/%Y"]
_TRUE = {"true", "yes", "1", "in stock", "available", "remote"}
_FALSE = {"false", "no", "0", "out of stock", "sold out", "on site", "office", "onsite"}


def to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    if not m:
        return None
    num = float(m.group())
    if re.search(rf"{re.escape(m.group())}\s*k\b", text, re.I):
        num *= 1000
    return num


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    return None


def to_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return text or None


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().casefold()
    return text or None


def normalize(value: Any, ftype: str) -> Any:
    if isinstance(value, str) and value.strip().lower() in {"", "null", "none", "n/a", "na"}:
        return None
    if ftype in ("number", "integer"):
        num = to_number(value)
        return None if num is None else round(num, 2)
    if ftype == "boolean":
        return to_bool(value)
    if ftype == "date":
        return to_date(value)
    return to_text(value)


def normalize_record(rec: dict[str, Any], schema: Schema) -> dict[str, Any]:
    return {f.name: normalize(rec.get(f.name), f.type) for f in schema.fields}


def equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 0.005
    return a == b
