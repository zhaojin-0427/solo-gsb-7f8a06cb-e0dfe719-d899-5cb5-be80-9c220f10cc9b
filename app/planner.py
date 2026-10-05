"""IPv6 prefix migration planner.

Assigns aligned, mutually non-overlapping prefixes inside a parent pool to a
set of targets. Old tenants (matched by id) are preferentially kept on their
original prefix.

The pool is modelled as ``space = 2**(128 - pool_len)`` consecutive /128
units, each represented by one bit of an occupancy integer. A prefix placed at
unit offset ``off`` occupies the aligned mask
``((1 << size) - 1) << off``, where ``size`` is the number of /128 addresses
in the prefix.

The optimization runs in three stages, matching the required ordering:

1. maximize the number of *old* tenants whose prefix stays identical (same
   network address **and** length — a length change never counts);
2. minimize the number of *old* tenants whose network (first) address changes;
3. lexicographically minimize the tuple of new network-address integers in
   target-id order.

All inputs are assumed to already be validated by the API layer.
"""

from dataclasses import dataclass
from itertools import combinations
from ipaddress import IPv6Network


class Infeasible(Exception):
    """No complete assignment exists for the given inputs."""


@dataclass(frozen=True)
class Existing:
    id: str
    prefix: IPv6Network


@dataclass(frozen=True)
class Target:
    id: str
    prefix_length: int


@dataclass
class _T:
    """Internal target record."""

    id: str
    length: int
    size: int  # number of /128 units covered
    candidates: tuple  # sorted tuple of aligned start offsets (in units)
    old: bool
    old_offset: int = -1
    old_length: int = -1

    @property
    def block_mask(self):
        # bit mask of `size` consecutive /128 units; shifted when placed
        return (1 << self.size) - 1

    def can_keep(self):
        return self.old and self.length == self.old_length

    def can_stay_address(self, mask):
        """Whether placing this target at its old offset is possible now."""
        if not self.old or self.old_offset not in self.candidates:
            return False
        block = self.block_mask << self.old_offset
        return not (mask & block)


def _suffix_sizes(ts, skip_mask=0):
    n = len(ts)
    suf = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suf[i] = suf[i + 1] + (0 if (skip_mask >> i) & 1 else ts[i].size)
    return suf


def _packable(rest_sizes, mask, space):
    """Can blocks with the given sizes be placed in the free /128 units?

    ``rest_sizes`` is sorted non-increasing. Memoised DFS; equal-sized blocks
    are interchangeable, so block ``i`` is constrained to offsets no smaller
    than the offset chosen for the previous equal-sized block.
    """
    full = (1 << space) - 1

    def dfs(i, occ, lo):
        if i == len(rest_sizes):
            return True
        size = rest_sizes[i]
        same_as_prev = i > 0 and rest_sizes[i - 1] == size
        key = (i, occ, lo if same_as_prev else -1)
        if key in memo:
            return memo[key]
        base = (1 << size) - 1
        start = lo if same_as_prev else 0
        for off in range(start, space, size):
            block = base << off
            if occ & block:
                continue
            if dfs(i + 1, occ | block, off):
                memo[key] = True
                return True
        memo[key] = False
        return False

    memo = {}
    return dfs(0, mask & full, 0)


def _forced_changes(ts, k, mask, skip_mask=0):
    """Old targets at/after k that can no longer keep their first address."""
    count = 0
    for j in range(k, len(ts)):
        if (skip_mask >> j) & 1:
            continue
        u = ts[j]
        if u.old and not u.can_stay_address(mask):
            count += 1
    return count


def _free_keepable(ts, k, mask):
    """Old targets at/after k that could still be kept identical."""
    count = 0
    for j in range(k, len(ts)):
        u = ts[j]
        if u.can_keep() and u.can_stay_address(mask):
            count += 1
    return count


def _feasible(ts, fixed_mask, occupied, space, suf):
    """Can every non-fixed target be placed?

    Targets are ordered non-increasing by size; fixed targets are skipped and
    their blocks are already part of ``occupied``. ``suf`` holds suffix sizes
    excluding fixed targets. Uses memoisation on ``(index, occupancy)``.
    """
    n = len(ts)
    memo = {}

    def dfs(i, mask):
        while i < n and (fixed_mask >> i) & 1:
            i += 1
        if i == n:
            return True
        key = (i, mask)
        if key in memo:
            return memo[key]
        t = ts[i]
        result = False
        if space - mask.bit_count() >= suf[i]:
            for off in t.candidates:
                block = t.block_mask << off
                if mask & block:
                    continue
                if dfs(i + 1, mask | block):
                    result = True
                    break
        memo[key] = result
        return result

    return dfs(0, occupied)


def _min_changes(ts, fixed_mask, occupied, space, suf):
    """Minimum network-address changes among old non-fixed targets."""
    n = len(ts)
    best = n + 1

    def dfs(i, mask, changed):
        nonlocal best
        if changed >= best:
            return
        while i < n and (fixed_mask >> i) & 1:
            i += 1
        if i == n:
            if changed < best:
                best = changed
            return
        t = ts[i]
        if space - mask.bit_count() < suf[i]:
            return
        lb = changed + _forced_changes(ts, i, mask, fixed_mask)
        if lb >= best:
            return
        for off in t.candidates:
            block = t.block_mask << off
            if mask & block:
                continue
            add = 1 if t.old and off != t.old_offset else 0
            dfs(i + 1, mask | block, changed + add)

    dfs(0, occupied, 0)
    return None if best > n else best


