"""Solver correctness tests, including brute-force cross-checks."""

from __future__ import annotations

import random

import pytest

from app.solver import solve_mask_assignment


def brute_force(fragments, conflict_edges, stitch_edges):
    """Enumerate all canonical colorings; returns (best_cost, solutions)."""
    order = sorted(fragments)
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    conf = [[] for _ in range(n)]
    for a, b in conflict_edges:
        conf[pos[a]].append(pos[b])
        conf[pos[b]].append(pos[a])
    st = [[] for _ in range(n)]
    for a, b, w in stitch_edges:
        st[pos[a]].append((pos[b], w))
        st[pos[b]].append((pos[a], w))

    best = None
    sols = []
    colors = [0] * n

    def rec(i, max_color, cost):
        nonlocal best, sols
        if best is not None and cost > best:
            return
        if i == n:
            if best is None or cost < best:
                best = cost
                sols = [tuple(colors)]
            elif cost == best:
                sols.append(tuple(colors))
            return
        for k in range(min(max_color + 1, 2) + 1):  # canonical growth only
            if any(colors[j] == k for j in conf[i] if j < i):
                continue
            extra = sum(w for j, w in st[i] if j < i and colors[j] != k)
            colors[i] = k
            rec(i + 1, max(max_color, k), cost + extra)

    rec(0, -1, 0)
    return best, sols


def check_against_brute_force(fragments, conflicts, stitches):
    result = solve_mask_assignment(fragments, conflicts, stitches)
    best, sols = brute_force(fragments, conflicts, stitches)
    order = sorted(fragments)

    if best is None:
        assert result["status"] == "infeasible"
        return

    assert result["status"] == "optimal"
    assert result["objective"] == best

    seq = tuple(result["assignment"][str(f)] for f in order)
    assert seq == min(sols), "not the lexicographically smallest canonical optimum"
    assert result["unique"] == (len(sols) == 1)

    pos = {f: i for i, f in enumerate(order)}
    expected_cut = sorted(
        (tuple(sorted((a, b))), w) for a, b, w in stitches if seq[pos[a]] != seq[pos[b]]
    )
    got_cut = sorted(
        (tuple(sorted(edge["pair"])), edge["weight"]) for edge in result["cut_stitches"]
    )
    assert got_cut == expected_cut
    assert sum(w for _pair, w in got_cut) == best

    if len(sols) > 1:
        witness = result["witness"]
        assert witness is not None
        wseq = tuple(witness["assignment"][str(f)] for f in order)
        assert wseq in sols
        assert wseq != seq
    else:
        assert result["witness"] is None


def test_unique_optimum_with_weighted_stitches():
    fragments = [1, 2, 3, 4]
    conflicts = [[1, 2], [2, 3], [1, 3]]
    stitches = [[3, 4, 5], [1, 4, 1]]
    result = solve_mask_assignment(fragments, conflicts, stitches)
    assert result["status"] == "optimal"
    assert result["objective"] == 1
    assert result["unique"] is True
    assert result["assignment"] == {"1": 0, "2": 1, "3": 2, "4": 2}
    assert result["cut_stitches"] == [{"pair": [1, 4], "weight": 1}]
    assert result["witness"] is None


def test_multiple_optima_returns_lexicographically_smallest_and_witness():
    result = solve_mask_assignment([1, 2, 3, 4], [], [])
    assert result["status"] == "optimal"
    assert result["objective"] == 0
    assert result["unique"] is False
    assert result["assignment"] == {"1": 0, "2": 0, "3": 0, "4": 0}
    witness = result["witness"]
    assert witness is not None
    assert witness["assignment"] != result["assignment"]
    # The witness must itself be canonical (first occurrences 0,1,2 in order).
    seq = [witness["assignment"][str(f)] for f in (1, 2, 3, 4)]
    next_color = 0
    for color in seq:
        assert color <= next_color
        next_color = max(next_color, color + 1)


def test_infeasible_k4():
    conflicts = [[a, b] for a in range(1, 5) for b in range(a + 1, 5)]
    result = solve_mask_assignment([1, 2, 3, 4], conflicts, [])
    assert result["status"] == "infeasible"


def test_non_contiguous_fragment_ids_are_normalized_by_ascending_id():
    fragments = [40, 7, 13, 21]
    conflicts = [[7, 13], [13, 21]]
    stitches = [[7, 40, 2]]
    check_against_brute_force(fragments, conflicts, stitches)


@pytest.mark.parametrize("seed", range(40))
def test_random_instances_match_brute_force(seed):
    rng = random.Random(seed)
    n = rng.randint(4, 8)
    fragments = sorted(rng.sample(range(1, 60), n))
    pairs = [
        (fragments[i], fragments[j])
        for i in range(n)
        for j in range(i + 1, n)
    ]
    rng.shuffle(pairs)
    conflicts, stitches = [], []
    for a, b in pairs:
        roll = rng.random()
        if roll < 0.25:
            conflicts.append([a, b])
        elif roll < 0.5:
            stitches.append([a, b, rng.randint(1, 9)])
    check_against_brute_force(fragments, conflicts, stitches)
