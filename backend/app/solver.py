"""Exact three-mask assignment with stitch minimization.

Every layout fragment is assigned to one of three masks so that each
conflict edge is bichromatic, while the total weight of stitch edges whose
endpoints land on different masks ("cut" stitches) is minimized.

All reported colorings are canonical: scanning fragments in ascending id
order, the first mask encountered is 0, the next new mask is 1, then 2.
Canonical form quotients out the six mask permutations, which makes
uniqueness of the optimum well defined.

Stitch weights are arbitrary positive Python ints (JSON integers have no
size cap).  A solver storing matrix coefficients as IEEE-754 doubles
cannot compare weights at or above 2**53: adjacent weights would merge and
the optimal face would blur by relative epsilon.  To keep an exact
ordering for every allowed weight, no large weight ever reaches the
solver matrix.  The cut total is written in base-DIGIT_BASE digits linked
by integer carry equalities (all entries stay far below 2**53), and it is
minimized one digit at a time, most significant digit first --
lexicographic digit minimization with every other digit free is exactly
ordinary numerical minimization.  The lexicographic main scheme and the
uniqueness witness are then built on that exact optimal face.

Every solution accepted from the MILP solver is independently verified in
Python with arbitrary-precision integer arithmetic; a failed verification
reruns the whole instance with stricter solver tolerances, so solver
round-off can never corrupt the reported optimum, optimal face, or
uniqueness.
"""

from __future__ import annotations

import pulp

MASKS = (0, 1, 2)
TIME_LIMIT_SECONDS = 30

# Digit base for the exact cut-total encoding.  The solver certifies rows
# only within its primal feasibility tolerance (tightened below, applied
# after row scaling), so the largest matrix number times that tolerance
# must stay far below 0.5 -- otherwise an accepted "integer" solution could
# mis-state a digit and the exact digit pinning below would become
# inconsistent.  With at most 48 fragments there are m <= 48*47/2 = 1128
# stitch edges, hence the largest entry is m * (DIGIT_BASE - 1) ~= 1.1e6
# here, leaving a margin of several thousand, while even a 1000-digit
# weight needs only ~334 cheap digit solves.
DIGIT_BASE = 1_000

# Solver binary values are trusted as 0/1 only when they land within this
# distance of an integer (they do under the tightened tolerances).
_INTEGER_EPS = 1e-6


class SolverError(Exception):
    """The solver could not certify an optimal solution."""


class _NumericalIssue(Exception):
    """An accepted solver solution failed exact verification.

    Raised internally to trigger a retry with stricter solver settings;
    never surfaces through the API.
    """


def _build_problem(order, conflict_edges, stitch_edges):
    """Build the constraint part of the canonical-form MILP."""
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    prob = pulp.LpProblem("mask_assignment", pulp.LpMinimize)
    x = pulp.LpVariable.dicts("x", (range(n), MASKS), cat=pulp.LpBinary)
    y = pulp.LpVariable.dicts("y", range(len(stitch_edges)), cat=pulp.LpBinary)

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

    # y[ei] is made EXACTLY the "endpoints differ" indicator (not merely a
    # relaxation of it).  The lower bounds force y to 1 when colors differ;
    # the upper bounds force y to 0 when they share a mask -- when both
    # take mask k, x_a[k] + x_b[k] = 2 and y <= 0, while differing colors
    # make every upper bound at least 1.  Exactness is essential for the
    # digit encoding below: a spurious y = 1 on an uncut edge could make a
    # pinned "minimum total" unrealizable by any concrete coloring.
    for ei, (a, b, _w) in enumerate(stitch_edges):
        ia, ib = pos[a], pos[b]
        for k in MASKS:
            prob += y[ei] >= x[ia][k] - x[ib][k], f"cut_lo_{ei}_{k}"
            prob += y[ei] <= 2 - x[ia][k] - x[ib][k], f"cut_hi_{ei}_{k}"

    return prob, x, y


def _milp_solver(strict=False):
    # HiGHS backend (via highspy).  Its presolve was observed to falsely
    # certify otherwise feasible models of the exact digit encoding
    # infeasible ("Presolve: Infeasible" despite an explicit feasible
    # point), so presolve is switched off; single-threaded execution keeps
    # results deterministic.  Tight feasibility tolerances keep accepted
    # integer solutions exact on the digit rows (the largest matrix entry
    # is around 1e6).  ``strict`` tightens them further for the exactness
    # retry, and the instances are tiny (<= 48 binary assignment vars).
    tol = 1e-11 if strict else 1e-9
    return pulp.HiGHS(
        msg=False,
        timeLimit=TIME_LIMIT_SECONDS,
        threads=1,
        # Prove every optimum exactly: the per-solve objectives are small
        # (a single 0..999 digit or a 0..2 lexicographic term), so a zero
        # MIP gap costs nothing here and forbids "close enough" stops.
        gapAbs=0.0,
        gapRel=0.0,
        primal_feasibility_tolerance=tol,
        mip_feasibility_tolerance=tol,
        presolve="off",
    )


