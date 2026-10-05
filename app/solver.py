"""Allocation solver for the IPv6 migration planner.

Given a pool (/123../127, i.e. at most 32 addresses) and a set of targets,
find a complete set of non-overlapping, network-aligned prefixes inside the
pool.  Among all complete plans the best one is chosen by these criteria, in
order:

1. maximise the number of old tenants (ids present in both ``existing`` and
   ``targets``) whose allocated prefix is *exactly* their previous prefix
   (same network address **and** same length);
2. minimise the number of old tenants whose first (network) address changes
   (a pure length change keeps the address unchanged);
3. minimise, lexicographically, the sequence of integer network addresses of
   the new prefixes ordered by tenant id.

The pool is tiny (<= 32 addresses), so each candidate block is represented as
a bitmask over the pool's addresses.  Step 1 finds the optimal (kept,
address-preserved) counts via depth-first search with optimistic bounds.
Step 2 greedily fixes targets in id order, always taking the smallest free
network address that can still be completed into an optimal plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv6Network


@dataclass(frozen=True)
class TargetSpec:
    id: str
    prefix_length: int


def _block_mask(offset: int, size: int) -> int:
    """Bitmask of ``size`` consecutive pool addresses starting at ``offset``."""
    return ((1 << size) - 1) << offset


def solve(
    pool: IPv6Network,
    existing: dict[str, IPv6Network],
    targets: list[TargetSpec],
) -> dict[str, IPv6Network] | None:
    """Return ``{id: prefix}`` for every target, or ``None`` if infeasible."""
    pool_base = int(pool.network_address)
    pool_size = 1 << (128 - pool.prefixlen)

    ordered = sorted(targets, key=lambda t: t.id)
    n = len(ordered)

    sizes: list[int] = []
    candidates: list[list[int]] = []  # block masks, ascending network address
    keep_mask: list[int | None] = []  # mask of the tenant's exact old prefix
    pres_mask: list[int | None] = []  # mask of a block starting at the old address

    for target in ordered:
        size = 1 << (128 - target.prefix_length)
        sizes.append(size)
        candidates.append(
            [
                _block_mask(offset, size)
                for offset in range(0, pool_size, size)
            ]
        )

        old = existing.get(target.id)
        keep: int | None = None
        pres: int | None = None
        if old is not None:
            old_offset = int(old.network_address) - pool_base
            if old.prefixlen == target.prefix_length:
                keep = _block_mask(old_offset, size)
                pres = keep
            elif old_offset % size == 0 and old_offset + size <= pool_size:
                # Same first address with the new length: only possible if the
                # old address is aligned for the new length and fits the pool.
                pres = _block_mask(old_offset, size)
        keep_mask.append(keep)
        pres_mask.append(pres)

    def kept_bound(start: int, occupied: int) -> int:
        """Optimistic count of remaining tenants that could keep their prefix."""
        return sum(
            1
            for j in range(start, n)
            if keep_mask[j] is not None and keep_mask[j] & occupied == 0
        )

    def pres_bound(start: int, occupied: int) -> int:
        """Optimistic count of remaining tenants that could keep their address."""
        return sum(
            1
            for j in range(start, n)
            if pres_mask[j] is not None and pres_mask[j] & occupied == 0
        )

    def gains(index: int, mask: int) -> tuple[int, int]:
        kept = 1 if keep_mask[index] is not None and mask == keep_mask[index] else 0
        pres = 1 if pres_mask[index] is not None and mask == pres_mask[index] else 0
        return kept, pres

    # ---- Step 1: lexicographic maximum of (kept, address-preserved) --------
    best: tuple[int, int] | None = None

    def search_best(index: int, occupied: int, kept: int, pres: int) -> None:
        nonlocal best
        if best is not None:
            opt_kept = kept + kept_bound(index, occupied)
            opt_pres = pres + pres_bound(index, occupied)
            # Prune when this branch cannot be strictly better than `best`.
            if opt_kept < best[0] or (opt_kept == best[0] and opt_pres <= best[1]):
                return
        if index == n:
            current = (kept, pres)
            if best is None or current > best:
                best = current
            return
        for mask in candidates[index]:
            if mask & occupied:
                continue
            add_kept, add_pres = gains(index, mask)
            search_best(index + 1, occupied | mask, kept + add_kept, pres + add_pres)

    search_best(0, 0, 0, 0)
    if best is None:
        return None
    target_kept, target_pres = best

    # ---- Step 2: lexicographically smallest address sequence ---------------
    def completable(index: int, occupied: int, kept: int, pres: int) -> bool:
        """True if targets[index:] can still reach (target_kept, target_pres)."""
        if kept + kept_bound(index, occupied) < target_kept:
            return False
        if pres + pres_bound(index, occupied) < target_pres:
            return False
        if index == n:
            return kept == target_kept and pres == target_pres
        for mask in candidates[index]:
            if mask & occupied:
                continue
            add_kept, add_pres = gains(index, mask)
            if completable(
                index + 1, occupied | mask, kept + add_kept, pres + add_pres
            ):
                return True
        return False

    chosen: list[int] = []
    occupied = 0
    kept = pres = 0
    for index in range(n):
        placed = False
        for mask in candidates[index]:
            if mask & occupied:
                continue
            add_kept, add_pres = gains(index, mask)
            if completable(
                index + 1, occupied | mask, kept + add_kept, pres + add_pres
            ):
                chosen.append(mask)
                occupied |= mask
                kept += add_kept
                pres += add_pres
                placed = True
                break
        if not placed:  # unreachable given step 1, but stay defensive
            return None

    result: dict[str, IPv6Network] = {}
    for target, mask in zip(ordered, chosen):
        offset = (mask & -mask).bit_length() - 1
        result[target.id] = IPv6Network((pool_base + offset, target.prefix_length))
    return result
