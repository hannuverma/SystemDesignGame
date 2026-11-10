from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import ValidationError

from engine.errors import FlawError
from engine.flaws.models import FlawSpec
from engine.workspace.stages import scan_text

if TYPE_CHECKING:
    from engine.manifest import CompanyManifest


def load_flaw(
    flaw_dir: Path,
    company_manifest: CompanyManifest,
    company_dir: Path,
) -> FlawSpec:
    """Load and strictly validate a flaw directory against a company."""
    flaw_yaml_path = flaw_dir / "flaw.yaml"
    if not flaw_yaml_path.is_file():
        raise FlawError(f"flaw.yaml not found in {flaw_dir}")

    try:
        raw_text = flaw_yaml_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)
    except Exception as exc:
        raise FlawError(f"cannot read {flaw_yaml_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise FlawError(f"{flaw_yaml_path} must define a mapping")

    try:
        flaw = FlawSpec.model_validate(data)
    except ValidationError as exc:
        raise FlawError(f"invalid flaw.yaml in {flaw_dir}: {exc}") from exc

    # Set dir
    flaw = flaw.model_copy(update={"dir": flaw_dir})

    # Validate reference fix file exists
    ref_fix_path = flaw_dir / flaw.reference_fix
    if not ref_fix_path.is_file():
        raise FlawError(
            f"reference fix file not found: {ref_fix_path} (flaw {flaw.id})"
        )

    # Validate attack command entrypoint exists
    attack_entry = flaw.attack.command[0]
    if attack_entry.endswith(".py"):
        entry_path = (flaw_dir / attack_entry).resolve()
        if not entry_path.is_file():
            raise FlawError(f"attack script not found: {entry_path} (flaw {flaw.id})")

    # Validate endpoint exists in manifest routes
    route_names = {r.name for r in company_manifest.routes}
    if flaw.endpoint not in route_names:
        raise FlawError(
            f"endpoint {flaw.endpoint!r} not found in company routes (flaw {flaw.id})"
        )

    # Scan company stages to verify referenced stage exists
    matching_stage = None
    for src_dir_name in company_manifest.source_dirs:
        src_dir = company_dir / src_dir_name
        if not src_dir.is_dir():
            continue
        for py_file in src_dir.rglob("*.py"):
            try:
                stages = scan_text(
                    py_file.read_text(encoding="utf-8"),
                    str(py_file.relative_to(company_dir)),
                )
            except Exception:
                continue
            for s in stages:
                if s.endpoint == flaw.endpoint and s.name == flaw.stage:
                    matching_stage = s
                    break
            if matching_stage:
                break
        if matching_stage:
            break

    if not matching_stage:
        raise FlawError(
            f"stage {flaw.stage!r} for endpoint {flaw.endpoint!r} not found in company source (flaw {flaw.id})"
        )

    # Validate reference fix syntax and symbol
    ref_code = ref_fix_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(ref_code)
    except SyntaxError as exc:
        raise FlawError(
            f"reference fix syntax error in {ref_fix_path}: {exc} (flaw {flaw.id})"
        ) from exc

    func_defs = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not any(f.name == matching_stage.symbol for f in func_defs):
        raise FlawError(
            f"reference fix {ref_fix_path} does not define expected function {matching_stage.symbol!r} (flaw {flaw.id})"
        )

    return flaw


def load_all_flaws(
    flaws_dir: Path,
    company_manifest: CompanyManifest,
    company_dir: Path,
) -> dict[str, FlawSpec]:
    """Load all flaws present in `flaws_dir`."""
    flaws: dict[str, FlawSpec] = {}
    if not flaws_dir.is_dir():
        return flaws

    for child in flaws_dir.iterdir():
        if child.is_dir() and (child / "flaw.yaml").is_file():
            flaw = load_flaw(child, company_manifest, company_dir)
            flaws[flaw.id] = flaw

    return flaws
