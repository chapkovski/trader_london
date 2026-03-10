from datetime import datetime


def as_float_or_none(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int_or_none(value):
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def timestamp_sort_key(value):
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp())
        except Exception:
            pass
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    candidate = text.replace("Z", "+00:00")
    try:
        return float(datetime.fromisoformat(candidate).timestamp())
    except Exception:
        return 0.0
