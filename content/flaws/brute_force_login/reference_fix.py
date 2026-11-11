def check_login_rate(ip: str, username: str) -> bool:
    """Sliding window rate limiter per username.

    Note: Limiting per username can lock out legitimate users while under attack;
    this is an intentional game tradeoff.
    """
    import time

    if not hasattr(check_login_rate, "_attempts"):
        check_login_rate._attempts = {}  # type: ignore[attr-defined]

    attempts = check_login_rate._attempts  # type: ignore[attr-defined]
    now = time.time()
    cutoff = now - 60.0

    recent = [t for t in attempts.get(username, []) if t > cutoff]
    if len(recent) >= 5:
        return False

    recent.append(now)
    attempts[username] = recent
    return True
