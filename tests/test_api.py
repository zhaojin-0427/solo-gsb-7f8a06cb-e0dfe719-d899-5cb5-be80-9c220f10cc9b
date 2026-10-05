"""Tests for the migration planner and the /plan HTTP API."""

from ipaddress import IPv6Network

from fastapi.testclient import TestClient

from app.main import app
from app.planner import Existing, Target, plan, Infeasible

client = TestClient(app)


def alloc(pool, existing, targets):
    return [
        (tid, str(net))
        for tid, net in plan(
            IPv6Network(pool),
            [Existing(i, IPv6Network(p)) for i, p in existing],
            [Target(i, l) for i, l in targets],
        )
    ]


# ---------------------------------------------------------------- planner


def test_spec_example_keeps_b_moves_a():
    result = alloc(
        "2001:db8::/125",
        [("a", "2001:db8::/127"), ("b", "2001:db8::2/127")],
        [("a", 126), ("b", 127), ("c", 127)],
    )
    assert result == [
        ("a", "2001:db8::4/126"),
        ("b", "2001:db8::2/127"),
        ("c", "2001:db8::/127"),
    ]


def test_keep_as_many_identical_as_possible():
    # /124 = 16 units. a originally ::/127, target /126 (length change, never
    # "identical"); b ::2/127 and c ::4/127 can both be kept identical.
    # Fixing b at 2 blocks a's ::/126 slot, forcing a to ::4 which collides
    # with fixed c; so {b,c} together is infeasible. The maximal identical
    # set keeps b and ... check via planner: 2 keeps must be feasible elsewhere
    # (a at ::8, b at 2, c at 4, d new). Stage 2 then keeps a's first address
    # if possible without sacrificing keeps.
    result = alloc(
        "2001:db8::/124",
        [
            ("a", "2001:db8::/127"),
            ("b", "2001:db8::2/127"),
            ("c", "2001:db8::4/127"),
        ],
        [("a", 126), ("b", 127), ("c", 127), ("d", 127)],
    )
    d = dict(result)
    # Max identical keeps: b and c both stay (2).
    assert d["b"] == "2001:db8::2/127"
    assert d["c"] == "2001:db8::4/127"
    # a's /126 cannot stay at :: (collides with b at 2), so its first address
    # changes; lex-min aligned free /126 slot is ::8.
    assert d["a"] == "2001:db8::8/126"
    assert d["d"] == "2001:db8::/127"
    # sorted by id
    assert [t[0] for t in result] == ["a", "b", "c", "d"]


def test_length_change_is_not_identical_even_if_start_kept():
    # a: ::/126 originally (units 0..3), target /127. Keeping network address
    # :: possible; counts as a changed prefix (length), but NOT as a changed
    # first address. Remaining free units 2,3 hold two /127s? /127 covers 2
    # units: b at ::2 fits (units 2,3).
    result = alloc(
        "2001:db8::/126",
        [("a", "2001:db8::/126")],
        [("a", 127), ("b", 127)],
    )
    d = dict(result)
    assert d["a"] == "2001:db8::/127"  # first address unchanged
    assert d["b"] == "2001:db8::2/127"


def test_minimize_first_address_changes_as_second_objective():
    # pool /125; a at ::6/127 (network ::6, covers units 6,7).
    # a expands to /126: aligned options are 0,2,4; ::6 cannot be a /126
    # network address, so a's first address must change. b is a new /127.
    # With no identical prefix keepable, stage 2 minimizes a's address change
    # (unavoidable) and stage 3 lex-min then puts a at ::/126 and b at ::4/127.
    result = alloc(
        "2001:db8::/125",
        [("a", "2001:db8::6/127")],
        [("a", 126), ("b", 127)],
    )
    d = dict(result)
    assert d["a"] == "2001:db8::/126"
    assert d["b"] == "2001:db8::4/127"


def test_first_address_kept_when_new_length_aligns_at_same_network():
    # a originally ::4/127 (network ::4); target /126 at ::4 is aligned,
    # so prefix length changes but first address ::4 stays.
    result = alloc(
        "2001:db8::/125",
        [("a", "2001:db8::4/127")],
        [("a", 126), ("b", 127)],
    )
    d = dict(result)
    assert d["a"] == "2001:db8::4/126"
    assert d["b"] == "2001:db8::/127"


