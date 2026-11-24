"""Authenticated encryption primitives with no third-party dependencies.

The vault uses ChaCha20 (RFC 8439) for confidentiality and HMAC-SHA256 for
integrity (encrypt-then-MAC).  Both algorithms are available in the Python
standard library or implemented here, so the portable ``.exe`` never needs
an extra cryptography wheel.

Key handling
------------
``load_or_create_key`` stores either:

* a raw 32-byte master key (default, protected by file permissions), or
* a salt plus a sealed copy of the master key when a passphrase is supplied.

Backing up the key file together with ``vault.db`` is required to restore the
data; losing the key makes the ciphertext unrecoverable by design.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import struct
from pathlib import Path
from typing import Optional, Union

_CONSTANTS = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
_MASK32 = 0xFFFFFFFF
_NONCE_SIZE = 12
_TAG_SIZE = 16
_PBKDF2_ITERATIONS = 210_000


class IntegrityError(ValueError):
    """Raised when the authentication tag of a sealed payload does not match."""


def _rotl(value: int, bits: int) -> int:
    value &= _MASK32
    return ((value << bits) | (value >> (32 - bits))) & _MASK32


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] = _rotl(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] = _rotl(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & _MASK32
    state[d] = _rotl(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & _MASK32
    state[b] = _rotl(state[b] ^ state[c], 7)


def chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """Return one 64-byte ChaCha20 keystream block (RFC 8439 section 2.3)."""
    if len(key) != 32:
        raise ValueError("ChaCha20 requires a 32-byte key")
    if len(nonce) != _NONCE_SIZE:
        raise ValueError("ChaCha20 requires a 12-byte nonce")
    if not 0 <= counter <= _MASK32:
        raise ValueError("counter must fit in 32 bits")

    key_words = struct.unpack("<8I", key)
    nonce_words = struct.unpack("<3I", nonce)
    state = [
        *_CONSTANTS,
        *key_words,
        counter & _MASK32,
        *nonce_words,
    ]
    working = list(state)
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    return struct.pack("<16I", *((x + y) & _MASK32 for x, y in zip(state, working)))


def chacha20_xor(key: bytes, nonce: bytes, data: bytes, counter: int = 0) -> bytes:
    """Encrypt or decrypt ``data`` with the ChaCha20 stream cipher."""
    if len(nonce) != _NONCE_SIZE:
        raise ValueError("ChaCha20 requires a 12-byte nonce")
    chunks = []
    for offset in range(0, len(data), 64):
        block = chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset : offset + 64]
        chunks.append(bytes(a ^ b for a, b in zip(chunk, block)))
    return b"".join(chunks)


def derive_key(passphrase: str, salt: bytes, iterations: int = _PBKDF2_ITERATIONS) -> bytes:
    """Derive a 32-byte key from a passphrase with PBKDF2-HMAC-SHA256."""
    if not passphrase:
        raise ValueError("passphrase must not be empty")
    if len(salt) < 16:
        raise ValueError("salt must be at least 16 bytes")
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32
    )


def _mac(key: bytes, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    message = struct.pack("<I", len(aad)) + aad + nonce + ciphertext
    return hmac.new(key, message, hashlib.sha256).digest()[:_TAG_SIZE]


def seal(plaintext: bytes, key: bytes, aad: bytes = b"") -> bytes:
    """Encrypt and authenticate ``plaintext``; returns nonce||tag||ciphertext."""
    if len(key) != 32:
        raise ValueError("a 32-byte key is required")
    nonce = secrets.token_bytes(_NONCE_SIZE)
    ciphertext = chacha20_xor(key, nonce, plaintext)
    tag = _mac(key, nonce, aad, ciphertext)
    return nonce + tag + ciphertext


def unseal(envelope: bytes, key: bytes, aad: bytes = b"") -> bytes:
    """Verify and decrypt an envelope produced by :func:`seal`."""
    if len(key) != 32:
        raise ValueError("a 32-byte key is required")
    minimum = _NONCE_SIZE + _TAG_SIZE
    if len(envelope) < minimum:
        raise IntegrityError("sealed payload is truncated")
    nonce = envelope[:_NONCE_SIZE]
    tag = envelope[_NONCE_SIZE:minimum]
    ciphertext = envelope[minimum:]
    expected = _mac(key, nonce, aad, ciphertext)
    if not hmac.compare_digest(tag, expected):
        raise IntegrityError("authentication failed: wrong key or modified data")
    return chacha20_xor(key, nonce, ciphertext)


def _restrict_file(path: Path) -> None:
    """Best-effort owner-only permissions (Windows ACLs are handled by NTFS)."""
    try:
        path.chmod(0o600)
    except OSError:
        pass


def load_or_create_key(path: Union[str, Path], passphrase: Optional[str] = None) -> bytes:
    """Load the master key from ``path``, creating it when missing.

    With ``passphrase`` the file stores ``salt||sealed_master_key``; otherwise
    it stores the raw master key.  A passphrase cannot be changed after the
    vault has been created without re-encrypting the database.
    """
    key_path = Path(path)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        raw = key_path.read_bytes()
        if raw.startswith(b"AFPB"):
            if not passphrase:
                raise ValueError("this vault key is passphrase protected")
            salt = raw[4:20]
            wrapped = raw[20:]
        else:
            if passphrase:
                raise ValueError("this vault key is not passphrase protected")
            if len(raw) != 32:
                raise IntegrityError("vault key file is corrupt")
            return raw
        return unseal(wrapped, derive_key(passphrase, salt), b"audiofutbol/master-key")

    master = secrets.token_bytes(32)
    if passphrase:
        salt = secrets.token_bytes(16)
        wrapped = seal(master, derive_key(passphrase, salt), b"audiofutbol/master-key")
        payload = b"AFPB" + salt + wrapped
    else:
        payload = master
    key_path.write_bytes(payload)
    _restrict_file(key_path)
    return master
