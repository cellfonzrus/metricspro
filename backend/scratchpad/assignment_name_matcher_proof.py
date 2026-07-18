"""Proof for agent/commission/assignment-name-matcher (owner-approved 2026-07-18, money-touching).

CHANGE: commission_engine._resolve_plan_for EMPLOYEE-scope matching is now name-ORDER-insensitive via a
new pure fn `_canon_person`. b2bsoft emits "Last, First" (POS salesperson); assignments are entered
"First Last" — both now canonicalize to the same string and resolve to the same plan. luxelink's 32
employee assignments that matched ZERO reps ("Antunez, Diana" vs "Diana Antunez") now match.

Canonicalization (VERBATIM): casefold · trim · collapse internal whitespace · if EXACTLY ONE comma ->
reorder "Last, First" -> "First Last" (multi-token surname order preserved); >1 comma or empty side ->
folded string unchanged. NO fuzzy / token-drop / middle-strip.

STORE / MARKET / DEFAULT matching is UNCHANGED (byte-identical). This harness drives the REAL
_resolve_plan_for + the REAL preview() over an in-memory FakeClient and compares winners against a
VENDORED COPY of the pre-change _resolve_plan_for.

Run:  cd backend && python3 scratchpad/assignment_name_matcher_proof.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app.modules.commcalc.commission_engine as CE
from app.modules.commcalc.commission_engine import _resolve_plan_for, _canon_person, preview

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name}   {extra}")


# ── VENDORED pre-change _resolve_plan_for (verbatim from origin/main) ────────────────────────────
def OLD_resolve_plan_for(rep_name, store, market, plans):
    SCOPE_RANK = {"employee": 3, "store": 2, "market": 1, "default": 0}
    rn = (rep_name or "").strip().lower()
    sv_store = (store or "").strip().lower()
    sv_mkt = (market or "").strip().lower()
    best, best_key = None, (-1, -1)
    for p in plans:
        if not p.get("is_active", True):
            continue
        for a in p.get("assignments", []):
            scope = (a.get("scope") or "default").strip().lower()
            val = (a.get("scope_value") or "").strip().lower()
            ok = ((scope == "default") or
                  (scope == "employee" and val and val == rn) or
                  (scope == "store" and val and val == sv_store) or
                  (scope == "market" and val and val == sv_mkt))
            if not ok:
                continue
            key = (SCOPE_RANK.get(scope, 0), int(a.get("priority") or 0))
            if key > best_key:
                best, best_key = p, key
    return best


def _win(fn, rep, store, market, plans):
    p = fn(rep, store, market, plans)
    return None if p is None else p.get("id")


def _win_role(fn, rep, store, market, plans, rep_role):
    p = fn(rep, store, market, plans, rep_role=rep_role)
    return None if p is None else p.get("id")


def mkplan(pid, name, scope, val, priority=0, active=True, rules=None, tiers=None):
    return {"id": pid, "name": name, "is_active": active,
            "rules": rules or [], "tiers": tiers or [],
            "assignments": [{"scope": scope, "scope_value": val, "priority": priority}]}


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 1. _canon_person unit behavior (the canonicalization rules verbatim)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 1. _canon_person unit ==")
check("First Last folds plain", _canon_person("Diana Antunez") == "diana antunez")
check("Last, First reorders", _canon_person("Antunez, Diana") == "diana antunez")
check("both formats -> same canon", _canon_person("Antunez, Diana") == _canon_person("Diana Antunez"))
check("multi-token surname 'Islam Khan, Ariful'",
      _canon_person("Islam Khan, Ariful") == "ariful islam khan")
check("multi-token surname round-trips to First Last form",
      _canon_person("Islam Khan, Ariful") == _canon_person("Ariful Islam Khan"))
check("casefold applied", _canon_person("DIANA ANTUNEZ") == "diana antunez")
check("internal whitespace collapsed", _canon_person("Diana   Antunez") == "diana antunez")
check("trim applied", _canon_person("  Antunez,  Diana  ") == "diana antunez")
check(">1 comma -> folded string, no reorder",
      _canon_person("Smith, John, Jr") == "smith, john, jr")
check("empty side (trailing comma) -> folded string", _canon_person("Smith,") == "smith,")
check("empty side (leading comma) -> folded string", _canon_person(", John") == ", john")
check("None -> empty", _canon_person(None) == "")
check("empty -> empty", _canon_person("   ") == "")
# by DESIGN: not fuzzy — extra middle token / spelling stay DISTINCT
check("middle token stays distinct", _canon_person("natasha cabrera") != _canon_person("natasha nicole cabrera"))
check("spelling variant stays distinct", _canon_person("jon smith") != _canon_person("john smith"))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 2. Last,First <-> First Last resolves BOTH directions through REAL _resolve_plan_for
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 2. employee-scope both directions ==")
# direction A: assignment "First Last", POS rep "Last, First"
plansA = [mkplan("PA", "Total Plan", "employee", "Diana Antunez")]
check("assign 'Diana Antunez' matches POS 'Antunez, Diana'",
      _win(_resolve_plan_for, "Antunez, Diana", "", "", plansA) == "PA")
check("  (OLD impl left this DEAD — no match)",
      _win(OLD_resolve_plan_for, "Antunez, Diana", "", "", plansA) is None)
# direction B: assignment "Last, First", rep "First Last"
plansB = [mkplan("PB", "Total Plan", "employee", "Antunez, Diana")]
check("assign 'Antunez, Diana' matches rep 'Diana Antunez'",
      _win(_resolve_plan_for, "Diana Antunez", "", "", plansB) == "PB")
# multi-token surname
plansC = [mkplan("PC", "Plan C", "employee", "Ariful Islam Khan")]
check("multi-token surname resolves ('Islam Khan, Ariful' -> assign 'Ariful Islam Khan')",
      _win(_resolve_plan_for, "Islam Khan, Ariful", "", "", plansC) == "PC")
plansD = [mkplan("PD", "Plan D", "employee", "Islam Khan, Ariful")]
check("multi-token surname resolves reverse (assign 'Islam Khan, Ariful' -> rep 'Ariful Islam Khan')",
      _win(_resolve_plan_for, "Ariful Islam Khan", "", "", plansD) == "PD")
# case + whitespace robustness
check("case/whitespace robust ('  antunez ,   diana ' -> assign 'Diana Antunez')",
      _win(_resolve_plan_for, "  antunez ,   diana ", "", "", plansA) == "PA")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 3. No-match stays no-match (extra middle token, different spelling) — BY DESIGN
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 3. no-match stays no-match (by design) ==")
plansMid = [mkplan("PM", "Plan M", "employee", "Natasha Cabrera")]
check("extra middle token does NOT match (assign 'Natasha Cabrera' vs rep 'Cabrera, Natasha Nicole')",
      _win(_resolve_plan_for, "Cabrera, Natasha Nicole", "", "", plansMid) is None)
check("extra middle token does NOT match (First-Last form)",
      _win(_resolve_plan_for, "Natasha Nicole Cabrera", "", "", plansMid) is None)
plansSp = [mkplan("PS", "Plan S", "employee", "John Smith")]
check("spelling variant does NOT match ('Jon Smith')",
      _win(_resolve_plan_for, "Jon Smith", "", "", plansSp) is None)
check("empty rep -> no employee match", _win(_resolve_plan_for, "", "", "", plansA) is None)
check("empty scope_value -> no match",
      _win(_resolve_plan_for, "Diana Antunez", "", "", [mkplan("PE", "Empty", "employee", "")]) is None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 4. COMMA-FREE byte-identity vs OLD impl — house-shaped input matrix (case/whitespace variants)
#    (house data has NO commas -> canon reduces to casefold/trim == old .strip().lower())
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 4. comma-free byte-identity vs OLD (house-shaped) ==")
# realistic house roster, comma-free, ASCII, single internal spaces
HOUSE_ASSIGNS = ["John Smith", "Maria Garcia", "David Kim", "Ana De La Cruz", "Robert Brown Jr"]
house_plans = [mkplan(f"H{i}", f"Plan {chr(65+i)}", "employee", nm, priority=i)
               for i, nm in enumerate(HOUSE_ASSIGNS)]
# the reps the calc will feed in — exact, cased, padded, double-spaced house variants (comma-free)
HOUSE_REPS = [
    "John Smith", "john smith", "JOHN SMITH", "  John Smith  ",
    "Maria Garcia", "MARIA garcia", "David Kim", "david kim",
    "Ana De La Cruz", "ANA DE LA CRUZ", "Robert Brown Jr", "robert brown jr",
    "Unknown Person", "", "Smith John",  # last two/three: no-match cases
]
mism = []
for rep in HOUSE_REPS:
    for store in ("", "Store 5", "STORE 5"):
        for mkt in ("", "North", "SOUTH"):
            new_w = _win(_resolve_plan_for, rep, store, mkt, house_plans)
            old_w = _win(OLD_resolve_plan_for, rep, store, mkt, house_plans)
            if new_w != old_w:
                mism.append((rep, store, mkt, new_w, old_w))
check(f"comma-free winner byte-identical across {len(HOUSE_REPS)*9} (rep x store x market) cells",
      not mism, f"mismatches={mism[:5]}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 5. STORE / MARKET / DEFAULT scopes byte-identical (full matrix, incl. comma edge cases)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 5. store/market/default byte-identical ==")
mixed_plans = [
    mkplan("D0", "Default Plan", "default", None, priority=0),
    mkplan("MK", "Market Plan", "market", "North", priority=1),
    mkplan("ST", "Store Plan", "store", "Store 5", priority=1),
    mkplan("EM", "Emp Plan", "employee", "Diana Antunez", priority=5),
]
sm_mism = []
for rep in ("Antunez, Diana", "Diana Antunez", "Nobody", ""):
    for store in ("", "Store 5", "store 5", "STORE 5", "Store, 5"):
        for mkt in ("", "North", "north", "NORTH", "North, East"):
            new_w = _win(_resolve_plan_for, rep, store, mkt, mixed_plans)
            old_w = _win(OLD_resolve_plan_for, rep, store, mkt, mixed_plans)
            # employee scope is INTENTIONALLY divergent (that's the fix); compare only when the winner
            # is NOT the employee plan in EITHER impl -> store/market/default must be identical.
            if new_w == "EM" or old_w == "EM":
                continue
            if new_w != old_w:
                sm_mism.append((rep, store, mkt, new_w, old_w))
check("store/market/default winner byte-identical (non-employee cells)",
      not sm_mism, f"mismatches={sm_mism[:5]}")
# explicit: a store scope_value that contains a comma is NOT reordered (store matching untouched)
check("store scope_value with comma matches literally (no reorder)",
      _win(_resolve_plan_for, "x", "Store, 5", "", [mkplan("SC", "SC", "store", "Store, 5")]) == "SC")
check("default always attaches regardless of rep name-order",
      _win(_resolve_plan_for, "Antunez, Diana", "", "", [mkplan("DF", "DF", "default", None)]) == "DF")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 6. COLLISION tie-break deterministic (two DIFFERENT assignments canon'ing identically)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 6. collision tie-break deterministic ==")
# same rep, two plans, both employee-scope, canon-identical assignments in the two entry formats.
def collide_plans(prio_alpha, prio_beta):
    return [
        {"id": "ALPHA", "name": "Alpha Plan", "is_active": True, "rules": [], "tiers": [],
         "assignments": [{"scope": "employee", "scope_value": "Diana Antunez", "priority": prio_alpha}]},
        {"id": "BETA", "name": "Beta Plan", "is_active": True, "rules": [], "tiers": [],
         "assignments": [{"scope": "employee", "scope_value": "Antunez, Diana", "priority": prio_beta}]},
    ]
# 6a: different priority -> higher priority wins regardless of order
cp = collide_plans(prio_alpha=1, prio_beta=9)
check("higher-priority assignment wins the collision (BETA prio 9)",
      _win(_resolve_plan_for, "Antunez, Diana", "", "", cp) == "BETA")
cp2 = collide_plans(prio_alpha=9, prio_beta=1)
check("higher-priority assignment wins the collision (ALPHA prio 9)",
      _win(_resolve_plan_for, "Antunez, Diana", "", "", cp2) == "ALPHA")
# 6b: EQUAL priority -> first plan in iteration order (name-sorted by _load_plans) wins, DETERMINISTIC
cp_eq = collide_plans(prio_alpha=3, prio_beta=3)
winners = {_win(_resolve_plan_for, "Antunez, Diana", "", "", cp_eq) for _ in range(50)}
check("equal-priority collision resolves DETERMINISTICALLY (same winner over 50 runs)",
      len(winners) == 1, f"winners={winners}")
check("equal-priority collision -> first-in-list (ALPHA, name-first) wins",
      winners == {"ALPHA"}, f"winners={winners}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# In-memory FakeClient (order-aware) to drive the REAL preview() + _load_plans end-to-end
# ═══════════════════════════════════════════════════════════════════════════════════════════════
class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, table):
        self.store, self.t, self.f = store, table, []
        self.rng, self.ordk, self.orddesc = None, None, False

    def select(self, *a, **k):
        return self

    def eq(self, c, v):
        self.f.append(("eq", c, v)); return self

    def neq(self, c, v):
        self.f.append(("neq", c, v)); return self

    def in_(self, c, v):
        self.f.append(("in", c, list(v))); return self

    def order(self, col, desc=False, **k):
        self.ordk, self.orddesc = col, bool(desc); return self

    def limit(self, n):
        return self

    def range(self, a, b):
        self.rng = (a, b); return self

    def _m(self, r):
        for k, c, v in self.f:
            rv = r.get(c)
            if k == "eq" and rv != v:
                return False
            if k == "neq" and rv == v:
                return False
            if k == "in" and rv not in v:
                return False
        return True

    def execute(self):
        rows = [dict(r) for r in self.store.get(self.t, []) if self._m(r)]
        if self.ordk:
            rows.sort(key=lambda r: (r.get(self.ordk) is None, str(r.get(self.ordk))), reverse=self.orddesc)
        if self.rng:
            a, b = self.rng
            rows = rows[a:b + 1]
        return FakeResult(rows)


class FakeSchema:
    def __init__(self, store):
        self.store = store

    def table(self, t):
        return FakeQuery(self.store, t)


class FakeClient:
    def __init__(self, store):
        self.store = store

    def schema(self, s):
        return FakeClient._Sch(self.store)

    class _Sch:
        def __init__(self, store):
            self.store = store

        def table(self, t):
            return FakeQuery(self.store, t)


PERIOD = "2026-07"


def sale(org, rep, tid, store="Store 5", period=PERIOD, ext=100.0, gp=20.0):
    return {"org_id": org, "period": period, "trans_id": tid, "store": store, "salesperson": rep,
            "category": "", "department": "", "contract_type": "New", "product_desc": "",
            "ext_price": ext, "gp": gp, "voided": "", "trans_type": ""}


# a $10-flat-per-line plan assigned to "Diana Antunez"; POS feeds salesperson "Antunez, Diana"
FLAT_RULE = {"id": "R1", "org_id": "lux", "plan_id": "LP", "match_field": "any", "match_op": "equals",
             "match_value": "", "qualifies": True, "payout_kind": "flat_per_unit",
             "amount": 10.0, "pct": 0, "tiered": False, "sort": 0}

STORE = {
    "commission_plan": [
        {"id": "LP", "org_id": "lux", "name": "Total Plan", "is_active": True,
         "carrier_id": None, "base_tier_metric": "none"},
        # a DIFFERENT org's plan with a colliding assignment — must NOT leak into lux
        {"id": "BP", "org_id": "boost", "name": "Boost Plan", "is_active": True,
         "carrier_id": None, "base_tier_metric": "none"},
    ],
    "commission_rule": [
        FLAT_RULE,
        {**FLAT_RULE, "id": "R2", "org_id": "boost", "plan_id": "BP", "amount": 999.0},
    ],
    "commission_tier": [],
    "commission_plan_assignment": [
        {"id": "A1", "org_id": "lux", "plan_id": "LP", "scope": "employee",
         "scope_value": "Diana Antunez", "priority": 0},
        {"id": "A2", "org_id": "boost", "plan_id": "BP", "scope": "employee",
         "scope_value": "Antunez, Diana", "priority": 0},
    ],
    "raw_sales": [
        sale("lux", "Antunez, Diana", "T1"),
        sale("lux", "Antunez, Diana", "T2"),
        sale("lux", "Antunez, Diana", "T3"),
        # a boost-org sale for the same-named rep — isolation guard
        sale("boost", "Antunez, Diana", "B1"),
    ],
    "raw_mi": [],
    "raw_catalog": [],
    "store_mapping": [],
    "daily_sales_feed": [],
}

print("\n== 7. REAL preview() end-to-end (Last,First POS salesperson gets paid) ==")
client = FakeClient(STORE)
pv = preview(client, "lux", PERIOD)
rows = pv.get("by_rep") or []
check("preview ready", pv.get("ready") is True)
check("preview returns exactly one lux rep", len(rows) == 1, f"rows={rows}")
if rows:
    r0 = rows[0]
    check("paid rep is the POS 'Antunez, Diana'", r0.get("rep") == "Antunez, Diana")
    check("resolved plan = 'Total Plan'", r0.get("plan_name") == "Total Plan", f"plan={r0.get('plan_name')}")
    check("total_payout = 3 lines x $10 = $30", abs((r0.get("total_payout") or 0) - 30.0) < 1e-9,
          f"payout={r0.get('total_payout')}")
check("preview total payout = $30", abs((pv.get("totals", {}).get("payout") or 0) - 30.0) < 1e-9)

print("\n== 8. org isolation unchanged ==")
# lux preview must NOT include boost's $999 plan; boost preview stays in boost
check("lux preview total is $30 (NOT $999 boost plan)",
      abs((pv.get("totals", {}).get("payout") or 0) - 30.0) < 1e-9)
pv_boost = preview(client, "boost", PERIOD)
brows = pv_boost.get("by_rep") or []
check("boost preview resolves ITS own plan for the same-named rep",
      len(brows) == 1 and brows[0].get("plan_name") == "Boost Plan", f"brows={brows}")
check("boost rep paid from boost plan ($999)",
      brows and abs((brows[0].get("total_payout") or 0) - 999.0) < 1e-9)
# cross-org load isolation
lux_plans, _ = CE._load_plans(client, "lux")
check("_load_plans('lux') returns ONLY lux plans", {p["id"] for p in lux_plans} == {"LP"},
      f"ids={[p['id'] for p in lux_plans]}")


# ── OLD-vs-NEW money delta for the luxelink case (the whole point) ────────────────────────────────
print("\n== 9. money-impact (OLD would pay $0, NEW pays $30) ==")
old_win = _win(OLD_resolve_plan_for, "Antunez, Diana", "Store 5", "",
               [{"id": "LP", "name": "Total Plan", "is_active": True, "rules": [], "tiers": [],
                 "assignments": [{"scope": "employee", "scope_value": "Diana Antunez", "priority": 0}]}])
check("OLD impl: 'Antunez, Diana' resolved NO plan -> $0 (the bug)", old_win is None)
new_win = _win(_resolve_plan_for, "Antunez, Diana", "Store 5", "",
               [{"id": "LP", "name": "Total Plan", "is_active": True, "rules": [], "tiers": [],
                 "assignments": [{"scope": "employee", "scope_value": "Diana Antunez", "priority": 0}]}])
check("NEW impl: resolves Total Plan -> pay appears", new_win == "LP")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 10. SCOPE_RANK renumber (employee>role>store>market>default) is BYTE-IDENTICAL when NO role
#     assignments exist — even with a rep_role PRESENT (the critical house-safety case).
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 10. renumber byte-identity: rep_role INERT without role assignments ==")
rr_mism = []
for rep in HOUSE_REPS:
    for store in ("", "Store 5", "STORE 5"):
        for mkt in ("", "North", "SOUTH"):
            for role in (None, "Sales Rep", "Store Manager", "DM"):
                new_w = _win_role(_resolve_plan_for, rep, store, mkt, house_plans, role)
                old_w = _win(OLD_resolve_plan_for, rep, store, mkt, house_plans)  # OLD has no role concept
                if new_w != old_w:
                    rr_mism.append((rep, store, mkt, role, new_w, old_w))
check(f"winner byte-identical across {len(HOUSE_REPS)*3*3*4} cells (rep x store x mkt x role) w/ NO role assignments",
      not rr_mism, f"mismatches={rr_mism[:5]}")
# same for the mixed store/market/default/employee plan set (still no role scope)
mixed_rr_mism = []
for rep in ("Antunez, Diana", "Diana Antunez", "Nobody", ""):
    for store in ("", "Store 5", "STORE 5"):
        for mkt in ("", "North", "NORTH"):
            for role in (None, "Sales Rep", "DM"):
                new_w = _win_role(_resolve_plan_for, rep, store, mkt, mixed_plans, role)
                old_w = _win(OLD_resolve_plan_for, rep, store, mkt, mixed_plans)
                if new_w == "EM" or old_w == "EM":
                    continue  # employee scope intentionally divergent (the comma fix)
                if new_w != old_w:
                    mixed_rr_mism.append((rep, store, mkt, role, new_w, old_w))
check("mixed-scope winner byte-identical with rep_role present & no role assignments (non-employee cells)",
      not mixed_rr_mism, f"mismatches={mixed_rr_mism[:5]}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 11. ROLE scope matching + specificity hierarchy (employee > role > store > market > default)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 11. role-scope matching + hierarchy ==")
role_plan = [mkplan("RP", "Role Plan", "role", "Sales Rep", priority=0)]
check("rep with role 'Sales Rep' matched by role-'Sales Rep' assignment",
      _win_role(_resolve_plan_for, "Antunez, Diana", "", "", role_plan, "Sales Rep") == "RP")
check("role match is case-insensitive ('sales rep' rep role)",
      _win_role(_resolve_plan_for, "x", "", "", role_plan, "sales rep") == "RP")
check("rep with DIFFERENT role does NOT match the role assignment",
      _win_role(_resolve_plan_for, "x", "", "", role_plan, "Store Manager") is None)
check("rep with NO role (None) can't match role scope, no raise",
      _win_role(_resolve_plan_for, "x", "", "", role_plan, None) is None)
check("rep with blank role ('') can't match role scope",
      _win_role(_resolve_plan_for, "x", "", "", role_plan, "  ") is None)

# employee OVERRIDES role (employee rank 4 > role rank 3) — even when role has higher priority
emp_vs_role = [
    mkplan("R", "Role Plan", "role", "Sales Rep", priority=9),
    mkplan("E", "Emp Plan", "employee", "Diana Antunez", priority=0),
]
check("EMPLOYEE assignment beats ROLE assignment for the same rep (even role prio 9)",
      _win_role(_resolve_plan_for, "Antunez, Diana", "", "", emp_vs_role, "Sales Rep") == "E")

# role BEATS store / market / default
role_vs_lower = [
    mkplan("D", "Default", "default", None, priority=9),
    mkplan("M", "Market", "market", "North", priority=9),
    mkplan("S", "Store", "store", "Store 5", priority=9),
    mkplan("R", "Role", "role", "Sales Rep", priority=0),
]
check("ROLE beats STORE/MARKET/DEFAULT (rep in Store 5 / North w/ role Sales Rep)",
      _win_role(_resolve_plan_for, "x", "Store 5", "North", role_vs_lower, "Sales Rep") == "R")
check("with NO role, same rep falls through to STORE (role can't match)",
      _win_role(_resolve_plan_for, "x", "Store 5", "North", role_vs_lower, None) == "S")

# role priority tie-break within scope unchanged
role_tie = [
    mkplan("RA", "Role A", "role", "Sales Rep", priority=1),
    mkplan("RB", "Role B", "role", "Sales Rep", priority=5),
]
check("role-scope priority tie-break: higher priority wins",
      _win_role(_resolve_plan_for, "x", "", "", role_tie, "Sales Rep") == "RB")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 12. _read_employee_roles canon bridge + REAL preview() role-scope end-to-end
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 12. _read_employee_roles bridge + preview() role end-to-end ==")
ROLE_STORE = {
    "commission_plan": [
        {"id": "RLP", "org_id": "lux", "name": "Role Plan", "is_active": True,
         "carrier_id": None, "base_tier_metric": "none"},
    ],
    "commission_rule": [
        {"id": "RR1", "org_id": "lux", "plan_id": "RLP", "match_field": "any", "match_op": "equals",
         "match_value": "", "qualifies": True, "payout_kind": "flat_per_unit", "amount": 5.0,
         "pct": 0, "tiered": False, "sort": 0},
    ],
    "commission_tier": [],
    "commission_plan_assignment": [
        # ROLE-scope assignment (no per-employee rows at all)
        {"id": "RA1", "org_id": "lux", "plan_id": "RLP", "scope": "role",
         "scope_value": "Sales Rep", "priority": 0},
    ],
    "employees": [   # storeops roster: name in "First Last" roster form, POS emits "Last, First"
        {"org_id": "lux", "name": "Diana Antunez", "role": "Sales Rep", "is_active": True},
        {"org_id": "lux", "name": "Bob Vance", "role": "Store Manager", "is_active": True},
        {"org_id": "boost", "name": "Diana Antunez", "role": "Sales Rep", "is_active": True},  # other org
    ],
    "raw_sales": [
        sale("lux", "Antunez, Diana", "T1"),   # Sales Rep -> covered by role plan
        sale("lux", "Antunez, Diana", "T2"),
        sale("lux", "Vance, Bob", "T3"),       # Store Manager -> NOT covered by 'Sales Rep' role plan
    ],
    "raw_mi": [], "raw_catalog": [], "store_mapping": [], "daily_sales_feed": [],
}
rclient = FakeClient(ROLE_STORE)
role_map = CE._read_employee_roles(rclient, "lux")
check("_read_employee_roles keys by _canon_person(name)", role_map.get("diana antunez") == "sales rep")
check("_read_employee_roles is org-scoped (only lux roster)",
      set(role_map.keys()) == {"diana antunez", "bob vance"}, f"keys={role_map}")
check("canon bridge: POS 'Antunez, Diana' resolves to roster role via _canon_person",
      role_map.get(_canon_person("Antunez, Diana")) == "sales rep")

rpv = preview(rclient, "lux", PERIOD)
rrows = {r["rep"]: r for r in (rpv.get("by_rep") or [])}
check("preview: Sales-Rep-roled 'Antunez, Diana' PAID via role plan ($5 x 2 = $10)",
      "Antunez, Diana" in rrows and abs(rrows["Antunez, Diana"]["total_payout"] - 10.0) < 1e-9,
      f"rrows={list(rrows)}")
check("preview: role plan name attached", rrows.get("Antunez, Diana", {}).get("plan_name") == "Role Plan")
check("preview: Store-Manager-roled 'Vance, Bob' NOT paid by the Sales-Rep role plan",
      "Vance, Bob" not in rrows, f"rrows={list(rrows)}")
# graceful when storeops unreachable: role plan simply pays no one, no raise
class Boom:
    def schema(self, s):
        class _S:
            def table(self, t):
                if t == "employees":
                    class _Q:
                        def select(self, *a, **k): return self
                        def eq(self, *a, **k): return self
                        def execute(self): raise RuntimeError("storeops down")
                    return _Q()
                return FakeQuery(ROLE_STORE, t)
        return _S()
check("_read_employee_roles degrades to {} when storeops read raises",
      CE._read_employee_roles(Boom(), "lux") == {})


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 13. F1 — same-canon roster collision resolves DETERMINISTICALLY (lowest id) + diagnose flags it
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 13. F1: roster canon-name collision -> deterministic lowest-id winner + diagnose flag ==")
check("two roster rows canon to the same name",
      _canon_person("Luis Martinez") == _canon_person("Martinez, Luis") == "luis martinez")


def collide_roster(order):
    emps = [
        {"id": 2, "org_id": "lux", "name": "Luis Martinez", "role": "Store Manager"},
        {"id": 7, "org_id": "lux", "name": "Martinez, Luis", "role": "Sales Rep"},
    ]
    return FakeClient({"employees": [emps[i] for i in order]})


winners = set()
for order in ([0, 1], [1, 0]):   # shuffle the fake-DB heap order
    winners.add(CE._read_employee_roles(collide_roster(order), "lux").get("luis martinez"))
check("winner DETERMINISTIC across shuffled DB order (lowest id=2 -> 'store manager')",
      winners == {"store manager"}, f"winners={winners}")
cols = CE._role_name_collisions(collide_roster([1, 0]), "lux")
check("_role_name_collisions reports the collision", len(cols) == 1 and cols[0]["canon"] == "luis martinez")
check("collision winner_role = lowest-id row's role ('Store Manager')", cols[0]["winner_role"] == "Store Manager")
check("collision flagged role_conflict (two DIFFERENT roles)", cols[0]["role_conflict"] is True)
check("no collision reported when canon-names are distinct",
      CE._role_name_collisions(FakeClient({"employees": [
          {"id": 1, "org_id": "lux", "name": "Ann Lee", "role": "Rep"},
          {"id": 2, "org_id": "lux", "name": "Bob Fox", "role": "Rep"}]}), "lux") == [])
check("same name SAME role -> collision reported but NOT a role_conflict",
      (lambda c: len(c) == 1 and c[0]["role_conflict"] is False)(
          CE._role_name_collisions(FakeClient({"employees": [
              {"id": 1, "org_id": "lux", "name": "Sam Roe", "role": "Rep"},
              {"id": 2, "org_id": "lux", "name": "Roe, Sam", "role": "Rep"}]}), "lux")))


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 14. F2 — INACTIVE rep's role still matches (engine ignores is_active) + UI count-consistency
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 14. F2: inactive rep still paid by role + UI count exposes active/inactive ==")
INACT_STORE = {
    "commission_plan": [{"id": "IP", "org_id": "lux", "name": "Role Plan", "is_active": True,
                         "carrier_id": None, "base_tier_metric": "none"}],
    "commission_rule": [{"id": "IR", "org_id": "lux", "plan_id": "IP", "match_field": "any",
                         "match_op": "equals", "match_value": "", "qualifies": True,
                         "payout_kind": "flat_per_unit", "amount": 7.0, "pct": 0, "tiered": False, "sort": 0}],
    "commission_tier": [],
    "commission_plan_assignment": [{"id": "IA", "org_id": "lux", "plan_id": "IP", "scope": "role",
                                    "scope_value": "Sales Rep", "priority": 0}],
    "employees": [
        {"id": 1, "org_id": "lux", "name": "Gone Guy", "role": "Sales Rep", "is_active": False},   # TERMINATED
        {"id": 2, "org_id": "lux", "name": "Here Gal", "role": "Sales Rep", "is_active": True},
    ],
    "raw_sales": [sale("lux", "Guy, Gone", "TG"), sale("lux", "Gal, Here", "TH")],
    "raw_mi": [], "raw_catalog": [], "store_mapping": [], "daily_sales_feed": [],
}
ic = FakeClient(INACT_STORE)
check("_read_employee_roles INCLUDES the inactive rep's role", CE._read_employee_roles(ic, "lux").get("gone guy") == "sales rep")
ipaid = {r["rep"]: r["total_payout"] for r in (preview(ic, "lux", PERIOD).get("by_rep") or [])}
check("INACTIVE terminated rep 'Guy, Gone' STILL paid via role plan ($7)",
      abs(ipaid.get("Guy, Gone", 0) - 7.0) < 1e-9, f"ipaid={ipaid}")
check("active rep also paid ($7)", abs(ipaid.get("Gal, Here", 0) - 7.0) < 1e-9)


def ui_role_stats(emps):   # mirrors commission-plans/page.tsx roleStats over the include_inactive roster
    m = {}
    for e in emps:
        r = (e.get("role") or "").strip()
        if not r:
            continue
        s = m.setdefault(r, {"active": 0, "inactive": 0})
        s["inactive" if e.get("is_active") is False else "active"] += 1
    return m


stats = ui_role_stats(INACT_STORE["employees"])
check("UI role-count exposes active+inactive split (1 active +1 inactive) == 2 engine-matched reps",
      stats["Sales Rep"] == {"active": 1, "inactive": 1}
      and (stats["Sales Rep"]["active"] + stats["Sales Rep"]["inactive"]) == len(ipaid), f"stats={stats}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════
# 15. F3 — employee assignment stores epay_salesperson || name (POS escape hatch beyond word-order)
# ═══════════════════════════════════════════════════════════════════════════════════════════════
print("\n== 15. F3: assignment stores epay_salesperson || name (rescues initials/nicknames) ==")


def ui_emp_option_id(e):   # mirrors commission-plans/page.tsx employeeOptions id derivation
    return (str(e.get("epay_salesperson") or "")) or (str(e.get("name") or ""))


check("option id = epay_salesperson when set",
      ui_emp_option_id({"name": "Diana Antunez", "epay_salesperson": "Antunez, D"}) == "Antunez, D")
check("option id falls back to name when epay blank",
      ui_emp_option_id({"name": "Diana Antunez", "epay_salesperson": ""}) == "Diana Antunez")
check("roster NAME alone would NOT canon-match the initial POS string (why epay matters)",
      _canon_person("Diana Antunez") != _canon_person("Antunez, D"))
EPAY_STORE = {
    "commission_plan": [{"id": "EP", "org_id": "lux", "name": "Emp Plan", "is_active": True,
                         "carrier_id": None, "base_tier_metric": "none"}],
    "commission_rule": [{"id": "ER", "org_id": "lux", "plan_id": "EP", "match_field": "any",
                         "match_op": "equals", "match_value": "", "qualifies": True,
                         "payout_kind": "flat_per_unit", "amount": 8.0, "pct": 0, "tiered": False, "sort": 0}],
    "commission_tier": [],
    # scope_value = the EPAY value the UI now stores (NOT the roster name)
    "commission_plan_assignment": [{"id": "EA", "org_id": "lux", "plan_id": "EP", "scope": "employee",
                                    "scope_value": "Antunez, D", "priority": 0}],
    "employees": [{"id": 1, "org_id": "lux", "name": "Diana Antunez", "epay_salesperson": "Antunez, D",
                   "role": "Sales Rep", "is_active": True}],
    "raw_sales": [sale("lux", "Antunez, D", "TE1"), sale("lux", "Antunez, D", "TE2")],
    "raw_mi": [], "raw_catalog": [], "store_mapping": [], "daily_sales_feed": [],
}
epaid = {r["rep"]: r["total_payout"] for r in (preview(FakeClient(EPAY_STORE), "lux", PERIOD).get("by_rep") or [])}
check("epay-valued assignment matches POS 'Antunez, D' ($8 x 2 = $16) where roster-name would be DEAD",
      abs(epaid.get("Antunez, D", 0) - 16.0) < 1e-9, f"epaid={epaid}")


print(f"\n{'='*60}\nPASS={PASS}  FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
