"""Climate schedules: "weekdays at 7:45, start Winter morning", run by this service.

They only fire while this PC and the service are running. Times are the PC's local time.
A schedule that was missed by more than GRACE (PC asleep, service down) is skipped, not
run late: a car warming up an hour after you left is worse than no warm-up.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from typing import Any, Callable

from .errors import ServiceError
from .validation import bad

log = logging.getLogger("server.schedules")

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")   # index = date.weekday()
GRACE = dt.timedelta(minutes=5)
TICK_SECONDS = 15
BUSY_RETRY = dt.timedelta(minutes=3)   # another command running: keep trying this long
MAX_SCHEDULES = 20
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def validate(body: Any, preset_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(body, dict) or not isinstance(body.get("schedules"), list):
        raise bad("Send {\"schedules\": [...]}.")
    items = body["schedules"]
    if len(items) > MAX_SCHEDULES:
        raise bad(f"Keep it to {MAX_SCHEDULES} schedules or fewer.")
    out, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise bad("Each schedule must be an object.")
        sid = item.get("id")
        if not isinstance(sid, str) or not sid.strip() or len(sid) > 64:
            raise bad("Each schedule needs an id.")
        if sid in seen:
            raise bad("Two schedules share the same id.")
        seen.add(sid)
        time = item.get("time")
        if not isinstance(time, str) or not _TIME.match(time):
            raise bad("A schedule time must look like 07:45 (24-hour).")
        days = item.get("days")
        if (not isinstance(days, list) or not days or len(days) > 7
                or any(d not in DAYS for d in days) or len(set(days)) != len(days)):
            raise bad("Pick at least one day for each schedule.")
        preset_id = item.get("preset_id")
        if not isinstance(preset_id, str) or preset_id not in preset_ids:
            raise bad("Each schedule must use one of your climate presets.")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise bad("enabled must be true or false.")
        out.append({"id": sid, "enabled": enabled, "time": time,
                    "days": [d for d in DAYS if d in days], "preset_id": preset_id})
    return out


def occurrences(schedule: dict[str, Any], start: dt.datetime, end: dt.datetime) -> list[dt.datetime]:
    """Local times in [start, end) when this schedule should fire."""
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    out = []
    day = start.date()
    while day <= end.date():
        if DAYS[day.weekday()] in schedule["days"]:
            at = dt.datetime.combine(day, dt.time(hour, minute))
            if start <= at < end:
                out.append(at)
        day += dt.timedelta(days=1)
    return out


def next_run(schedules: list[dict[str, Any]], now: dt.datetime) -> dict[str, Any] | None:
    best = None
    for s in schedules:
        if not s.get("enabled"):
            continue
        times = occurrences(s, now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1),
                            now + dt.timedelta(days=8))
        if times and (best is None or times[0] < best[1]):
            best = (s["id"], times[0])
    if best is None:
        return None
    return {"id": best[0], "at": best[1].astimezone().isoformat(timespec="minutes")}


class Scheduler:
    """Checks the schedules every few seconds and starts climate when one is due."""

    def __init__(self, storage: Any, service: Any,
                 now: Callable[[], dt.datetime] = dt.datetime.now) -> None:
        self.storage = storage
        self.service = service
        self.now = now
        self._last_check: dt.datetime | None = None
        self._fired: set[tuple[str, dt.datetime]] = set()
        self._waiting: list[tuple[dict[str, Any], dt.datetime]] = []   # due but car busy
        self._task: asyncio.Task[Any] | None = None

    def start(self) -> None:
        self._last_check = self.now()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick()
            except Exception:
                log.exception("Schedule check failed")

    async def tick(self) -> None:
        now = self.now()
        start = self._last_check or now
        self._last_check = now
        if now < start:              # clock went backwards (DST): don't double-fire
            return
        for s in self.storage.load_schedules():
            if not s.get("enabled"):
                continue
            for at in occurrences(s, max(start, now - GRACE), now + dt.timedelta(seconds=1)):
                if (s["id"], at) not in self._fired:
                    self._fired.add((s["id"], at))
                    self._waiting.append((s, at))
        self._fired = {f for f in self._fired if now - f[1] < dt.timedelta(days=2)}
        still = []
        for s, at in self._waiting:
            if not await self._fire(s, at, now):
                still.append((s, at))
        self._waiting = still

    async def _fire(self, schedule: dict[str, Any], at: dt.datetime, now: dt.datetime) -> bool:
        """True when handled (sent, or given up on); False to try again on the next tick."""
        presets = {p["id"]: p for p in self.storage.load_presets()}
        preset = presets.get(schedule["preset_id"])
        label = f"schedule {schedule['time']}"
        if preset is None:
            log.warning("Schedule %s skipped: its preset was deleted.", schedule["id"])
            self.service.record_skipped("climate_start", "Scheduled start skipped: its preset was deleted.",
                                        who=label)
            return True
        try:
            await self.service.command("climate_start", preset.get("options") or {}, who=label,
                                       via=f"preset:{preset['name']}")
            log.info("Schedule %s started %s.", schedule["id"], preset["name"])
            return True
        except ServiceError as err:
            if err.status == 409 and now - at < BUSY_RETRY:
                return False         # another command is running; try again shortly
            log.warning("Schedule %s couldn't start: %s", schedule["id"], err.message)
            self.service.record_skipped("climate_start", f"Scheduled start didn't run. {err.message}",
                                        who=label, options=preset.get("options") or {})
            return True
