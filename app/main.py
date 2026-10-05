"""FastAPI application for IPv6 prefix migration planning."""

from typing import List

from ipaddress import IPv6Network

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictStr, StrictInt, ValidationError

from .planner import Existing, Target, Infeasible, plan


app = FastAPI(
    title="IPv6 Migration Planner",
    version="1.0.0",
)


class ExistingIn(BaseModel):
    id: StrictStr
    prefix: StrictStr


class TargetIn(BaseModel):
    id: StrictStr
    prefix_length: StrictInt


class PlanRequest(BaseModel):
    pool: StrictStr
    existing: List[ExistingIn] = Field(default_factory=list)
    targets: List[TargetIn]


def _err(loc, msg, typ="value_error"):
    return {"loc": list(loc), "msg": msg, "type": typ}


def _parse_network(value, loc, errors):
    """Parse an IPv6 network, requiring host bits to be absent/aligned."""
    try:
        net = IPv6Network(value, strict=True)
    except ValueError as exc:
        errors.append(_err(loc, f"invalid IPv6 network {value!r}: {exc}"))
        return None
    if net.version != 6:
        errors.append(_err(loc, f"{value!r} is not an IPv6 prefix"))
        return None
    return net


def _validate(payload: dict):
    errors = []

    # Structural validation through Pydantic (strict typing).
    try:
        req = PlanRequest.model_validate(payload)
    except ValidationError as exc:
        structural = []
        for err in exc.errors():
            loc = err["loc"]
            structural.append(
                {**err, "loc": ("body",) + tuple(loc)}
            )
        return None, structural

    # ---- pool -------------------------------------------------------------
    pool = _parse_network(req.pool, ("body", "pool"), errors)
    pool_len = None
    if pool is not None:
        if not (123 <= pool.prefixlen <= 127):
            errors.append(
                _err(
                    ("body", "pool"),
                    f"pool prefix length must be between /123 and /127, "
                    f"got /{pool.prefixlen}",
                )
            )
        else:
            pool_len = pool.prefixlen

    # ---- list size limits -------------------------------------------------
    if len(req.existing) > 8:
        errors.append(
            _err(
                ("body", "existing"),
                "at most 8 existing allocations are allowed",
            )
        )
    if not (1 <= len(req.targets) <= 8):
        errors.append(
            _err(
                ("body", "targets"),
                "between 1 and 8 targets are required",
            )
        )

    # ---- existing ---------------------------------------------------------
    existing = []
    seen_existing_ids = set()
    existing_nets = []
    existing_items = req.existing[:8] if len(req.existing) > 8 else req.existing
    for i, item in enumerate(existing_items):
        loc = ("body", "existing", i)
        if not item.id:
            errors.append(_err(loc + ("id",), "id must be a non-empty string"))
        elif not item.id.isascii():
            errors.append(_err(loc + ("id",), "id must contain only ASCII characters"))
        elif item.id in seen_existing_ids:
            errors.append(_err(loc + ("id",), f"duplicate id {item.id!r}"))
        else:
            seen_existing_ids.add(item.id)

        net = _parse_network(item.prefix, loc + ("prefix",), errors)
        if net is not None:
            if pool_len is not None and not net.subnet_of(pool):
                errors.append(
                    _err(
                        loc + ("prefix",),
                        f"{net} is not inside pool {pool}",
                    )
                )
            existing_nets.append((loc, net))
            existing.append(Existing(id=item.id, prefix=net))

    # Mutual non-overlap among existing prefixes.
    for a in range(len(existing_nets)):
        loc_a, net_a = existing_nets[a]
        for b in range(a + 1, len(existing_nets)):
            loc_b, net_b = existing_nets[b]
            if net_a.overlaps(net_b):
                errors.append(
                    _err(
                        loc_b + ("prefix",),
                        f"{net_b} overlaps existing prefix {net_a}",
                    )
                )

    # ---- targets ----------------------------------------------------------
    targets = []
    seen_target_ids = set()
    for i, item in enumerate(req.targets):
        loc = ("body", "targets", i)
        if not item.id:
            errors.append(_err(loc + ("id",), "id must be a non-empty string"))
        elif not item.id.isascii():
            errors.append(_err(loc + ("id",), "id must contain only ASCII characters"))
        elif item.id in seen_target_ids:
            errors.append(_err(loc + ("id",), f"duplicate id {item.id!r}"))
        else:
            seen_target_ids.add(item.id)

        if pool_len is not None:
            if not (pool_len < item.prefix_length <= 128):
                errors.append(
                    _err(
                        loc + ("prefix_length",),
                        f"prefix_length must be greater than the pool "
                        f"length (/{pool_len}) and at most 128, "
                        f"got {item.prefix_length}",
                    )
                )
        elif item.prefix_length < 0 or item.prefix_length > 128:
            errors.append(
                _err(
                    loc + ("prefix_length",),
                    f"prefix_length must be in [0, 128], got {item.prefix_length}",
                )
            )
        targets.append(Target(id=item.id, prefix_length=item.prefix_length))

    return (pool, existing, targets), errors


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/plan")
async def create_plan(payload: dict):
    validated, errors = _validate(payload)
    if errors:
        return JSONResponse(status_code=422, content={"detail": errors})
    pool, existing, targets = validated

    try:
        allocations = plan(pool, existing, targets)
    except Infeasible:
        return JSONResponse(
            status_code=409,
            content={"detail": "no complete non-overlapping assignment exists"},
        )

    return {
        "allocations": [
            {"id": tid, "prefix": str(net)} for tid, net in allocations
        ]
    }
