"""A fake car (a Sportage): no keyring, no Kia. Commands go pending, then succeed and change the state."""

from __future__ import annotations

import copy
import datetime as dt
import threading
import time
from typing import Any

from hyundai_kia_connect_api.const import SEAT_STATUS

from .errors import ServiceError
from .timeutil import iso
from .validation import DIAL_MAX, DIAL_MIN, SEAT_KEYS, TEMP_RANGE_NEW

_SEAT_STATE_KEYS = dict(zip(SEAT_KEYS, ("front_left", "front_right", "rear_left", "rear_right")))
DEMO_OTP_FAIL_CODE = "000000"   # lets the dashboard show a wrong-code error
DEMO_PIN = "1234"               # the demo car's PIN for unlock (no keyring in demo mode)
DEMO_EMAIL = "demo@example.com"   # approving a device before any demo sign-in
DEMO_PASSWORD = "demo"


def _system(name: str, code: str, status: str = "G") -> dict[str, str]:
    return {"name": name, "code": code, "status": status}


# Kia's monthly report, in Kia's own format (see server/health.py). One system needs a look,
# so the dashboard's warning row can be seen in demo mode.
DEMO_HEALTH_REPORT: dict[str, Any] = {
    "reportId": 1, "reportDate": "2026-10-25", "odometer": 12873.0, "odometerUnit": 1,
    "systems": [
        {"name": "CHASSIS", "subSystems": [
            _system("Electronic Parking Brake", "EPB"), _system("Electronic Power Steering", "EPS"),
            _system("Electronic Stability Control", "ESC"),
            _system("Lane Departure Warning System", "LKAS"), _system("Smart Cruise Control", "SCC"),
            _system("Tire Pressure Monitoring System", "TPMS", "Y")]},
        {"name": "HEV", "subSystems": [
            _system("Battery Management System", "BMS"), _system("Hybrid Control Unit", "HCULDC"),
            _system("Microprocessor Control Unit", "MCU")]},
        {"name": "POWERTRAIN", "subSystems": [
            _system("Engine Management System", "EMS"), _system("Transmission Control System", "AT")]},
        {"name": "SAFETY", "subSystems": [_system("Airbag System", "Airbag")]},
    ],
    "drivingInfo": {"startsCount": 96, "runTime": 112380, "runTimeUnit": 3,
                    "mileageTime": 86100, "mileageTimeUnit": 3,
                    "idleEngineTime": 26280, "idleEngineTimeUnit": 3,
                    "runDistance": 1312, "runDistanceUnit": 1},
}


def _initial_vehicle() -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC)
    door = {"open": False, "locked": True}
    return {
        "name": "SPORTAGE", "model": "SPORTAGE", "year": 2026,
        "updated_at": iso(now - dt.timedelta(minutes=23)),
        "locked": True,
        "doors": {k: dict(door) for k in ("front_left", "front_right", "rear_left", "rear_right")},
        "trunk_open": False, "hood_open": False, "sunroof_open": False,
        "windows": {"front_left": False, "front_right": False, "rear_left": False, "rear_right": False},
        "engine_on": False,
        "fuel_percent": 72, "range_km": 540, "odometer_km": 8423,
        "battery_12v_percent": 88,
        "climate": {"on": False, "set_temp_c": 21, "defrost": False,
                    "steering_heat": False, "rear_window_heat": False, "mirror_heat": False},
        "seats": {"front_left": "Off", "front_right": "Off", "rear_left": "Off", "rear_right": "Off"},
        "outside_temp_c": -4,
        "tires": {"warning": False, "front_left": False, "front_right": False,
                  "rear_left": False, "rear_right": False},
        "warnings": {"washer_fluid": False, "brake_fluid": False, "oil": False,
                     "smart_key_battery": False, "aux_battery": False},
        "location": {"lat": 45.4215, "lon": -75.6972, "updated_at": iso(now - dt.timedelta(minutes=23))},
        "next_service_km": 6577,
    }


