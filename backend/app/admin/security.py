"""Password hashing and settings for the optional single-superadmin console."""

from dataclasses import dataclass
from getpass import getpass
import hashlib
import hmac
import re
import secrets

HASH_PREFIX = "scrypt:32768:8:3:"
PATH_PATTERN = r"/[A-Za-z0-9][A-Za-z0-9_-]{7,63}"


def valid_hash(value: str) -> bool:
    return bool(re.fullmatch(re.escape(HASH_PREFIX) + r"[0-9a-f]{32}:[0-9a-f]{64}", value))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=32768, r=8, p=3,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return f"{HASH_PREFIX}{salt.hex()}:{digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not valid_hash(stored):
        return False
    salt = bytes.fromhex(stored.split(":")[-2])
    return hmac.compare_digest(hash_password(password, salt), stored)


@dataclass(frozen=True)
class AdminSettings:
    path: str = ""
    password_hash: str = ""
    ttl: int = 1800
    secure_cookie: bool = False

    def __post_init__(self):
        if self.path and (not re.fullmatch(PATH_PATTERN, self.path)
                          or self.path in {"/nginx-health", "/openapi"}):
            raise ValueError("ADMIN_PATH must be / followed by 8..64 ASCII letters, digits, _ or -")
        if self.password_hash and not valid_hash(self.password_hash):
            raise ValueError("ADMIN_PASSWORD_HASH is invalid; generate it with python -m app.admin.security")
        if not 60 <= self.ttl <= 86400:
            raise ValueError("ADMIN_SESSION_TTL_SECONDS must be within 60..86400")

    @property
    def enabled(self) -> bool:
        return bool(self.path and self.password_hash)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256((self.path + "\n" + self.password_hash).encode()).hexdigest()


if __name__ == "__main__":
    password = getpass("Admin password (16..128 characters): ")
    if not 16 <= len(password) <= 128:
        raise SystemExit("Password must contain 16..128 characters.")
    if not hmac.compare_digest(password.encode(), getpass("Confirm password: ").encode()):
        raise SystemExit("Passwords do not match.")
    print(f"ADMIN_PATH=/control-{secrets.token_hex(12)}")
    print(f"ADMIN_PASSWORD_HASH={hash_password(password)}")
    print("ADMIN_SESSION_TTL_SECONDS=1800")
