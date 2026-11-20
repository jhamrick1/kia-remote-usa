"""A second factor for UNLOCK only: a passkey (fingerprint / Face ID) or the Kia PIN.

Passkeys are WebAuthn credentials made by the phone's own authenticator (platform, user
verification required). Their public keys, sign counts and labels live in
%LOCALAPPDATA%\\kia-remote\\passkeys.json (public keys aren't secrets). Challenges are single-use,
expire after 2 minutes and live only in memory. The PIN is compared in constant time with the
saved Kia PIN, never echoed, never logged; 5 wrong PINs in 15 minutes close the PIN path for
15 minutes. Registering a passkey needs the PIN too, so a Cloudflare Access session alone can't
enrol a new fingerprint.
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .errors import ServiceError
from .timeutil import now_iso

log = logging.getLogger("server.unlock")

CHALLENGE_TTL = 120.0          # seconds
MAX_CHALLENGES = 50            # outstanding at once; the oldest go first
PIN_MAX_FAILS = 5
PIN_WINDOW = 15 * 60.0         # 5 wrong PINs within this many seconds...
PIN_LOCKOUT = 15 * 60.0        # ...close the PIN path for this long
RP_NAME = "Car Remote"
LABEL_MAX = 40

NEEDS = "verification"
MSG_NEEDS = "Unlocking needs your fingerprint or the Kia PIN."
MSG_BAD_PIN = "That PIN isn't right."
MSG_PIN_LOCKED = ("Too many wrong PINs. The PIN is refused for {minutes} more minute{s}. "
                  "Use the fingerprint, or wait.")
MSG_PASSKEY_FAILED = "The fingerprint check didn't go through. Try again or use the PIN."
MSG_CHALLENGE_GONE = "That check expired or was already used. Try again."
MSG_NO_PIN_SAVED = "Sign in to Kia Connect first."
MSG_IP_HOST = ("Fingerprint unlock needs a name, not a number, in the address. On this PC open "
               "http://localhost:{port} instead.")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- where the browser is

def relying_party(host_header: str | None) -> tuple[str, str]:
    """(rp_id, origin) for the address the browser used, from the Host header.

    localhost is plain http (a secure context anyway); any other name is https (the tunnel).
    An IP address can't be a WebAuthn RP ID, so 127.0.0.1 is refused with a pointer to localhost.
    """
    host = (host_header or "").strip().lower()
    if not host:
        raise ServiceError(400, "The request has no Host header.")
    if host.startswith("["):                      # [::1]:8765
        name, _, rest = host[1:].partition("]")
        port = rest.lstrip(":")
    else:
        name, _, port = host.partition(":")
    try:
        ipaddress.ip_address(name)
        raise ServiceError(400, MSG_IP_HOST.format(port=port or "8765"))
    except ValueError:
        pass
    scheme = "http" if name == "localhost" or name.endswith(".localhost") else "https"
    return name, f"{scheme}://{host}"


# ---------------------------------------------------------------- stored passkeys

class PasskeyStore:
    """passkeys.json: [{id, public_key, sign_count, label, rp_id, created_at, last_used_at}]."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> list[dict[str, Any]]:
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return []
        items = data.get("passkeys") if isinstance(data, dict) else None
        return [p for p in items if isinstance(p, dict) and isinstance(p.get("id"), str)] if isinstance(items, list) else []

    def _write(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump({"passkeys": items}, fh, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read()

    def get(self, cred_id: str) -> dict[str, Any] | None:
        return next((p for p in self.all() if p["id"] == cred_id), None)

    def add(self, item: dict[str, Any]) -> None:
        with self._lock:
            items = [p for p in self._read() if p["id"] != item["id"]]
            items.append(item)
            self._write(items)

    def update(self, cred_id: str, **fields: Any) -> None:
        with self._lock:
            items = self._read()
            for p in items:
                if p["id"] == cred_id:
                    p.update(fields)
            self._write(items)

    def remove(self, cred_id: str) -> bool:
        with self._lock:
            items = self._read()
            kept = [p for p in items if p["id"] != cred_id]
            if len(kept) == len(items):
                return False
            self._write(kept)
            return True


# ---------------------------------------------------------------- the guard

class UnlockGuard:
    def __init__(self, passkeys: PasskeyStore, saved_pin: Callable[[], str | None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.passkeys = passkeys
        self.saved_pin = saved_pin
        self.clock = clock
        self._challenges: dict[str, dict[str, Any]] = {}
        self._pin_fails: list[float] = []
        self._pin_locked_until: float | None = None
        self._lock = threading.Lock()

    # Challenges

    def _issue(self, kind: str, rp_id: str, **data: Any) -> bytes:
        challenge = secrets.token_bytes(32)
        now = self.clock()
        with self._lock:
            for key in [k for k, c in self._challenges.items() if c["expires"] <= now]:
                del self._challenges[key]
            while len(self._challenges) >= MAX_CHALLENGES:
                del self._challenges[next(iter(self._challenges))]
            self._challenges[b64url(challenge)] = {"kind": kind, "rp_id": rp_id,
                                                   "expires": now + CHALLENGE_TTL, **data}
        return challenge

    def _take(self, credential: Any, kind: str) -> tuple[bytes, dict[str, Any]]:
        """Find the challenge the browser signed (from clientDataJSON) and use it up."""
        try:
            client_data = json.loads(unb64url(credential["response"]["clientDataJSON"]))
            key = client_data["challenge"]
            if not isinstance(key, str):
                raise TypeError
        except (KeyError, TypeError, ValueError):
            raise ServiceError(400, "The fingerprint answer isn't complete.", needs=NEEDS) from None
        with self._lock:
            record = self._challenges.pop(key, None)
        if record is None or record["kind"] != kind or record["expires"] <= self.clock():
            raise ServiceError(403, MSG_CHALLENGE_GONE, needs=NEEDS)
        return unb64url(key), record

    # PIN

    def _pin_wait(self) -> float:
        if self._pin_locked_until is None:
            return 0.0
        left = self._pin_locked_until - self.clock()
        if left <= 0:
            self._pin_locked_until = None
            self._pin_fails.clear()
            return 0.0
        return left

    def check_pin(self, pin: Any) -> None:
        """Raise unless `pin` is the saved Kia PIN. Wrong tries count toward the lockout."""
        with self._lock:
            left = self._pin_wait()
            if left:
                minutes = max(1, -(-int(left) // 60))
                raise ServiceError(429, MSG_PIN_LOCKED.format(minutes=minutes, s="s" if minutes != 1 else ""),
                                   needs=NEEDS)
        saved = self.saved_pin()
        if not saved:
            raise ServiceError(401, MSG_NO_PIN_SAVED)
        given = pin if isinstance(pin, str) else ""
        ok = hmac.compare_digest(given.encode("utf-8"), saved.encode("utf-8"))
        with self._lock:
            if ok:
                self._pin_fails.clear()
                return
            now = self.clock()
            self._pin_fails = [t for t in self._pin_fails if now - t < PIN_WINDOW] + [now]
            fails = len(self._pin_fails)
            if fails >= PIN_MAX_FAILS:
                self._pin_locked_until = now + PIN_LOCKOUT
        log.warning("Wrong PIN for a protected action (%d of %d).", min(fails, PIN_MAX_FAILS), PIN_MAX_FAILS)
        if fails >= PIN_MAX_FAILS:
            raise ServiceError(429, MSG_PIN_LOCKED.format(minutes=int(PIN_LOCKOUT // 60), s="s"), needs=NEEDS)
        raise ServiceError(403, MSG_BAD_PIN, needs=NEEDS)

    # Unlock

    def unlock_options(self, host: str | None) -> dict[str, Any]:
        rp_id, _ = relying_party(host)
        mine = [p for p in self.passkeys.all() if p.get("rp_id") == rp_id]
        if not mine:
            raise ServiceError(404, "No fingerprint is set up for unlock at this address.", needs="pin")
        options = generate_authentication_options(
            rp_id=rp_id,
            challenge=self._issue("unlock", rp_id),
            timeout=int(CHALLENGE_TTL * 1000),
            allow_credentials=[PublicKeyCredentialDescriptor(id=unb64url(p["id"])) for p in mine],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return json.loads(options_to_json(options))

    def verify_unlock(self, body: dict[str, Any], host: str | None) -> str:
        """Check the body's assertion or PIN for an unlock. Returns how ("passkey:<label>" or "pin")."""
        assertion, pin = body.get("assertion"), body.get("pin")
        if assertion is not None:
            return self._verify_assertion(assertion, host)
        if pin is not None:
            self.check_pin(pin)
            return "pin"
        raise ServiceError(403, MSG_NEEDS, needs=NEEDS)

    def _verify_assertion(self, assertion: Any, host: str | None) -> str:
        if not isinstance(assertion, dict):
            raise ServiceError(400, "The fingerprint answer isn't complete.", needs=NEEDS)
        rp_id, origin = relying_party(host)
        challenge, record = self._take(assertion, "unlock")
        cred = self.passkeys.get(str(assertion.get("rawId") or assertion.get("id") or ""))
        if cred is None or cred.get("rp_id") != rp_id or record["rp_id"] != rp_id:
            raise ServiceError(403, "This fingerprint isn't set up for unlock any more. Use the PIN.", needs=NEEDS)
        try:
            verified = verify_authentication_response(
                credential=assertion,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=unb64url(cred["public_key"]),
                credential_current_sign_count=int(cred.get("sign_count") or 0),
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
            log.warning("Passkey unlock refused: %s", type(exc).__name__)
            raise ServiceError(403, MSG_PASSKEY_FAILED, needs=NEEDS) from None
        self.passkeys.update(cred["id"], sign_count=verified.new_sign_count, last_used_at=now_iso())
        return f"passkey:{cred.get('label') or 'phone'}"

    # Registration

    def registration_options(self, host: str | None, label: Any) -> dict[str, Any]:
        """Options for navigator.credentials.create. The PIN is checked when the result comes back."""
        rp_id, _ = relying_party(host)
        label = _clean_label(label)
        existing = [p for p in self.passkeys.all() if p.get("rp_id") == rp_id]
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=RP_NAME,
            user_id=secrets.token_bytes(16),
            user_name=f"Car unlock ({label})",
            user_display_name=f"Car unlock ({label})",
            challenge=self._issue("register", rp_id, label=label),
            timeout=int(CHALLENGE_TTL * 1000),
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=[PublicKeyCredentialDescriptor(id=unb64url(p["id"])) for p in existing],
        )
        return json.loads(options_to_json(options))

    def register(self, body: Any, host: str | None) -> dict[str, Any]:
        if not isinstance(body, dict) or not isinstance(body.get("credential"), dict):
            raise ServiceError(400, "Send the new passkey and the Kia PIN.")
        self.check_pin(body.get("pin"))
        rp_id, origin = relying_party(host)
        credential = body["credential"]
        challenge, record = self._take(credential, "register")
        if record["rp_id"] != rp_id:
            raise ServiceError(403, MSG_CHALLENGE_GONE, needs=NEEDS)
        try:
            verified = verify_registration_response(
                credential=credential,
                expected_challenge=challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                require_user_verification=True,
            )
        except (WebAuthnException, ValueError, TypeError, KeyError) as exc:
            log.warning("Passkey registration refused: %s", type(exc).__name__)
            raise ServiceError(400, "The phone's fingerprint setup didn't go through. Try again.") from None
        item = {
            "id": b64url(verified.credential_id),
            "public_key": b64url(verified.credential_public_key),
            "sign_count": verified.sign_count,
            "label": record.get("label") or "Phone",
            "rp_id": rp_id,
            "created_at": now_iso(),
            "last_used_at": None,
        }
        self.passkeys.add(item)
        log.info("Passkey added for unlock: %s", item["label"])
        return public_view(item)

    def list(self) -> list[dict[str, Any]]:
        return [public_view(p) for p in self.passkeys.all()]

    def remove(self, cred_id: str) -> None:
        if not self.passkeys.remove(cred_id):
            raise ServiceError(404, "No passkey with that id.")
        log.info("Passkey removed for unlock.")


def public_view(p: dict[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("id", "label", "rp_id", "created_at", "last_used_at")}


def _clean_label(label: Any) -> str:
    if not isinstance(label, str):
        return "Phone"
    label = " ".join(label.split())[:LABEL_MAX]
    return label if label and all(c.isprintable() for c in label) else "Phone"
