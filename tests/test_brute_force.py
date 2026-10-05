"""Cross-check the staged planner against exhaustive brute force on random
small instances. Run directly: python tests/test_brute_force.py
"""

import random
import sys

from ipaddress import IPv6Network

from app.planner import Existing, Target, Infeasible, plan

POOL = IPv6Network("2001:db8::/124")  # 16 units — brute force stays small
SPACE = 16


def brute(pool, existing_list, target_list):
    """Enumerate every complete placement; return the optimum per the spec.

    Result is ``((-keeps, changes, offset_tuple), [(id, offset), ...])`` or
    None when no complete assignment exists.
    """
    old = {
        e.id: (
            int(e.prefix.network_address) - int(pool.network_address),
            e.prefix.prefixlen,
        )
        for e in existing_list
    }
    tlen = {t.id: t.prefix_length for t in target_list}
    best = [None]

    def rec(i, mask, assign):
        if i == len(target_list):
            keeps = 0
            changes = 0
            for tid, off in assign:
                if tid in old:
                    oo, ol = old[tid]
                    if off == oo and tlen[tid] == ol:
                        keeps += 1
                    if off != oo:
                        changes += 1
            ordered = sorted(assign, key=lambda x: x[0])
            key = (-keeps, changes, tuple(off for _, off in ordered))
            if best[0] is None or key < best[0][0]:
                best[0] = (key, list(assign))
            return
        t = target_list[i]
        size = 1 << (128 - t.prefix_length)
        for off in range(0, SPACE, size):
            block = ((1 << size) - 1) << off
            if mask & block:
                continue
            rec(i + 1, mask | block, assign + [(t.id, off)])

    rec(0, 0, [])
    return best[0]


def random_instance(rng):
    n_existing = rng.randrange(0, 5)
    existing = []
    occupied = 0
    for tid in rng.sample(["a", "b", "c", "d", "e", "f"], n_existing):
        length = rng.choice([128, 128, 127, 126])
        size = 1 << (128 - length)
        spots = [
            o
            for o in range(0, SPACE, size)
            if not (occupied & (((1 << size) - 1) << o))
        ]
        if not spots:
            continue
        off = rng.choice(spots)
        occupied |= ((1 << size) - 1) << off
        existing.append(
            Existing(tid, IPv6Network((int(POOL.network_address) + off, length)))
        )

    n_targets = rng.randrange(1, 6)
    tids = rng.sample(["a", "b", "c", "d", "e", "x", "y", "z"], n_targets)
    targets = [
        Target(tid, rng.choice([125, 126, 127, 127, 128, 128]))
        for tid in tids
    ]
    return existing, targets


def main():
    rng = random.Random(1234)
    trials = 400
    mismatches = 0
    for trial in range(trials):
        existing, targets = random_instance(rng)
        expected = brute(POOL, existing, targets)
        try:
            got = plan(POOL, existing, targets)
        except Infeasible:
            if expected is not None:
                mismatches += 1
                print(
                    "FALSE INFEASIBLE",
                    trial,
                    [(e.id, str(e.prefix)) for e in existing],
                    [(t.id, t.prefix_length) for t in targets],
                )
            continue
        if expected is None:
            mismatches += 1
            print(
                "FALSE FEASIBLE",
                trial,
                [(e.id, str(e.prefix)) for e in existing],
                [(t.id, t.prefix_length) for t in targets],
            )
            continue
        exp_map = dict(expected[1])
        got_map = {
            tid: int(net.network_address) - int(POOL.network_address)
            for tid, net in got
        }
        if exp_map != got_map:
            mismatches += 1
            print("MISMATCH", trial)
            print(" existing:", [(e.id, str(e.prefix)) for e in existing])
            print(" targets:", [(t.id, t.prefix_length) for t in targets])
            print(" expected:", sorted(exp_map.items()))
            print(" got:     ", sorted(got_map.items()))
    print(f"{trials - mismatches}/{trials} instances match brute force")
    sys.exit(1 if mismatches else 0)


if __name__ == "__main__":
    main()
