"""PROOF HARNESS — the month-over-month GOAL ROLL-FORWARD is a DECLARED policy, not a constant.

Run: python3 backend/harness_goal_rollforward.py      (exit 0 = every proof holds)

OWNER DIRECTIVE 2026-11-25 (Sanjot, verbatim): "Update the Nov goals for boost same as Oct and set
that as default for tenant as we discussed - it will be the same if they did not achieve the goal if
they met the goal then it will be 10% higher than they achieved".

THE DEFECT THIS CLOSES — read the last six words again. The platform already carried a missed goal
forward and stretched a met one by 10%, in `commcalc/router._carry_forward_map`. But it stretched to
10% above the GOAL, not 10% above what the store ACHIEVED. Measured on live October 2026 house data,
five store-categories beat their goal and the two readings disagree on every one of them:

    B-6507 accessories  goal $7,500  achieved $8,471  ->  old $8,250   declared $9,318
    B-2778 activations  goal 75      achieved 78      ->  old 82       declared 86
    B-117  upgrades     goal 45      achieved 52      ->  old 50       declared 57
    B-151  activations  goal 61      achieved 65      ->  old 67       declared 72
    B-5619 upgrades     goal 50      achieved 51      ->  old 55       declared 56

A store that beats its goal by a wide margin is where the two readings diverge most, and the old one
rewards it with the smaller stretch — it measures the PLAN rather than the PERFORMANCE.

THE CLASS, NOT THE INSTANCE (CLAUDE.md "a fix is a DESIGN fix"). The instance is "Boost's November
goals are empty and the stretch is computed off the wrong number". The class is: **how next month's
goal follows from this month's is a POLICY, and a policy is declared per tenant with a house default
— never a module constant.** `STRETCH_FACTOR = 1.10` sitting in `router.py` is exactly the shape
RULE TWO forbids: a tenant that wants 5%, or wants to stretch off the plan, has nowhere to say so.

So this harness pins a MECHANISM:
  §A  the house default is declared once, in ONE home, and says basis='achieved', factor=1.10.
  §B  the arithmetic: missed -> carry, met -> factor x achieved, no prior goal -> 'new' (never 0).
  §C  'met' is `achieved >= goal`, boundary included, and a goal of 0 is not a goal.
  §D  rounding is asked of the CATEGORY'S UNIT — money to the cent, a tally to a whole unit.
  §E  a tenant row OVERRIDES the house default, per field, and a partial row inherits the rest.
  §F  EVERY TENANT (owner 2026-10-30): no tenant's policy can be reached from another's org_id, and
      a tenant with no row behaves as the house default rather than as nothing.
  §G  integration — the REAL `router._carry_forward_map` over an in-memory fake, proving it
      DEREFERENCES this module instead of keeping its own copy of the arithmetic.
  §H  THE UN-WIRE LOCK: `router.py` must not contain a second copy of the stretch arithmetic or of
      the factor, scanned over TOKENISED CODE with comments and docstrings stripped — the
      [[annual-goal-governs-the-months]] lesson, where a whole-file grep passed because the name it
      was looking for appeared in a docstring while a planted duplicate sat below it.
  §I  the roll-forward WRITER persists a declared category's goal too, so the day a tenant sets a
      drop-off goal it rolls like every built-in. `commcalc.target_line` is empty on all four
      tenants today, which is exactly why this half has to be proved rather than observed.

Not money: `targets_engine.achieved_for_cat` pays on ACTUALS and never reads a goal, so nothing
proved here can move a payout.
"""
import io
import re
import os
import sys
import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

FAILURES = []
CHECKS = [0]


def ok(label, cond, detail=""):
    CHECKS[0] += 1
    if not cond:
        FAILURES.append(f"{label}: {detail or 'assertion failed'}")


def eq(label, got, want):
    CHECKS[0] += 1
    if got != want:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")


# ── the module under proof ────────────────────────────────────────────────────────────────────────
from app.modules.commcalc import goal_rollforward as GR     # noqa: E402

HOUSE = "00000000-0000-0000-0000-000000000001"
LUXE = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
VZONE = "f4f1c16e-2acf-4221-854a-c29a605754a7"


