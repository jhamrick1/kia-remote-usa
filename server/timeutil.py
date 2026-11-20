from __future__ import annotations

import datetime as dt


def iso(value: dt.datetime | None) -> str | None:
    """ISO 8601 in the computer's local offset, to the second."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.UTC)
    return value.astimezone().isoformat(timespec="seconds")


def now_iso() -> str:
    return iso(dt.datetime.now(dt.UTC))  # type: ignore[return-value]


def parse_iso(value: object) -> dt.datetime | None:
    """An ISO string from the log or the car, as an aware datetime (None if missing or bad)."""
    if not isinstance(value, str):
        return None
    try:
        out = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return out if out.tzinfo else out.astimezone()
