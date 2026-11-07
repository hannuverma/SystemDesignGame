from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from .auth import check_credentials, check_login_rate
from .db import get_all_items, init_db, reset_database, store_token
from .greeting import build_greeting
from .items import check_item_ownership, delete_item_from_db, verify_token
from .search import execute_search, validate_search_query


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Starter Company", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/hello")
def hello(name: str = "world"):
    return build_greeting(name=name)


@app.post("/__game/reset")
def game_reset(request: Request):
    expected_secret = os.environ.get("GAME_RESET_SECRET")
    secret = request.headers.get("X-Game-Secret")
    if not expected_secret or secret != expected_secret:
        raise HTTPException(status_code=403, detail="Forbidden")
    reset_database()
    return {"status": "ok"}


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/login")
def login(req: LoginRequest, request: Request):
    ip = request.headers.get("x-forwarded-for") or (
        request.client.host if request.client else "127.0.0.1"
    )

    if not check_login_rate(ip, req.username):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    if not check_credentials(req.username, req.password):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    token = secrets.token_hex(16)
    store_token(token, req.username)
    return {"token": token, "username": req.username}


@app.get("/search")
def search(q: str = ""):
    clean_q = validate_search_query(q)
    results = execute_search(clean_q)
    return {"query": q, "results": results}


@app.get("/items")
def list_items():
    return get_all_items()


@app.delete("/items/{item_id}")
def delete_item(item_id: int, authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    token = authorization[len("Bearer ") :].strip()
    username = verify_token(token)
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token")

    if not check_item_ownership(username, item_id):
        raise HTTPException(status_code=403, detail="Forbidden")

    if not delete_item_from_db(item_id):
        raise HTTPException(status_code=404, detail="Item not found")

    return {"deleted": item_id}
