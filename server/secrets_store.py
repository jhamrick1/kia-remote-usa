"""Kia email, password, PIN and token: in the computer's own password store, via keyring.

Windows: Credential Manager. macOS: the login Keychain. Linux (a Raspberry Pi, a server) usually
has no password store a background service can open, so there they go in `secrets.json` in the
data folder instead, readable by this user only (mode 600). Choose with `"secret_store":
"keyring"` or `"file"` in settings.json; the default is the file on Linux, keyring elsewhere.

Nothing here logs a value. Every secret that passes through is also
registered with `REDACTOR`, which scrubs it from log lines and error sentences.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from hyundai_kia_connect_api.Token import Token

SERVICE = "kia-remote"
_EMAIL, _PASSWORD, _PIN, _TOKEN = "email", "password", "pin", "token"
# Windows Credential Manager caps one entry at 2560 bytes (UTF-16), so long values are split.
_CHUNK = 1000
# The Token repeats the username, password and PIN; those already have their own entries.
_TOKEN_DROP = ("username", "password", "pin")
_TOKEN_SECRET_FIELDS = (
    "access_token", "refresh_token", "control_token", "cci_access_token",
    "exchangeable_token", "exchangeable_refresh_token", "non_ccs_token",
    "non_ccs_refresh_token", "id_token", "stamp",
)


class Redactor:
    """Remembers secret strings and replaces them with [hidden]."""

    def __init__(self) -> None:
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def add(self, *values: object) -> None:
        with self._lock:
            for value in values:
                if isinstance(value, str) and len(value) >= 4:
                    self._values.add(value)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()

    def scrub(self, text: str) -> str:
        with self._lock:
            values = sorted(self._values, key=len, reverse=True)
        for value in values:
            if value in text:
                text = text.replace(value, "[hidden]")
        return text


REDACTOR = Redactor()


class RedactingFilter(logging.Filter):
    """Logging filter: no registered secret reaches a log handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string must not break logging
            message = str(record.msg)
        scrubbed = REDACTOR.scrub(message)
        if record.exc_info:
            import traceback

            scrubbed += "\n" + REDACTOR.scrub("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
            record.exc_text = None
        record.msg = scrubbed
        record.args = None
        return True


def default_kind() -> str:
    return "file" if sys.platform.startswith("linux") else "keyring"


class FileSecrets:
    """The secrets as one JSON object in a file only this user can read (Linux: mode 600)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("%s is unreadable; signed out.", self.path.name)
            return {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        if tmp.exists():
            tmp.unlink()
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.path)

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._read().get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str) -> None:
        with self._lock:
            data = self._read()
            data[key] = value
            self._write(data)

    def delete(self, key: str) -> None:
        with self._lock:
            data = self._read()
            if data.pop(key, None) is not None:
                if data:
                    self._write(data)
                else:
                    self.path.unlink(missing_ok=True)


class SecretStore:
    def __init__(self, file: Path | None = None) -> None:
        """file: keep secrets in this file instead of the system password store (see above)."""
        self.file = FileSecrets(file) if file else None

    def _get(self, key: str) -> str | None:
        if self.file:
            return self.file.get(key)
        try:
            return keyring.get_password(SERVICE, key)
        except KeyringError:
            return None

    def _set(self, key: str, value: str) -> None:
        if self.file:
            self.file.set(key, value)
            return
        keyring.set_password(SERVICE, key, value)

    def _delete(self, key: str) -> None:
        if self.file:
            self.file.delete(key)
            return
        try:
            keyring.delete_password(SERVICE, key)
        except (PasswordDeleteError, KeyringError):
            pass

    def _set_long(self, key: str, value: str) -> None:
        self._delete_long(key)
        if len(value) <= _CHUNK:
            self._set(key, value)
            return
        parts = [value[i:i + _CHUNK] for i in range(0, len(value), _CHUNK)]
        for index, part in enumerate(parts, start=1):
            self._set(f"{key}.{index}", part)
        self._set(key, f"chunks:{len(parts)}")

    def _get_long(self, key: str) -> str | None:
        head = self._get(key)
        if head is None or not head.startswith("chunks:"):
            return head
        try:
            count = int(head.split(":", 1)[1])
        except ValueError:
            return None
        parts = [self._get(f"{key}.{i}") for i in range(1, count + 1)]
        if any(p is None for p in parts):
            return None
        return "".join(parts)  # type: ignore[arg-type]

    def _delete_long(self, key: str) -> None:
        head = self._get(key)
        if head and head.startswith("chunks:"):
            try:
                count = int(head.split(":", 1)[1])
            except ValueError:
                count = 0
            for i in range(1, count + 1):
                self._delete(f"{key}.{i}")
        self._delete(key)

    # Credentials

    def save_credentials(self, email: str, password: str, pin: str) -> None:
        REDACTOR.add(email, password, pin)
        self._set(_EMAIL, email)
        self._set(_PASSWORD, password)
        self._set(_PIN, pin)

    def load_credentials(self) -> tuple[str, str, str] | None:
        email, password, pin = self._get(_EMAIL), self._get(_PASSWORD), self._get(_PIN)
        if not (email and password and pin):
            return None
        REDACTOR.add(email, password, pin)
        return email, password, pin

    def load_pin(self) -> str | None:
        """The saved Kia PIN, for checking an unlock. Never returned by the API or logged."""
        pin = self._get(_PIN)
        if pin:
            REDACTOR.add(pin)
        return pin or None

    # Token

    def save_token(self, token: Token) -> None:
        data = token.to_dict()
        REDACTOR.add(*(data.get(f) for f in _TOKEN_SECRET_FIELDS))
        for field in _TOKEN_DROP:
            data.pop(field, None)
        self._set_long(_TOKEN, json.dumps(data, separators=(",", ":")))

    def load_token(self, email: str, password: str, pin: str) -> Token | None:
        raw = self._get_long(_TOKEN)
        if not raw:
            return None
        try:
            data = json.loads(raw)
            data.update(username=email, password=password, pin=pin)
            token = Token.from_dict(data)
        except (ValueError, KeyError, TypeError):
            self._delete_long(_TOKEN)
            return None
        REDACTOR.add(*(getattr(token, f, None) for f in _TOKEN_SECRET_FIELDS))
        return token

    def token_fingerprint(self, token: Token | None) -> str | None:
        """A comparable form of the token (never stored or shown)."""
        if token is None:
            return None
        data = token.to_dict()
        return json.dumps({k: data.get(k) for k in ("access_token", "refresh_token", "valid_until", "device_id")})

    # Forgetting

    def forget_token(self) -> None:
        self._delete_long(_TOKEN)

    def forget_password_and_token(self) -> None:
        self._delete(_PASSWORD)
        self._delete_long(_TOKEN)

    def clear(self) -> None:
        for key in (_EMAIL, _PASSWORD, _PIN):
            self._delete(key)
        self._delete_long(_TOKEN)
