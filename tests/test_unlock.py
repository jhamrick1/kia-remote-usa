"""Unlock's second factor (passkey or Kia PIN) and the Cloudflare Access check, in demo mode."""

from __future__ import annotations

import json
import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from server.access import AccessVerifier, team_issuer
from server.app import create_app
from server.errors import ServiceError
from server.unlock_guard import PasskeyStore, UnlockGuard
from conftest import wait_for_command
from fake_authenticator import FakeAuthenticator

LOCAL = "http://localhost:8765"
DEMO_PIN = "1234"


def make_client(tmp_path, *, base_url=LOCAL, settings=None, access_fetch=None, client=("127.0.0.1", 50000)):
    app = create_app(demo=True, data_dir=tmp_path / "data", demo_delay=0.1, demo_refresh_delay=0.01,
                     web_dir=tmp_path / "web", settings=settings or {}, access_fetch=access_fetch)
    return TestClient(app, base_url=base_url, client=client)


@pytest.fixture
def c(tmp_path, forbidden_keyring):
    with make_client(tmp_path) as client:
        yield client


def unlock(c, **extra):
    return c.post("/api/command", json={"action": "unlock", **extra})


def enrol(c, auth=None, pin=DEMO_PIN, label="Test phone"):
    auth = auth or FakeAuthenticator("localhost", LOCAL)
    options = c.post("/api/passkeys/register/options", json={"label": label}).json()
    r = c.post("/api/passkeys/register", json={"credential": auth.create(options), "pin": pin})
    return auth, r


def passkey_unlock(c, auth, **get_kwargs):
    options = c.post("/api/unlock/challenge").json()
    assertion = auth.get(options, **get_kwargs)
    return unlock(c, assertion=assertion), assertion


# ---------------------------------------------------------------- PIN

def test_unlock_needs_verification(c):
    r = unlock(c)
    assert r.status_code == 403
    assert r.json()["needs"] == "verification" and r.json()["error"]
    assert c.get("/api/state").json()["pending"] is None
    # Lock, climate and lights never ask.
    cid = c.post("/api/command", json={"action": "lock"}).json()["id"]
    assert wait_for_command(c, cid)["status"] == "success"


def test_unlock_with_pin(c):
    r = unlock(c, pin=DEMO_PIN)
    assert r.status_code == 200
    entry = wait_for_command(c, r.json()["id"])
    assert entry["status"] == "success" and entry["via"] == "pin" and entry["who"] == "local"
    assert c.get("/api/state").json()["vehicle"]["locked"] is False


@pytest.mark.parametrize("pin", ["0000", "12345", 1234, "", None])
def test_wrong_pin_is_refused(c, pin):
    r = unlock(c, pin=pin) if pin is not None else unlock(c)
    assert r.status_code == 403 and r.json()["needs"] == "verification"
    assert "1234" not in r.text


def test_five_wrong_pins_close_the_pin_path(c):
    for _ in range(4):
        assert unlock(c, pin="0000").status_code == 403
    r = unlock(c, pin="0000")
    assert r.status_code == 429 and "15" in r.json()["error"]
    # Even the right PIN is refused now...
    assert unlock(c, pin=DEMO_PIN).status_code == 429
    # ...and so is enrolling a passkey with it,
    assert enrol(c)[1].status_code == 429
    # ...until 15 minutes pass.
    guard = c.app.state.guard
    real_clock = guard.clock
    guard.clock = lambda: real_clock() + 15 * 60 + 1
    assert unlock(c, pin=DEMO_PIN).status_code == 200


def test_right_pin_resets_the_count(c):
    for _ in range(4):
        unlock(c, pin="0000")
    cid = unlock(c, pin=DEMO_PIN).json()["id"]
    wait_for_command(c, cid)
    for _ in range(4):
        assert unlock(c, pin="0000").status_code == 403


def test_busy_car_does_not_spend_a_pin_try(c):
    c.post("/api/command", json={"action": "lock"})
    for _ in range(6):
        assert unlock(c, pin="0000").status_code == 409
    time.sleep(0.3)
    assert unlock(c, pin=DEMO_PIN).status_code == 200


# ---------------------------------------------------------------- passkeys

