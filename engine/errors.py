"""Typed errors. The API layer maps these to HTTP codes in one place (engine/api/app.py)."""

from __future__ import annotations


class GameError(Exception):
    """Base class for every error the engine raises on purpose."""


class ManifestError(GameError):
    """company.yaml is missing, unreadable or invalid."""


class CompanyNotFound(GameError):
    """No such company template under content/companies/."""


class SessionNotFound(GameError):
    """No such session (or the id is malformed)."""


class PathEscape(GameError):
    """A path tried to leave the directory it is confined to."""


class StageNotFound(GameError):
    """No stage with that endpoint/name exists in the workspace."""


class StageParseError(GameError):
    """A @stage tag in the source is malformed or misplaced."""


class InvalidStageCode(GameError):
    """Submitted stage code is structurally unacceptable (nothing was written)."""


class GitError(GameError):
    """A git operation failed."""


class FlawError(GameError):
    """A flaw module is missing, malformed or references invalid stages."""


class FlawNotFound(FlawError):
    """No flaw with the given ID was found."""


class LevelError(GameError):
    """A level specification is missing, malformed or invalid."""


class LevelNotFound(LevelError):
    """No level with the given ID was found."""


class AttackConflict(GameError):
    """An attack is already actively running for this session."""


class AttackNotFound(GameError):
    """No attack run with the given ID was found."""


class RuntimeStartError(GameError):
    """The company process could not be started or did not become healthy."""

    def __init__(self, message: str, log_tail: list[str] | None = None) -> None:
        super().__init__(message)
        self.log_tail = log_tail or []
