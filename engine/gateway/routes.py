from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.manifest import RouteSpec


@dataclass(frozen=True)
class _ParsedRoute:
    name: str
    method: str
    segments: tuple[str, ...]
    segment_scores: tuple[int, ...]
    index: int


class RouteMatcher:
    def __init__(self, routes: Sequence[RouteSpec]) -> None:
        self._parsed: list[_ParsedRoute] = []
        for idx, r in enumerate(routes):
            normalized = r.path.rstrip("/") if r.path != "/" else "/"
            segments = tuple(normalized[1:].split("/")) if normalized != "/" else ()
            scores = tuple(
                0 if (seg.startswith("{") and seg.endswith("}")) else 1
                for seg in segments
            )
            self._parsed.append(
                _ParsedRoute(
                    name=r.name,
                    method=r.method.upper(),
                    segments=segments,
                    segment_scores=scores,
                    index=idx,
                )
            )

    def match(self, method: str, path: str) -> str:
        method_upper = method.upper()
        clean_path = path.split("?")[0].split("#")[0]
        if not clean_path.startswith("/"):
            clean_path = "/" + clean_path
        normalized = clean_path.rstrip("/") if clean_path != "/" else "/"
        req_segments = tuple(normalized[1:].split("/")) if normalized != "/" else ()

        candidates: list[tuple[tuple[int, ...], int, int, str]] = []
        for r in self._parsed:
            if len(r.segments) != len(req_segments):
                continue
            if r.method != method_upper and r.method != "*":
                continue

            matched = True
            for r_seg, req_seg in zip(r.segments, req_segments, strict=True):
                is_param = r_seg.startswith("{") and r_seg.endswith("}")
                if not is_param and r_seg != req_seg:
                    matched = False
                    break
            if not matched:
                continue

            is_exact = 1 if r.method == method_upper else 0
            candidates.append((r.segment_scores, is_exact, -r.index, r.name))

        if not candidates:
            return "unmatched"

        candidates.sort(reverse=True)
        return candidates[0][3]
