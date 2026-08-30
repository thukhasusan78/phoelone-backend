from __future__ import annotations

import base64
import hashlib
import hmac
import secrets


def _wrap_key(pepper: str) -> bytes:
    return hashlib.sha256(b"phoe-lone-token-wrap|" + pepper.encode("utf-8")).digest()


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(key, nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


def wrap_token(token: str, pepper: str) -> str:
    nonce = secrets.token_bytes(16)
    key = _wrap_key(pepper)
    raw = token.encode("utf-8")
    stream = _keystream(key, nonce, len(raw))
    ciphertext = bytes(a ^ b for a, b in zip(raw, stream, strict=True))
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + mac + ciphertext).decode("ascii")


def unwrap_token(blob: str | None, pepper: str) -> str | None:
    if not blob:
        return None
    try:
        packed = base64.urlsafe_b64decode(blob.encode("ascii"))
    except Exception:
        return None
    if len(packed) < 48:
        return None
    nonce, mac, ciphertext = packed[:16], packed[16:48], packed[48:]
    key = _wrap_key(pepper)
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        return None
    stream = _keystream(key, nonce, len(ciphertext))
    raw = bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True))
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