def test_register_needs_the_pin(c, tmp_path):
    auth = FakeAuthenticator("localhost", LOCAL)
    options = c.post("/api/passkeys/register/options", json={"label": "Test phone"}).json()
    assert options["rp"]["id"] == "localhost"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["authenticatorAttachment"] == "platform"
    cred = auth.create(options)
    r = c.post("/api/passkeys/register", json={"credential": cred})
    assert r.status_code == 403
    r = c.post("/api/passkeys/register", json={"credential": cred, "pin": "0000"})
    assert r.status_code == 403
    assert c.get("/api/passkeys").json()["passkeys"] == []
    # The challenge is still unused, so the right PIN goes through.
    r = c.post("/api/passkeys/register", json={"credential": cred, "pin": DEMO_PIN})
    assert r.status_code == 200
    item = r.json()
    assert item["label"] == "Test phone" and item["rp_id"] == "localhost"
    assert "public_key" not in item
    assert [p["id"] for p in c.get("/api/passkeys").json()["passkeys"]] == [item["id"]]
    stored = json.loads((tmp_path / "data" / "passkeys-demo.json").read_text(encoding="utf-8"))
    assert stored["passkeys"][0]["public_key"] and "1234" not in json.dumps(stored)
    assert not (tmp_path / "data" / "passkeys.json").exists()   # demo keeps its own file


def test_register_without_user_verification_is_refused(c):
    _, r = enrol(c, FakeAuthenticator("localhost", LOCAL, user_verified=False))
    assert r.status_code == 400
    assert c.get("/api/passkeys").json()["passkeys"] == []


def test_register_challenge_is_single_use(c):
    auth = FakeAuthenticator("localhost", LOCAL)
    options = c.post("/api/passkeys/register/options", json={}).json()
    cred = auth.create(options)
    assert c.post("/api/passkeys/register", json={"credential": cred, "pin": DEMO_PIN}).status_code == 200
    assert c.post("/api/passkeys/register", json={"credential": cred, "pin": DEMO_PIN}).status_code == 403


def test_unlock_with_passkey(c):
    auth, r = enrol(c)
    assert r.status_code == 200
    options = c.post("/api/unlock/challenge").json()
    assert options["rpId"] == "localhost" and options["userVerification"] == "required"
    assert [x["id"] for x in options["allowCredentials"]] == [r.json()["id"]]
    res, _ = passkey_unlock(c, auth)
    assert res.status_code == 200
    entry = wait_for_command(c, res.json()["id"])
    assert entry["status"] == "success" and entry["via"] == "passkey:Test phone"
    assert c.get("/api/passkeys").json()["passkeys"][0]["last_used_at"]


def test_assertion_replay_is_refused(c):
    auth, _ = enrol(c)
    res, assertion = passkey_unlock(c, auth)
    wait_for_command(c, res.json()["id"])
    again = unlock(c, assertion=assertion)
    assert again.status_code == 403 and again.json()["needs"] == "verification"


def test_bad_assertions_are_refused(c):
    auth, _ = enrol(c)
    # signature over other data
    options = c.post("/api/unlock/challenge").json()
    a = auth.get(options)
    other = auth.get(options)
    a["response"]["signature"] = other["response"]["signature"]
    assert unlock(c, assertion=a).status_code == 403
    # wrong origin (a page elsewhere)
    options = c.post("/api/unlock/challenge").json()
    assert unlock(c, assertion=auth.get(options, origin="https://evil.example")).status_code == 403
    # no user verification (presence only)
    auth.user_verified = False
    options = c.post("/api/unlock/challenge").json()
    assert unlock(c, assertion=auth.get(options)).status_code == 403
    auth.user_verified = True
    # a challenge the server never issued
    a = auth.get({"challenge": "bm90LWlzc3VlZA", "rpId": "localhost"})
    assert unlock(c, assertion=a).status_code == 403
    # a key nobody registered
    stranger = FakeAuthenticator("localhost", LOCAL)
    options = c.post("/api/unlock/challenge").json()
    assert unlock(c, assertion=stranger.get(options)).status_code == 403
    assert unlock(c, assertion="nope").status_code == 400
    assert c.get("/api/state").json()["pending"] is None
    # the real one still works
    assert passkey_unlock(c, auth)[0].status_code == 200