# ── §A — ONE HOME for the house default ───────────────────────────────────────────────────────────
def section_a():
    d = GR.HOUSE_DEFAULT
    eq("A1 basis", d["stretch_basis"], "achieved")
    eq("A2 factor", round(float(d["stretch_factor"]), 4), 1.10)
    eq("A3 auto_fill on", bool(d["auto_fill"]), True)
    # The owner's words are "10% higher than they achieved" — the default must not be the old reading.
    ok("A4 default is not the plan-based reading", d["stretch_basis"] != "target")
    # A policy with no rows at all is still a complete policy.
    p = GR.policy_for([], HOUSE)
    for k in ("stretch_basis", "stretch_factor", "auto_fill"):
        ok(f"A5 {k} present with no rows", k in p, f"missing {k}")
    eq("A6 no rows -> house default basis", p["stretch_basis"], "achieved")


# ── §B — the arithmetic ───────────────────────────────────────────────────────────────────────────
def section_b():
    p = GR.policy_for([], HOUSE)
    # MISSED -> carry the same number forward, unchanged.
    r = GR.next_goal(prior_target=75, achieved=60, policy=p, unit="count")
    eq("B1 missed basis", r["basis"], "carry")
    eq("B2 missed value", r["value"], 75)
    # MET -> 10% above what they ACHIEVED (not above the goal).
    r = GR.next_goal(prior_target=75, achieved=78, policy=p, unit="count")
    eq("B3 met basis", r["basis"], "stretch")
    eq("B4 met value is 1.1 x achieved", r["value"], 86)      # 78 * 1.10 = 85.8 -> 86
    ok("B5 met value is NOT 1.1 x goal", r["value"] != 82, "stretched off the plan, not the result")
    # The live B-6507 accessories case, to the cent.
    r = GR.next_goal(prior_target=7500, achieved=8471.0, policy=p, unit="dollars")
    eq("B6 live accessories case", r["value"], 9318.1)
    # NO PRIOR GOAL -> 'new'. It must NOT invent a goal, and must NOT read as a goal of zero.
    r = GR.next_goal(prior_target=0, achieved=40, policy=p, unit="count")
    eq("B7 no prior goal basis", r["basis"], "new")
    eq("B8 no prior goal carries no figure", r["value"], None)
    ok("B9 'new' is not a zero goal", r["value"] is not None or r["value"] is None)
    eq("B10 no prior goal, no achievement", GR.next_goal(0, 0, p, unit="count")["basis"], "new")


# ── §C — what "met" means ─────────────────────────────────────────────────────────────────────────
def section_c():
    p = GR.policy_for([], HOUSE)
    eq("C1 exactly on goal counts as met", GR.next_goal(50, 50, p, unit="count")["basis"], "stretch")
    eq("C2 one short is missed", GR.next_goal(50, 49, p, unit="count")["basis"], "carry")
    eq("C3 one over is met", GR.next_goal(50, 51, p, unit="count")["basis"], "stretch")
    # A goal of zero is the ABSENCE of a goal, so beating it is not an achievement to stretch from.
    eq("C4 zero goal is no goal", GR.next_goal(0, 999, p, unit="count")["basis"], "new")
    # A negative stored goal is nonsense data, never a goal.
    eq("C5 negative goal is no goal", GR.next_goal(-5, 10, p, unit="count")["basis"], "new")
    # Missing achievement on a real goal is a MISS, not a crash.
    eq("C6 no achievement is a miss", GR.next_goal(50, None, p, unit="count")["basis"], "carry")
    eq("C7 ...and carries the goal", GR.next_goal(50, None, p, unit="count")["value"], 50)


# ── §D — rounding is asked of the unit ────────────────────────────────────────────────────────────
def section_d():
    p = GR.policy_for([], HOUSE)
    eq("D1 money keeps cents", GR.next_goal(6000, 6001.11, p, unit="dollars")["value"], 6601.22)
    eq("D2 a tally is whole", GR.next_goal(30, 31, p, unit="count")["value"], 34)   # 34.1 -> 34
    eq("D3 a carried tally is whole", GR.next_goal(33, 20, p, unit="count")["value"], 33)
    # An unknown unit must not silently become money; a goal is counted unless declared otherwise.
    eq("D4 unknown unit rounds whole", GR.next_goal(30, 31, p, unit="widgets")["value"], 34)
    ok("D5 the unit is a required argument",
       "unit" in GR.next_goal.__code__.co_varnames[:GR.next_goal.__code__.co_argcount
                                                   + GR.next_goal.__code__.co_kwonlyargcount])


