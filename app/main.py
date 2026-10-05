"""FastAPI application exposing the IPv6 migration planner."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from app.models import Allocation, PlanRequest
from app.solver import TargetSpec, solve

app = FastAPI(title="IPv6 Migration Planner", version="1.0.0")


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ipv6-migration-planner", "plan_endpoint": "/plan"}


@app.post("/plan", response_model=list[Allocation])
def create_plan(request: PlanRequest) -> list[Allocation]:
    existing = {entry.id: entry.prefix for entry in request.existing}
    targets = [
        TargetSpec(id=entry.id, prefix_length=entry.prefix_length)
        for entry in request.targets
    ]

    plan = solve(request.pool, existing, targets)
    if plan is None:
        raise HTTPException(
            status_code=409,
            detail="no complete allocation exists for the given pool and targets",
        )

    return [
        Allocation(id=target.id, prefix=str(plan[target.id]))
        for target in sorted(targets, key=lambda t: t.id)
    ]
