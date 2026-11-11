from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from engine.errors import LevelError
from engine.flaws.models import FlawSpec
from engine.levels.models import LevelSpec


def _check_cycles(level: LevelSpec) -> None:
    """Verify objective dependency graph (requires) is a DAG with no cycles."""
    obj_ids = {obj.id for obj in level.objectives}
    adj: dict[str, list[str]] = {obj.id: list(obj.requires) for obj in level.objectives}

    # Verify all dependencies exist and no self-loops
    for obj in level.objectives:
        for req in obj.requires:
            if req == obj.id:
                raise LevelError(f"objective {obj.id!r} cannot require itself")
            if req not in obj_ids:
                raise LevelError(
                    f"objective {obj.id!r} requires unknown objective {req!r}"
                )

    # 0 = unvisited, 1 = visiting, 2 = visited
    state: dict[str, int] = {node: 0 for node in obj_ids}

    def dfs(node: str, path: list[str]) -> None:
        state[node] = 1
        for dep in adj[node]:
            if state[dep] == 1:
                cycle_str = " -> ".join([*path, dep])
                raise LevelError(
                    f"cycle detected in objective requirements: {cycle_str}"
                )
            if state[dep] == 0:
                dfs(dep, [*path, dep])
        state[node] = 2

    for node in obj_ids:
        if state[node] == 0:
            dfs(node, [node])


def load_level(
    level_path: Path,
    flaws: dict[str, FlawSpec] | None = None,
    companies_dir: Path | None = None,
) -> LevelSpec:
    """Load and validate a level specification from a YAML file."""
    if not level_path.is_file():
        raise LevelError(f"level file not found: {level_path}")

    try:
        raw_text = level_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)
    except Exception as exc:
        raise LevelError(f"cannot read level file {level_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise LevelError(f"level file {level_path} must define a mapping")

    # If id not in file, default to file stem
    if "id" not in data:
        data["id"] = level_path.stem

    try:
        level = LevelSpec.model_validate(data)
    except ValidationError as exc:
        raise LevelError(f"invalid level specification in {level_path}: {exc}") from exc

    # Check for duplicate objective IDs
    seen_ids: set[str] = set()
    for obj in level.objectives:
        if obj.id in seen_ids:
            raise LevelError(f"duplicate objective id {obj.id!r} in level {level.id!r}")
        seen_ids.add(obj.id)

    # Validate company exists if companies_dir provided
    if companies_dir is not None:
        comp_dir = companies_dir / level.company
        if not comp_dir.is_dir() or not (comp_dir / "company.yaml").is_file():
            raise LevelError(
                f"referenced company {level.company!r} does not exist in {companies_dir}"
            )

    # Validate flaws exist if flaws dict provided
    if flaws is not None:
        for obj in level.objectives:
            if obj.flaw not in flaws:
                raise LevelError(
                    f"objective {obj.id!r} references unknown flaw {obj.flaw!r}"
                )

    # Cycle and dependency check
    _check_cycles(level)

    return level


def load_all_levels(
    levels_dir: Path,
    flaws: dict[str, FlawSpec] | None = None,
    companies_dir: Path | None = None,
) -> dict[str, LevelSpec]:
    """Load all YAML level files found in levels_dir."""
    levels: dict[str, LevelSpec] = {}
    if not levels_dir.is_dir():
        return levels

    for p in sorted(levels_dir.glob("*.yaml")):
        level = load_level(p, flaws=flaws, companies_dir=companies_dir)
        levels[level.id] = level
    for p in sorted(levels_dir.glob("*.yml")):
        if p.stem not in levels:
            level = load_level(p, flaws=flaws, companies_dir=companies_dir)
            levels[level.id] = level

    return levels
