from fastapi import FastAPI
from .greetings import build_greeting

app = FastAPI(title="Starter Company")


@app.get("/health")
def health():
    return{"status": "ok"}


@app.get("/hello")
def hello(name: str = "world"):
    return build_greeting(name=name)
