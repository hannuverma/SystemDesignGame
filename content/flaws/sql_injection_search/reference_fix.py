def execute_search(q: str) -> list[dict]:
    """Safely execute item search using parameterized SQL query."""
    from .db import get_db_connection

    conn = get_db_connection()
    try:
        query = "SELECT id, name, price FROM items WHERE name LIKE ?"
        rows = conn.execute(query, (f"%{q}%",)).fetchall()
        return [
            {"id": row["id"], "name": row["name"], "price": row["price"]}
            for row in rows
        ]
    finally:
        conn.close()
