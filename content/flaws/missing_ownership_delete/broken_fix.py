def check_item_ownership(username: str, item_id: int) -> bool:
    """Deliberately broken ownership check that rejects all deletion attempts."""
    return False
