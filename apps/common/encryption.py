from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


DEV_FERNET_KEY = "K7gNU3sdo-OL0wNhqoVWhr3g6s1xYv72ol_pe_UnGGY="


def _get_fernet() -> Fernet:
    key = settings.FIELD_ENCRYPTION_KEY
    if not key:
        if settings.DEBUG:
            key = DEV_FERNET_KEY
        else:
            raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be set in environment")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plaintext: str) -> bytes:
    if not plaintext:
        return b""
    return _get_fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_value(ciphertext: bytes) -> str:
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt value") from exc


def generate_encryption_key() -> str:
    return Fernet.generate_key().decode("utf-8")
