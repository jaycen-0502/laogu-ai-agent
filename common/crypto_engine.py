"""High-security encryption and decryption engine for automation script assets.

Protects proprietary business logic with AES-256-GCM authenticated encryption.
Script assets are stored on disk in encrypted ciphertext and decrypted
exclusively in process memory (RAM) at execution time with zero disk landing.
"""

from __future__ import annotations

import hashlib
import os
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC_HEADER: Final[bytes] = b"LGENC1\x00"  # 8-byte container header
_DEFAULT_KEY_SEED: Final[bytes] = b"laogu-ai-agent-engine-asset-secret-v1-2026-matrix-shield"


def get_master_key() -> bytes:
    """Derive 256-bit AES master key from environment or built-in high-entropy seed."""
    env_key = os.getenv("LAOGU_ENGINE_ENCRYPTION_KEY", "").strip()
    seed = env_key.encode("utf-8") if env_key else _DEFAULT_KEY_SEED
    return hashlib.sha256(seed).digest()


def is_encrypted_engine(payload: bytes) -> bool:
    """Return whether the byte payload begins with the encrypted asset header."""
    return bool(payload and payload.startswith(MAGIC_HEADER) and len(payload) >= len(MAGIC_HEADER) + 28)


def encrypt_engine_code(source: bytes | str, *, deterministic: bool = False) -> bytes:
    """Encrypt plaintext Python source code into AES-256-GCM asset payload.

    Container layout:
    - 8 bytes: MAGIC_HEADER (b'LGENC1\\x00')
    - 12 bytes: Nonce (IV, random or deterministic)
    - N bytes: Ciphertext + 16-byte Authentication Tag
    """
    data = source.encode("utf-8") if isinstance(source, str) else source
    key = get_master_key()
    aesgcm = AESGCM(key)
    nonce = hashlib.sha256(data).digest()[:12] if deterministic else os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, data, None)
    return MAGIC_HEADER + nonce + ciphertext


def decrypt_engine_code(payload: bytes) -> bytes:
    """Decrypt AES-256-GCM asset payload strictly in memory.

    If the payload is not encrypted (e.g. legacy cached code), returns payload
    as-is for backward compatibility.
    """
    if not is_encrypted_engine(payload):
        return payload
    key = get_master_key()
    nonce = payload[len(MAGIC_HEADER) : len(MAGIC_HEADER) + 12]
    ciphertext = payload[len(MAGIC_HEADER) + 12 :]
    aesgcm = AESGCM(key)
    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise ValueError("Failed to decrypt automation engine: payload corrupted or key mismatch") from exc
