"""One error type for the API, and the plain sentences Kia's failures turn into."""

from __future__ import annotations

import re

import requests
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationBlockedError,
    AuthenticationError,
    AuthenticationInvalidCredentialsError,
    AuthenticationOTPRequired,
    AuthenticationTokenExpiredError,
    RateLimitingError,
)


class ServiceError(Exception):
    """An error the API returns as HTTP `status` with {"error": message}."""

    def __init__(self, status: int, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra   # more fields for the JSON body, e.g. needs="verification"


LOCKOUT_MESSAGE = (
    "Kia locked the account after too many failed sign-ins. Stop here: don't try again from "
    "this dashboard. Sign in at {site} in a browser to unlock it or reset the password, "
    "then sign in here once."
)
BAD_CREDENTIALS_MESSAGE = (
    "Kia rejected the email, password or PIN. Stop here and check them by signing in at "
    "{site} first: Kia locks the account after a few failed tries. The saved password "
    "was forgotten so nothing retries it."
)
CAPTCHA_MESSAGE = (
    "Kia is asking for a captcha. Stop here, sign in once at {site} in a browser, "
    "then sign in here again."
)
OTP_MESSAGE = "Kia wants a one-time code before it lets this computer in."
NETWORK_MESSAGE = "Couldn't reach Kia Connect. Check the internet connection and try again."
UNEXPECTED_MESSAGE = "Kia Connect gave an unexpected answer. Try again in a minute."
NOT_SUPPORTED_MESSAGE = "Kia Connect doesn't offer this command in your region through this service yet."

# Kinds tell the caller what to do with the auth state.
KIND_OTP = "otp"
KIND_LOCKED = "locked"            # stop, forget the password, never retry
KIND_BAD_CREDENTIALS = "bad_credentials"
KIND_CAPTCHA = "captcha"          # stop, no automatic sign-in until the person signs in again
KIND_EXPIRED = "expired"
KIND_OTHER = "other"

_SERVER_SAID = re.compile(r"Server returned: '([^']{1,200})'")


def classify(exc: BaseException, site: str = "the Kia Connect website") -> tuple[str, int, str]:
    """Return (kind, http_status, plain sentence) for a library or network exception.

    Never passes raw exception text through, except Kia's own short error description.
    """
    if isinstance(exc, ServiceError):
        return KIND_OTHER, exc.status, exc.message
    if isinstance(exc, AuthenticationOTPRequired):
        return KIND_OTP, 409, OTP_MESSAGE
    if isinstance(exc, AuthenticationBlockedError):
        return KIND_LOCKED, 403, LOCKOUT_MESSAGE.format(site=site)
    if isinstance(exc, AuthenticationInvalidCredentialsError):
        return KIND_BAD_CREDENTIALS, 401, BAD_CREDENTIALS_MESSAGE.format(site=site)
    if isinstance(exc, AuthenticationTokenExpiredError):
        return KIND_EXPIRED, 503, "The Kia session expired. Try again."
    if isinstance(exc, AuthenticationError):
        text = str(exc).lower()
        if "captcha" in text or "7549" in text:
            return KIND_CAPTCHA, 403, CAPTCHA_MESSAGE.format(site=site)
        if "otp" in text:
            return KIND_OTHER, 400, "That code didn't work. Check it, or send a new one."
        return KIND_CAPTCHA, 401, (
            f"Kia refused the sign-in. Stop here and check the account at {site} "
            "before trying again."
        )
    if isinstance(exc, RateLimitingError):
        return KIND_OTHER, 429, "Kia is limiting requests right now. Wait a few minutes."
    if isinstance(exc, NotImplementedError):
        return KIND_OTHER, 400, NOT_SUPPORTED_MESSAGE
    if isinstance(exc, requests.RequestException):
        return KIND_OTHER, 502, NETWORK_MESSAGE
    if isinstance(exc, APIError):
        match = _SERVER_SAID.search(str(exc))
        if match:
            return KIND_OTHER, 502, f"Kia Connect said: {match.group(1).strip()}"
        return KIND_OTHER, 502, UNEXPECTED_MESSAGE
    return KIND_OTHER, 502, UNEXPECTED_MESSAGE
