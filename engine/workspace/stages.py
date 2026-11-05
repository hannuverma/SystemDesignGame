"""Stage parser and writer.

A stage is an editable region of company code, marked by a tag comment directly above a
top-level function or class:

    # @stage: <name> endpoint=<endpoint> order=<int>

Regions are located with Python's own `ast` module, so multi-line strings, decorators and
odd indentation can't confuse the boundary. Writing replaces ONLY the region; every other
byte of the file is preserved. To support another language, add a RegionFinder for it.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass

from engine.errors import InvalidStageCode, StageNotFound, StageParseError
from engine.workspace.models import Stage

TAG_RE = re.compile(
    r"^#[ \t]*@stage:[ \t]*(?P<name>\w+)"
    r"[ \t]+endpoint=(?P<endpoint>\w+)"
    r"[ \t]+order=(?P<order>\d+)[ \t]*$"
)
LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+")
DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

# glob of source files to scan, per manifest language
LANGUAGE_GLOBS = {"python": "*.py"}


def split_lines(text: str) -> list[str]:
    """Split keeping line endings. Only \\n, \\r\\n and \\r count (matches `ast`/`tokenize`)."""
    return LINE_RE.findall(text)


def _normalize(code: str) -> str:
    return code.replace("\r\n", "\n").replace("\r", "\n")


def _first_line(node: ast.AST) -> int:
    decorators = getattr(node, "decorator_list", [])
    return min([d.lineno for d in decorators] + [node.lineno])  # type: ignore[attr-defined]


@dataclass(frozen=True)
class _Tag:
    line_index: int  # 0-based index of the tag line
    name: str
    endpoint: str
    order: int


def _find_tags(text: str, filename: str) -> list[_Tag]:
    if "@stage" not in text:
        return []
    tags = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type != tokenize.COMMENT or "@stage" not in tok.string:
                continue
            row, col = tok.start
            match = TAG_RE.match(tok.string)
            if col != 0 or match is None:
                raise StageParseError(
                    f"{filename}:{row}: malformed stage tag, expected "
                    "'# @stage: <name> endpoint=<endpoint> order=<int>' at column 0"
                )
            tags.append(
                _Tag(row - 1, match["name"], match["endpoint"], int(match["order"]))
            )
    except (tokenize.TokenError, SyntaxError) as exc:
        raise StageParseError(f"{filename}: cannot tokenize source: {exc}") from exc
    return tags


def scan_text(text: str, filename: str) -> list[Stage]:
    """Return every stage in one file's text. Raises StageParseError on malformed tags."""
    tags = _find_tags(text, filename)
    if not tags:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise StageParseError(
            f"{filename}: cannot parse source: {exc.msg} (line {exc.lineno})"
        ) from exc
    top_level = {_first_line(n): n for n in tree.body if isinstance(n, DEFS)}
    lines = split_lines(text)

    stages: list[Stage] = []
    seen: set[tuple[str, str]] = set()
    for tag in tags:
        node = top_level.get(tag.line_index + 2)  # 1-based line right below the tag
        if node is None:
            raise StageParseError(
                f"{filename}:{tag.line_index + 1}: stage tag must sit directly above a "
                "top-level function or class"
            )
        if (tag.endpoint, tag.name) in seen:
            raise StageParseError(
                f"{filename}: duplicate stage {tag.name!r} for endpoint {tag.endpoint!r}"
            )
        seen.add((tag.endpoint, tag.name))
        start_idx, end_idx = tag.line_index + 1, node.end_lineno - 1  # type: ignore[attr-defined]
        code = _normalize("".join(lines[start_idx : end_idx + 1])).rstrip("\n")
        stages.append(
            Stage(
                name=tag.name,
                endpoint=tag.endpoint,
                order=tag.order,
                file=filename,
                symbol=node.name,  # type: ignore[attr-defined]
                start_line=start_idx + 1,
                end_line=end_idx + 1,
                code=code,
            )
        )
    return stages


def _validate_new_code(new_code: str, max_bytes: int) -> tuple[str, str]:
    """Return (normalized code, symbol name) or raise InvalidStageCode."""
    if not isinstance(new_code, str):
        raise InvalidStageCode("code must be a string")
    if len(new_code.encode("utf-8")) > max_bytes:
        raise InvalidStageCode(f"code is larger than {max_bytes} bytes")
    if "\x00" in new_code:
        raise InvalidStageCode("code contains a NUL byte")
    code = _normalize(new_code).strip("\n")
    if not code.strip():
        raise InvalidStageCode("code is empty")
    if "@stage" in code:
        raise InvalidStageCode("code must not contain '@stage' tags")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise InvalidStageCode(f"syntax error on line {exc.lineno}: {exc.msg}") from exc
    if len(tree.body) != 1 or not isinstance(tree.body[0], DEFS):
        raise InvalidStageCode(
            "a stage must be exactly one top-level function or class "
            "(put imports and helpers inside it)"
        )
    return code, tree.body[0].name  # type: ignore[attr-defined]


def apply_stage_edit(
    text: str, filename: str, endpoint: str, name: str, new_code: str, *, max_bytes: int
) -> str:
    """Return `text` with one stage's region replaced. Pure function: nothing is written."""
    code, symbol = _validate_new_code(new_code, max_bytes)
    before = scan_text(text, filename)
    target = next((s for s in before if s.key == (endpoint, name)), None)
    if target is None:
        raise StageNotFound(
            f"no stage {name!r} for endpoint {endpoint!r} in {filename}"
        )
    if symbol != target.symbol:
        raise InvalidStageCode(
            f"the stage must keep defining {target.symbol!r}, not {symbol!r}"
        )

    lines = split_lines(text)
    eol = "\r\n" if "\r\n" in text else "\n"
    last = lines[target.end_line - 1]
    trailing_eol = last[
        len(last.rstrip("\r\n")) :
    ]  # keep "no newline at EOF" as it was
    new_region = code.replace("\n", eol) + trailing_eol
    new_text = (
        "".join(lines[: target.start_line - 1])
        + new_region
        + "".join(lines[target.end_line :])
    )

    # Re-verify: the edit must change this stage and nothing else.
    after = scan_text(new_text, filename)
    others_before = [(s.key, s.symbol, s.code) for s in before if s.key != target.key]
    others_after = [(s.key, s.symbol, s.code) for s in after if s.key != target.key]
    edited = [s for s in after if s.key == target.key]
    if others_before != others_after or len(edited) != 1 or edited[0].code != code:
        raise InvalidStageCode("edit would change more than the stage region; rejected")
    return new_text
