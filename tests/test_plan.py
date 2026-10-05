from ipaddress import IPv6Network

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

POOL = "2001:db8::/125"


def plan(payload):
    return client.post("/plan", json=payload)


def test_spec_example_keeps_b_and_moves_a():
    resp = plan(
        {
            "pool": POOL,
            "existing": [
                {"id": "a", "prefix": "2001:db8::/127"},
                {"id": "b", "prefix": "2001:db8::2/127"},
            ],
            "targets": [
                {"id": "a", "prefix_length": 126},
                {"id": "b", "prefix_length": 127},
                {"id": "c", "prefix_length": 127},
            ],
        }
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"id": "a", "prefix": "2001:db8::4/126"},
        {"id": "b", "prefix": "2001:db8::2/127"},
        {"id": "c", "prefix": "2001:db8::/127"},
    ]


def test_output_sorted_by_id_not_input_order():
    resp = plan(
        {
            "pool": POOL,
            "targets": [
                {"id": "z", "prefix_length": 127},
                {"id": "a", "prefix_length": 127},
            ],
        }
    )
    assert resp.status_code == 200
    assert [row["id"] for row in resp.json()] == ["a", "z"]


def test_lexicographically_smallest_addresses_when_no_history():
    resp = plan(
        {
            "pool": POOL,
            "targets": [
                {"id": "a", "prefix_length": 127},
                {"id": "b", "prefix_length": 127},
            ],
        }
    )
    assert resp.json() == [
        {"id": "a", "prefix": "2001:db8::/127"},
        {"id": "b", "prefix": "2001:db8::2/127"},
    ]


def test_length_change_keeps_first_address_when_possible():
    # a grows /126 -> /125; its old first address 8 stays valid for /125,
    # so criterion 2 prefers a at ::8/125 even though ::/125 is numerically
    # smaller.
    resp = plan(
        {
            "pool": "2001:db8::/124",
            "existing": [{"id": "a", "prefix": "2001:db8::8/126"}],
            "targets": [
                {"id": "a", "prefix_length": 125},
                {"id": "b", "prefix_length": 125},
            ],
        }
    )
    assert resp.json() == [
        {"id": "a", "prefix": "2001:db8::8/125"},
        {"id": "b", "prefix": "2001:db8::/125"},
    ]


def test_unchanged_prefix_beats_address_preservation():
    # Keeping b's /126 exactly (criterion 1) forces a to ::8/125, even though
    # the alternative would preserve a's first address (criterion 2).
    resp = plan(
        {
            "pool": "2001:db8::/124",
            "existing": [
                {"id": "a", "prefix": "2001:db8::/126"},
                {"id": "b", "prefix": "2001:db8::4/126"},
            ],
            "targets": [
                {"id": "a", "prefix_length": 125},
                {"id": "b", "prefix_length": 126},
            ],
        }
    )
    assert resp.json() == [
        {"id": "a", "prefix": "2001:db8::8/125"},
        {"id": "b", "prefix": "2001:db8::4/126"},
    ]


def test_both_old_tenants_kept_when_possible():
    resp = plan(
        {
            "pool": POOL,
            "existing": [
                {"id": "a", "prefix": "2001:db8::/126"},
                {"id": "b", "prefix": "2001:db8::4/127"},
            ],
            "targets": [
                {"id": "a", "prefix_length": 126},
                {"id": "b", "prefix_length": 127},
                {"id": "c", "prefix_length": 127},
            ],
        }
    )
    assert resp.json() == [
        {"id": "a", "prefix": "2001:db8::/126"},
        {"id": "b", "prefix": "2001:db8::4/127"},
        {"id": "c", "prefix": "2001:db8::6/127"},
    ]


def test_released_tenant_space_is_reused():
    # old tenant x is not in targets -> released; a may take its space.
    resp = plan(
        {
            "pool": "2001:db8::/127",
            "existing": [{"id": "x", "prefix": "2001:db8::/128"}],
            "targets": [{"id": "a", "prefix_length": 128}],
        }
    )
    assert resp.json() == [{"id": "a", "prefix": "2001:db8::/128"}]


def test_unchanged_prefix_preferred_when_free():
    resp = plan(
        {
            "pool": POOL,
            "existing": [{"id": "a", "prefix": "2001:db8::6/127"}],
            "targets": [{"id": "a", "prefix_length": 127}],
        }
    )
    assert resp.json() == [{"id": "a", "prefix": "2001:db8::6/127"}]


def test_full_pool_single_target():
    resp = plan({"pool": POOL, "targets": [{"id": "all", "prefix_length": 125}]})
    assert resp.json() == [{"id": "all", "prefix": "2001:db8::/125"}]


def test_409_when_targets_do_not_fit():
    resp = plan(
        {
            "pool": "2001:db8::/127",
            "targets": [
                {"id": "a", "prefix_length": 127},
                {"id": "b", "prefix_length": 127},
            ],
        }
    )
    assert resp.status_code == 409


