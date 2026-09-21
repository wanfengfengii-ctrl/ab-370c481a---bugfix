"""API-level tests: validation, itemized errors, and result shapes."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_solve_optimal():
    resp = client.post(
        "/api/solve",
        json={
            "fragments": [1, 2, 3, 4],
            "conflict_edges": [[1, 2], [2, 3], [1, 3]],
            "stitch_edges": [{"pair": [3, 4], "weight": 5}, [1, 4, 1]],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "optimal"
    assert body["objective"] == 1
    assert body["unique"] is True
    assert body["assignment"] == {"1": 0, "2": 1, "3": 2, "4": 2}
    assert body["cut_stitches"] == [{"pair": [1, 4], "weight": 1}]


def test_solve_infeasible_is_distinguished():
    resp = client.post(
        "/api/solve",
        json={
            "fragments": [1, 2, 3, 4],
            "conflict_edges": [[a, b] for a in range(1, 5) for b in range(a + 1, 5)],
            "stitch_edges": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "infeasible"


def test_invalid_input_returns_itemized_errors():
    resp = client.post(
        "/api/solve",
        json={
            "fragments": [1, 1, 2, "x"],
            "conflict_edges": [[1, 9], [2, 2], [1, 2], [1, 2]],
            "stitch_edges": [{"pair": [1, 2], "weight": 3}, {"pair": [2, 3], "weight": 0}],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["status"] == "invalid"
    errors = body["errors"]
    assert len(errors) >= 8
    assert all(set(e) == {"loc", "message"} for e in errors)
    locs = {e["loc"] for e in errors}
    # duplicate id, non-integer id, fragment count
    assert "fragments[1]" in locs
    assert "fragments[3]" in locs
    assert "fragments" in locs
    # unknown endpoint, self-loop, duplicate pair
    assert "conflict_edges[0]" in locs
    assert "conflict_edges[1]" in locs
    assert "conflict_edges[3]" in locs
    # pair in both edge types, non-positive weight
    assert "stitch_edges[0]" in locs
    assert "stitch_edges[1]" in locs


def test_fragment_count_bounds():
    too_few = client.post("/api/solve", json={"fragments": [1, 2, 3]})
    assert too_few.status_code == 400
    too_many = client.post("/api/solve", json={"fragments": list(range(1, 50))})
    assert too_many.status_code == 400
    assert any("4–48" in e["message"] for e in too_many.json()["errors"])


def test_non_object_body_is_invalid():
    resp = client.post("/api/solve", json=[1, 2, 3])
    assert resp.status_code == 400
    assert resp.json()["status"] == "invalid"


def test_witness_returned_when_multiple_optima():
    resp = client.post("/api/solve", json={"fragments": [1, 2, 3, 4]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "optimal"
    assert body["unique"] is False
    assert body["witness"]["assignment"] != body["assignment"]