# ── §E — a tenant row overrides, field by field ───────────────────────────────────────────────────
def section_e():
    rows = [{"org_id": LUXE, "stretch_basis": "target", "stretch_factor": 1.05, "auto_fill": False}]
    p = GR.policy_for(rows, LUXE)
    eq("E1 basis overridden", p["stretch_basis"], "target")
    eq("E2 factor overridden", round(float(p["stretch_factor"]), 4), 1.05)
    eq("E3 auto_fill overridden", bool(p["auto_fill"]), False)
    # ...and the declared 'target' basis then stretches off the PLAN, which is what it means.
    eq("E4 target basis uses the goal", GR.next_goal(100, 200, p, unit="count")["value"], 105)
    # A PARTIAL row inherits every field it does not state. A NULL column is not a declaration.
    p2 = GR.policy_for([{"org_id": LUXE, "stretch_factor": 1.20,
                         "stretch_basis": None, "auto_fill": None}], LUXE)
    eq("E5 partial row keeps house basis", p2["stretch_basis"], "achieved")
    eq("E6 partial row takes its factor", round(float(p2["stretch_factor"]), 4), 1.20)
    eq("E7 partial row keeps house auto_fill", bool(p2["auto_fill"]), True)
    # Nonsense in a column must not silently become a different policy.
    p3 = GR.policy_for([{"org_id": LUXE, "stretch_basis": "sideways"}], LUXE)
    eq("E8 unknown basis falls back to house", p3["stretch_basis"], "achieved")
    p4 = GR.policy_for([{"org_id": LUXE, "stretch_factor": 0}], LUXE)
    ok("E9 a factor below 1 cannot shrink a goal", float(p4["stretch_factor"]) >= 1.0,
       f"got {p4.get('stretch_factor')}")


# ── §F — EVERY TENANT, and no leakage between them (owner 2026-10-30) ─────────────────────────────
def section_f():
    rows = [
        {"org_id": HOUSE, "stretch_basis": "achieved", "stretch_factor": 1.10, "auto_fill": True},
        {"org_id": LUXE, "stretch_basis": "target", "stretch_factor": 1.05, "auto_fill": True},
    ]
    eq("F1 house reads its own", GR.policy_for(rows, HOUSE)["stretch_basis"], "achieved")
    eq("F2 luxe reads its own", GR.policy_for(rows, LUXE)["stretch_basis"], "target")
    eq("F3 luxe factor does not reach house",
       round(float(GR.policy_for(rows, HOUSE)["stretch_factor"]), 4), 1.10)
    # A tenant with NO row of its own gets the HOUSE DEFAULT POLICY — never another tenant's row,
    # and never "no policy at all". This is the leak the 2026-10-30 directive is about: a vocabulary
    # or a rate reached from another tenant's request.
    p = GR.policy_for(rows, VZONE)
    eq("F4 undeclared tenant gets house default basis", p["stretch_basis"], "achieved")
    eq("F5 undeclared tenant gets house default factor", round(float(p["stretch_factor"]), 4), 1.10)
    ok("F6 undeclared tenant is not handed LuxeLink's basis", p["stretch_basis"] != "target")
    # The owner chose auto-fill for EVERY tenant, so the default must be on without a row.
    eq("F7 auto_fill is on by default for every tenant", bool(p["auto_fill"]), True)
    # org_id is a REQUIRED argument — a caller that has not said which tenant cannot get an answer.
    try:
        GR.policy_for(rows)            # type: ignore[call-arg]
        FAILURES.append("F8 policy_for answered without an org_id")
    except TypeError:
        pass
    CHECKS[0] += 1
    # A row for one org must not be matched case-or-whitespace-loosely onto another.
    eq("F9 org match is exact", GR.policy_for([{"org_id": LUXE + " ", "stretch_basis": "target"}],
                                              LUXE)["stretch_basis"], "achieved")


