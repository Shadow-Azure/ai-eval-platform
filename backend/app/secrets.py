"""Credential encryption boundary.

Production uses a KMS envelope implementation; the local box encrypts with
Fernet and a versioned key from configuration. Plaintext never leaves this
module except when the model gateway proxies a request upstream.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

_DEV_KEY_SEED = b"dsh-ai-eval-platform-local-secret-box"


class SecretBoxError(RuntimeError):
    """Encryption is unavailable or the ciphertext cannot be decrypted."""


class LocalSecretBox:
    def __init__(self, key: str, version: str = "local:1") -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as error:
            raise SecretBoxError("密钥格式无效：需要 urlsafe base64 的 32 字节 Fernet 密钥") from error
        self.version = version

    def encrypt(self, plaintext: str) -> tuple[str, str]:
        if not plaintext:
            raise SecretBoxError("密钥不能为空")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii"), self.version

    def decrypt(self, ciphertext: str, key_version: str) -> str:
        if key_version != self.version:
            raise SecretBoxError(f"不支持的密钥版本：{key_version}")
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as error:
            raise SecretBoxError("密钥解密失败") from error


def _dev_key() -> str:
    digest = hashlib.sha256(_DEV_KEY_SEED).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")


@lru_cache
def get_secret_box() -> LocalSecretBox:
    settings = get_settings()
    key = settings.secret_box_key
    if not key:
        if settings.environment not in ("development", "test"):
            raise SecretBoxError("生产环境必须配置 SECRET_BOX_KEY")
        key = _dev_key()
    return LocalSecretBox(key)


def mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "****"
    return f"****{secret[-4:]}"


def reset_secret_box_cache() -> None:
    """Test hook: settings changes require a fresh box."""

    get_secret_box.cache_clear()
