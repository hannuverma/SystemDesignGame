from __future__ import annotations

import time

# Sliding window of recent attempts per username.
# Note: Limiting per username can lock out legitimate users while under attack;
# this is an intentional game tradeoff.
_ATTEMPTS: dict[str, list[float]] = {}


# @stage: rate_limit endpoint=login order=1
def check_login_rate(ip: str, username: str) -> bool:
    now = time.time()
    cutoff = now - 60.0
    recent = [t for t in _ATTEMPTS.get(username, []) if t > cutoff]
    if len(recent) >= 5:
        return False
    recent.append(now)
    _ATTEMPTS[username] = recent
    return True

