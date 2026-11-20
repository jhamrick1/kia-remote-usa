"""Climate schedules: validation, the API, and the scheduler firing on a fake clock (demo car)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from server.app import create_app
from server.schedules import next_run, occurrences
from conftest import wait_for_command

MONDAY = dt.datetime(2026, 11, 16, 7, 44, 50)   # a Monday


class Clock:
    def __init__(self, now: dt.datetime) -> None:
        self.t = now

    def __call__(self) -> dt.datetime:
        return self.t


@pytest.fixture
def setup(tmp_path, forbidden_keyring):
    app = create_app(demo=True, data_dir=tmp_path / "data", demo_delay=0.2,
                     demo_refresh_delay=0.01, web_dir=tmp_path / "web-missing")
    clock = Clock(MONDAY)
    app.state.scheduler.now = clock
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        app.state.scheduler._last_check = clock()
        yield c, app.state.scheduler, clock


def weekday_745(**extra):
    return {"id": "s1", "enabled": True, "time": "07:45", "days": ["mon", "tue", "wed", "thu", "fri"],
            "preset_id": "winter-morning", **extra}


def tick(client, scheduler):
    client.portal.call(scheduler.tick)


def test_save_and_read_back_with_next_run(setup):
    c, _, _ = setup
    r = c.put("/api/schedules", json={"schedules": [weekday_745(days=["fri", "mon"])]})
    assert r.status_code == 200, r.text
    body = c.get("/api/schedules").json()
    assert body["schedules"][0]["days"] == ["mon", "fri"]       # kept in week order
    assert body["next"]["id"] == "s1" and "T07:45" in body["next"]["at"]
    assert body["next"]["at"].startswith("2026-11-16")          # later today (it's 7:44)


@pytest.mark.parametrize("bad", [
    {"time": "7:45"}, {"time": "24:00"}, {"days": []}, {"days": ["mon", "mon"]}, {"days": ["monday"]},
    {"preset_id": "nope"}, {"enabled": "yes"}, {"id": ""},
])
def test_bad_schedules_are_refused(setup, bad):
    c, _, _ = setup
    r = c.put("/api/schedules", json={"schedules": [weekday_745(**bad)]})
    assert r.status_code == 400 and r.json()["error"]


def test_fires_once_at_the_time_and_logs_who(setup):
    c, scheduler, clock = setup
    c.put("/api/schedules", json={"schedules": [weekday_745()]})
    tick(c, scheduler)
    assert c.get("/api/state").json()["pending"] is None          # 7:44:50, not yet
    clock.t = MONDAY + dt.timedelta(seconds=15)                    # 7:45:05
    tick(c, scheduler)
    pending = c.get("/api/state").json()["pending"]
    assert pending["action"] == "climate_start" and pending["who"] == "schedule 07:45"
    assert pending["via"] == "preset:Winter morning" and pending["options"]["set_temp_c"] == 22
    wait_for_command(c, pending["id"])
    clock.t += dt.timedelta(seconds=15)
    tick(c, scheduler)                                             # same minute: no second start
    assert len(c.get("/api/log").json()["entries"]) == 1


def test_disabled_and_wrong_day_do_not_fire(setup):
    c, scheduler, clock = setup
    c.put("/api/schedules", json={"schedules": [weekday_745(enabled=False),
                                                weekday_745(id="s2", days=["sat"])]})
    clock.t = MONDAY + dt.timedelta(minutes=1)
    tick(c, scheduler)
    assert c.get("/api/log").json()["entries"] == []


def test_missed_by_more_than_grace_is_skipped(setup):
    c, scheduler, clock = setup
    c.put("/api/schedules", json={"schedules": [weekday_745()]})
    clock.t = MONDAY + dt.timedelta(minutes=30)    # PC was asleep from 7:44 to 8:14
    tick(c, scheduler)
    assert c.get("/api/log").json()["entries"] == []


def test_busy_car_retries_then_runs(setup):
    c, scheduler, clock = setup
    c.put("/api/schedules", json={"schedules": [weekday_745()]})
    lock = c.post("/api/command", json={"action": "lock"}).json()["id"]
    clock.t = MONDAY + dt.timedelta(seconds=15)
    tick(c, scheduler)                              # lock still running: waits
    assert c.get("/api/state").json()["pending"]["action"] == "lock"
    wait_for_command(c, lock)
    clock.t += dt.timedelta(seconds=15)
    tick(c, scheduler)
    assert c.get("/api/state").json()["pending"]["action"] == "climate_start"


def test_preset_used_by_a_schedule_cannot_be_deleted(setup):
    c, _, _ = setup
    c.put("/api/schedules", json={"schedules": [weekday_745()]})
    presets = c.get("/api/presets").json()["presets"]
    r = c.put("/api/presets", json={"presets": [p for p in presets if p["id"] != "winter-morning"]})
    assert r.status_code == 409


def test_occurrences_and_next_run_cross_the_week():
    s = weekday_745(days=["mon"])
    sat = dt.datetime(2026, 11, 21, 12, 0)
    assert occurrences(s, sat, sat + dt.timedelta(days=3)) == [dt.datetime(2026, 11, 23, 7, 45)]
    assert next_run([s], sat)["at"].startswith("2026-11-23T07:45")
    assert next_run([weekday_745(enabled=False)], sat) is None
