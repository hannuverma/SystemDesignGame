def check_item_ownership(username: str, item_id: int) -> bool:
    """Verify that the item belongs to the authenticated user before deletion."""
    try:
        from app.db import get_item_by_id
    except ImportError:
        from content.companies.starter.app.db import get_item_by_id

    item = get_item_by_id(item_id)
    if not item:
        return False
    return item.get("owner") == username
