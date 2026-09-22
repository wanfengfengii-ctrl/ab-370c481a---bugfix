"""Exact three-mask assignment with stitch minimization.

Every layout fragment is assigned to one of three masks so that each
conflict edge is bichromatic, while the total weight of stitch edges whose
endpoints land on different masks ("cut" stitches) is minimized.

All reported colorings are canonical: scanning fragments in ascending id
order, the first mask encountered is 0, the next new mask is 1, then 2.
Canonical form quotients out the six mask permutations, which makes
uniqueness of the optimum well defined.

Two exact engines share the work:

* The CBC MILP is used whenever every stitch weight survives the pipeline
  exactly — PuLP serializes the MPS with thirteen significant digits and
  CBC computes in IEEE-754 doubles, so weights up to ``10**9`` (with the
  total below ``2**53``) keep exact integer semantics in the objective, the
  optimal face and the uniqueness probe.  If CBC still fails to certify a
  result, the solve falls back to the second engine.
* Heavier weights are handled by an exact branch-and-bound that never
  leaves Python's arbitrary-precision integers, so any positive integer
  weight keeps its exact order in the objective, the optimal face, the
  canonical primary solution and the uniqueness determination.
"""

from __future__ import annotations

import time

import pulp

MASKS = (0, 1, 2)
TIME_LIMIT_SECONDS = 30

# Largest weight the MILP engine handles exactly: PuLP serializes the MPS
# with thirteen significant digits and CBC computes in IEEE-754 doubles, so
# on this domain every coefficient and every reachable objective value is an
# exactly represented integer, far inside CBC's numerical tolerances.
# Heavier weights go to the exact integer search below.
_MILP_MAX_SAFE_WEIGHT = 10**9
_MILP_MAX_SAFE_TOTAL = 2**53 - 1

# Resource budget for the exact integer search.  Exhausting it is reported
# like a solver timeout instead of returning an uncertified answer.
_EXACT_NODE_BUDGET = 4_000_000
_EXACT_TIME_LIMIT_SECONDS = 25.0


class SolverError(Exception):
    """The solver could not certify an optimal solution."""


class _SearchExhausted(Exception):
    """The exact search exceeded its node or time budget."""


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


def _solve_proven(prob):
    """Solve ``prob`` and return the certificate: ``"optimal"`` when CBC
    proved optimality, ``"infeasible"`` when it proved infeasibility, or
    ``None`` otherwise.

    PuLP maps CBC's "Stopped on time - objective ..." to ``LpStatusOptimal``
    when an incumbent exists, so ``LpStatus`` alone cannot distinguish a
    proven optimum from an unproven incumbent; the solution status keeps
    them apart.
    """
    prob.solve(_cbc())
    if _status(prob) == "Infeasible":
        return "infeasible"
    if (
        _status(prob) == "Optimal"
        and prob.sol_status == pulp.constants.LpSolutionOptimal
    ):
        return "optimal"
    return None


def _color_of(x, i):
    return max(MASKS, key=lambda k: pulp.value(x[i][k]) or 0.0)


def _cut_stitches(stitch_edges, pos, colors):
    return [
        {"pair": [a, b], "weight": w}
        for a, b, w in stitch_edges
        if colors[pos[a]] != colors[pos[b]]
    ]


def _solve_milp(order, conflict_edges, stitches):
    """Solve one validated instance with the CBC MILP engine.

    Callers must guarantee every weight is within ``_MILP_MAX_SAFE_WEIGHT``
    and the weight total within ``_MILP_MAX_SAFE_TOTAL`` so the solve is
    exact.  Returns the same response shape as ``solve_mask_assignment``.
    """
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}

    prob, x, objective = _build_problem(order, conflict_edges, stitches)
    cert = _solve_proven(prob)
    if cert == "infeasible":
        return {"status": "infeasible"}
    if cert != "optimal":
        raise SolverError("求解器未能在限定时间内证明最优解")

    # Read the optimum off the optimal coloring itself: stitch weights are
    # positive integers, so the cut total is an exact integer.
    first_colors = [_color_of(x, i) for i in range(n)]
    best = sum(w for a, b, w in stitches if first_colors[pos[a]] != first_colors[pos[b]])
    # Pin the optimal face.  Objectives are integers, so ``<= best`` selects
    # exactly the optimal face; the integer bound serializes exactly (unlike
    # a fractional slack, which CBC's tolerances and the MPS rounding can
    # corrupt — a tiny 1e-6 slack is known to break CBC's cut generation).
    prob += objective <= best, "optimal_bound"

    # Lexicographically smallest canonical optimum: minimize the mask at
    # each position in turn (ids ascending), then pin it before moving on.
    colors = [0] * n
    for i in range(n):
        prob.setObjective(pulp.lpSum(k * x[i][k] for k in MASKS))
        if _solve_proven(prob) != "optimal":
            raise SolverError("求解器在构造字典序最小方案时失败")
        colors[i] = _color_of(x, i)
        prob += x[i][colors[i]] == 1, f"fix_{i}"

    # Uniqueness probe: any canonical optimum different from the one above?
    for i in range(n):
        prob.constraints.pop(f"fix_{i}", None)
    prob += pulp.lpSum(x[i][colors[i]] for i in range(n)) <= n - 1, "exclude_assignment"
    cert = _solve_proven(prob)
    if cert is None:
        raise SolverError("求解器未能在限定时间内判定唯一性")
    unique = cert != "optimal"

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