# ── §G — INTEGRATION: the real router dereferences this module ────────────────────────────────────
def section_g():
    import _harness_dbfree

    OCT = "October 2026"
    stores = [{"store_code": "B-117", "address": "1 A St", "market": "LI", "is_active": True,
               "monthly_target": 0},
              {"store_code": "B-999", "address": "2 B St", "market": "LI", "is_active": True,
               "monthly_target": 0}]

    class _Q:
        def __init__(self, tbl, store):
            self.tbl, self.store, self.filters = tbl, store, {}

        def select(self, *_a, **_k):
            return self

        def eq(self, k, v):
            self.filters[k] = v
            return self

        def in_(self, k, v):
            self.filters[k] = list(v)
            return self

        def __getattr__(self, _n):
            return lambda *_a, **_k: self

        def execute(self):
            class R:
                pass
            r = R()
            r.data = list(self.store.get(self.tbl, []))
            return r

    class _Schema:
        def __init__(self, store):
            self.store = store

        def table(self, tbl):
            return _Q(tbl, self.store)

    class FakeClient:
        def __init__(self, store):
            self.store = store

        def schema(self, _s):
            return _Schema(self.store)

        def table(self, tbl):
            return _Q(tbl, self.store)

        def rpc(self, *_a, **_k):
            return _Q("__rpc__", {})

    STORE = {
        # B-117's October goal: 45 upgrades. It achieved 52 -> a stretch off 52, not off 45.
        "targets": [{"org_id": HOUSE, "store_code": "B-117", "period": OCT,
                     "activations_monthly": 82, "upgrades_monthly": 45,
                     "accessories_monthly": 14000, "byod_pct": 35}],
        "goal_rollforward_policy": [],
        "target_line": [],
    }
    fake = FakeClient(STORE)
    _harness_dbfree.install(fake)

    from app.modules.commcalc import router as R

    # Everything the carry-forward asks for that is not the arithmetic itself is stubbed, so this
    # section proves ONE thing: which number the stretch is taken from, inside the real function.
    R._fetch_actuals = lambda *_a, **_k: []
    R._byod_pct_default = lambda *_a, **_k: 35
    R._target_lines = lambda *_a, **_k: []
    R._target_catalog = lambda *_a, **_k: []

    import app.modules.commcalc.targets_engine as TE
    TE_scope = TE.scope_achieved_mtd
    TE.scope_achieved_mtd = lambda actuals, code, rep, today: (
        {"activations": 60, "upgrades": 52, "accessories": 100.0} if code == "B-117" else {})
    # RESTORED in `finally`. A stub left bound on the shared module object leaks into every later
    # section — the first version of this harness stubbed `_rollforward_policy_rows` here and §J
    # then read an empty policy list for EVERY tenant, so a tenant with auto_fill OFF was reported
    # as filled and the section passed for the wrong reason. A proof that depends on the order its
    # sections run in is not a proof.
    _policy_rows = R._rollforward_policy_rows
    R._rollforward_policy_rows = lambda *_a, **_k: []
    try:
        cf = R._carry_forward_map(fake, HOUSE, "November 2026", stores, catalog=[])
    finally:
        TE.scope_achieved_mtd = TE_scope
        R._rollforward_policy_rows = _policy_rows

    eq("G1 prior period resolved", cf["prior_period"], OCT)
    row = cf["by_code"]["B-117"]
    eq("G2 upgrades met -> stretch", row["basis"]["upgrades"], "stretch")
    # 52 achieved x 1.10 = 57.2 -> 57. The OLD reading would have produced 50 (45 x 1.1).
    eq("G3 the real router stretches off ACHIEVED", row["upgrades_monthly"], 57)
    ok("G4 ...and not off the goal", row["upgrades_monthly"] != 50,
       "router still computing 1.1 x goal")
    eq("G5 activations missed -> carry", row["basis"]["activations"], "carry")
    eq("G6 carried value unchanged", row["activations_monthly"], 82)
    # A store with no prior goal row must come back 'new' and carry no invented figure.
    eq("G7 store with no prior goal", cf["by_code"]["B-999"]["basis"]["activations"], "new")