def _lex_min(ts, space, suf, must_keep, change_budget):
    """Lexicographically smallest offset tuple (targets already in id order).

    The first complete assignment found while trying offsets in ascending
    order is the lexicographically smallest one.
    """
    n = len(ts)
    # Remaining block sizes at each index, sorted non-increasing, for the
    # strong geometric feasibility prune.
    rest_sorted = [None] * (n + 1)
    rest_sorted[n] = ()
    for k in range(n - 1, -1, -1):
        rest_sorted[k] = tuple(
            sorted((ts[k].size,) + rest_sorted[k + 1], reverse=True)
        )

    def dfs(k, mask, kept, changed):
        need = must_keep - kept
        if need < 0 or changed > change_budget:
            return None
        if space - mask.bit_count() < suf[k]:
            return None
        if not (0 <= need <= _free_keepable(ts, k, mask)):
            return None
        if changed + _forced_changes(ts, k, mask) > change_budget:
            return None
        if k < n and not _packable(rest_sorted[k], mask, space):
            return None
        if k == n:
            return {} if need == 0 else None
        t = ts[k]
        for off in t.candidates:
            block = t.block_mask << off
            if mask & block:
                continue
            nk, nc = kept, changed
            if t.old:
                if t.can_keep() and off == t.old_offset:
                    nk += 1
                elif off != t.old_offset:
                    nc += 1
            if nk > must_keep or nc > change_budget:
                continue
            # Do not cover a later old tenant's original spot if that tenant
            # would then have to be kept (but would no longer be able to).
            if nk + _free_keepable(ts, k + 1, mask | block) < must_keep:
                continue
            rest = dfs(k + 1, mask | block, nk, nc)
            if rest is not None:
                rest[t.id] = off
                return rest
        return None

    return dfs(0, 0, 0, 0)


def plan(pool: IPv6Network, existing, targets):
    """Compute the optimal assignment.

    Returns a list of ``(id, IPv6Network)`` sorted by id. Raises
    :class:`Infeasible` when no complete assignment exists.
    """
    pool_len = pool.prefixlen
    space = 1 << (128 - pool_len)  # /128 units in the pool (8..32)
    base = int(pool.network_address)

    old_by_id = {}
    for e in existing:
        net = e.prefix
        old_by_id[e.id] = (int(net.network_address) - base, net.prefixlen)

    ts = []
    for tgt in targets:
        length = tgt.prefix_length
        # /128 addresses contained in one prefix of this length.
        size = 1 << (128 - length)
        count = space // size  # aligned start positions within the pool
        candidates = tuple(off * size for off in range(count))
        old = tgt.id in old_by_id
        old_off, old_len = old_by_id.get(tgt.id, (-1, -1))
        ts.append(
            _T(
                id=tgt.id,
                length=length,
                size=size,
                candidates=candidates,
                old=old,
                old_offset=old_off,
                old_length=old_len,
            )
        )

    # Large blocks first for the combinatorial searches; keepable old blocks
    # first among equal sizes.
    order = sorted(
        range(len(ts)),
        key=lambda i: (
            -ts[i].size,
            0 if ts[i].can_keep() else 1,
            ts[i].id,
        ),
    )
    ots = [ts[i] for i in order]

    def build_fixed(subset):
        """Fixed blocks forced onto their tenants' original offsets."""
        fixed_mask = 0
        occupied = 0
        for idx in subset:
            t = ots[idx]
            block = t.block_mask << t.old_offset
            if occupied & block:
                return None
            occupied |= block
            fixed_mask |= 1 << idx
        return fixed_mask, occupied

    # ---- Stage 1: maximum number of identical old prefixes ----------------
    compat = [i for i, t in enumerate(ots) if t.can_keep()]
    best_k = None
    best_subsets = []
    for k in range(len(compat), -1, -1):
        found = []
        for subset in combinations(compat, k):
            built = build_fixed(subset)
            if built is None:
                continue
            fixed_mask, occupied = built
            fsuf = _suffix_sizes(ots, fixed_mask)
            if _feasible(ots, fixed_mask, occupied, space, fsuf):
                found.append((fixed_mask, occupied))
        if found:
            best_k = k
            best_subsets = found
            break
    if best_k is None:
        raise Infeasible

    # ---- Stage 2: minimum changed network addresses -----------------------
    best_c = None
    for fixed_mask, occupied in best_subsets:
        fsuf = _suffix_sizes(ots, fixed_mask)
        c = _min_changes(ots, fixed_mask, occupied, space, fsuf)
        if c is not None and (best_c is None or c < best_c):
            best_c = c
    if best_c is None:
        raise Infeasible

    # ---- Stage 3: lex-smallest network-address tuple in id order ----------
    its = sorted(ts, key=lambda t: t.id)
    isuf = _suffix_sizes(its)
    offsets = _lex_min(its, space, isuf, best_k, best_c)
    if offsets is None:
        raise Infeasible

    allocations = []
    for t in its:
        off = offsets[t.id]
        net = IPv6Network((base + off, t.length))
        allocations.append((t.id, net))
    allocations.sort(key=lambda x: x[0])
    return allocations
