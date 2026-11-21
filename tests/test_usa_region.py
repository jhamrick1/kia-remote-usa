"""The USA port: facts taken from hyundai_kia_connect_api 5.2.1's KiaUvoApiUSA."""

from __future__ import annotations

from hyundai_kia_connect_api.KiaUvoApiUSA import KiaUvoApiUSA

from server import regions
from server.regions.usa import Usa


def test_usa_is_registered_with_the_right_library_numbers():
    assert regions.available()["usa"] is Usa
    r = regions.get("usa")
    assert r.key == "usa" and r.name == "Kia Access USA"
    assert r.library_region == 3 and r.library_brand == 1
    assert "owners.kia.com" in r.account_site
    assert r.tested is False   # until PORTING.md's real-car test order has been run


def test_the_library_takes_fahrenheit_and_the_region_converts():
    # KiaUvoApiUSA sends airTemp unit 1 (F) and turns <62 into "LOW", >82 into "HIGH".
    assert KiaUvoApiUSA.temperature_range == range(62, 83)
    r = Usa()
    assert r.climate_request({"duration_min": 5}, 21).set_temp == 70     # 21 C -> 70 F
    assert r.climate_request({}, 22.5).set_temp == 73
    assert r.climate_request({}, 17).set_temp == 63
    assert r.climate_request({}, 27).set_temp == 81   # HI ends at the top of the dial
    assert r.climate_request({}, 21).duration == 10   # the app's default duration_min


def test_the_heating_options_map_onto_the_usa_payload():
    # The USA API moves the steering wheel to its own option and pairs the rear window with
    # the mirrors (the heatingAccessory block in KiaUvoApiUSA.start_climate).
    r = Usa()
    req = r.climate_request({"heating": 1}, 21)
    assert (req.heating, req.steering_wheel) == (1, 1)   # rear window + mirrors + steering wheel
    req = r.climate_request({"heating": 2}, 21)
    assert (req.heating, req.steering_wheel) == (1, 0)   # rear window + mirrors
    req = r.climate_request({"heating": 3}, 21)
    assert (req.heating, req.steering_wheel) == (0, 1)   # steering wheel only
    req = r.climate_request({}, 21)
    assert (req.heating, req.steering_wheel) == (0, 0)
    assert r.climate_request({"front_left_seat": 8}, 21).front_left_seat == 8


def test_the_setpoint_the_car_reported_shows_in_celsius():
    # KiaUvoApiUSA keeps the reported setpoint as a Fahrenheit number (TEMPERATURE_UNITS[1]).
    r = Usa()
    assert r.reported_set_temp(70) == 21.0
    assert r.reported_set_temp(62) == 16.5
    assert r.reported_set_temp(72.5) == 22.5
    assert r.reported_set_temp("HI") == "HI"   # the dial's ends stay words for the dashboard


def test_the_fahrenheit_dial_belongs_to_the_usa_region():
    assert Usa().dial_f() == (62.0, 82.0)
    from server.regions.canada import Canada
    assert Canada().dial_f() is None   # canada keeps its °C dial and °C-only options


def test_a_fahrenheit_command_reaches_the_library_unchanged(tmp_path, memory_keyring,
                                                            vm_factory, world):
    from fastapi.testclient import TestClient

    from server.app import create_app
    from server.kia import KiaCar
    from server.secrets_store import SecretStore
    from conftest import OTP_CODE, wait_for_command

    backend = KiaCar(SecretStore(), vm_factory=vm_factory, region=Usa())
    backend.poll_interval = 0.05
    app = create_app(demo=False, data_dir=tmp_path / "data", backend=backend,
                     web_dir=tmp_path / "web")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        c.post("/api/login", json={"email": "owner.fake@example.test", "password": "Pw-SECRET-ZQX-4471",
                                   "pin": "8642"})
        c.post("/api/otp/send", json={"method": "EMAIL"})
        assert c.post("/api/otp/verify", json={"code": OTP_CODE}).json()["auth"] == "ok"
        assert c.get("/api/state").json()["region"] == {"key": "usa", "name": "Kia Access USA",
                                                        "unit": "f", "dial": {"min": 62, "max": 82}}
        cid = c.post("/api/command", json={"action": "climate_start",
                                           "options": {"set_temp_f": 72}}).json()["id"]
        assert wait_for_command(c, cid)["status"] == "success"
        assert "climate:72" in world.commands   # the °F value reached ClimateRequestOptions unchanged

        # A °F preset is stored as the °F the user picked (schedules run it the same way).
        r = c.put("/api/presets", json={"presets": [{"id": "p1", "name": "Warm", "icon": None,
                                                     "options": {"set_temp_f": 72, "duration_min": 5}}]})
        assert r.status_code == 200
        assert r.json()["presets"][0]["options"] == {"set_temp_f": 72, "duration_min": 5}
        assert c.get("/api/presets").json()["presets"][0]["options"]["set_temp_f"] == 72

        # Both units at once, and anything off the 62-82 dial, is refused before Kia sees it.
        for bad in ({"set_temp_c": 21, "set_temp_f": 70}, {"set_temp_f": 61}, {"set_temp_f": 83},
                    {"set_temp_f": 72.5}, {"set_temp_f": "72"}):
            assert c.post("/api/command", json={"action": "climate_start",
                                                "options": bad}).status_code == 400
        assert "climate:72" in world.commands and world.commands == ["climate:72"]


def test_a_us_backend_round_trips_celsius_out_and_fahrenheit_back(tmp_path, memory_keyring,
                                                                  vm_factory, world):
    from fastapi.testclient import TestClient

    from server.app import create_app
    from server.kia import KiaCar
    from server.secrets_store import SecretStore
    from conftest import OTP_CODE, wait_for_command

    backend = KiaCar(SecretStore(), vm_factory=vm_factory, region=Usa())
    backend.poll_interval = 0.05
    app = create_app(demo=False, data_dir=tmp_path / "data", backend=backend,
                     web_dir=tmp_path / "web")
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        world.air_temp = 70.0   # what the US car last reports (°F, from the first status read)
        c.post("/api/login", json={"email": "owner.fake@example.test", "password": "Pw-SECRET-ZQX-4471",
                                   "pin": "8642"})
        c.post("/api/otp/send", json={"method": "EMAIL"})
        assert c.post("/api/otp/verify", json={"code": OTP_CODE}).json()["auth"] == "ok"
        assert c.get("/api/state").json()["vehicle"]["climate"]["set_temp_c"] == 21
        cid = c.post("/api/command", json={"action": "climate_start",
                                           "options": {"set_temp_c": 22.5}}).json()["id"]
        assert wait_for_command(c, cid)["status"] == "success"
        assert "climate:73" in world.commands   # °F on the wire
