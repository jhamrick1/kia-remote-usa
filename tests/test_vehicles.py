"""Two cars on one Kia account: the vehicle list and the picker (docs/api-contract.md)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from server.app import create_app
from server.kia import KiaCar
from server.secrets_store import SecretStore
from conftest import EMAIL, OTP_CODE, PASSWORD, PIN

CREDS = {"email": EMAIL, "password": PASSWORD, "pin": PIN}


def make_client(tmp_path, vm_factory):
    backend = KiaCar(SecretStore(), vm_factory=vm_factory)
    backend.poll_interval = 0.05
    app = create_app(demo=False, data_dir=tmp_path / "data", backend=backend,
                     web_dir=tmp_path / "web")
    return TestClient(app, client=("127.0.0.1", 50000))


def sign_in(client):
    client.post("/api/login", json=CREDS)
    client.post("/api/otp/send", json={"method": "EMAIL"})
    s = client.post("/api/otp/verify", json={"code": OTP_CODE}).json()
    assert s["auth"] == "ok"
    return s


def test_lists_both_cars_with_the_active_one_marked(tmp_path, memory_keyring, vm_factory):
    with make_client(tmp_path, vm_factory) as c:
        assert c.get("/api/vehicles").status_code == 401   # signed out
        sign_in(c)
        res = c.get("/api/vehicles").json()
        assert [(v["id"], v["name"]) for v in res["vehicles"]] == [
            ("veh-1", "SPORTAGE"), ("veh-2", "TELLURIDE")]
        assert [v["active"] for v in res["vehicles"]] == [True, False]   # the first car by default
        assert res["vehicles"][0]["model"] == "SPORTAGE" and res["vehicles"][0]["year"] == 2026


def test_select_switches_the_state_and_the_commands(tmp_path, memory_keyring, vm_factory, world):
    with make_client(tmp_path, vm_factory) as c:
        sign_in(c)
        world.odometer = {"veh-2": 4321.0}
        s = c.post("/api/vehicle/select", json={"vehicle_id": "veh-2"}).json()
        assert s["vehicle"]["model"] == "TELLURIDE" and s["vehicle"]["odometer_km"] == 4321
        assert [v["active"] for v in c.get("/api/vehicles").json()["vehicles"]] == [False, True]
        # A forced refresh now wakes the selected car (and the odometer proves whose it was).
        s = c.post("/api/refresh", json={"force": True}).json()
        assert s["vehicle"]["model"] == "TELLURIDE" and s["vehicle"]["odometer_km"] == 4321


def test_select_refuses_unknown_cars_and_bad_bodies(tmp_path, memory_keyring, vm_factory):
    with make_client(tmp_path, vm_factory) as c:
        sign_in(c)
        assert c.post("/api/vehicle/select", json={"vehicle_id": "nope"}).status_code == 404
        assert c.post("/api/vehicle/select", json={}).status_code == 400
        assert c.post("/api/vehicle/select", json={"vehicle_id": 3}).status_code == 400
        assert c.post("/api/vehicle/select", json={"vehicle_id": " veh-2 "}).status_code == 200


def test_selection_survives_a_restart(tmp_path, memory_keyring, vm_factory, world):
    with make_client(tmp_path, vm_factory) as c:
        sign_in(c)
        c.post("/api/vehicle/select", json={"vehicle_id": "veh-2"})
    calls = world.login_calls
    with make_client(tmp_path, vm_factory) as c:   # token reuse: no new sign-in
        s = c.get("/api/state").json()
        assert s["auth"] == "ok" and s["vehicle"]["model"] == "TELLURIDE"
        assert [(v["id"], v["active"]) for v in c.get("/api/vehicles").json()["vehicles"]] == [
            ("veh-1", False), ("veh-2", True)]
    assert world.login_calls == calls


def test_demo_has_the_endpoints_too(tmp_path):
    app = create_app(demo=True, data_dir=tmp_path / "data", web_dir=tmp_path / "web")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        res = c.get("/api/vehicles").json()
        assert [(v["id"], v["active"]) for v in res["vehicles"]] == [("demo-1", True)]
        assert c.post("/api/vehicle/select", json={"vehicle_id": "demo-1"}).status_code == 200
        assert c.post("/api/vehicle/select", json={"vehicle_id": "other"}).status_code == 404
