from __future__ import annotations

from fastapi import APIRouter

from engine.api.deps import ServicesDep, SessionDep
from engine.api.schemas import AttackOut, ReplayAttackRequest, StartAttackRequest

router = APIRouter(prefix="/sessions/{session_id}/attacks", tags=["attacks"])


@router.post("", response_model=AttackOut)
def start_attack(
    session: SessionDep,
    services: ServicesDep,
    req: StartAttackRequest,
) -> AttackOut:
    target_url = f"{services.gateway.url}/{session.id}"
    company_dir = services.settings.content_dir / "companies" / session.manifest.name
    flaws_dir = services.settings.content_dir / "flaws"

    rec = services.attacks.start_attack(
        session_id=session.id,
        flaw_id=req.flaw_id,
        target_url=target_url,
        probe_token=services.probe_token,
        manifest=session.manifest,
        flaws_dir=flaws_dir,
        company_dir=company_dir,
        seed=req.seed,
        budget=req.budget,
        bus=services.bus,
        reset_fn=lambda: services.deployer.reset_company_state(session),
        wait=req.wait,
    )
    return AttackOut.of(rec)


@router.post("/replay", response_model=AttackOut)
def replay_attack(
    session: SessionDep,
    services: ServicesDep,
    req: ReplayAttackRequest | None = None,
) -> AttackOut:
    wait = req.wait if req else False
    target_url = f"{services.gateway.url}/{session.id}"
    company_dir = services.settings.content_dir / "companies" / session.manifest.name
    flaws_dir = services.settings.content_dir / "flaws"

    rec = services.attacks.replay_attack(
        session_id=session.id,
        target_url=target_url,
        probe_token=services.probe_token,
        manifest=session.manifest,
        flaws_dir=flaws_dir,
        company_dir=company_dir,
        bus=services.bus,
        reset_fn=lambda: services.deployer.reset_company_state(session),
        wait=wait,
    )
    return AttackOut.of(rec)


@router.get("/{attack_id}", response_model=AttackOut)
def get_attack(
    session: SessionDep,
    services: ServicesDep,
    attack_id: str,
) -> AttackOut:
    rec = services.attacks.get_attack(attack_id)
    return AttackOut.of(rec)


@router.get("", response_model=list[AttackOut])
def list_attacks(
    session: SessionDep,
    services: ServicesDep,
) -> list[AttackOut]:
    recs = services.attacks.list_attacks(session.id)
    return [AttackOut.of(r) for r in recs]
