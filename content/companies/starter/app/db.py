from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "app.db"


def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, bytes]:
    salt = salt or os.urandom(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 1000)
    return hashed.hex(), salt


def verify_user_password(username: str, password: str) -> bool:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE username = ?", (username,)
        ).fetchone()
        if not row:
            return False
        stored_hash = row["password_hash"]
        salt = row["salt"]
        computed_hash, _ = hash_password(password, salt)
        return computed_hash == stored_hash
    finally:
        conn.close()


def store_token(token: str, username: str) -> None:
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO tokens (token, username) VALUES (?, ?)",
            (token, username),
        )
        conn.commit()
    finally:
        conn.close()


def get_username_for_token(token: str) -> str | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT username FROM tokens WHERE token = ?", (token,)
        ).fetchone()
        return row["username"] if row else None
    finally:
        conn.close()


def get_item_by_id(item_id: int) -> dict | None:
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, name, price, owner FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
    finally:
        conn.close()


def delete_item_by_id(item_id: int) -> bool:
    conn = get_db_connection()
    try:
        cur = conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_all_items() -> list[dict]:
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT id, name, price FROM items ORDER BY id ASC"
        ).fetchall()
        return [
            {"id": row["id"], "name": row["name"], "price": row["price"]}
            for row in rows
        ]
    finally:
        conn.close()


def reset_database() -> None:
    """Drop, recreate and seed database tables."""
    conn = get_db_connection()
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS users;
            DROP TABLE IF EXISTS items;
            DROP TABLE IF EXISTS tokens;

            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt BLOB NOT NULL
            );

            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price REAL NOT NULL,
                owner TEXT NOT NULL
            );

            CREATE TABLE tokens (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL
            );
            """
        )

        # Seed users
        users = [
            ("alice", "alice_super_secret_password_123!"),
            ("admin", "admin"),
            ("bob", "bob_password_123"),
        ]
        for uname, pwd in users:
            h, s = hash_password(pwd)
            conn.execute(
                "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
                (uname, h, s),
            )

        # Seed items
        items = [
            ("laptop", 999.0, "alice"),
            ("mouse", 25.0, "bob"),
            ("keyboard", 75.0, "alice"),
        ]
        conn.executemany(
            "INSERT INTO items (name, price, owner) VALUES (?, ?, ?)",
            items,
        )
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database if it does not exist yet."""
    if not DB_PATH.is_file():
        reset_database()