def test_lex_min_address_tuple_third_objective():
    # Pure fresh allocation: two /128 targets z then a (ids sort -> a, z).
    result = alloc(
        "2001:db8::/127",
        [],
        [("z", 128), ("a", 128)],
    )
    assert result == [("a", "2001:db8::/128"), ("z", "2001:db8::1/128")]


def test_infeasible_too_few_addresses():
    # /127 has 2 units; need 3 /128s.
    try:
        alloc("2001:db8::/127", [], [("a", 128), ("b", 128), ("c", 128)])
    except Infeasible:
        pass
    else:
        raise AssertionError("expected Infeasible")


def test_infeasible_fragmentation():
    # /124 (16 units); two /125 blocks need 16 units in total, leaving no room
    # for anything else, so even a single extra /128 target is impossible.
    try:
        alloc(
            "2001:db8::/124",
            [
                ("a", "2001:db8::2/128"),
                ("b", "2001:db8::6/128"),
            ],
            [
                ("a", 128),
                ("b", 128),
                ("x", 125),
                ("y", 125),
            ],
        )
    except Infeasible:
        pass
    else:
        raise AssertionError("expected Infeasible")


def test_infeasible_when_keeps_forced_block_alignment():
    # /124; four old units at 2,6,10,14. A new /125 has aligned starts 0 or 8,
    # both blocked while all four are kept; keeping 3 also fails because each
    # aligned half contains two pinned units, so the best achievable is 2
    # identical keeps. Regression guard: the planner must release tenants as
    # needed (returning 200 with the optimum) rather than failing with 409.
    result = alloc(
        "2001:db8::/124",
        [
            ("a", "2001:db8::2/128"),
            ("b", "2001:db8::6/128"),
            ("c", "2001:db8::a/128"),
            ("d", "2001:db8::e/128"),
        ],
        [
            ("a", 128),
            ("b", 128),
            ("c", 128),
            ("d", 128),
            ("x", 125),
        ],
    )
    d = dict(result)
    assert d["x"] in ("2001:db8::/125", "2001:db8::8/125")
    original = {
        ("a", "2001:db8::2/128"),
        ("b", "2001:db8::6/128"),
        ("c", "2001:db8::a/128"),
        ("d", "2001:db8::e/128"),
    }
    kept = sum(1 for item in result if item in original)
    assert kept == 2


def test_unlisted_old_allocation_is_released():
    # b exists but is not a target -> its space is reusable.
    result = alloc(
        "2001:db8::/127",
        [("b", "2001:db8::1/128")],
        [("a", 128), ("x", 128)],
    )
    assert dict(result)["a"] == "2001:db8::/128"
    assert dict(result)["x"] == "2001:db8::1/128"


def test_pool_123_with_many_targets():
    # /123 = 32 units; 32 fresh /128 tenants.
    targets = [(f"t{i:02d}", 128) for i in range(32)]
    result = alloc("2001:db8::/123", [], targets)
    assert len(result) == 32
    assert result[0] == ("t00", "2001:db8::/128")
    assert result[-1] == ("t31", "2001:db8::1f/128")


def test_all_outputs_aligned_nonoverlapping_within_pool():
    result = alloc(
        "2001:db8::/123",
        [
            ("a", "2001:db8::4/126"),
            ("b", "2001:db8::10/128"),
        ],
        [
            ("a", 127),
            ("b", 125),
            ("c", 124),
            ("d", 128),
            ("e", 128),
        ],
    )
    pool = IPv6Network("2001:db8::/123")
    nets = [IPv6Network(p) for _, p in result]
    for n in nets:
        assert n.subnet_of(pool)
    for i, n in enumerate(nets):
        for m in nets[i + 1 :]:
            assert not n.overlaps(m), (n, m)


# ---------------------------------------------------------------- HTTP


def test_http_ok_spec_example():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [
                {"id": "a", "prefix": "2001:db8::/127"},
                {"id": "b", "prefix": "2001:db8::2/127"},
            ],
            "targets": [
                {"id": "a", "prefix_length": 126},
                {"id": "b", "prefix_length": 127},
                {"id": "c", "prefix_length": 127},
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "allocations": [
            {"id": "a", "prefix": "2001:db8::4/126"},
            {"id": "b", "prefix": "2001:db8::2/127"},
            {"id": "c", "prefix": "2001:db8::/127"},
        ]
    }


