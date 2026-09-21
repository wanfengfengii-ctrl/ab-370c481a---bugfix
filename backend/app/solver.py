"""Exact three-mask assignment with stitch minimization.

Every layout fragment is assigned to one of three masks so that each
conflict edge is bichromatic, while the total weight of stitch edges whose
endpoints land on different masks ("cut" stitches) is minimized.

All reported colorings are canonical: scanning fragments in ascending id
order, the first mask encountered is 0, the next new mask is 1, then 2.
Canonical form quotients out the six mask permutations, which makes
uniqueness of the optimum well defined.
"""

from __future__ import annotations

import pulp

MASKS = (0, 1, 2)
TIME_LIMIT_SECONDS = 30


class SolverError(Exception):
    """The solver could not certify an optimal solution."""


def _build_problem(order, conflict_edges, stitch_edges):
    """Build the canonical-form MILP for one instance."""
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    prob = pulp.LpProblem("mask_assignment", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", (range(n), MASKS), cat=pulp.LpBinary)
    y = pulp.LpVariable.dicts("y", range(len(stitch_edges)), lowBound=0, upBound=1)

    for i in range(n):
        prob += pulp.lpSum(x[i][k] for k in MASKS) == 1, f"assign_{i}"

    # Canonical first-occurrence order: fragment i may take mask k > 0 only
    # if some earlier fragment (ascending id) already took mask k - 1.
    for i in range(n):
        for k in (1, 2):
            prob += (
                x[i][k] <= pulp.lpSum(x[j][k - 1] for j in range(i)),
                f"canon_{i}_{k}",
            )

    for a, b in conflict_edges:
        ia, ib = pos[a], pos[b]
        for k in MASKS:
            prob += x[ia][k] + x[ib][k] <= 1, f"conflict_{ia}_{ib}_{k}"

    for ei, (a, b, _w) in enumerate(stitch_edges):
        ia, ib = pos[a], pos[b]
        for k in MASKS:
            prob += y[ei] >= x[ia][k] - x[ib][k], f"cut_{ei}_{k}"

    objective = pulp.lpSum(w * y[ei] for ei, (_a, _b, w) in enumerate(stitch_edges))
    prob += objective
    return prob, x, objective


def _cbc():
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=TIME_LIMIT_SECONDS)


def _status(prob):
    return pulp.LpStatus[prob.status]


def _color_of(x, i):
    return max(MASKS, key=lambda k: pulp.value(x[i][k]) or 0.0)


def _cut_stitches(stitch_edges, pos, colors):
    return [
        {"pair": [a, b], "weight": w}
        for a, b, w in stitch_edges
        if colors[pos[a]] != colors[pos[b]]
    ]


def solve_mask_assignment(fragments, conflict_edges, stitch_edges):
    """Solve one validated instance.

    Returns ``{"status": "infeasible"}`` when the conflict graph admits no
    three-mask coloring.  Otherwise returns the lexicographically smallest
    canonical optimum (fragment ids ascending), whether that optimum is the
    unique canonical optimum, and — when it is not — a second, different
    canonical optimum as a witness.
    """
    order = sorted(fragments)
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    stitches = [tuple(edge) for edge in stitch_edges]

    prob, x, objective = _build_problem(order, conflict_edges, stitches)
    prob.solve(_cbc())
    status = _status(prob)
    if status == "Infeasible":
        return {"status": "infeasible"}
    if status != "Optimal":
        raise SolverError(f"求解器未能求得最优解（状态：{status}）")

    # Read the optimum off the optimal coloring itself: stitch weights are
    # positive integers, so the cut total is an exact integer.
    first_colors = [_color_of(x, i) for i in range(n)]
    best = sum(w for a, b, w in stitches if first_colors[pos[a]] != first_colors[pos[b]])
    prob += objective <= best + 1e-6, "optimal_bound"

    # Lexicographically smallest canonical optimum: minimize the mask at
    # each position in turn (ids ascending), then pin it before moving on.
    colors = [0] * n
    for i in range(n):
        prob.setObjective(pulp.lpSum(k * x[i][k] for k in MASKS))
        prob.solve(_cbc())
        if _status(prob) != "Optimal":
            raise SolverError("求解器在构造字典序最小方案时失败")
        colors[i] = _color_of(x, i)
        prob += x[i][colors[i]] == 1, f"fix_{i}"

    # Uniqueness probe: any canonical optimum different from the one above?
    for i in range(n):
        prob.constraints.pop(f"fix_{i}", None)
    prob += pulp.lpSum(x[i][colors[i]] for i in range(n)) <= n - 1, "exclude_assignment"
    prob.solve(_cbc())
    unique = _status(prob) != "Optimal"

    witness = None
    if not unique:
        witness_colors = [_color_of(x, i) for i in range(n)]
        witness = {
            "assignment": {str(order[i]): witness_colors[i] for i in range(n)},
            "cut_stitches": _cut_stitches(stitches, pos, witness_colors),
        }

    return {
        "status": "optimal",
        "objective": best,
        "unique": unique,
        "assignment": {str(order[i]): colors[i] for i in range(n)},
        "cut_stitches": _cut_stitches(stitches, pos, colors),
        "witness": witness,
    }
