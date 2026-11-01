from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TokenEnvelopeError(ValueError):
    pass


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise TokenEnvelopeError("encrypted token envelope is malformed") from exc


class TokenCipher:
    """Versioned AES-256-GCM envelopes bound to a tenant/resource context."""

    _VERSION = "v1"
    _AAD_PREFIX = b"zohra-clipos:publishing-token:v1:"

    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("token encryption material must contain at least 32 characters")
        self._key = hashlib.sha256(secret.encode("utf-8")).digest()
        self._cipher = AESGCM(self._key)

    @classmethod
    def _aad(cls, context: str) -> bytes:
        if not context or len(context) > 500:
            raise ValueError("token envelope context is invalid")
        return cls._AAD_PREFIX + context.encode("utf-8")

    def encrypt(self, value: dict[str, Any], *, context: str) -> str:
        plaintext = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, self._aad(context))
        return f"{self._VERSION}.{_encode(nonce)}.{_encode(ciphertext)}"

    def decrypt(self, envelope: str, *, context: str) -> dict[str, Any]:
        parts = envelope.split(".")
        if len(parts) != 3 or parts[0] != self._VERSION:
            raise TokenEnvelopeError("encrypted token envelope version is unsupported")
        nonce = _decode(parts[1])
        ciphertext = _decode(parts[2])
        if len(nonce) != 12 or len(ciphertext) < 16:
            raise TokenEnvelopeError("encrypted token envelope is malformed")
        try:
            plaintext = self._cipher.decrypt(nonce, ciphertext, self._aad(context))
            parsed = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TokenEnvelopeError("encrypted token envelope authentication failed") from exc
        if not isinstance(parsed, dict):
            raise TokenEnvelopeError("encrypted token envelope payload is invalid")
        return parsed
