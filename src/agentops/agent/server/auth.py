"""Copilot Extension request signature validation.

Validates the ``X-GitHub-Public-Key-Identifier`` and
``X-GitHub-Public-Key-Signature`` headers against GitHub's published
public keys. The validation can be disabled (``--no-verify``) for local
development and tests.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger(__name__)

GITHUB_KEYS_URL = "https://api.github.com/meta/public_keys/copilot_api"
KEY_CACHE_TTL_SECONDS = 60 * 30  # 30 minutes


@dataclass
class _KeyCache:
    keys: Dict[str, str] = field(default_factory=dict)
    fetched_at: float = 0.0


_cache = _KeyCache()


def _fetch_keys() -> Dict[str, str]:
    import httpx  # local import keeps base CLI lean

    with httpx.Client(timeout=10.0) as client:
        response = client.get(GITHUB_KEYS_URL)
    response.raise_for_status()
    payload = response.json()
    keys = {}
    for entry in payload.get("public_keys", []):
        identifier = entry.get("key_identifier")
        key = entry.get("key")
        if identifier and key:
            keys[identifier] = key
    return keys


def _get_keys(force_refresh: bool = False) -> Dict[str, str]:
    now = time.time()
    if (
        force_refresh
        or not _cache.keys
        or now - _cache.fetched_at > KEY_CACHE_TTL_SECONDS
    ):
        _cache.keys = _fetch_keys()
        _cache.fetched_at = now
    return _cache.keys


def verify_signature(
    body: bytes,
    key_identifier: Optional[str],
    signature_b64: Optional[str],
) -> None:
    """Raise ``ValueError`` if the request signature is invalid."""
    if not key_identifier or not signature_b64:
        raise ValueError("missing Copilot signature headers")

    import base64

    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    keys = _get_keys()
    pem = keys.get(key_identifier)
    if pem is None:
        keys = _get_keys(force_refresh=True)
        pem = keys.get(key_identifier)
    if pem is None:
        raise ValueError(f"unknown key identifier {key_identifier!r}")

    public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise ValueError("Copilot public key is not an EC key")

    try:
        signature = base64.b64decode(signature_b64)
    except Exception as exc:  # pragma: no cover - malformed inputs
        raise ValueError(f"invalid signature encoding: {exc}") from exc

    try:
        public_key.verify(signature, body, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError("signature verification failed") from exc