def _status(prob):
    return pulp.LpStatus[prob.status]


def _integer_value(var):
    """Exact nearest integer of a solved binary/integer variable."""
    value = pulp.value(var)
    if value is None or abs(value - round(value)) > _INTEGER_EPS:
        raise _NumericalIssue(f"整数变量取值不精确：{value!r}")
    return int(round(value))


def _binary_vector(variables, count):
    return [_integer_value(variables[e]) for e in range(count)]


def _colors_from_x(x, n):
    """Read a coloring off a solved model, verified to be an exact 0/1 pick."""
    colors = []
    for i in range(n):
        picked = [k for k in MASKS if _integer_value(x[i][k]) == 1]
        if len(picked) != 1:
            raise _NumericalIssue("片段的掩模取值不是唯一的 0/1 分配")
        colors.append(picked[0])
    return colors


def _cut_stitches(stitch_edges, pos, colors):
    return [
        {"pair": [a, b], "weight": w}
        for a, b, w in stitch_edges
        if colors[pos[a]] != colors[pos[b]]
    ]


def _cut_total(stitches, pos, colors):
    """Exact (arbitrary-precision) cut total of a concrete coloring."""
    return sum(
        w for a, b, w in stitches if colors[pos[a]] != colors[pos[b]]
    )


def _digit_count(value, base=DIGIT_BASE):
    """Number of base digits needed to represent ``value`` (>= 1)."""
    count = 0
    while value:
        count += 1
        value //= base
    return max(1, count)


def _add_cut_total_digits(prob, y, weights, digits):
    """Encode T = sum(w_e * y_e) in exact base-DIGIT_BASE digits.

    Adds digit variables ``t_d`` (0..base-1) and non-negative carry
    variables with the usual carrying equalities::

        col_d + carry_d = t_d + base * carry_{d+1}

    where ``col_d`` is the d-th digit column of the weighted sum and
    carry_0 is 0 / carry_{digits} is 0.  Every coefficient is below
    DIGIT_BASE, so IEEE-754 round-off cannot blur any comparison, even when
    the weights themselves are far above 2**53.

    Returns ``(t_digit_vars, weights_digits)``, least significant digit
    first.
    """
    base = DIGIT_BASE
    m = len(weights)
    # Each carry is at most m (m edges contribute at most base - 1 per
    # digit column); the explicit bound also keeps the search tight.
    carry = [
        pulp.LpVariable(f"carry_{d}", lowBound=0, upBound=m, cat="Integer")
        for d in range(digits + 1)
    ]
    t = [
        pulp.LpVariable(
            f"total_digit_{d}", lowBound=0, upBound=base - 1, cat="Integer"
        )
        for d in range(digits)
    ]

    weight_digits = []
    for w in weights:
        column, v = [], w
        for _ in range(digits):
            column.append(v % base)
            v //= base
        weight_digits.append(column)

    for d in range(digits):
        column = pulp.lpSum(
            wd[d] * y[e]
            for e, wd in enumerate(weight_digits)
            if wd[d]
        )
        prob += (
            column + carry[d] == t[d] + base * carry[d + 1],
            f"total_digit_{d}",
        )
    prob += carry[0] == 0, "carry_in_zero"
    prob += carry[digits] == 0, "carry_out_zero"
    return t, weight_digits


def _is_canonical(colors):
    next_color = 0
    for color in colors:
        if color > next_color:
            return False
        if color == next_color:
            next_color += 1
    return True