# ── §H — THE UN-WIRE LOCK ─────────────────────────────────────────────────────────────────────────
def _code_only(path):
    """`path`'s source with comments and docstrings stripped.

    A whole-file grep is not a lock. §E9 of harness_annual_goal.py passed while a planted duplicate
    of the arithmetic sat in the file, because the NAME it searched for appeared in the module
    docstring. Words are proved over tokenised code; figures are proved over the whole file."""
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    out, prev, prev_end = [], tokenize.INDENT, (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        ttype, tstr, start, end, _ = tok
        if ttype == tokenize.COMMENT:
            continue
        if ttype == tokenize.STRING and prev in (tokenize.INDENT, tokenize.NEWLINE,
                                                 tokenize.NL, tokenize.DEDENT):
            prev = ttype                       # a bare string statement = a docstring
            continue
        if ttype not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(tstr)
        prev, prev_end = ttype, end
    return "\n".join(out)


def section_h():
    router = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
    code = _code_only(router)

    # BOTH of these are CODE-ONLY scans, and that is deliberate. The lock is about what the router
    # DOES, not about what its comments remember: the header above `_carry_forward_map` cites the
    # deleted `STRETCH_FACTOR = 1.10` by name and value precisely so the next reader knows why the
    # arithmetic is not there, and a lock that forbids documenting the defect would push the reason
    # out of the file. A whole-file figure scan is the right shape when the figure itself must never
    # appear (a rate nobody may hard-code anywhere); here the figure is the HOUSE DEFAULT and is
    # supposed to be written down — in `goal_rollforward.HOUSE_DEFAULT`, and nowhere else that runs.
    ok("H1 STRETCH_FACTOR no longer defined in router code",
       "STRETCH_FACTOR" not in code, "router still defines or reads its own stretch factor")
    ok("H2 no bare 1.10 stretch literal in router code",
       "1.10" not in code, "a stretch factor literal is back in router code")

    # The router must DEREFERENCE the one home.
    ok("H3 router imports the one home",
       "goal_rollforward" in code, "router does not reference goal_rollforward at all")
    ok("H4 the carry-forward asks for the next goal",
       "next_goal" in code, "router no longer calls goal_rollforward.next_goal")
    ok("H5 the carry-forward asks for the policy",
       "policy_for" in code or "_rollforward_policy" in code,
       "router no longer resolves a declared policy")

    # No second copy of the arithmetic: the comparison that decides 'met' belongs to the one home.
    seg = code
    i = seg.find("_carry_forward_map")
    body = seg[i:i + 6000] if i >= 0 else ""
    ok("H6 the carry-forward keeps no 'met' comparison of its own",
       "'stretch'" not in body and '"stretch"' not in body,
       "the router is deciding 'met' itself again instead of asking")

    # The one home must not be bypassed by a caller multiplying by a factor itself.
    ok("H7 the carry-forward does no stretching arithmetic",
       "stretch_factor" not in body, "the router is applying the factor itself")


# ── §I — the WRITER persists a declared category too ──────────────────────────────────────────────
def section_i():
    """The writer's REAL body, bounded by the next decorator.

    A fixed-width window is not a scope. The first version of this section took `code[i:i+9000]`
    and both checks passed while the function wrote nothing of the sort — the window simply ran past
    the end of the function into neighbours that do mention `target_line`. A vacuous check is worse
    than no check, because it reports the defect as fixed."""
    router = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
    with open(router, "r", encoding="utf-8") as fh:
        src = fh.read()
    i = src.find("def roll_forward_targets")
    j = src.find("\n@router", i + 10)
    ok("I1 roll_forward_targets found", i >= 0 and j > i)
    body = src[i:j] if (i >= 0 and j > i) else ""
    # PROOF THE BOUND IS REAL: the body must stop before the next endpoint, so a later function's
    # mention of target_line cannot satisfy I3.
    ok("I1b the scan is bounded to one function", 0 < len(body) < 14000, f"body {len(body)} chars")
    ok("I1c the bound excludes the next endpoint", "@router" not in body)
    # The suggestion already comes back under `lines`; writing only the built-in COLUMNS would mean a
    # declared category can never roll, which is the duplicate-shape defect wearing a hat.
    ok("I2 the writer reads the declared half", "lines" in body,
       "roll-forward ignores the declared categories' suggestions")
    # I3 — THE DECLARED HALF REALLY REACHES THE TABLE. Asserting `"target_line" in body` is NOT
    # enough and was briefly passing for the wrong reason: the declared write moved into the helper
    # `_write_rolled_target_lines`, and "target_line" is a SUBSTRING of that function's own name, so
    # the check went on passing while the body contained no table reference at all. Prove the chain:
    # the endpoint calls the writer, and the writer upserts the table.
    ok("I3 the writer hands the declared half to its writer",
       "_write_rolled_target_lines" in body,
       "roll-forward does not call the declared-half writer, so a declared goal never rolls")
    wi = src.find("def _write_rolled_target_lines")
    wj = src.find("\nclass ", wi + 10)
    wbody = src[wi:wj] if (wi >= 0 and wj > wi) else ""
    ok("I3b the declared-half writer exists and is bounded", 0 < len(wbody) < 4000, f"{len(wbody)}")
    ok("I3c ...and it upserts commcalc.target_line",
       "table('target_line')" in wbody and "upsert" in wbody,
       "the declared-half writer does not write the table")
    # ...and it must NOT stamp a process name into a UUID actor column (harness_actor_uid_lock).
    # CODE ONLY, past the docstring — which explains at length WHY 'roll-forward' and `updated_by`
    # must not appear here, and so contains both words. Checking the whole body made this fail on
    # its own explanation: the §H lesson again, in a new place.
    wcode = wbody.split('"""')[-1] if wbody.count('"""') >= 2 else wbody
    ok("I3d ...without stamping a process into the actor column",
       "roll-forward" not in wcode and "updated_by" not in wcode,
       f"the declared-half writer is putting a non-UUID actor into target_line.updated_by")
    # A 'new' category has NO goal to roll (value None) and must never be written as a goal of zero.
    ok("I4 the writer skips a category with no goal to roll",
       "is not None" in body, "the writer cannot be distinguishing 'no goal' from a goal of zero")


# ── §J — THE MONTHLY AUTO-FILL, driven through the real endpoint ──────────────────────────────────
class _RecQ:
    """A query builder that RECORDS writes, so a proof can assert what was written and what was not."""

    def __init__(self, tbl, store, writes):
        self.tbl, self.store, self.writes, self.filters = tbl, store, writes, {}

    def select(self, *_a, **_k):
        return self

    def eq(self, k, v):
        self.filters[k] = v
        return self

    def in_(self, k, v):
        self.filters[k] = list(v)
        return self

    def limit(self, _n):
        return self

    def upsert(self, rows, **_k):
        rows = rows if isinstance(rows, list) else [rows]
        self.writes.append((self.tbl, rows))
        self.store.setdefault(self.tbl, []).extend(rows)
        return self

    def __getattr__(self, _n):
        return lambda *_a, **_k: self

    def execute(self):
        class R:
            pass
        r = R()
        rows = list(self.store.get(self.tbl, []))
        for k, v in self.filters.items():
            if isinstance(v, list):
                rows = [x for x in rows if str(x.get(k)) in {str(y) for y in v}]
            else:
                rows = [x for x in rows if str(x.get(k)) == str(v)]
        r.data = rows
        return r


class _RecClient:
    def __init__(self, store):
        self.store, self.writes = store, []

    def schema(self, _s):
        return self

    def table(self, tbl):
        return _RecQ(tbl, self.store, self.writes)

    def rpc(self, *_a, **_k):
        return _RecQ("__rpc__", {}, [])

    def written_to(self, tbl):
        return [r for t, rows in self.writes if t == tbl for r in rows]


def section_j():
    import _harness_dbfree
    from fastapi import HTTPException

    SECRET = "harness-secret-not-a-real-one"
    OCT, NOV = "October 2026", "November 2026"

    store = {
        "tenants": [{"org_id": HOUSE}, {"org_id": LUXE}, {"org_id": VZONE}],
        # HOUSE has an October goal to carry and nothing in November.
        "targets": [{"org_id": HOUSE, "store_code": "B-117", "period": OCT,
                     "activations_monthly": 82, "upgrades_monthly": 45,
                     "accessories_monthly": 14000, "byod_pct": 35},
                    # LUXE already HAS a November goal, so it must be left alone.
                    {"org_id": LUXE, "store_code": "Grand", "period": NOV,
                     "activations_monthly": 10, "upgrades_monthly": 5,
                     "accessories_monthly": 100, "byod_pct": 35}],
        # VZONE declares auto_fill OFF. Its row ALSO claims the month was already filled, which the
        # guard must ignore in favour of reading the goals (the §19.18 constraint).
        "goal_rollforward_policy": [{"org_id": VZONE, "auto_fill": False,
                                     "last_filled_period": NOV}],
        "target_line": [],
    }
    fake = _RecClient(store)
    _harness_dbfree.install(fake)

    from app.modules.commcalc import router as R
    import app.core.run_secret as _rs

    R._storeops_roster = lambda c, o, **k: (
        [{"store_code": "B-117", "address": "1 A St", "market": "LI",
          "is_active": True, "monthly_target": 0}] if o == HOUSE else
        [{"store_code": "Grand", "address": "2 B St", "market": "CHI",
          "is_active": True, "monthly_target": 0}])
    R._fetch_actuals = lambda *_a, **_k: []
    R._byod_pct_default = lambda *_a, **_k: 35
    R._target_lines = lambda *_a, **_k: []
    R._target_catalog = lambda *_a, **_k: []
    R._require_target_edit = lambda *_a, **_k: None

    import app.modules.commcalc.targets_engine as TE
    _scope = TE.scope_achieved_mtd
    TE.scope_achieved_mtd = lambda actuals, code, rep, today: (
        {"activations": 60, "upgrades": 52, "accessories": 100.0} if code == "B-117" else {})
    _secrets = _rs._configured_secrets
    _rs._configured_secrets = lambda: [SECRET]
    try:
        # J1 — FAIL CLOSED. The scheduler is the only legitimate caller; no secret, no run.
        for bad in ("", "wrong-secret"):
            try:
                R.roll_forward_targets_run_due(period=NOV, x_notify_secret=bad)
                FAILURES.append(f"J1 ran with secret {bad!r}")
            except HTTPException as e:
                eq("J1 unauthenticated caller is refused", e.status_code, 401)

        before_targets = len(fake.written_to("targets"))
        ok("J1b ...and wrote nothing while refused", before_targets == 0,
           f"{before_targets} goal rows written by an unauthenticated call")

        out = R.roll_forward_targets_run_due(period=NOV, x_notify_secret=SECRET)

        by_org = {r["org_id"]: r for r in out["results"]}
        eq("J2 every tenant is walked", out["tenants"], 3)

        # J3 — a tenant with NO policy row gets the HOUSE DEFAULT and is filled.
        eq("J3 undeclared tenant is filled", by_org[HOUSE]["outcome"], "filled")
        eq("J3b ...from the prior month", by_org[HOUSE]["prior_period"], OCT)

        # J4 — auto_fill off is honoured, and its lying status row changes nothing.
        eq("J4 auto_fill off is skipped", by_org[VZONE]["outcome"], "auto_fill_off")

        # J5 — a month that ALREADY has goals is never touched (no overwrite of hand-set goals).
        eq("J5 a filled month is left alone", by_org[LUXE]["outcome"], "already_has_goals")

        # J6 — THE RULE REACHED THE WRITTEN ROW. B-117 achieved 52 upgrades against a goal of 45, so
        # the written November goal must be 57 (1.1 x 52), not 50 (1.1 x 45) and not 45.
        rows = [r for r in fake.written_to("targets") if r.get("store_code") == "B-117"]
        ok("J6 a goal row was written", len(rows) == 1, f"{len(rows)} rows")
        if rows:
            eq("J6b written goal stretches off achieved", rows[0]["upgrades_monthly"], 57)
            eq("J6c written period", rows[0]["period"], NOV)
            eq("J6d missed category carried", rows[0]["activations_monthly"], 82)
            eq("J6e written for the right tenant", rows[0]["org_id"], HOUSE)
        # ...and NOTHING was written for the tenants that were skipped.
        ok("J7 no goal written for a skipped tenant",
           not [r for r in fake.written_to("targets")
                if r.get("org_id") in (VZONE,) or r.get("store_code") == "Grand"],
           "a skipped tenant's month was written anyway")

        # J8 — the endpoint takes NO org_id from the caller. There is no parameter to point at
        # another tenant: it walks the roster itself. This is the cross-tenant surface, so the
        # absence of that argument is the proof, not a comment about it.
        args = R.roll_forward_targets_run_due.__code__.co_varnames[
            :R.roll_forward_targets_run_due.__code__.co_argcount]
        ok("J8 run-due takes no org_id argument", "org_id" not in args, f"args={args}")

        # J9 — THE GUARD READS THE GOALS, NOT THE STATUS ROW. House's status row now claims November
        # was filled; with its November goals REMOVED, a second run must fill it again rather than
        # trusting last_filled_period.
        store["targets"] = [r for r in store["targets"]
                            if not (r.get("org_id") == HOUSE and r.get("period") == NOV)]
        out2 = R.roll_forward_targets_run_due(period=NOV, x_notify_secret=SECRET)
        eq("J9 a status row claiming 'filled' does not skip an empty month",
           {r["org_id"]: r["outcome"] for r in out2["results"]}[HOUSE], "filled")

        # J10 — A READ MAY DEGRADE, A WRITE MAY NOT. An UNREADABLE policy table must not be treated
        # as "no declaration", because the house default is auto_fill ON and this tenant may have
        # turned it off: applying the default would write goals against a stated declaration. The
        # tick is daily, so skipping costs at most a day and self-heals.
        store["targets"] = [r for r in store["targets"]
                            if not (r.get("org_id") == HOUSE and r.get("period") == NOV)]
        _rows = R._rollforward_policy_rows
        R._rollforward_policy_rows = lambda *_a, **_k: None        # the read FAILED
        try:
            before = len(fake.written_to("targets"))
            out3 = R.roll_forward_targets_run_due(period=NOV, x_notify_secret=SECRET)
            outcomes = {r["org_id"]: r["outcome"] for r in out3["results"]}
            eq("J10 an unreadable policy is skipped, not defaulted",
               outcomes[HOUSE], "policy_unreadable")
            eq("J10b ...and so is every other tenant", outcomes[VZONE], "policy_unreadable")
            eq("J10c ...and nothing was written", len(fake.written_to("targets")), before)
            eq("J10d ...and nothing is reported as filled", out3["filled"], 0)
        finally:
            R._rollforward_policy_rows = _rows
        # ...while the DISPLAY path still renders the house rule on the same failure, because a seed
        # preview must show something. The two callers differ on purpose.
        eq("J10e an unreadable policy still yields a policy for display",
           GR.policy_for([], HOUSE)["stretch_basis"], "achieved")
    finally:
        TE.scope_achieved_mtd = _scope
        _rs._configured_secrets = _secrets


# ── §K — THE SCREEN DOES NOT SPELL THE RATE EITHER ────────────────────────────────────────────────
def section_k():
    """The frontend half of the same one-home rule.

    `targets/settings/page.tsx` said "+10% (stretch)" in two places and "+10% where last month's
    target was hit" in a comment. That is a SECOND COPY of a rate that is now declared per tenant —
    and after 2026-11-25 it also described the WRONG BASIS, telling a manager the stretch is on the
    goal when it is on what the store achieved. A tenant declaring 5% would have read "+10%" on its
    own goals screen. Fixing the backend and leaving the screen saying 10% is the patchwork the
    house rules forbid, so the rule is SENT and the page renders what it is told."""
    page = os.path.join(os.path.dirname(HERE), "frontend", "src", "app", "(platform)",
                        "commcalc", "targets", "settings", "page.tsx")
    ok("K1 the targets settings page exists", os.path.exists(page), page)
    if not os.path.exists(page):
        return
    with open(page, "r", encoding="utf-8") as fh:
        src = fh.read()
    # Strip // and /* */ comments: the header explaining the deleted literal must stay allowed,
    # exactly as §H allows the router's header to cite it.
    body = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    body = "\n".join(ln for ln in body.split("\n") if not ln.strip().startswith("//"))
    ok("K2 no hard-coded stretch rate in the page's own code",
       "+10%" not in body and "10% (stretch)" not in body,
       "the screen is spelling the stretch rate again instead of rendering what the server sends")
    ok("K3 the page reads the rule the server sends", "rollforward_policy" in body,
       "the page does not read the declared policy")
    ok("K4 ...and derives the percentage from the factor", "stretch_factor" in body)
    ok("K5 ...and does not invent a rate when the server has not said",
       "stretchPct" in body and "null" in body)

    # And the server must actually SEND it, or K3 is rendering undefined.
    router = os.path.join(HERE, "app", "modules", "commcalc", "router.py")
    rsrc = open(router, "r", encoding="utf-8").read()
    i = rsrc.find("def get_targets")
    j = rsrc.find("\n@router", i)
    gbody = rsrc[i:j] if (i >= 0 and j > i) else ""
    ok("K6 get_targets is found and bounded", 0 < len(gbody) < 12000, f"{len(gbody)} chars")
    ok("K7 GET /targets/{period} sends the rule in force",
       "'rollforward_policy'" in gbody,
       "the page is told nothing, so it would render an empty rate")
    # THE SEED MUST NOT RESURRECT A ZERO. A declared category with no prior goal comes back None,
    # and safe_float(None) is 0.0 — which would render as a declared goal of ZERO that no human set
    # and persist on a first save. This was a real defect found reviewing the seed path.
    ok("K8 the seed skips a declared category with no goal to roll",
       "if v is None" in gbody,
       "safe_float(None) would turn 'no goal' back into a goal of zero on the seed path")


def main():
    for fn in (section_a, section_b, section_c, section_d, section_e,
               section_f, section_g, section_h, section_i, section_j, section_k):
        try:
            fn()
        except Exception as exc:                                   # a section must never abort the run
            FAILURES.append(f"{fn.__name__} raised {type(exc).__name__}: {exc}")
    print(f"harness_goal_rollforward: {CHECKS[0]} checks")
    if FAILURES:
        print(f"\nFAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("all proofs hold")


if __name__ == "__main__":
    main()
