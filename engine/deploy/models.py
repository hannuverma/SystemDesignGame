from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

DeployStatus = Literal["deployed", "rejected", "rolled_back", "broken"]
# rejected     - refused before anything was written (syntax/structure); nothing changed
# rolled_back  - the new code failed to run; previous good version is live again
# broken       - even the rollback failed; the company is down


@dataclass(frozen=True)
class DeployResult:
    status: DeployStatus
    step: str | None  # which step failed: validate | write | commit | restart | health | rollback
    message: str
    endpoint: str | None = None
    stage: str | None = None
    commit: str | None = None  # commit that is live after this deploy
    previous_commit: str | None = None
    url: str | None = None
    duration_s: float = 0.0
    log_tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# Maps result status to the HTTP status the API answers with.
HTTP_STATUS = {"deployed": 200, "rejected": 422, "rolled_back": 409, "broken": 500}
