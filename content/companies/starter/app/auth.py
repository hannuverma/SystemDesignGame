from __future__ import annotations

from .db import verify_user_password


# @stage: rate_limit endpoint=login order=1
def check_login_rate(ip: str, username: str) -> bool:
    return True


# @stage: auth_check endpoint=login order=2
def check_credentials(username: str, password: str) -> bool:
    return verify_user_password(username, password)
