def check_login_rate(ip: str, username: str) -> bool:
    """Deliberately broken rate limiter that rejects all login attempts."""
    return False
