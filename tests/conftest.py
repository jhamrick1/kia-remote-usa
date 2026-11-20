"""Shared fixtures: an in-memory keyring and a fake Kia Connect Canada API. No real Kia calls."""

from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hyundai_kia_connect_api import VehicleManager  # noqa: E402
from hyundai_kia_connect_api.ApiImpl import OTPRequest  # noqa: E402
from hyundai_kia_connect_api.const import ENGINE_TYPES, ORDER_STATUS  # noqa: E402
from hyundai_kia_connect_api.exceptions import (  # noqa: E402
    AuthenticationBlockedError,
    AuthenticationInvalidCredentialsError,
)
from hyundai_kia_connect_api.Token import Token  # noqa: E402
from hyundai_kia_connect_api.Vehicle import Vehicle  # noqa: E402

from server.secrets_store import REDACTOR  # noqa: E402

# Fake account values. Distinctive so a leak is easy to spot.
EMAIL = "owner.fake@example.test"
PASSWORD = "Pw-SECRET-ZQX-4471"
PIN = "8642"
OTP_CODE = "135790"


class MemoryKeyring(KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.data.get((service, username))

    def set_password(self, service, username, password):
        self.data[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.data:
            raise PasswordDeleteError("missing")
        del self.data[(service, username)]


class ForbiddenKeyring(KeyringBackend):
    """Demo mode must never touch the keyring."""
    priority = 1  # type: ignore[assignment]

    def get_password(self, service, username):
        raise AssertionError("keyring used")

    def set_password(self, service, username, password):
        raise AssertionError("keyring used")

    def delete_password(self, service, username):
        raise AssertionError("keyring used")


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    REDACTOR.clear()
    yield backend
    keyring.set_keyring(previous)
    REDACTOR.clear()


@pytest.fixture
def forbidden_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(ForbiddenKeyring())
    yield
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_kia_remote", False):
            root.removeHandler(handler)
            handler.close()


class FakeKiaWorld:
    """What 'Kia' remembers between app restarts in one test."""

    def __init__(self) -> None:
        self.known_devices: set[str] = set()
        self.login_calls = 0
        self.blocked = False
        self.access_counter = 0
        self.token_lifetime = dt.timedelta(hours=1)
        self.action_status: list[ORDER_STATUS] = [ORDER_STATUS.PENDING, ORDER_STATUS.SUCCESS]
        self.commands: list[str] = []
        self.posts: list[str] = []      # bodies Kia received for climate starts
        self.vehicle_lists = 0          # vehicle list reads (model and paint codes)
        self.paint = "HRB"
        self.locked = True
        self.health_report: dict | None = None   # Kia's monthly report; None: there isn't one yet


class FakeSession:
    def __init__(self, world: FakeKiaWorld) -> None:
        self.world = world

    def post(self, url, headers=None, data=None):
        if url.endswith("vhcllst"):
            self.world.vehicle_lists += 1
            return FakeResponse({"result": {"vehicles": [
                {"vehicleId": "veh-1", "modelCode": "JWWDD5G1GEV1GG749", "modelYear": "2026",
                 "exteriorColor": self.world.paint}]}})
        self.world.posts.append(data)


class FakeResponse:
    def __init__(self, body) -> None:
        self.body = body

    def json(self):
        return self.body


class FakeApi:
    API_URL = "https://kia.example/tods/api/"
    API_HEADERS = {"from": "fake"}
    temperature_range_c_old = tuple(x * 0.5 for x in range(32, 64))
    temperature_range_c_new = tuple(x * 0.5 for x in range(28, 64))
    temperature_range_model_year = 2020

    def __init__(self, world: FakeKiaWorld) -> None:
        self.world = world
        self._device_id: str | None = None
        self.lib_log = logging.getLogger("hyundai_kia_connect_api.fake")
        self.sessions = FakeSession(world)

    def _get_device_id(self) -> str:
        if self._device_id is None:
            self._device_id = "device-abc"
        return self._device_id

    def _token(self, username, password, pin) -> Token:
        self.world.access_counter += 1
        return Token(username=username, password=password, pin=pin,
                     access_token=f"ACCESS-TOKEN-{self.world.access_counter}-XYZ",
                     refresh_token="REFRESH-TOKEN-QWE-998",
                     device_id=self._get_device_id(),
                     valid_until=dt.datetime.now(dt.UTC) + self.world.token_lifetime)

    def login(self, username, password, pin=None):
        self.world.login_calls += 1
        # A careless library line, to prove the log filter hides secrets.
        self.lib_log.warning("login attempt %s / %s / %s", username, password, pin)
        if self.world.blocked:
            raise AuthenticationBlockedError("Account locked out")
        if password != PASSWORD:
            raise AuthenticationInvalidCredentialsError("Wrong username or password")
        if self._get_device_id() not in self.world.known_devices:
            return OTPRequest(request_id="req-1", otp_key=None, has_email=True, has_sms=False,
                              email=username, sms=None)
        return self._token(username, password, pin)

    def refresh_access_token(self, token):
        if token.device_id:
            self._device_id = token.device_id
        return self.login(token.username, token.password, token.pin)

    def test_token(self, token):
        return token.access_token is not None and token.valid_until > dt.datetime.now(dt.UTC)

    def refresh_vehicles(self, token, vehicles):
        return None

    def send_otp(self, otp_request, notify_type):
        otp_request.otp_key = "otp-key-1"

    def verify_otp_and_complete_login(self, username, password, otp_code, otp_request, pin):
        if otp_code != OTP_CODE:
            from hyundai_kia_connect_api.exceptions import AuthenticationError
            raise AuthenticationError("OTP verification failed: Invalid OTP code")
        self.world.known_devices.add(self._get_device_id())
        return self._token(username, password, pin)

    def get_vehicles(self, token):
        v = Vehicle(id="veh-1", name="SPORTAGE", model="SPORTAGE", year=2026,
                    VIN="FAKEVIN0000000000", engine_type=ENGINE_TYPES.ICE)
        return [v]

    def _fill(self, token, vehicle):
        self.lib_log.warning("status call with token %s", token.access_token)
        vehicle.last_updated_at = dt.datetime(2026, 11, 15, 22, 6, 23, tzinfo=dt.UTC)
        vehicle.is_locked = self.world.locked
        vehicle.fuel_level = 71.5
        vehicle.fuel_driving_range = (533, "km")
        vehicle.total_driving_range = (None, None)
        vehicle.odometer = (1234.0, "km")
        vehicle.car_battery_percentage = 90
        vehicle.engine_is_running = False
        vehicle.front_left_door_is_open = 0
        vehicle.air_temperature = (21.0, "c")
        vehicle.air_control_is_on = False
        vehicle.front_left_seat_status = "Off"
        vehicle.next_service_distance = (8000, "km")
        vehicle.location = (45.0, -75.0, dt.datetime(2026, 11, 15, 22, 0, tzinfo=dt.UTC))

    def update_vehicle_with_cached_state(self, token, vehicle):
        self._fill(token, vehicle)
        if self.world.health_report is not None:   # the real library reads it here too
            self._get_mvr_report(token, vehicle, self.world.health_report.get("reportId"))

    def _get_mvr_report(self, token, vehicle, report_id):
        return self.world.health_report

    def force_refresh_vehicle_state(self, token, vehicle):
        self._fill(token, vehicle)

    def lock_action(self, token, vehicle, action):
        self.world.commands.append(f"lock:{action.name}")
        self.world.locked = action.name == "LOCK"
        return "tx-1"

    def start_climate(self, token, vehicle, options):
        # Same check the real library makes.
        index = self.temperature_range_c_new.index(options.set_temp)
        self.world.commands.append(f"climate:{options.set_temp}")
        import json
        body = {"setting": {"airTemp": {"value": (hex(index).split("x")[1] + "H").zfill(3).upper(),
                                        "unit": 0, "hvacTempType": 0}}, "pin": "x"}
        self.sessions.post("rmtstrt", headers={}, data=json.dumps(body))
        return "tx-2"

    def stop_climate(self, token, vehicle):
        return "tx-3"

    def hornlight(self, token, vehicle, horn):
        self.world.commands.append(f"hornlight:{horn}")
        return "tx-4"

    def check_action_status(self, token, vehicle, action_id, synchronous=False, timeout=0):
        if len(self.world.action_status) > 1:
            return self.world.action_status.pop(0)
        return self.world.action_status[0]


@pytest.fixture
def world():
    return FakeKiaWorld()


@pytest.fixture
def vm_factory(world):
    def make(**kwargs):
        vm = VehicleManager(**kwargs)   # the real manager, so its token logic is exercised
        vm.api = FakeApi(world)
        return vm
    return make


def wait_for_command(client, cid, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = client.get(f"/api/command/{cid}").json()
        if entry["status"] != "pending":
            return entry
        time.sleep(0.05)
    raise AssertionError("command still pending")
