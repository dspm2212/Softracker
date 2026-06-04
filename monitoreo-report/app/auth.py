"""Authentication helpers for the monitoring report application.

Provides password hashing and credential validation for the dashboard.
The report app uses username + password auth (not the agent token).

Author: Daniel Perez
"""

from __future__ import annotations

import hashlib

from app.config import AppConfig


def hash_password(password: str) -> str:
    """Return the SHA-256 hex digest of a plaintext password.

    Args:
        password: Plaintext password string.

    Returns:
        Lowercase hexadecimal SHA-256 digest.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


# Keep hash_token as an alias so existing imports don't break.
hash_token = hash_password


def check_credentials(username: str, password: str, config: AppConfig) -> bool:
    """Validate a username and plaintext password against the stored credentials.

    Args:
        username: Username submitted via the login form.
        password: Plaintext password submitted via the login form.
        config: Application config containing the stored credentials.

    Returns:
        True if both username and hashed password match the config.
    """
    return (
        username == config.credentials.username
        and hash_password(password) == config.credentials.password_hash
    )
