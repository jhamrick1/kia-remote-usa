"""A software WebAuthn authenticator for tests: a real P-256 key, real signatures, 'none' attestation."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class FakeAuthenticator:
    def __init__(self, rp_id: str, origin: str, *, user_verified: bool = True, counter: int = 0) -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.user_verified = user_verified
        self.counter = counter
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = secrets.token_bytes(32)

    def _flags(self, extra: int = 0) -> int:
        return FLAG_UP | (FLAG_UV if self.user_verified else 0) | extra

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge: str, origin: str | None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def create(self, options: dict, origin: str | None = None) -> dict:
        """navigator.credentials.create(options) -> the JSON the browser sends back."""
        rp_hash = hashlib.sha256(options["rp"]["id"].encode()).digest()
        attested = bytes(16) + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose_key()
        auth_data = rp_hash + bytes([self._flags(FLAG_AT)]) + struct.pack(">I", self.counter) + attested
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", options["challenge"], origin)
        return {"id": b64url(self.cred_id), "rawId": b64url(self.cred_id), "type": "public-key",
                "authenticatorAttachment": "platform", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64url(cd), "attestationObject": b64url(att),
                             "transports": ["internal"]}}

    def get(self, options: dict, origin: str | None = None, rp_id: str | None = None) -> dict:
        """navigator.credentials.get(options) -> the JSON the browser sends back."""
        self.counter += 1
        rp_hash = hashlib.sha256((rp_id or options.get("rpId") or self.rp_id).encode()).digest()
        auth_data = rp_hash + bytes([self._flags()]) + struct.pack(">I", self.counter)
        cd = self._client_data("webauthn.get", options["challenge"], origin)
        sig = self.key.sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64url(self.cred_id), "rawId": b64url(self.cred_id), "type": "public-key",
                "authenticatorAttachment": "platform", "clientExtensionResults": {},
                "response": {"clientDataJSON": b64url(cd), "authenticatorData": b64url(auth_data),
                             "signature": b64url(sig), "userHandle": None}}
