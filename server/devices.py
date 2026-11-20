"""Approved devices: a browser that comes through the tunnel must be approved once before it sees
anything, even after Cloudflare Access let it in.

Approving takes the car's Kia account (email, password and PIN), checked against what's saved on
this PC; nothing goes to Kia. Signing in to Kia from a device (when the PC isn't signed in) also
approves it, since that proves the same. The device then keeps a random token in an HttpOnly
cookie; this PC keeps only the token's SHA-256, with a label and dates, in devices.json.

So getting into one of the allowed email inboxes (Cloudflare's one-time code) is no longer
enough: a new browser also needs the Kia password and PIN. A browser on this PC itself needs no
approval. When a sign-in brings a different Kia account than the one the devices were approved
for, every approval is dropped.

5 wrong tries within 15 minutes close approvals for 15 minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .errors import ServiceError
from .timeutil import now_iso

log = logging.getLogger("server.devices")

COOKIE = "car_device"
COOKIE_MAX_AGE = 400 * 24 * 3600     # the longest browsers keep a cookie
TOUCH_EVERY = 3600.0                 # write last_used_at at most this often per device
MAX_FAILS = 5
FAIL_WINDOW = 15 * 60.0
LOCKOUT = 15 * 60.0
MAX_DEVICES = 50

NEEDS = "device"
MSG_NEEDED = "This browser hasn't been approved for the car yet."
MSG_WRONG = "That doesn't match the Kia account saved on the PC."
MSG_LOCKED = "Too many wrong tries. Approving a new device is paused for {minutes} minutes."
MSG_PC_SIGNED_OUT = "The PC isn't signed in to Kia. Sign in to Kia to approve this device."


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DeviceGate:
    def __init__(self, path: Path, saved_credentials: Callable[[], tuple[str, str, str] | None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self.saved_credentials = saved_credentials
        self.clock = clock
        self._lock = threading.Lock()
        self._fails: list[float] = []
        self._locked_until: float | None = None
        self._touched: dict[str, float] = {}
        self._data = self._read()

    # storage

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("devices"), list):
                return data
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("devices.json is unreadable; every device has to be approved again.")
        return {"account": None, "devices": []}

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # checks

    def find(self, token: str | None) -> dict[str, Any] | None:
        """The device record for this cookie value, or None. Notes when it was last used."""
        if not token or len(token) > 200:
            return None
        digest = _hash(token)
        with self._lock:
            for device in self._data["devices"]:
                if hmac.compare_digest(device["token_sha256"], digest):
                    now = self.clock()
                    if now - self._touched.get(device["id"], -TOUCH_EVERY) >= TOUCH_EVERY:
                        self._touched[device["id"]] = now
                        device["last_used_at"] = now_iso()
                        self._write()
                    return device
        return None

    def _wait(self) -> float:
        if self._locked_until is None:
            return 0.0
        left = self._locked_until - self.clock()
        if left <= 0:
            self._locked_until = None
            self._fails.clear()
            return 0.0
        return left

    def check_account(self, email: Any, password: Any, pin: Any) -> None:
        """Raise unless these are the Kia email, password and PIN saved on this PC."""
        with self._lock:
            left = self._wait()
        if left:
            raise ServiceError(429, MSG_LOCKED.format(minutes=max(1, -(-int(left) // 60))), needs=NEEDS)
        saved = self.saved_credentials()
        if not saved:
            raise ServiceError(409, MSG_PC_SIGNED_OUT, needs=NEEDS)
        given = [v if isinstance(v, str) else "" for v in (email, password, pin)]
        given[0] = given[0].strip().lower()
        want = [saved[0].strip().lower(), saved[1], saved[2]]
        ok = all([hmac.compare_digest(g.encode("utf-8"), w.encode("utf-8")) for g, w in zip(given, want)])
        if ok:
            with self._lock:
                self._fails.clear()
            return
        with self._lock:
            now = self.clock()
            self._fails = [t for t in self._fails if now - t < FAIL_WINDOW] + [now]
            fails = len(self._fails)
            if fails >= MAX_FAILS:
                self._locked_until = now + LOCKOUT
        log.warning("Wrong Kia account details to approve a device (%d of %d).", min(fails, MAX_FAILS), MAX_FAILS)
        if fails >= MAX_FAILS:
            raise ServiceError(429, MSG_LOCKED.format(minutes=int(LOCKOUT // 60)), needs=NEEDS)
        raise ServiceError(403, MSG_WRONG, needs=NEEDS)

    # changes

    def approve(self, label: Any, who: str | None, via: str) -> str:
        """Add a device; returns the token for its cookie (never stored here)."""
        token = secrets.token_urlsafe(32)
        name = label.strip()[:40] if isinstance(label, str) and label.strip() else "Browser"
        device = {"id": secrets.token_hex(6), "label": name, "token_sha256": _hash(token),
                  "who": who, "via": via, "created_at": now_iso(), "last_used_at": now_iso()}
        with self._lock:
            devices = self._data["devices"] + [device]
            self._data["devices"] = devices[-MAX_DEVICES:]
            self._write()
        log.info("Approved a new device: %s (%s, by %s).", name, via, who or "unknown")
        return token

    def signed_in(self, email: str) -> None:
        """After a Kia sign-in: a different account than before drops every approval."""
        account = _hash(email.strip().lower())
        with self._lock:
            if self._data.get("account") == account:
                return
            dropped = len(self._data["devices"])
            self._data = {"account": account, "devices": [] if self._data.get("account") else self._data["devices"]}
            if self._data["devices"] == [] and dropped:
                log.warning("A different Kia account signed in: %d approved device(s) dropped.", dropped)
            self._write()

    def list(self, current: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [{k: d.get(k) for k in ("id", "label", "who", "via", "created_at", "last_used_at")}
                    | {"this_device": bool(current and current["id"] == d["id"])}
                    for d in reversed(self._data["devices"])]

    def remove(self, device_id: str) -> None:
        with self._lock:
            before = len(self._data["devices"])
            self._data["devices"] = [d for d in self._data["devices"] if d["id"] != device_id]
            if len(self._data["devices"]) == before:
                raise ServiceError(404, "There's no such device.")
            self._write()
        log.info("Removed an approved device.")