def test_unlock_challenge_expires(tmp_path):
    now = [1000.0]
    store = PasskeyStore(tmp_path / "p.json")
    guard = UnlockGuard(store, lambda: DEMO_PIN, clock=lambda: now[0])
    auth = FakeAuthenticator("localhost", LOCAL)
    opts = guard.registration_options("localhost:8765", "x")
    guard.register({"credential": auth.create(opts), "pin": DEMO_PIN}, "localhost:8765")
    options = guard.unlock_options("localhost:8765")
    now[0] += 121
    with pytest.raises(ServiceError) as err:
        guard.verify_unlock({"assertion": auth.get(options)}, "localhost:8765")
    assert err.value.status == 403
    options = guard.unlock_options("localhost:8765")
    assert guard.verify_unlock({"assertion": auth.get(options)}, "localhost:8765") == "passkey:x"


def test_challenge_without_passkeys_and_on_an_ip_address(tmp_path, forbidden_keyring):
    with make_client(tmp_path) as c:
        r = c.post("/api/unlock/challenge")
        assert r.status_code == 404 and r.json()["needs"] == "pin"
    with make_client(tmp_path, base_url="http://127.0.0.1:8765") as c:
        r = c.post("/api/passkeys/register/options", json={})
        assert r.status_code == 400 and "localhost:8765" in r.json()["error"]
        # The PIN still works there.
        assert unlock(c, pin=DEMO_PIN).status_code == 200


def test_passkey_from_another_address_is_refused(tmp_path, forbidden_keyring):
    with make_client(tmp_path) as c:
        auth, _ = enrol(c)
    # Same passkey file, but the browser is now on a different host name.
    with make_client(tmp_path, base_url="http://other.localhost:8765") as c:
        assert c.post("/api/unlock/challenge").status_code == 404
        options = {"challenge": "x", "rpId": "localhost"}
        assert unlock(c, assertion=auth.get(options)).status_code == 403


def test_remove_passkey(c):
    auth, r = enrol(c)
    cid = r.json()["id"]
    assert c.delete(f"/api/passkeys/{cid}").json() == {"passkeys": []}
    assert c.delete(f"/api/passkeys/{cid}").status_code == 404
    assert c.post("/api/unlock/challenge").status_code == 404


def test_tunnel_host_uses_https_origin(tmp_path, forbidden_keyring):
    with make_client(tmp_path, base_url="http://car.example.test") as c:
        auth = FakeAuthenticator("car.example.test", "https://car.example.test")
        _, r = enrol(c, auth)
        assert r.status_code == 200 and r.json()["rp_id"] == "car.example.test"
        assert passkey_unlock(c, auth)[0].status_code == 200
        # http:// would be a page that isn't the tunnel's
        wait_for_command(c, c.get("/api/state").json()["pending"]["id"])
        opts = c.post("/api/unlock/challenge").json()
        assert unlock(c, assertion=auth.get(opts, origin="http://car.example.test")).status_code == 403


# ---------------------------------------------------------------- Cloudflare Access

TEAM = "kiatest"
AUD = "aud-tag-123"
ISS = "https://kiatest.cloudflareaccess.com"


class FakeAccess:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "kid-1"
        self.fetches = 0

    def jwks(self, url: str) -> dict:
        assert url == f"{ISS}/cdn-cgi/access/certs"
        self.fetches += 1
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid=self.kid, alg="RS256", use="sig")
        return {"keys": [jwk]}

    def token(self, *, aud=AUD, iss=ISS, exp_in=3600, email="partner@example.test", kid=None, key=None) -> str:
        now = int(time.time())
        claims = {"aud": [aud], "iss": iss, "exp": now + exp_in, "iat": now, "nbf": now,
                  "email": email, "sub": "user-1", "type": "app"}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": kid or self.kid})


TUNNEL = {"Cf-Connecting-IP": "203.0.113.9", "Cf-Ray": "8a1b2c3d4e5f-YYZ"}


@pytest.fixture
def fake_access():
    return FakeAccess()


@pytest.fixture
def ac(tmp_path, forbidden_keyring, fake_access):
    settings = {"access_team_domain": f"{TEAM}.cloudflareaccess.com", "access_aud": AUD}
    with make_client(tmp_path, base_url="http://car.example.test", settings=settings,
                     access_fetch=fake_access.jwks) as client:
        yield client