class _ExactSearch:
    """Branch-and-bound over canonical colorings in exact integer arithmetic.

    Fragments are colored in ascending id order; at each step the usable
    masks are those not forbidden by already-colored conflict neighbors and
    not beyond the canonical first-occurrence order.  The lower bound is
    the exact cut weight inside the colored prefix plus, for every
    uncolored fragment, the cheapest cut weight its already-colored stitch
    neighbors force on its best remaining mask (edges between two uncolored
    fragments contribute nothing).  All quantities are Python integers, so
    stitch weights of any size compare exactly.
    """

    def __init__(self, order, conflict_edges, stitches):
        n = len(order)
        pos = {v: i for i, v in enumerate(order)}
        self.n = n
        self.conf = [[] for _ in range(n)]
        for a, b in conflict_edges:
            ia, ib = pos[a], pos[b]
            self.conf[ia].append(ib)
            self.conf[ib].append(ia)
        self.st = [[] for _ in range(n)]
        for a, b, w in stitches:
            ia, ib = pos[a], pos[b]
            self.st[ia].append((ib, w))
            self.st[ib].append((ia, w))
        self.total = sum(w for _a, _b, w in stitches)
        self.colors = [-1] * n
        self.allowed = [7] * n  # bitmask of masks not ruled out by conflicts
        # cut_if[j][k]: weight of edges from colored neighbors of j that
        # would be cut if j took mask k.
        self.cut_if = [[0, 0, 0] for _ in range(n)]
        # marginal[j]: cheapest such weight over the masks still allowed
        # for j; bound_sum sums it over the uncolored fragments.
        self.marginal = [0] * n
        self.bound_sum = 0
        self.cost = 0  # exact cut weight inside the colored prefix
        self.max_used = -1
        self.limit = self.total + 1  # prune when cost + bound >= limit
        self.hit = lambda cost: None
        self.stop = False
        self.nodes = 0
        self.deadline = time.monotonic() + _EXACT_TIME_LIMIT_SECONDS

    # -- incremental state maintenance -----------------------------------

    def _place(self, i, k):
        """Color fragment i with mask k; returns an undo token or ``None``
        if the placement makes the instance infeasible."""
        colors = self.colors
        for j in self.conf[i]:
            if j < i and colors[j] == k:
                return None
        rec = [("bs", self.bound_sum), ("cost", self.cost), ("mu", self.max_used)]
        colors[i] = k
        if k > self.max_used:
            self.max_used = k
        added = 0
        st_i = self.st[i]
        for j, w in st_i:
            if j < i and colors[j] != k:
                added += w
        self.cost += added
        # Fragment i leaves the uncolored pool.
        rec.append(("marg", i, self.marginal[i]))
        self.bound_sum -= self.marginal[i]
        self.marginal[i] = 0
        # Edges from i to uncolored neighbors join their forced-cut terms.
        for j, w in st_i:
            if j > i:
                cj = self.cut_if[j]
                for b in MASKS:
                    if b != k:
                        cj[b] += w
                rec.append(("marg", j, self.marginal[j]))
                new_m = min(cj[b] for b in MASKS if (self.allowed[j] >> b) & 1)
                self.bound_sum += new_m - self.marginal[j]
                self.marginal[j] = new_m
        # Mask k becomes forbidden for uncolored conflict neighbors.
        for j in self.conf[i]:
            if j > i and (self.allowed[j] >> k) & 1:
                rec.append(("allowed", j, self.allowed[j]))
                self.allowed[j] &= ~(1 << k)
                if self.allowed[j] == 0:
                    self._undo(i, rec)
                    return None
                rec.append(("marg", j, self.marginal[j]))
                new_m = min(
                    self.cut_if[j][b] for b in MASKS if (self.allowed[j] >> b) & 1
                )
                self.bound_sum += new_m - self.marginal[j]
                self.marginal[j] = new_m
        return rec

    def _undo(self, i, rec):
        k = self.colors[i]
        for j, w in self.st[i]:
            if j > i:
                cj = self.cut_if[j]
                for b in MASKS:
                    if b != k:
                        cj[b] -= w
        for item in reversed(rec):
            tag = item[0]
            if tag == "marg":
                self.marginal[item[1]] = item[2]
            elif tag == "allowed":
                self.allowed[item[1]] = item[2]
            elif tag == "bs":
                self.bound_sum = item[1]
            elif tag == "cost":
                self.cost = item[1]
            else:  # "mu"
                self.max_used = item[1]
        self.colors[i] = -1

    # -- depth-first search -----------------------------------------------

    def _dfs(self, i):
        if self.stop:
            return
        self.nodes += 1
        if self.nodes > _EXACT_NODE_BUDGET:
            raise _SearchExhausted
        if self.nodes & 4095 == 0 and time.monotonic() > self.deadline:
            raise _SearchExhausted
        if self.cost + self.bound_sum >= self.limit:
            return
        if i == self.n:
            self.hit(self.cost)
            return
        max_color = self.max_used + 1
        if max_color > 2:
            max_color = 2
        ai = self.allowed[i]
        colors = self.colors
        st_i = self.st[i]
        # Cheapest added cut weight first finds good incumbents early;
        # ties break by mask id so the search is deterministic.
        cands = []
        for k in MASKS:
            if k > max_color or not (ai >> k) & 1:
                continue
            added = 0
            for j, w in st_i:
                if j < i and colors[j] != k:
                    added += w
            cands.append((added, k))
        cands.sort()
        for _added, k in cands:
            rec = self._place(i, k)
            if rec is None:
                continue
            self._dfs(i + 1)
            self._undo(i, rec)
            if self.stop:
                return

    # -- search phases ------------------------------------------------------

    def optimize(self):
        """Return the minimum cut weight, or ``None`` if infeasible."""
        self.limit = self.total + 1
        self.best = None
        self.stop = False

        def hit(cost):
            self.best = cost
            self.limit = cost
            if cost == 0:
                self.stop = True

        self.hit = hit
        self._dfs(0)
        return self.best

    def _feasible_from(self, start, limit_cost):
        """Whether the current prefix extends to a coloring of cost at most
        ``limit_cost``."""
        self.limit = limit_cost + 1
        self.stop = False
        self.found = False

        def hit(cost):
            self.found = True
            self.stop = True

        self.hit = hit
        self._dfs(start)
        return self.found

    def lexmin(self, best_cost):
        """Lexicographically smallest canonical coloring of cost ``best_cost``."""
        colors = []
        pinned = []
        try:
            for i in range(self.n):
                chosen = None
                for k in MASKS:
                    if k > min(self.max_used + 1, 2) or not (self.allowed[i] >> k) & 1:
                        continue
                    rec = self._place(i, k)
                    if rec is None:
                        continue
                    if self._feasible_from(i + 1, best_cost):
                        chosen = rec
                        colors.append(k)
                        break
                    self._undo(i, rec)
                if chosen is None:  # pragma: no cover - best_cost is achievable
                    raise SolverError("求解器在构造字典序最小方案时失败")
                pinned.append((i, chosen))
        finally:
            for i, rec in reversed(pinned):
                self._undo(i, rec)
        return colors

    def find_other(self, best_cost, exclude):
        """Return a canonical optimum different from ``exclude``, or ``None``."""
        self.limit = best_cost + 1
        self.stop = False
        self.other = None

        def hit(cost):
            if tuple(self.colors) != exclude:
                self.other = tuple(self.colors)
                self.stop = True

        self.hit = hit
        self._dfs(0)
        return self.other