def _attempt(order, n, pos, stitches, weights, conflict_edges, strict):
    """One full exact solve attempt; raises _NumericalIssue to retry."""
    solver = _milp_solver(strict)
    prob, x, y = _build_problem(order, conflict_edges, stitches)
    base = DIGIT_BASE
    pinned = {}  # digit index -> exact pinned value

    def run():
        # Solve once; an "infeasible" verdict is corroborated with the
        # stricter profile unless this attempt already uses it, because MILP
        # presolve has been observed to falsely certify feasible
        # digit/probe models infeasible on large-weight encodings.  Once
        # corroboration is needed, keep using the strict solver for every
        # later solve.
        nonlocal solver
        prob.solve(solver)
        status = _status(prob)
        if status == "Infeasible" and not strict:
            solver = _milp_solver(strict=True)
            prob.solve(solver)
            status = _status(prob)
        return status

    if weights:
        digits = _digit_count(sum(weights))
        t, _weight_digits = _add_cut_total_digits(prob, y, weights, digits)

        # Exact minimization: lexicographically minimize the digits of the
        # cut total, most significant first (lower digits stay free, so
        # ties resolve exactly).  Each pinned value is derived from the
        # exact (Python big-int) cut total of the returned 0/1 vector --
        # never from a solver-reported integer variable -- and checked
        # against every previously pinned digit.
        for d in range(digits - 1, -1, -1):
            prob.setObjective(t[d])
            status = run()
            if status == "Infeasible":
                # With no pins yet the digit/link rows always admit a point
                # (any coloring with y all 1), so this certifies that the
                # conflict graph is uncolorable.  A later infeasible verdict
                # contradicts a point the solver itself returned earlier
                # and triggers the strict retry.
                if not pinned:
                    return {"status": "infeasible"}
                raise _NumericalIssue(f"最优数字 {d} 的求解被错误地判定为无解")
            if status != "Optimal":
                raise SolverError(f"求解器未能求得最优解（状态：{status}）")

            cut_vector = _binary_vector(y, len(weights))
            total = sum(w for w, cut in zip(weights, cut_vector) if cut)
            value = (total // base**d) % base
            for dd, pinned_value in pinned.items():
                if (total // base**dd) % base != pinned_value:
                    raise _NumericalIssue("返回方案违反此前已固定的最优数字")
            if _integer_value(t[d]) != value:
                raise _NumericalIssue("求解器报告的最优数字与精确值不一致")
            prob += t[d] == value, f"fix_total_digit_{d}"
            pinned[d] = value

        best = sum(pinned[d] * base**d for d in range(digits))
    else:
        best = 0
        # Structurally non-constant feasibility objective: a genuinely
        # constant objective makes PuLP cache a fixed __dummy variable on
        # the problem, which corrupts later MPS output.  Its value is
        # pinned to n by the assignment constraints anyway.
        prob.setObjective(pulp.lpSum(x[i][k] for i in range(n) for k in MASKS))
        status = run()
        if status == "Infeasible":
            return {"status": "infeasible"}
        if status != "Optimal":
            raise SolverError(f"求解器未能求得最优解（状态：{status}）")

    def verified_coloring():
        colors = _colors_from_x(x, n)
        if not _is_canonical(colors):
            raise _NumericalIssue("返回方案不满足颜色首次出现规范")
        if any(colors[pos[a]] == colors[pos[b]] for a, b in conflict_edges):
            raise _NumericalIssue("返回方案违反冲突边约束")
        return colors

    # Lexicographically smallest canonical optimum on the exact optimal
    # face: minimize the mask at each position in turn (ids ascending),
    # then pin it before moving on.
    colors = [0] * n
    for i in range(n):
        prob.setObjective(pulp.lpSum(k * x[i][k] for k in MASKS))
        status = run()
        if status != "Optimal":
            raise SolverError("求解器在构造字典序最小方案时失败")
        colors = verified_coloring()
        if _cut_total(stitches, pos, colors) != best:
            raise _NumericalIssue("字典序主方案不在精确最优面上")
        prob += x[i][colors[i]] == 1, f"fix_{i}"

    # Uniqueness probe: any canonical optimum different from the one above?
    for i in range(n):
        prob.constraints.pop(f"fix_{i}", None)
    prob += (
        pulp.lpSum(x[i][colors[i]] for i in range(n)) <= n - 1,
        "exclude_assignment",
    )
    status = run()
    if status not in ("Optimal", "Infeasible"):
        raise SolverError(f"唯一性判定失败（状态：{status}）")
    unique = status == "Infeasible"

    witness = None
    if not unique:
        witness_colors = verified_coloring()
        if witness_colors == colors:
            raise _NumericalIssue("见证方案与主方案相同")
        if _cut_total(stitches, pos, witness_colors) != best:
            raise _NumericalIssue("见证方案不在精确最优面上")
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


def solve_mask_assignment(fragments, conflict_edges, stitch_edges):
    """Solve one validated instance.

    Returns ``{"status": "infeasible"}`` when the conflict graph admits no
    three-mask coloring.  Otherwise returns the lexicographically smallest
    canonical optimum (fragment ids ascending), whether that optimum is the
    unique canonical optimum, and — when it is not — a second, different
    canonical optimum as a witness.

    All weight arithmetic is exact: the optimal value, the optimal face
    used to build the canonical main scheme, and the uniqueness decision
    agree on arbitrary positive integer weights, including adjacent
    integers above 2**53.
    """
    order = sorted(fragments)
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    stitches = [tuple(edge) for edge in stitch_edges]
    weights = [w for _a, _b, w in stitches]

    result = None
    for strict in (False, True):
        try:
            result = _attempt(
                order, n, pos, stitches, weights, conflict_edges, strict
            )
        except _NumericalIssue:
            if strict:
                raise SolverError("求解器返回的结果未通过精确校验")
        else:
            break
    return result
