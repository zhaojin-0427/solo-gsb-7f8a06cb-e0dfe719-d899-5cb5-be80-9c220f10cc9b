"""Request/response schemas for the IPv6 migration planner.

All validation errors are raised as ``ValueError`` so that FastAPI/Pydantic
turn them into HTTP 422 responses whose ``loc`` points at the offending field.
"""

from __future__ import annotations

from ipaddress import IPv6Network
from typing import Annotated

from pydantic import BaseModel, Field, ValidationInfo, field_validator
from pydantic.functional_validators import AfterValidator, BeforeValidator

MIN_POOL_PREFIX = 123
MAX_POOL_PREFIX = 127
MAX_EXISTING = 8
MAX_TARGETS = 8


def _parse_aligned_prefix(value: object) -> IPv6Network:
    """Parse a string into an IPv6Network, rejecting misaligned prefixes."""
    if isinstance(value, IPv6Network):
        return value
    if not isinstance(value, str):
        raise ValueError("must be an IPv6 prefix string such as '2001:db8::/125'")
    try:
        # strict=True rejects prefixes whose host bits are set (not aligned
        # to the network address).
        return IPv6Network(value, strict=True)
    except ValueError as exc:
        raise ValueError(
            f"must be a valid IPv6 prefix aligned to its network address: {exc}"
        ) from exc


def _check_pool_length(net: IPv6Network) -> IPv6Network:
    if not (MIN_POOL_PREFIX <= net.prefixlen <= MAX_POOL_PREFIX):
        raise ValueError(
            f"pool prefix length must be between /{MIN_POOL_PREFIX} and "
            f"/{MAX_POOL_PREFIX}, got /{net.prefixlen}"
        )
    return net


def _check_ascii(value: str) -> str:
    if not value.isascii():
        raise ValueError("id must contain only ASCII characters")
    return value


AlignedPrefix = Annotated[IPv6Network, BeforeValidator(_parse_aligned_prefix)]
PoolPrefix = Annotated[AlignedPrefix, AfterValidator(_check_pool_length)]
TenantId = Annotated[str, Field(min_length=1), AfterValidator(_check_ascii)]
PrefixLength = Annotated[int, Field(strict=True, ge=0, le=128)]


class ExistingAllocation(BaseModel):
    """One currently allocated prefix."""

    id: TenantId
    prefix: AlignedPrefix


class Target(BaseModel):
    """One requested allocation."""

    id: TenantId
    prefix_length: PrefixLength


class PlanRequest(BaseModel):
    pool: PoolPrefix
    existing: Annotated[list[ExistingAllocation], Field(max_length=MAX_EXISTING)] = []
    targets: Annotated[list[Target], Field(min_length=1, max_length=MAX_TARGETS)]

    @field_validator("existing")
    @classmethod
    def _check_existing(
        cls, value: list[ExistingAllocation], info: ValidationInfo
    ) -> list[ExistingAllocation]:
        seen: set[str] = set()
        for entry in value:
            if entry.id in seen:
                raise ValueError(f"duplicate existing id: {entry.id!r}")
            seen.add(entry.id)

        pool = info.data.get("pool")
        if pool is not None:
            for entry in value:
                if not entry.prefix.subnet_of(pool):
                    raise ValueError(
                        f"existing prefix {entry.prefix} (id {entry.id!r}) is not "
                        f"contained in pool {pool}"
                    )
            ordered = sorted(value, key=lambda e: int(e.prefix.network_address))
            for first, second in zip(ordered, ordered[1:]):
                if first.prefix.overlaps(second.prefix):
                    raise ValueError(
                        f"existing prefixes {first.prefix} (id {first.id!r}) and "
                        f"{second.prefix} (id {second.id!r}) overlap"
                    )
        return value

    @field_validator("targets")
    @classmethod
    def _check_targets(
        cls, value: list[Target], info: ValidationInfo
    ) -> list[Target]:
        seen: set[str] = set()
        for entry in value:
            if entry.id in seen:
                raise ValueError(f"duplicate target id: {entry.id!r}")
            seen.add(entry.id)

        pool = info.data.get("pool")
        if pool is not None:
            pool_len = pool.prefixlen
            for entry in value:
                if not (pool_len <= entry.prefix_length <= 128):
                    raise ValueError(
                        f"target id {entry.id!r}: prefix_length "
                        f"{entry.prefix_length} must be between the pool prefix "
                        f"length /{pool_len} and /128"
                    )
        return value


class Allocation(BaseModel):
    id: str
    prefix: str
