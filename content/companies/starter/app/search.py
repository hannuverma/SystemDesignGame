from __future__ import annotations

from .db import get_db_connection


# @stage: validation endpoint=search order=1
def validate_search_query(q: str) -> str:
    return q


# @stage: db_query endpoint=search order=2
def execute_search(q: str) -> list[dict]:
    conn = get_db_connection()
    try:
        # Vulnerable: string formatted SQL query
        query = f"SELECT id, name, price FROM items WHERE name LIKE '%{q}%'"
        rows = conn.execute(query).fetchall()
        return [
            {"id": row["id"], "name": row["name"], "price": row["price"]}
            for row in rows
        ]
    finally:
        conn.close()
