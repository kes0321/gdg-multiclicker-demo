"""Environment settings shared by direct Windows runs and Linux containers."""

import os

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

_cookie_secure = os.environ.get("SESSION_COOKIE_SECURE", "false").lower()
if _cookie_secure not in {"true", "false", "1", "0"}:
    raise ValueError("SESSION_COOKIE_SECURE must be true, false, 1, or 0")
SESSION_COOKIE_SECURE = _cookie_secure in {"true", "1"}

ADMIN_PATH = os.environ.get("ADMIN_PATH", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_SESSION_TTL_SECONDS = int(os.environ.get("ADMIN_SESSION_TTL_SECONDS", "1800"))
