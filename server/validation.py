"""Checks every request body before anything reaches Kia. Errors are plain sentences."""

from __future__ import annotations

import re
from typing import Any

from .errors import ServiceError

ACTIONS = ("lock", "unlock", "climate_start", "climate_stop", "hazard", "horn_lights")
# Kia's Canadian range for 2020+ cars (the library raises on anything else).
TEMP_RANGE_NEW = tuple(x * 0.5 for x in range(28, 64))   # 14.0 .. 31.5
# The car's own dial: LO, 17-27 C in 0.5 steps, HI. Kia's table goes 14-31.5, but the car answers
# a value past its dial by falling back to 22 C (seen 2026-11-17: 31.5 ran at 22 with a low fan).
# HI is what gives full heat and the top fan speed.
DIAL_MIN, DIAL_MAX = 17.0, 27.0
TEMP_ENDS = ("LO", "HI")
SEAT_LEVELS = (0, 3, 4, 5, 6, 7, 8)
SEAT_KEYS = ("front_left_seat", "front_right_seat", "rear_left_seat", "rear_right_seat")
# Kia's heating1 code: 0 off, 1 steering wheel + rear window + mirrors, 2 rear window + mirrors,
# 3 steering wheel only (the hyundai_kia_connect_api convention). 4 was tried on this car on
# 2026-11-17: steering wheel only, no heated windshield, so it isn't accepted.
HEATING = (0, 1, 2, 3)
CLIMATE_KEYS = ("set_temp_c", "duration_min", "defrost", "heating", *SEAT_KEYS)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def bad(message: str) -> ServiceError:
    return ServiceError(400, message)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(t: float) -> str:
    return f"{t:g}"


def climate_options(raw: Any, temp_range: tuple[float, ...] = TEMP_RANGE_NEW,
                    dial: tuple[float, float] = (DIAL_MIN, DIAL_MAX)) -> dict[str, Any]:
    """Validate climate options; returns a clean copy (only the keys given)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise bad("Climate options must be an object.")
    unknown = sorted(set(raw) - set(CLIMATE_KEYS))
    if unknown:
        raise bad(f"Unknown climate option: {unknown[0]}.")
    out: dict[str, Any] = {}
    if raw.get("set_temp_c") is not None:
        temp = raw["set_temp_c"]
        if temp in TEMP_ENDS:
            out["set_temp_c"] = temp
        else:
            low, high = dial
            match = next((t for t in temp_range if _is_number(temp) and abs(t - temp) < 1e-9
                          and low <= t <= high), None)
            if match is None:
                raise bad(f"Temperature must be LO, {_fmt(low)} to {_fmt(high)} C in 0.5 steps, or HI.")
            out["set_temp_c"] = int(match) if match == int(match) else match
    if raw.get("duration_min") is not None:
        duration = raw["duration_min"]
        if not _is_int(duration) or not 1 <= duration <= 10:
            raise bad("Duration must be a whole number of minutes from 1 to 10.")
        out["duration_min"] = duration
    if raw.get("defrost") is not None:
        if not isinstance(raw["defrost"], bool):
            raise bad("Defrost must be true or false.")
        out["defrost"] = raw["defrost"]
    if raw.get("heating") is not None:
        if raw["heating"] not in HEATING or isinstance(raw["heating"], bool):
            raise bad("Heating must be 0 (off), 1 (steering wheel, rear window and mirrors), "
                      "2 (rear window and mirrors) or 3 (steering wheel).")
        out["heating"] = raw["heating"]
    for key in SEAT_KEYS:
        if raw.get(key) is not None:
            level = raw[key]
            if not _is_int(level) or level not in SEAT_LEVELS:
                raise bad("Seat levels are 0 (off), 6, 7, 8 (heat low to high) or 3, 4, 5 (cool low to high).")
            out[key] = level
    return out


def command(body: Any, temp_range: tuple[float, ...] = TEMP_RANGE_NEW,
            dial: tuple[float, float] = (DIAL_MIN, DIAL_MAX)) -> tuple[str, dict[str, Any] | None]:
    if not isinstance(body, dict):
        raise bad("Send a JSON object with an action.")
    action = body.get("action")
    if action not in ACTIONS:
        raise bad("Unknown action. Use lock, unlock, climate_start, climate_stop, hazard or horn_lights.")
    if action == "climate_start":
        return action, climate_options(body.get("options"), temp_range, dial)
    return action, None


def presets(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("presets"), list):
        raise bad("Send {\"presets\": [...]}.")
    items = body["presets"]
    if len(items) > 30:
        raise bad("Keep it to 30 presets or fewer.")
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise bad("Each preset must be an object.")
        pid, name, icon = item.get("id"), item.get("name"), item.get("icon")
        if not isinstance(pid, str) or not pid.strip() or len(pid) > 64:
            raise bad("Each preset needs an id.")
        if pid in seen:
            raise bad("Two presets share the same id.")
        seen.add(pid)
        if not isinstance(name, str) or not name.strip() or len(name) > 60:
            raise bad("Each preset needs a name (up to 60 characters).")
        if icon is not None and (not isinstance(icon, str) or len(icon) > 40):
            raise bad("A preset icon must be a short text.")
        try:
            options = climate_options(item.get("options") or {})
        except ServiceError as err:
            raise bad(f"Preset \"{name.strip()}\": {err.message}") from None
        out.append({"id": pid, "name": name.strip(), "icon": icon, "options": options})
    return out


def login(body: Any) -> tuple[str, str, str]:
    if not isinstance(body, dict):
        raise bad("Send email, password and PIN.")
    email, password, pin = body.get("email"), body.get("password"), body.get("pin")
    if not isinstance(email, str) or not _EMAIL.match(email.strip()):
        raise bad("That email address doesn't look right.")
    if not isinstance(password, str) or not password:
        raise bad("Enter the Kia Connect password.")
    if len(password) > 200 or any(not c.isprintable() for c in password):
        raise bad("The password contains an invisible character (often from pasting). Type it again.")
    if not isinstance(pin, str) or not (pin.isdigit() and len(pin) == 4):
        raise bad("The PIN is the 4-digit Kia Connect PIN.")
    return email.strip(), password, pin


def otp_method(body: Any) -> str:
    method = body.get("method") if isinstance(body, dict) else None
    if method not in ("EMAIL", "SMS"):
        raise bad("Choose EMAIL or SMS for the code.")
    return method


def otp_code(body: Any) -> str:
    code = body.get("code") if isinstance(body, dict) else None
    if not isinstance(code, str) or not code.strip().isdigit() or not 4 <= len(code.strip()) <= 8:
        raise bad("Enter the code from the email or text message (digits only).")
    return code.strip()


def refresh_force(body: Any) -> bool:
    if body is None:
        return False
    if not isinstance(body, dict):
        raise bad("Send {\"force\": true} or {\"force\": false}.")
    force = body.get("force", False)
    if not isinstance(force, bool):
        raise bad("force must be true or false.")
    return force