def test_tunnel_requests_need_a_valid_access_jwt(ac, fake_access):
    for path in ("/api/state", "/", "/app.js"):
        r = ac.get(path, headers=TUNNEL)
        assert r.status_code == 403 and "Cloudflare Access" in r.json()["error"]
        assert r.json()["needs"] == "access"   # the dashboard reloads into the Access login
        assert "default-src 'self'" in r.headers["content-security-policy"]
    ok = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()}
    assert ac.get("/api/state", headers=ok).status_code == 200
    bad = [
        fake_access.token(aud="another-app"),
        fake_access.token(iss="https://evil.cloudflareaccess.com"),
        fake_access.token(exp_in=-3600),
        fake_access.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),
        fake_access.token(kid="unknown-kid"),
        jwt.encode({"aud": [AUD], "iss": ISS, "exp": int(time.time()) + 60}, "x" * 32, algorithm="HS256"),
        "not-a-jwt",
    ]
    for token in bad:
        r = ac.get("/api/state", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": token})
        assert r.status_code == 403, token
    # A Cf-Ray alone also marks the tunnel.
    assert ac.get("/api/state", headers={"Cf-Ray": "x"}).status_code == 403
    # Keys are cached; an unknown key id refetches at most once a minute.
    assert fake_access.fetches == 1


def test_access_keys_rotate(fake_access):
    now = [0.0]
    v = AccessVerifier(TEAM, AUD, fetch=fake_access.jwks, clock=lambda: now[0])
    assert v.verify(fake_access.token()) == "partner@example.test"
    fake_access.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fake_access.kid = "kid-2"
    with pytest.raises(ServiceError):
        v.verify(fake_access.token())          # fetched less than a minute ago
    now[0] += 61
    assert v.verify(fake_access.token(email="b@example.test")) == "b@example.test"
    assert fake_access.fetches == 2


def test_local_requests_need_no_jwt(ac):
    assert ac.get("/api/state").status_code == 200
    r = unlock(ac, pin=DEMO_PIN)
    assert wait_for_command(ac, r.json()["id"])["who"] == "local"


def test_access_email_is_recorded_on_commands(ac, fake_access):
    h = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token(email="partner@example.test")}
    h = approve_device(ac, h)
    cid = ac.post("/api/command", json={"action": "lock"}, headers=h).json()["id"]
    entry = wait_for_command(ac, cid)
    assert entry["who"] == "partner@example.test"
    log = ac.get("/api/log").json()["entries"]
    assert log[0]["who"] == "partner@example.test"


def test_unreachable_access_keys_fail_closed(tmp_path, forbidden_keyring, fake_access):
    def down(url):
        raise OSError("offline")
    settings = {"access_team_domain": TEAM, "access_aud": AUD}
    with make_client(tmp_path, settings=settings, access_fetch=down) as c:
        r = c.get("/api/state", headers={**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()})
        assert r.status_code == 503
        assert c.get("/api/state").status_code == 200   # this PC still works


def test_without_settings_tunnel_is_refused_with_a_warning(tmp_path, forbidden_keyring, caplog):
    caplog.set_level(logging.WARNING)
    with make_client(tmp_path) as c:
        assert c.get("/api/state", headers=TUNNEL).status_code == 403          # fails closed
        assert c.post("/api/command", json={"action": "lock"}, headers=TUNNEL).status_code == 403
        assert c.get("/", headers=TUNNEL).status_code == 403
    assert any("Cloudflare Access is NOT checked" in rec.getMessage() for rec in caplog.records)


def test_settings_file_is_read(tmp_path, forbidden_keyring, fake_access):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(json.dumps({"access_team_domain": TEAM, "access_aud": AUD}),
                                        encoding="utf-8")
    app = create_app(demo=True, data_dir=data, web_dir=tmp_path / "web", access_fetch=fake_access.jwks)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert c.app.state.access.configured
        assert c.get("/api/state", headers=TUNNEL).status_code == 403


@pytest.mark.parametrize("given", ["kiatest", "kiatest.cloudflareaccess.com",
                                   "https://kiatest.cloudflareaccess.com/", "KIATEST.cloudflareaccess.com"])
def test_team_domain_forms(given):
    assert team_issuer(given) == ISS
    assert AccessVerifier(given, AUD).certs_url == f"{ISS}/cdn-cgi/access/certs"


# ---------------------------------------------------------------- approved devices

DEMO_ACCOUNT = {"email": "demo@example.com", "password": "demo", "pin": DEMO_PIN}


def device_cookie(response) -> str:
    header = response.headers.get("set-cookie", "")
    assert header.startswith("car_device=")
    for part in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
        assert part.lower() in header.lower(), part
    return header.split(";")[0].split("=", 1)[1]


