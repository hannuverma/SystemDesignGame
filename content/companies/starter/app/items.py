from __future__ import annotations

from .db import delete_item_by_id, get_item_by_id, get_username_for_token


# @stage: auth_check endpoint=delete_item order=1
def verify_token(token: str) -> str | None:
    return get_username_for_token(token)


# @stage: ownership_check endpoint=delete_item order=2
def check_item_ownership(username: str, item_id: int) -> bool:
    # Vulnerable: always allows deletion without checking item owner
    return True


# @stage: db_write endpoint=delete_item order=3
def delete_item_from_db(item_id: int) -> bool:
    return delete_item_by_id(item_id)
