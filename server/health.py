"""Kia's monthly vehicle health report, turned into what the dashboard shows.

Kia Connect makes this report once a month (the owner picks the day in the Kia app): a check of
each system in the car, plus driving totals for the month. The library already downloads it on a
status read; server/kia.py keeps Kia's whole answer in health-report.json, and this module reads
that answer. Nothing here talks to Kia.

Kia's answer (Canada), as seen on a 2026 Sportage hybrid; numbers changed:

    {"reportId": 1234567, "reportDate": "2026-10-25", "odometer": 5021.6, "odometerUnit": 1,
     "systems": [{"name": "CHASSIS", "subSystems": [{"name": "Electronic Parking Brake",
                  "code": "EPB", "status": "G", "img": "..."}, ...]}, ...],
     "drivingInfo": {"startsCount": 120, "runTime": 118320, "runTimeUnit": 3,
                     "mileageTime": 84600, "idleEngineTime": 33720, "runDistance": 980,
                     "runDistanceUnit": 1, ...}}

Status "G" is a pass. Anything else is shown as "needs a look". Times with unit 3 are seconds,
which the Kia app shows the same way (118320 s = 32 h 52 min). Distance unit 1 is km, 2 is miles.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("server.health")

PASS = "G"
_SECONDS_PER = {3: 1}          # the only time unit seen so far; others are left out, not guessed
_KM_PER = {1: 1.0, 2: 1.609344}
GROUPS = {"CHASSIS": "chassis", "HEV": "hybrid", "EV": "electric", "POWERTRAIN": "powertrain",
          "SAFETY": "safety"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _seconds(info: dict[str, Any], key: str) -> int | None:
    value = _number(info.get(key))
    per = _SECONDS_PER.get(info.get(f"{key}Unit"))
    return None if value is None or per is None else round(value * per)


def _km(value: Any, unit: Any) -> int | None:
    value = _number(value)
    per = _KM_PER.get(unit)
    return None if value is None or per is None else round(value * per)


def parse(raw: Any) -> dict[str, Any] | None:
    """The report as the dashboard uses it, or None if there's nothing usable."""
    if not isinstance(raw, dict):
        return None
    systems = []
    for group in raw.get("systems") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "")
        for sub in group.get("subSystems") or []:
            if not isinstance(sub, dict) or not sub.get("name"):
                continue
            status = sub.get("status")
            systems.append({
                "group": GROUPS.get(group_name.upper(), group_name.lower() or None),
                "name": str(sub["name"]),
                "code": sub.get("code"),
                "ok": status == PASS if isinstance(status, str) and status else None,
            })
    info = raw.get("drivingInfo") if isinstance(raw.get("drivingInfo"), dict) else {}
    starts = _number(info.get("startsCount"))
    driving = {
        "starts": None if starts is None else round(starts),
        "engine_on_s": _seconds(info, "runTime"),
        "drive_s": _seconds(info, "mileageTime"),
        "idle_s": _seconds(info, "idleEngineTime"),
        "distance_km": _km(info.get("runDistance"), info.get("runDistanceUnit")),
    }
    date = raw.get("reportDate")
    if not systems and all(v is None for v in driving.values()):
        return None
    return {
        "date": date if isinstance(date, str) else None,
        "odometer_km": _km(raw.get("odometer"), raw.get("odometerUnit")),
        "systems": systems,
        "driving": driving,
    }


def read(path: Path | None) -> dict[str, Any] | None:
    """Kia's answer as kept on disk, or None."""
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("health-report.json is unreadable; it's replaced at the next report.")
        return None