def test_http_409_no_partial_results():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/127",
            "existing": [],
            "targets": [
                {"id": "a", "prefix_length": 128},
                {"id": "b", "prefix_length": 128},
                {"id": "c", "prefix_length": 128},
            ],
        },
    )
    assert resp.status_code == 409
    body = resp.json()
    assert "allocations" not in body
    assert "detail" in body


def test_http_422_pool_length_out_of_range():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/128",
            "existing": [],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "pool") in locs


def test_http_422_unaligned_existing_prefix():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/124",
            "existing": [{"id": "a", "prefix": "2001:db8::1/125"}],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 0, "prefix") in locs


def test_http_422_existing_outside_pool():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [{"id": "a", "prefix": "2001:db9::/127"}],
            "targets": [{"id": "a", "prefix_length": 127}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 0, "prefix") in locs


def test_http_422_overlapping_existing():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/124",
            "existing": [
                {"id": "a", "prefix": "2001:db8::/126"},
                {"id": "b", "prefix": "2001:db8::1/128"},
            ],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 1, "prefix") in locs


def test_http_422_target_length_equal_pool_length():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [],
            "targets": [{"id": "a", "prefix_length": 125}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "targets", 0, "prefix_length") in locs


def test_http_422_duplicate_target_ids_and_empty_id():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [],
            "targets": [
                {"id": "a", "prefix_length": 128},
                {"id": "a", "prefix_length": 128},
                {"id": "", "prefix_length": 128},
            ],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "targets", 1, "id") in locs
    assert ("body", "targets", 2, "id") in locs


def test_http_422_non_ascii_id():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [{"id": "租", "prefix": "2001:db8::/128"}],
            "targets": [{"id": "x", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 0, "id") in locs


def test_http_422_too_many_existing():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/123",
            "existing": [
                {"id": f"e{i}", "prefix": f"2001:db8::{i:x}/128"}
                for i in range(9)
            ],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing") in locs


def test_http_422_missing_targets_and_wrong_types():
    resp = client.post(
        "/plan",
        json={"pool": "2001:db8::/125", "existing": []},
    )
    assert resp.status_code == 422

    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [],
            "targets": [{"id": 42, "prefix_length": "128"}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "targets", 0, "id") in locs
    assert ("body", "targets", 0, "prefix_length") in locs


def test_http_422_boolean_prefix_length_rejected():
    # bool must not be accepted as an integer.
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [],
            "targets": [{"id": "a", "prefix_length": True}],
        },
    )
    assert resp.status_code == 422


def test_http_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_http_422_existing_prefix_longer_than_pool_but_outside():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/127",
            "existing": [{"id": "a", "prefix": "2001:db9::/128"}],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 0, "prefix") in locs


def test_http_422_target_prefix_length_129():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [],
            "targets": [{"id": "a", "prefix_length": 129}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "targets", 0, "prefix_length") in locs


def test_http_422_empty_targets():
    resp = client.post(
        "/plan",
        json={"pool": "2001:db8::/125", "existing": [], "targets": []},
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "targets") in locs


def test_http_422_whitespace_id_is_nonempty_but_ascii():
    # A single space is non-empty ASCII, so it is a legitimate (if odd) id;
    # only truly empty and non-ASCII ids are rejected.
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/127",
            "existing": [{"id": " ", "prefix": "2001:db8::/128"}],
            "targets": [{"id": " ", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 200


def test_http_422_duplicate_existing_ids():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/124",
            "existing": [
                {"id": "a", "prefix": "2001:db8::/128"},
                {"id": "a", "prefix": "2001:db8::1/128"},
            ],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 1, "id") in locs


def test_http_422_not_ipv6():
    resp = client.post(
        "/plan",
        json={
            "pool": "2001:db8::/125",
            "existing": [{"id": "a", "prefix": "10.0.0.0/24"}],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "existing", 0, "prefix") in locs


def test_http_422_pool_not_ipv6():
    resp = client.post(
        "/plan",
        json={
            "pool": "10.0.0.0/30",
            "existing": [],
            "targets": [{"id": "a", "prefix_length": 128}],
        },
    )
    assert resp.status_code == 422
    locs = [tuple(e["loc"]) for e in resp.json()["detail"]]
    assert ("body", "pool") in locs
