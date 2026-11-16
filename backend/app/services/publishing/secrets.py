"""Encryption for stored account tokens (Fernet: AES-128-CBC + HMAC-SHA256).

The key comes from TOKEN_ENCRYPTION_KEY, or is generated once into storage/secret.key
(storage/ is never committed). If the key is lost, saved tokens can't be read: reconnect
the accounts.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.exceptions import AppError


class TokenDecryptionError(AppError):
    status_code = 409
    code = "account_reconnect_required"


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.token_encryption_key is not None:
        return Fernet(settings.token_encryption_key.get_secret_value().encode())
    key_file = settings.storage_dir / "secret.key"
    if not key_file.is_file():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(Fernet.generate_key())
    return Fernet(key_file.read_bytes().strip())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str, *, ttl: int | None = None) -> str:
    try:
        return _fernet().decrypt(value.encode(), ttl=ttl).decode()
    except InvalidToken as exc:
        raise TokenDecryptionError(
            "Saved login data could not be read (expired or the encryption key changed). "
            "Reconnect the account."
        ) from exc


def reset_cache() -> None:
    _fernet.cache_clear()
