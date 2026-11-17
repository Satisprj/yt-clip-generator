"""Shared HTTP client and OAuth helpers for all platform connectors."""

import base64
import hashlib
import json
import secrets as pysecrets
from typing import Any

import httpx

from app.core.exceptions import AppError, InvalidInputError
from app.services.publishing import secrets

# Tests replace this with httpx.MockTransport to route every platform call to a fake server.
transport: httpx.BaseTransport | None = None

STATE_TTL = 600  # seconds the user has to finish signing in


def client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)


def make_state(platform: str, **extra: Any) -> str:
    """Encrypted, time-limited OAuth `state`: proves the callback belongs to our request."""
    return secrets.encrypt(json.dumps({"platform": platform, **extra}))


def read_state(state: str, platform: str) -> dict[str, Any]:
    try:
        data = json.loads(secrets.decrypt(state, ttl=STATE_TTL))
    except (AppError, ValueError) as exc:
        raise InvalidInputError("The sign-in link expired or is invalid. Try again.") from exc
    if data.get("platform") != platform:
        raise InvalidInputError(f"The sign-in response doesn't belong to {platform}.")
    return data


def pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge) for OAuth PKCE."""
    verifier = pysecrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()) \
        .rstrip(b"=").decode()
    return verifier, challenge