def test_409_when_total_size_exceeds_pool():
    resp = plan(
        {
            "pool": "2001:db8::/123",
            "targets": [{"id": f"t{i}", "prefix_length": 125} for i in range(8)],
        }
    )
    assert resp.status_code == 409


def test_422_pool_prefix_length_out_of_range():
    for pool in ("2001:db8::/122", "2001:db8::/128"):
        resp = plan({"pool": pool, "targets": [{"id": "a", "prefix_length": 128}]})
        assert resp.status_code == 422, pool
        assert resp.json()["detail"][0]["loc"][:1] == ["body"]


def test_422_pool_not_aligned():
    resp = plan(
        {"pool": "2001:db8::1/125", "targets": [{"id": "a", "prefix_length": 125}]}
    )
    assert resp.status_code == 422


def test_422_pool_not_ipv6():
    resp = plan({"pool": "10.0.0.0/24", "targets": [{"id": "a", "prefix_length": 125}]})
    assert resp.status_code == 422


def test_422_existing_misaligned():
    resp = plan(
        {
            "pool": POOL,
            "existing": [{"id": "a", "prefix": "2001:db8::1/127"}],
            "targets": [{"id": "a", "prefix_length": 127}],
        }
    )
    assert resp.status_code == 422


def test_422_existing_outside_pool():
    resp = plan(
        {
            "pool": POOL,
            "existing": [{"id": "a", "prefix": "2001:db8::8/127"}],
            "targets": [{"id": "a", "prefix_length": 127}],
        }
    )
    assert resp.status_code == 422
    assert "existing" in str(resp.json()["detail"][0]["loc"])


def test_422_existing_overlapping():
    resp = plan(
        {
            "pool": POOL,
            "existing": [
                {"id": "a", "prefix": "2001:db8::/126"},
                {"id": "b", "prefix": "2001:db8::2/127"},
            ],
            "targets": [{"id": "a", "prefix_length": 126}],
        }
    )
    assert resp.status_code == 422


def test_422_duplicate_ids():
    resp = plan(
        {
            "pool": POOL,
            "existing": [
                {"id": "a", "prefix": "2001:db8::/127"},
                {"id": "a", "prefix": "2001:db8::2/127"},
            ],
            "targets": [{"id": "a", "prefix_length": 127}],
        }
    )
    assert resp.status_code == 422

    resp = plan(
        {
            "pool": POOL,
            "targets": [
                {"id": "a", "prefix_length": 127},
                {"id": "a", "prefix_length": 127},
            ],
        }
    )
    assert resp.status_code == 422


def test_422_bad_ids():
    for bad_id in ("", "租户", "idé"):
        resp = plan({"pool": POOL, "targets": [{"id": bad_id, "prefix_length": 127}]})
        assert resp.status_code == 422, bad_id


def test_422_target_prefix_length_bounds():
    # shorter than the pool
    resp = plan({"pool": POOL, "targets": [{"id": "a", "prefix_length": 124}]})
    assert resp.status_code == 422
    # longer than /128
    resp = plan({"pool": POOL, "targets": [{"id": "a", "prefix_length": 129}]})
    assert resp.status_code == 422
    # not an integer
    resp = plan({"pool": POOL, "targets": [{"id": "a", "prefix_length": "127"}]})
    assert resp.status_code == 422
    resp = plan({"pool": POOL, "targets": [{"id": "a", "prefix_length": 127.5}]})
    assert resp.status_code == 422


def test_422_cardinality_limits():
    resp = plan({"pool": POOL, "targets": []})
    assert resp.status_code == 422
    resp = plan(
        {
            "pool": "2001:db8::/123",
            "targets": [{"id": f"t{i}", "prefix_length": 128} for i in range(9)],
        }
    )
    assert resp.status_code == 422
    resp = plan(
        {
            "pool": "2001:db8::/123",
            "existing": [
                {"id": f"e{i}", "prefix": f"2001:db8::{i:x}/128"} for i in range(9)
            ],
            "targets": [{"id": "a", "prefix_length": 128}],
        }
    )
    assert resp.status_code == 422


def test_existing_may_be_omitted():
    resp = plan({"pool": POOL, "targets": [{"id": "a", "prefix_length": 127}]})
    assert resp.status_code == 200


def test_128_targets_in_123_pool():
    resp = plan(
        {
            "pool": "2001:db8::/123",
            "targets": [{"id": f"t{i}", "prefix_length": 128} for i in range(8)],
        }
    )
    assert resp.status_code == 200
    base = int(IPv6Network("2001:db8::/123").network_address)
    assert [row["prefix"] for row in resp.json()] == [
        str(IPv6Network((base + i, 128))) for i in range(8)
    ]