class DemoCar:
    demo = True
    poll_interval = 0.5
    command_timeout = 30.0

    def __init__(self, command_delay: float = 3.0, refresh_delay: float = 2.0) -> None:
        self.command_delay = command_delay
        self.poll_interval = max(0.05, min(0.5, command_delay / 4))
        self.refresh_delay = refresh_delay
        self.auth = "ok"
        self.auth_error: str | None = None
        self.otp: dict[str, bool] | None = None
        self._otp_sent = False
        self._vehicle = _initial_vehicle()
        self._climate_until: float | None = None
        self._lock = threading.Lock()

    # Auth (pretends, to exercise the dashboard's sign-in screens)

    def startup(self) -> None:
        pass

    def login(self, email: str, password: str, pin: str) -> None:
        self._account = (email, password, pin)
        self.auth, self.auth_error = "needs_otp", None
        self.otp = {"has_email": True, "has_sms": True}
        self._otp_sent = False

    def otp_send(self, method: str) -> None:
        if self.auth != "needs_otp":
            raise ServiceError(409, "No sign-in is waiting for a code.")
        self._otp_sent = True

    def otp_verify(self, code: str) -> None:
        if self.auth != "needs_otp":
            raise ServiceError(409, "No sign-in is waiting for a code.")
        if not self._otp_sent:
            raise ServiceError(400, "Send a code first.")
        if code == DEMO_OTP_FAIL_CODE:
            raise ServiceError(400, "That code didn't work. Check it, or send a new one.")
        self.auth, self.otp = "ok", None

    def logout(self) -> None:
        self.auth, self.auth_error, self.otp = "needs_login", None, None

    def saved_pin(self) -> str | None:
        return DEMO_PIN

    def saved_credentials(self) -> tuple[str, str, str] | None:
        """Approving a device in demo mode: the last sign-in, else demo@example.com / demo / 1234."""
        return getattr(self, "_account", None) or (DEMO_EMAIL, DEMO_PASSWORD, DEMO_PIN)

    def health_report(self) -> dict[str, Any] | None:
        self.require_ok()
        return copy.deepcopy(DEMO_HEALTH_REPORT)

    def require_ok(self) -> None:
        if self.auth != "ok":
            raise ServiceError(401, "Sign in to Kia Connect first.")

    def dial(self) -> tuple[float, float]:
        return DIAL_MIN, DIAL_MAX

    def temp_range(self) -> tuple[float, ...]:
        return TEMP_RANGE_NEW

    # State

    def _expire_climate(self) -> None:
        if self._climate_until is not None and time.monotonic() >= self._climate_until:
            self._climate_off()

    def _climate_off(self) -> None:
        v = self._vehicle
        v["climate"].update(on=False, defrost=False, steering_heat=False,
                            rear_window_heat=False, mirror_heat=False)
        v["seats"] = {k: "Off" for k in v["seats"]}
        v["engine_on"] = False
        self._climate_until = None

    def vehicle(self) -> dict[str, Any] | None:
        if self.auth != "ok":
            return None
        with self._lock:
            self._expire_climate()
            return copy.deepcopy(self._vehicle)

    # Vehicles (one demo car; the picker only shows when there are two or more)

    def vehicles(self) -> list[dict[str, Any]]:
        self.require_ok()
        v = self._vehicle
        return [{"id": "demo-1", "name": v["name"], "model": v["model"], "year": v["year"],
                 "active": True}]

    def select_vehicle(self, vehicle_id: str) -> None:
        self.require_ok()
        if vehicle_id != "demo-1":
            raise ServiceError(404, "No such car on this Kia account.")

    def refresh(self, force: bool) -> None:
        self.require_ok()
        if force:
            time.sleep(self.refresh_delay)
            with self._lock:
                now = dt.datetime.now(dt.UTC)
                self._vehicle["updated_at"] = iso(now)
                self._vehicle["location"]["updated_at"] = iso(now)

    # Commands

    def send_command(self, action: str, options: dict[str, Any] | None) -> Any:
        self.require_ok()
        return {"action": action, "options": options or {}, "sent": time.monotonic()}

    def check_command(self, handle: dict[str, Any]) -> tuple[str, str | None]:
        if time.monotonic() - handle["sent"] < self.command_delay:
            return "pending", None
        self._apply(handle["action"], handle["options"])
        return "success", None

    def after_success(self) -> None:
        pass   # the fake state already changed in _apply

    def _apply(self, action: str, options: dict[str, Any]) -> None:
        with self._lock:
            v = self._vehicle
            if action in ("lock", "unlock"):
                locked = action == "lock"
                v["locked"] = locked
                for door in v["doors"].values():
                    door["locked"] = locked
            elif action == "climate_start":
                heating = options.get("heating", 0)
                wheel, rear = heating in (1, 3), heating in (1, 2)
                v["climate"].update(
                    on=True,
                    set_temp_c=options.get("set_temp_c", 21),
                    defrost=bool(options.get("defrost", False)),
                    steering_heat=wheel, rear_window_heat=rear, mirror_heat=rear,
                )
                for key, state_key in _SEAT_STATE_KEYS.items():
                    v["seats"][state_key] = SEAT_STATUS.get(options.get(key, 0)) or "Off"
                v["engine_on"] = True
                self._climate_until = time.monotonic() + 60 * options.get("duration_min", 5)
            elif action == "climate_stop":
                self._climate_off()
            v["updated_at"] = iso(dt.datetime.now(dt.UTC))
