"""Demo mode end to end through FastAPI's TestClient, plus validation and limits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from conftest import wait_for_command

VEHICLE_KEYS = {
    "name", "model", "year", "updated_at", "locked", "doors", "trunk_open", "hood_open",
    "sunroof_open", "windows", "engine_on", "fuel_percent", "range_km", "odometer_km",
    "battery_12v_percent", "climate", "seats", "outside_temp_c", "tires", "warnings",
    "location", "next_service_km",
}


@pytest.fixture
def client(tmp_path, forbidden_keyring):
    web = tmp_path / "web-missing"
    app = create_app(demo=True, data_dir=tmp_path / "data", demo_delay=0.2,
                     demo_refresh_delay=0.01, web_dir=web)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        yield c


def test_state_has_the_contract_shape(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    s = r.json()
    assert set(s) == {"demo", "auth", "auth_error", "otp", "vehicle", "pending", "dial"}
    assert s["demo"] is True and s["auth"] == "ok" and s["pending"] is None
    v = s["vehicle"]
    assert set(v) == VEHICLE_KEYS
    assert v["model"] == "SPORTAGE" and v["locked"] is True
    assert 60 <= v["fuel_percent"] <= 80 and 500 <= v["range_km"] <= 580
    assert v["outside_temp_c"] == -4
    assert set(v["doors"]["front_left"]) == {"open", "locked"}
    assert set(v["climate"]) == {"on", "set_temp_c", "defrost", "steering_heat",
                                 "rear_window_heat", "mirror_heat"}
    assert v["tires"]["rear_left"] is False and v["tires"]["warning"] is False
    assert 45 < v["location"]["lat"] < 46 and -76 < v["location"]["lon"] < -75


def test_unlock_then_lock(client):
    r = client.post("/api/command", json={"action": "unlock", "pin": "1234"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending" and body["id"].startswith("cmd_")
    assert client.get("/api/state").json()["pending"]["id"] == body["id"]
    entry = wait_for_command(client, body["id"])
    assert entry["status"] == "success" and entry["finished_at"]
    state = client.get("/api/state").json()
    assert state["pending"] is None
    assert state["vehicle"]["locked"] is False
    assert state["vehicle"]["doors"]["rear_right"]["locked"] is False

    cid = client.post("/api/command", json={"action": "lock"}).json()["id"]
    wait_for_command(client, cid)
    assert client.get("/api/state").json()["vehicle"]["locked"] is True


def test_climate_start_and_stop(client):
    options = {"set_temp_c": 22.5, "duration_min": 10, "defrost": True, "heating": 1,
               "front_left_seat": 8, "front_right_seat": 3}
    cid = client.post("/api/command", json={"action": "climate_start", "options": options}).json()["id"]
    entry = wait_for_command(client, cid)
    assert entry["status"] == "success"
    assert entry["options"]["set_temp_c"] == 22.5
    v = client.get("/api/state").json()["vehicle"]
    assert v["climate"]["on"] is True and v["climate"]["set_temp_c"] == 22.5
    assert v["climate"]["defrost"] is True and v["climate"]["steering_heat"] is True
    assert v["seats"]["front_left"] == "High Heat" and v["seats"]["front_right"] == "Low Cool"
    assert v["engine_on"] is True

    cid = client.post("/api/command", json={"action": "climate_stop"}).json()["id"]
    wait_for_command(client, cid)
    v = client.get("/api/state").json()["vehicle"]
    assert v["climate"]["on"] is False and v["engine_on"] is False
    assert v["seats"]["front_left"] == "Off"


def test_hazard_and_horn_succeed_in_demo(client):
    for action in ("hazard", "horn_lights"):
        cid = client.post("/api/command", json={"action": action}).json()["id"]
        assert wait_for_command(client, cid)["status"] == "success"


def test_one_command_at_a_time(client):
    first = client.post("/api/command", json={"action": "lock"})
    assert first.status_code == 200
    second = client.post("/api/command", json={"action": "unlock", "pin": "1234"})
    assert second.status_code == 409
    assert "error" in second.json()
    wait_for_command(client, first.json()["id"])
    assert client.post("/api/command", json={"action": "unlock", "pin": "1234"}).status_code == 200


def test_log_newest_first(client):
    ids = []
    for action in ("lock", "unlock"):
        cid = client.post("/api/command", json={"action": action, "pin": "1234"}).json()["id"]
        wait_for_command(client, cid)
        ids.append(cid)
    entries = client.get("/api/log").json()["entries"]
    assert [e["id"] for e in entries[:2]] == list(reversed(ids))
    assert set(entries[0]) == {"id", "action", "options", "status", "message", "sent_at", "finished_at",
                               "who", "via"}
    assert entries[0]["via"] == "pin" and entries[1]["via"] is None


@pytest.mark.parametrize("options", [
    {"set_temp_c": 13.5}, {"set_temp_c": 32}, {"set_temp_c": 22.3}, {"set_temp_c": "22"},
    {"set_temp_c": 31.5}, {"set_temp_c": 16.5}, {"set_temp_c": "hi"},
    {"set_temp_c": True}, {"duration_min": 0}, {"duration_min": 11}, {"duration_min": 5.5},
    {"front_left_seat": 2}, {"heating": 4}, {"heating": True}, {"defrost": "yes"}, {"turbo": 1},
])
def test_bad_climate_options(client, options):
    r = client.post("/api/command", json={"action": "climate_start", "options": options})
    assert r.status_code == 400
    assert isinstance(r.json()["error"], str) and r.json()["error"]
    assert client.get("/api/state").json()["pending"] is None


def test_temperature_edges_are_accepted(client):
    for temp in ("LO", 17, 27, "HI"):
        cid = client.post("/api/command", json={"action": "climate_start",
                                                "options": {"set_temp_c": temp}}).json()["id"]
        wait_for_command(client, cid)


def test_bad_requests(client):
    assert client.post("/api/command", json={"action": "launch"}).status_code == 400
    r = client.post("/api/command", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]
    assert client.get("/api/command/cmd_nope").status_code == 404
    assert client.get("/api/nothing").status_code == 404
    assert client.post("/api/refresh", json={"force": "yes"}).status_code == 400


def test_forced_refresh_once_per_10_minutes(client):
    before = client.get("/api/state").json()["vehicle"]["updated_at"]
    r = client.post("/api/refresh", json={"force": True})
    assert r.status_code == 200
    assert r.json()["vehicle"]["updated_at"] >= before
    again = client.post("/api/refresh", json={"force": True})
    assert again.status_code == 429
    assert "10 minutes" in again.json()["error"]
    # A normal refresh is always fine.
    assert client.post("/api/refresh", json={"force": False}).status_code == 200
    assert client.post("/api/refresh").status_code == 200


def test_presets_defaults_and_save(client):
    presets = client.get("/api/presets").json()["presets"]
    names = [p["name"] for p in presets]
    assert names == ["Winter morning", "Quick warm", "Summer cool"]
    winter = presets[0]["options"]
    assert winter == {"set_temp_c": 22, "defrost": True, "heating": 1, "front_left_seat": 8,
                      "duration_min": 10}
    assert presets[2]["options"]["front_left_seat"] == 3

    new = presets + [{"id": "p4", "name": "Hot day", "icon": "cool",
                      "options": {"set_temp_c": "LO", "front_left_seat": 5}}]
    r = client.put("/api/presets", json={"presets": new})
    assert r.status_code == 200 and len(r.json()["presets"]) == 4
    assert client.get("/api/presets").json()["presets"][3]["name"] == "Hot day"

    bad = [{"id": "x", "name": "Bad", "icon": None, "options": {"set_temp_c": 40}}]
    r = client.put("/api/presets", json={"presets": bad})
    assert r.status_code == 400 and "Bad" in r.json()["error"]
    dup = [{"id": "x", "name": "A", "options": {}}, {"id": "x", "name": "B", "options": {}}]
    assert client.put("/api/presets", json={"presets": dup}).status_code == 400


def test_demo_sign_in_screens(client):
    s = client.post("/api/logout").json()
    assert s["auth"] == "needs_login" and s["vehicle"] is None
    assert client.post("/api/command", json={"action": "lock"}).status_code == 401
    assert client.post("/api/login", json={"email": "x@y.ca", "password": "pw", "pin": "12"}).status_code == 400
    s = client.post("/api/login", json={"email": "x@y.ca", "password": "pw", "pin": "1234"}).json()
    assert s["auth"] == "needs_otp" and s["otp"] == {"has_email": True, "has_sms": True}
    assert client.post("/api/otp/send", json={"method": "FAX"}).status_code == 400
    assert client.post("/api/otp/send", json={"method": "EMAIL"}).json() == {"sent": True}
    assert client.post("/api/otp/verify", json={"code": "000000"}).status_code == 400
    s = client.post("/api/otp/verify", json={"code": "123456"}).json()
    assert s["auth"] == "ok" and s["vehicle"]["model"] == "SPORTAGE"


def test_static_placeholder_and_files(tmp_path, forbidden_keyring):
    web = tmp_path / "web"
    with TestClient(create_app(demo=True, data_dir=tmp_path / "d", web_dir=web), client=("127.0.0.1", 50000)) as c:
        r = c.get("/")
        assert r.status_code == 200 and "isn't" in r.text
    web.mkdir()
    (web / "index.html").write_text("<h1>dash</h1>", encoding="utf-8")
    (web / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    with TestClient(create_app(demo=True, data_dir=tmp_path / "d", web_dir=web), client=("127.0.0.1", 50000)) as c:
        assert c.get("/").text == "<h1>dash</h1>"
        r = c.get("/app.js")
        assert r.status_code == 200 and "javascript" in r.headers["content-type"]
        assert "no-store" not in r.headers.get("cache-control", "")
        assert c.get("/..%2Fsecret.txt").status_code == 404
        assert c.get("/missing.css").status_code == 404


def test_security_headers_and_dev_tools_hidden(client):
    r = client.get('/api/state')
    assert "default-src 'self'" in r.headers['content-security-policy']
    assert client.get('/dev/mock-server.mjs').status_code == 404
    assert client.get('/dev/').status_code == 404


@pytest.mark.parametrize("heating,wheel,rear", [(0, False, False), (1, True, True), (2, False, True), (3, True, False)])
def test_heating_codes_split_wheel_and_rear(client, heating, wheel, rear):
    r = client.post("/api/command", json={"action": "climate_start", "options": {"heating": heating}})
    assert r.status_code == 200
    cmd_id = r.json()["id"]
    import time
    for _ in range(100):
        if client.get(f"/api/command/{cmd_id}").json()["status"] != "pending":
            break
        time.sleep(0.1)
    climate = client.get("/api/state").json()["vehicle"]["climate"]
    assert (climate["steering_heat"], climate["rear_window_heat"], climate["mirror_heat"]) == (wheel, rear, rear)


def test_diag_events_land_in_the_server_log(client, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="server.app")
    r = client.post("/api/diag", json={"events": [
        {"t": "2026-11-17T14:00:00Z", "e": "api-fail", "d": "state timeout 6001ms\nforged line"}]})
    assert r.status_code == 200
    lines = [m for m in caplog.messages if m.startswith("phone:")]
    assert lines == ["phone: 2026-11-17T14:00:00Z api-fail state timeout 6001ms forged line"]
    assert client.post("/api/diag", json={"nope": 1}).status_code == 400


def test_presets_saved_past_the_dial_read_back_as_hi_or_lo(tmp_path):
    import json
    from server.storage import Storage
    (tmp_path / "presets.json").write_text(json.dumps({"presets": [
        {"id": "a", "name": "Winter", "options": {"set_temp_c": 31.5}},
        {"id": "b", "name": "Cold", "options": {"set_temp_c": 14}},
        {"id": "c", "name": "Mid", "options": {"set_temp_c": 22}}]}), encoding="utf-8")
    temps = [p["options"]["set_temp_c"] for p in Storage(tmp_path, False).load_presets()]
    assert temps == [27, 17, 22]


def test_app_name_from_settings_names_the_home_screen_icon(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "manifest.webmanifest").write_text('{"name": "Car Remote", "short_name": "Car Remote"}', encoding="utf-8")
    plain = TestClient(create_app(demo=True, data_dir=tmp_path / "d1", web_dir=web, settings={}), client=("127.0.0.1", 50000))
    assert plain.get("/manifest.webmanifest").json()["name"] == "Car Remote"
    named = TestClient(create_app(demo=True, data_dir=tmp_path / "d2", web_dir=web, settings={"app_name": "Sportage"}), client=("127.0.0.1", 50000))
    m = named.get("/manifest.webmanifest").json()
    assert m["name"] == m["short_name"] == "Sportage"
