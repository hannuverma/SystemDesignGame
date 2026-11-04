"""Cheap pre-flight checks that run before anything touches the workspace."""

from __future__ import annotations

import ast
from collections.abc import Callable


def _check_python(code: str) -> str | None:
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return f"syntax error on line {exc.lineno}: {exc.msg}"
    return None


# language -> checker returning an error message, or None when the code is fine.
SYNTAX_CHECKERS: dict[str, Callable[[str], str | None]] = {"python": _check_python}


def check_syntax(language: str, code: str) -> str | None:
    checker = SYNTAX_CHECKERS.get(language)
    return checker(code) if checker else None