def _solve_exact(order, conflict_edges, stitches):
    """Solve one validated instance in exact integer arithmetic.

    Used when stitch weights exceed the domain where the CBC MILP is
    exact; returns the same response shape as ``solve_mask_assignment``.
    """
    n = len(order)
    pos = {v: i for i, v in enumerate(order)}
    search = _ExactSearch(order, conflict_edges, stitches)
    try:
        best = search.optimize()
        if best is None:
            return {"status": "infeasible"}
        colors = search.lexmin(best)
        other = search.find_other(best, tuple(colors))
    except _SearchExhausted:
        raise SolverError("求解器未能在限定时间内求得最优解") from None

    unique = other is None
    witness = None
    if not unique:
        witness = {
            "assignment": {str(order[i]): other[i] for i in range(n)},
            "cut_stitches": _cut_stitches(stitches, pos, other),
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
    """
    order = sorted(fragments)
    stitches = [tuple(edge) for edge in stitch_edges]
    weights = [w for _a, _b, w in stitches]
    milp_exact = not weights or (
        max(weights) <= _MILP_MAX_SAFE_WEIGHT
        and sum(weights) <= _MILP_MAX_SAFE_TOTAL
    )
    if milp_exact:
        try:
            return _solve_milp(order, conflict_edges, stitches)
        except SolverError:
            # CBC could not certify a result; the exact integer search
            # always can (within its resource budget).
            return _solve_exact(order, conflict_edges, stitches)
    return _solve_exact(order, conflict_edges, stitches)
