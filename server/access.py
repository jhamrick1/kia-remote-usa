"""Cloudflare Access: every request that came through the tunnel must carry a valid Access JWT.

Settings (not secrets) in %LOCALAPPDATA%\\kia-remote\\settings.json:
  {"access_team_domain": "yourteam.cloudflareaccess.com", "access_aud": "<Application Audience tag>"}

A request is from the tunnel when it carries Cloudflare's headers (Cf-Connecting-IP or Cf-Ray):
cloudflared also connects from localhost, so the client address alone can't tell. Without those
headers (a browser on this PC) no JWT is needed. When the settings are missing every tunnel request is
refused (fails closed) and the service logs a loud warning at startup; the PC itself still works.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable

import jwt

from .errors import ServiceError

log = logging.getLogger("server.access")

SETTINGS_FILE = "settings.json"
JWT_HEADER = "cf-access-jwt-assertion"
TUNNEL_HEADERS = ("cf-connecting-ip", "cf-ray")
KEYS_TTL = 3600.0          # re-read Cloudflare's signing keys hourly
KEYS_RETRY = 60.0          # an unknown key id refetches at most once a minute
LEEWAY = 60                # seconds of clock skew allowed on exp / iat / nbf

MSG_DENIED = "Sign in through Cloudflare Access first."
MSG_KEYS = "Couldn't check the Cloudflare Access sign-in right now. Try again in a minute."


def load_settings(data_dir: Path) -> dict[str, Any]:
    try:
        with (Path(data_dir) / SETTINGS_FILE).open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.error("settings.json in %s isn't valid JSON; ignoring it.", data_dir)
        return {}
    return data if isinstance(data, dict) else {}


def team_issuer(team_domain: str) -> str:
    """'yourteam', 'yourteam.cloudflareaccess.com' or the https URL -> 'https://yourteam.cloudflareaccess.com'."""
    name = team_domain.strip().lower()
    for prefix in ("https://", "http://"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    name = name.split("/", 1)[0]
    if "." not in name:
        name += ".cloudflareaccess.com"
    return f"https://{name}"


def _fetch_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "kia-remote"})
    with urllib.request.urlopen(req, timeout=10) as res:   # noqa: S310 (fixed https URL)
        return json.loads(res.read().decode("utf-8"))


def is_tunnel(headers: Any, client_host: str | None) -> bool:
    if any(headers.get(h) for h in TUNNEL_HEADERS):
        return True
    # The service listens on 127.0.0.1 only; anything else is unexpected, so treat it as remote.
    return client_host not in (None, "127.0.0.1", "::1", "localhost")


class AccessVerifier:
    def __init__(self, team_domain: str | None, aud: str | None,
                 fetch: Callable[[str], dict[str, Any]] = _fetch_json,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.configured = bool(team_domain and aud)
        self.issuer = team_issuer(team_domain) if team_domain else None
        self.aud = aud
        self.certs_url = f"{self.issuer}/cdn-cgi/access/certs" if self.issuer else None
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: dict[str, Any], **kwargs: Any) -> "AccessVerifier":
        team, aud = settings.get("access_team_domain"), settings.get("access_aud")
        return cls(team if isinstance(team, str) and team.strip() else None,
                   aud.strip() if isinstance(aud, str) and aud.strip() else None, **kwargs)

    def _load_keys(self, force: bool) -> None:
        now = self._clock()
        with self._lock:
            if self._fetched_at is not None and now - self._fetched_at < (KEYS_RETRY if force else KEYS_TTL):
                return
        try:
            jwks = jwt.PyJWKSet.from_dict(self._fetch(self.certs_url))
        except Exception as exc:   # network, JSON, key format
            log.error("Couldn't read Cloudflare Access keys: %s", type(exc).__name__)
            with self._lock:
                if not self._keys:
                    raise ServiceError(503, MSG_KEYS) from None
            return
        with self._lock:
            self._keys = {k.key_id: k for k in jwks.keys if k.key_id}
            self._fetched_at = now

    def _key_for(self, kid: str) -> Any:
        self._load_keys(force=False)
        key = self._keys.get(kid)
        if key is None:
            self._load_keys(force=True)   # Cloudflare rotates keys
            key = self._keys.get(kid)
        return key

    def verify(self, token: str | None) -> str:
        """Return the signed-in email, or raise 403."""
        if not token:
            raise ServiceError(403, MSG_DENIED, needs="access")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise ServiceError(403, MSG_DENIED, needs="access") from None
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ServiceError(403, MSG_DENIED, needs="access")
        key = self._key_for(header["kid"])
        if key is None:
            raise ServiceError(403, MSG_DENIED, needs="access")
        try:
            claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=self.aud,
                                issuer=self.issuer, leeway=LEEWAY,
                                options={"require": ["exp", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            log.warning("Cloudflare Access token refused: %s", type(exc).__name__)
            raise ServiceError(403, MSG_DENIED, needs="access") from None
        email = claims.get("email")
        return email if isinstance(email, str) and email else str(claims.get("sub") or "access")