def approve_device(c, headers, label="Test phone"):
    r = c.post("/api/device/approve", json={**DEMO_ACCOUNT, "label": label}, headers=headers)
    assert r.status_code == 200, r.text
    return {**headers, "Cookie": f"car_device={device_cookie(r)}"}


def test_a_new_browser_must_be_approved_even_after_access(ac, fake_access):
    h = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()}
    assert ac.get("/api/state", headers=h).json() == {"demo": True, "auth": "needs_device"}
    for method, path in (("post", "/api/command"), ("get", "/api/log"), ("get", "/api/presets"),
                         ("get", "/api/car-photo"), ("get", "/api/devices"), ("post", "/api/login")):
        r = getattr(ac, method)(path, headers=h, **({"json": {"action": "lock"}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["needs"] == "device", path
    assert ac.get("/", headers=h).status_code == 200            # the page itself, to show the approval screen
    assert ac.get("/api/state", headers={**h, "Cookie": "car_device=made-up"}).json()["auth"] == "needs_device"

    approved = approve_device(ac, h)
    assert ac.get("/api/state", headers=approved).json()["auth"] == "ok"
    devices = ac.get("/api/devices", headers=approved).json()["devices"]
    assert [(d["label"], d["who"], d["this_device"]) for d in devices] == [("Test phone", "partner@example.test", True)]
    assert "token_sha256" not in devices[0]
    saved = json.loads((ac.app.state.gate.path).read_text(encoding="utf-8"))
    assert approved["Cookie"].split("=", 1)[1] not in json.dumps(saved)   # only the hash is kept

    ac.delete(f"/api/devices/{devices[0]['id']}", headers=approved)
    assert ac.get("/api/state", headers=approved).json()["auth"] == "needs_device"


def test_wrong_account_details_dont_approve_and_lock_out(ac, fake_access):
    h = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()}
    for wrong in ({"password": "nope"}, {"pin": "9999"}, {"email": "other@example.com"}, {"password": ""}):
        r = ac.post("/api/device/approve", json={**DEMO_ACCOUNT, **wrong}, headers=h)
        assert r.status_code == 403 and r.json()["needs"] == "device" and "set-cookie" not in r.headers
    r = ac.post("/api/device/approve", json={**DEMO_ACCOUNT, "pin": "0000"}, headers=h)
    assert r.status_code == 429
    r = ac.post("/api/device/approve", json=DEMO_ACCOUNT, headers=h)
    assert r.status_code == 429                                 # even the right details wait now
    # email case and spaces don't matter
    ac.app.state.gate._locked_until = None
    ac.app.state.gate._fails.clear()
    assert ac.post("/api/device/approve", json={**DEMO_ACCOUNT, "email": " Demo@Example.com "},
                   headers=h).status_code == 200


def test_this_pc_needs_no_approval(ac):
    assert ac.get("/api/state").json()["auth"] == "ok"
    assert ac.get("/api/devices").json() == {"devices": []}


def test_signing_in_to_kia_from_a_new_browser_approves_it(ac, fake_access):
    h = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()}
    ac.post("/api/logout")                                      # the PC is signed out of Kia
    assert ac.get("/api/state", headers=h).json()["auth"] == "needs_login"
    r = ac.post("/api/login", json={**DEMO_ACCOUNT, "label": "iPhone"}, headers=h)
    assert r.json()["auth"] == "needs_otp"
    approved = {**h, "Cookie": f"car_device={device_cookie(r)}"}
    ac.post("/api/otp/send", json={"method": "EMAIL"}, headers=approved)
    assert ac.post("/api/otp/verify", json={"code": "123456"}, headers=approved).json()["auth"] == "ok"
    assert ac.get("/api/log", headers=approved).status_code == 200


def test_a_different_kia_account_drops_every_approval(ac, fake_access):
    h = {**TUNNEL, "Cf-Access-Jwt-Assertion": fake_access.token()}
    ac.post("/api/login", json=DEMO_ACCOUNT)                    # on the PC: the account devices belong to
    approved = approve_device(ac, h)
    ac.post("/api/login", json=DEMO_ACCOUNT)                    # same account again: kept
    assert ac.get("/api/state", headers=approved).json()["auth"] != "needs_device"
    ac.post("/api/login", json={**DEMO_ACCOUNT, "email": "someone@else.example"})
    ac.post("/api/otp/send", json={"method": "EMAIL"})
    ac.post("/api/otp/verify", json={"code": "123456"})
    assert ac.get("/api/state", headers=approved).json()["auth"] == "needs_device"
