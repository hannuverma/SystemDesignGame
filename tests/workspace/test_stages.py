from __future__ import annotations

import pytest

from engine.errors import InvalidStageCode, StageNotFound, StageParseError
from engine.workspace.stages import apply_stage_edit, scan_text

MAX = 20_000

SRC = '''\
import os

# @stage: greeting endpoint=hello order=1
def build(name):
    return {"m": name}


def other():
    return 1


# @stage: price endpoint=cart order=2
@staticmethod
def price(x):
    text = """
not_indented_on_purpose
"""
    return x
'''


def edit(text, endpoint, name, code):
    return apply_stage_edit(text, "f.py", endpoint, name, code, max_bytes=MAX)


def test_scan_finds_regions_even_with_decorators_and_col0_strings():
    stages = {s.name: s for s in scan_text(SRC, "f.py")}
    assert set(stages) == {"greeting", "price"}
    assert stages["greeting"].code == 'def build(name):\n    return {"m": name}'
    assert stages["greeting"].symbol == "build"
    assert stages["price"].code.startswith("@staticmethod\ndef price")
    assert stages["price"].code.endswith("    return x")  # the col-0 string did not end it early


def test_round_trip_is_byte_identical():
    for stage in scan_text(SRC, "f.py"):
        assert edit(SRC, stage.endpoint, stage.name, stage.code) == SRC


def test_only_the_region_changes():
    new = edit(SRC, "hello", "greeting", 'def build(name):\n    return {"m": "yo " + name}')
    assert new.replace('"yo " + name', "name") == SRC
    assert "def other():\n    return 1" in new


def test_edit_can_grow_and_shrink():
    big = "def build(name):\n" + "".join(f"    x{i} = {i}\n" for i in range(30)) + "    return {}"
    grown = edit(SRC, "hello", "greeting", big)
    assert scan_text(grown, "f.py")[0].code == big
    shrunk = edit(grown, "hello", "greeting", "def build(name):\n    return {}")
    assert "x29" not in shrunk and "def other" in shrunk


def test_crlf_files_keep_their_line_endings():
    crlf = SRC.replace("\n", "\r\n")
    new = edit(crlf, "hello", "greeting", "def build(name):\n    return 1")
    assert "\n" not in new.replace("\r\n", "")  # no bare LFs introduced
    assert edit(crlf, "hello", "greeting", scan_text(crlf, "f.py")[0].code) == crlf


def test_no_trailing_newline_at_eof_is_preserved():
    text = "# @stage: a endpoint=e order=1\ndef f():\n    return 1"
    assert edit(text, "e", "a", "def f():\n    return 2") == text.replace("return 1", "return 2")
    assert not edit(text, "e", "a", "def f():\n    return 2\n\n").endswith("\n")


def test_leading_and_trailing_blank_lines_in_new_code_are_ignored():
    new = edit(SRC, "hello", "greeting", "\n\ndef build(name):\n    return 2\n\n\n")
    assert "# @stage: greeting endpoint=hello order=1\ndef build(name):\n    return 2\n" in new


@pytest.mark.parametrize(
    "code, message",
    [
        ("", "empty"),
        ("   \n", "empty"),
        ("def build(name:\n    pass", "syntax error"),
        ("x = 1", "exactly one"),
        ("def build(n):\n    pass\n\ndef extra():\n    pass", "exactly one"),
        ("import os\ndef build(n):\n    pass", "exactly one"),
        ("def renamed(n):\n    pass", "keep defining"),
        ("# @stage: x endpoint=y order=1\ndef build(n):\n    pass", "@stage"),
        ("def build(n):\n    return '@stage'", "@stage"),
        ("def build(n):\n    return 1\x00", "NUL"),
        ("  def build(n):\n      pass", "syntax error"),
    ],
)
def test_invalid_new_code_rejected(code, message):
    with pytest.raises(InvalidStageCode, match=message):
        edit(SRC, "hello", "greeting", code)


def test_size_limit():
    with pytest.raises(InvalidStageCode, match="larger"):
        apply_stage_edit(
            SRC, "f.py", "hello", "greeting", "def build(n):\n    pass\n" + "#" * 50, max_bytes=30
        )


def test_unknown_stage():
    with pytest.raises(StageNotFound):
        edit(SRC, "hello", "nope", "def build(n):\n    pass")


@pytest.mark.parametrize(
    "text, message",
    [
        ("# @stage: a endpoint=e order=1\n", "directly above"),  # tag at EOF
        ("# @stage: a endpoint=e order=1\n\ndef f():\n    pass\n", "directly above"),  # blank gap
        ("# @stage: a endpoint=e order=1\nx = 1\n", "directly above"),  # not a def
        ("# @stage: a endpoint=e\ndef f():\n    pass\n", "malformed"),  # missing order
        ("  # @stage: a endpoint=e order=1\ndef f():\n    pass\n", "malformed"),  # indented tag
        (
            "# @stage: a endpoint=e order=1\ndef f():\n    pass\n"
            "# @stage: a endpoint=e order=2\ndef g():\n    pass\n",
            "duplicate",
        ),
        ("# @stage: a endpoint=e order=1\ndef f(:\n", "cannot (parse|tokenize)"),
    ],
)
def test_malformed_sources_raise(text, message):
    with pytest.raises(StageParseError, match=message):
        scan_text(text, "f.py")


def test_tag_inside_a_string_is_not_a_tag():
    text = 'DOC = """\n# @stage: fake endpoint=x order=1\ndef f(): pass\n"""\n'
    assert scan_text(text, "f.py") == []


def test_same_name_different_endpoints_is_fine():
    text = (
        "# @stage: a endpoint=e1 order=1\ndef f():\n    pass\n\n"
        "# @stage: a endpoint=e2 order=1\ndef g():\n    pass\n"
    )
    assert len(scan_text(text, "f.py")) == 2
