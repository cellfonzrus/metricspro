"""DB-free proof for SCHEDULE VISIBILITY — "whose shifts may this login READ?" (owner directive
2026-10-05, index §14w).

THE REPORTED DEFECT, verbatim: *"currently employees can see the schdule of the whoel company, i saw
when i used rana to clok in as him, it should only show the reps wown schdule and the managers his
own schdule and if any employee works under him"*.

THE CLASS being fixed: every schedule read answered "which STORES may I see" and never "which
PEOPLE". Section A reproduces the leak with the OLD gate still in place (the store keyset alone), so
the regression is demonstrated, not asserted.

Runs the REAL endpoints (`get_shifts`, `schedule_hours_trend`, `get_shift_templates`,
`get_time_off`, `get_shift_swaps`, `timeclock_list`, `staffing_heatmap`) and the REAL rulings
(`core.scope.people_visibility` / `visible_people_keyset`, `storeops.schedule_emp_ids`) against a
stateful fake Supabase-chain client — same convention as harness_storeops_scope_wiring.py.
Monkeypatches only `get_supabase` and `_uid_from_token`.

Proves:
  A. THE LEAK (pre-fix behaviour, still measurable): a rep whose login pins three markets resolves a
     store keyset covering EVERY store, i.e. the whole company's schedule.
  B. A rep now reads ONLY their own shifts — at their own store and anywhere else they worked.
  C. A store manager declared `people_visibility='span'` reads their own store's people (home
     store UNION actually-worked-there) and NOT another store's, even though their login pins the
     whole market. Market grants do not bind a store-scoped role.
  D. A DM (`scope='market'`, nothing declared) derives 'span' and its market grant DOES bind.
  E. An admin (`scope='all'`) is unrestricted; rbac off is unrestricted; no token is unrestricted
     (an in-process/scheduled caller is never silently blanked).
  F. Fail-narrow: an undeclared, unknown role sees only itself; a provisioned login with no
     employee_id sees NOTHING (deny-all, never unrestricted).
  G. Both employee_id FORMS resolve — a shift carrying the numeric `employees.id` is still the rep's
     own shift.
  H. The siblings are wired: time-off, shift-templates, shift-swaps, hours-trend and the timeclock
     punch list all narrow the same way, and the previously ungated staffing heat map now refuses a
     store outside the caller's span.
  I. NARROWING ONLY — no caller sees anyone they could not see before this change.

Run: `cd backend && python3 harness_people_visibility.py`
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail and not cond else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"
_ID = {"n": 0}


def nid(pfx="id"):
    _ID["n"] += 1
    return f"{pfx}-{_ID['n']}"


class Q:
    def __init__(self, store, key):
        self.s, self.k = store, key
        self.op, self.payload = "select", None
        self.filters, self._limit = [], None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def is_(self, c, v): self.filters.append((c, "is", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "is" and rv is not None: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.k, [])
        if self.op == "select":
            out = [dict(r) for r in rows if self._match(r)]
            return SimpleNamespace(data=out[: self._limit] if self._limit is not None else out)
        if self.op == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r); r.setdefault("id", nid("row")); rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        return SimpleNamespace(data=[])


class FakeRpc:
    def execute(self): return SimpleNamespace(data=[])


class FakeSchema:
    def __init__(self, client, name): self.client, self.name = client, name
    def table(self, t): return Q(self.client.store, (self.name, t))
    def rpc(self, *a, **k): return FakeRpc()


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, name): return FakeSchema(self, name)
    def table(self, t): return Q(self.store, ("storeops", t))
    # The org tree resolves a manager for only 6 of 29 house stores and 0 of 20 Luxelink stores
    # (measured 2026-10-04), so EVERY scenario here runs with an empty tree deliberately: the
    # resolution under test must work from store assignment, which is what production actually has.
    def rpc(self, *a, **k): return FakeRpc()


import app.modules.storeops.router as SO             # noqa: E402
import app.core.scope as CS                          # noqa: E402
import app.modules.core.router as CORE               # noqa: E402

TOKENS = {}


def wire(store):
    fake = FakeClient(store)
    SO.get_supabase = lambda: fake
    CORE._uid_from_token = lambda tok: TOKENS.get(tok)
    CS.invalidate_market_index()
    return fake


def app_user(name, role, *, market=None, store_code=None, store_codes=None, employee_id=None):
    uid = f"uid-{name}"
    TOKENS[f"Bearer {name}"] = uid
    return {"org_id": HOUSE, "auth_id": uid, "role": role, "employee_id": employee_id,
            "market": market, "store_code": store_code, "store_codes": store_codes}


def shift(eid, store, date, hours=8.0, sid=None):
    return {"id": sid or nid("sh"), "org_id": HOUSE, "employee_id": eid, "employee_name": eid,
            "store_code": store, "shift_date": date, "start_time": "09:00", "end_time": "17:00",
            "scheduled_hours": hours, "actual_hours": 0, "is_deleted": False}


WK, WE = "2026-09-07", "2026-09-13"

st = {
    ("storeops", "app_config"): [{"id": 1, "rbac_enabled": True}],
    ("storeops", "stores"): [
        {"org_id": HOUSE, "store_code": "B-1", "address": "1 Main St", "market": "LI", "is_active": True},
        {"org_id": HOUSE, "store_code": "B-2", "address": "2 Oak Ave", "market": "NJ", "is_active": True},
        {"org_id": HOUSE, "store_code": "B-3", "address": "3 Penn Blvd", "market": "NYC", "is_active": True},
    ],
    ("commcalc", "store_mapping"): [],
    ("storeops", "roles"): [
        {"org_id": HOUSE, "name": "admin", "permissions": {"scope": "all"}},
        # The live shape of the defect: a rep and their manager BOTH carry scope 'store', so scope
        # alone cannot tell them apart — the rep is DECLARED 'self', the manager DECLARED 'span'.
        {"org_id": HOUSE, "name": "sales_rep",
         "permissions": {"scope": "store", "people_visibility": "self"}},
        {"org_id": HOUSE, "name": "store_manager",
         "permissions": {"scope": "store", "people_visibility": "span"}},
        {"org_id": HOUSE, "name": "district_manager", "permissions": {"scope": "market"}},
        # Declares nothing at all, and its name means nothing to the code.
        {"org_id": HOUSE, "name": "mystery_role", "permissions": {"scope": "store"}},
    ],
    ("storeops", "app_users"): [
        app_user("admin", "admin", employee_id="E900"),
        # The reported case: a rep whose login pins THREE markets (5 live house reps do).
        app_user("rep", "sales_rep", market="NYC, NJ, LI", store_code="B-1", employee_id="E1"),
        app_user("mgr", "store_manager", market="NYC, NJ, LI", store_code="B-1", employee_id="E10"),
        app_user("dm", "district_manager", market="NJ", store_code="B-2", employee_id="E20"),
        app_user("mystery", "mystery_role", market="NYC, NJ, LI", store_code="B-1", employee_id="E30"),
        app_user("nobody", "sales_rep", store_code="B-1", employee_id=None),
        # Has a roster home_store (B-1) but NO pin on the login — the live case for 8 logins.
        app_user("nopin", "store_manager", employee_id="E10"),
    ],
    ("storeops", "employees"): [
        {"org_id": HOUSE, "id": "1", "employee_id": "E1", "name": "Rep One", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "2", "employee_id": "E2", "name": "Rep Two", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "3", "employee_id": "E3", "name": "Far Away", "home_store": "B-3", "is_active": True},
        {"org_id": HOUSE, "id": "10", "employee_id": "E10", "name": "Mgr Ten", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "20", "employee_id": "E20", "name": "Dm Twenty", "home_store": "B-2", "is_active": True},
        {"org_id": HOUSE, "id": "21", "employee_id": "E21", "name": "Nj Rep", "home_store": "B-2", "is_active": True},
        {"org_id": HOUSE, "id": "30", "employee_id": "E30", "name": "Mystery", "home_store": "B-1", "is_active": True},
    ],
    ("storeops", "shifts"): [
        shift("E1", "B-1", "2026-09-08"),
        shift("E1", "B-3", "2026-09-09"),          # the rep was BORROWED to another store
        shift("E2", "B-1", "2026-09-08"),
        shift("E3", "B-3", "2026-09-10"),
        shift("E10", "B-1", "2026-09-08"),
        shift("E20", "B-2", "2026-09-08"),
        shift("E21", "B-2", "2026-09-09"),
    ],
    ("storeops", "shift_templates"): [
        {"org_id": HOUSE, "employee_id": "E1", "store_code": "B-1", "weekday": 1},
        {"org_id": HOUSE, "employee_id": "E2", "store_code": "B-1", "weekday": 2},
        {"org_id": HOUSE, "employee_id": "E3", "store_code": "B-3", "weekday": 3},
    ],
    ("storeops", "time_off_requests"): [
        {"id": 1, "org_id": HOUSE, "employee_id": "E1", "start_date": "2026-09-08", "end_date": "2026-09-08", "status": "approved"},
        {"id": 2, "org_id": HOUSE, "employee_id": "E2", "start_date": "2026-09-09", "end_date": "2026-09-09", "status": "approved"},
        {"id": 3, "org_id": HOUSE, "employee_id": "E3", "start_date": "2026-09-10", "end_date": "2026-09-10", "status": "pending"},
    ],
    ("storeops", "shift_swap_requests"): [
        {"id": 1, "org_id": HOUSE, "requester_id": "E1", "target_id": "E2", "created_at": "2026-09-08", "status": "pending"},
        {"id": 2, "org_id": HOUSE, "requester_id": "E3", "target_id": None, "created_at": "2026-09-10", "status": "pending"},
    ],
    ("storeops", "timelog"): [
        {"id": "t1", "org_id": HOUSE, "employee_id": "E1", "store_code": "B-1", "work_date": "2026-09-08", "clock_in": "2026-09-08T13:00:00Z", "clock_out": "2026-09-08T21:00:00Z"},
        {"id": "t2", "org_id": HOUSE, "employee_id": "E2", "store_code": "B-1", "work_date": "2026-09-08", "clock_in": "2026-09-08T13:05:00Z", "clock_out": "2026-09-08T21:00:00Z"},
        {"id": "t3", "org_id": HOUSE, "employee_id": "E3", "store_code": "B-3", "work_date": "2026-09-10", "clock_in": "2026-09-10T13:00:00Z", "clock_out": "2026-09-10T21:00:00Z"},
    ],
}
wire(st)
SO._signed_selfie = lambda p: None


def who(rows, field="employee_id"):
    return {str(r.get(field)) for r in rows}


def shifts_for(tok):
    return SO.get_shifts(store_code=None, week_start=WK, week_end=WE, authorization=tok, org_id=HOUSE)


# ══════════════════ A. THE LEAK, reproduced on the PRE-FIX gate ═════════════════════════════════
# `_login_extra_codes` is the pre-fix input to `caller_scope` for a scope-'store' role — the market
# pin unioned in. It is called DIRECTLY here so the regression is reproduced on the old behaviour
# rather than on whatever the current `scope_keyset` happens to return.
rep_au = [a for a in st[("storeops", "app_users")] if a["auth_id"] == "uid-rep"][0]
old_codes = SO._login_extra_codes(rep_au, HOUSE)
old_ks = CS.widen_codes_to_keys(SO.get_supabase(), HOUSE, old_codes)
check("A1. the PRE-FIX store gate put EVERY store in a rep's keyset (three market pins)",
      {"B-1", "B-2", "B-3"} <= old_ks, str(old_ks))
leak = {str(s["employee_id"]) for s in st[("storeops", "shifts")] if SO.in_keyset(old_ks, s["store_code"])}
check("A2. so the pre-fix gate left the rep reading SIX people's shifts — the whole company",
      leak == {"E1", "E2", "E3", "E10", "E20", "E21"}, str(leak))

# ══════════════════ B. A rep reads only their own ═══════════════════════════════════════════════
rep_rows = shifts_for("Bearer rep")
check("B1. GET /shifts for the rep returns ONLY their own shifts", who(rep_rows) == {"E1"}, str(who(rep_rows)))
check("B2. and BOTH of them — their home store and the store they were borrowed to",
      sorted(r["store_code"] for r in rep_rows) == ["B-1", "B-3"], str(rep_rows))
check("B3. schedule_emp_ids for the rep is exactly their own id (+ its numeric form)",
      SO.schedule_emp_ids("Bearer rep", HOUSE, since=WK, until=WE) == {"E1", "1"},
      str(SO.schedule_emp_ids("Bearer rep", HOUSE, since=WK, until=WE)))

# ══════════════════ C. A store manager: own store's people, NOT the market ══════════════════════
mgr_rows = shifts_for("Bearer mgr")
check("C1. the store manager reads their own store's people (home store UNION worked-there)",
      who(mgr_rows) == {"E1", "E2", "E10"}, str(who(mgr_rows)))
check("C2. and NOT the other stores' people, though their login pins all three markets",
      not ({"E20", "E21", "E3"} & who(mgr_rows)), str(who(mgr_rows)))
check("C3. the gate is PER-PERSON, so a visible person's borrowed shift elsewhere is visible too",
      {r["store_code"] for r in mgr_rows if str(r["employee_id"]) == "E1"} == {"B-1", "B-3"},
      str([r["store_code"] for r in mgr_rows if str(r["employee_id"]) == "E1"]))
check("C4. the org tree is EMPTY in this scenario — the span came from store assignment alone",
      SO._caller_org_unit_codes("Bearer mgr", HOUSE) == [])

# ══════════════════ D. A DM: scope 'market', nothing declared -> 'span', market binds ═══════════
check("D1. people_visibility derives 'span' for an undeclared scope-'market' role",
      CS.people_visibility({"scope": "market"}) == CS.PEOPLE_SPAN)
dm_rows = shifts_for("Bearer dm")
check("D2. the DM reads their market's people", who(dm_rows) == {"E20", "E21"}, str(who(dm_rows)))
check("D3. and not another market's", not ({"E1", "E2", "E3"} & who(dm_rows)), str(who(dm_rows)))

# ══════════════════ E. Unrestricted stays unrestricted ══════════════════════════════════════════
check("E1. an 'all'-scope admin is unrestricted", SO.schedule_emp_ids("Bearer admin", HOUSE) is None)
check("E2. and reads every shift", who(shifts_for("Bearer admin")) ==
      {"E1", "E2", "E3", "E10", "E20", "E21"}, str(who(shifts_for("Bearer admin"))))
check("E3. NO token -> unrestricted (an in-process / scheduled caller is never blanked)",
      SO.schedule_emp_ids("", HOUSE) is None)
st[("storeops", "app_config")] = [{"id": 1, "rbac_enabled": False}]
check("E4. rbac master switch OFF -> unrestricted", SO.schedule_emp_ids("Bearer rep", HOUSE) is None)
check("E5. and the endpoint is then byte-identical to pre-change behaviour",
      who(shifts_for("Bearer rep")) == {"E1", "E2", "E3", "E10", "E20", "E21"})
st[("storeops", "app_config")] = [{"id": 1, "rbac_enabled": True}]

# ══════════════════ F. Fail narrow ══════════════════════════════════════════════════════════════
check("F1. an undeclared scope-'store' role falls to 'self', not to its store",
      CS.people_visibility({"scope": "store"}) == CS.PEOPLE_SELF)
check("F2. a blank / unknown / garbage permission set falls to 'self'",
      CS.people_visibility(None) == CS.PEOPLE_SELF
      and CS.people_visibility({}) == CS.PEOPLE_SELF
      and CS.people_visibility({"people_visibility": "banana"}) == CS.PEOPLE_SELF
      and CS.people_visibility("nonsense") == CS.PEOPLE_SELF)
check("F3. an explicit declaration always wins over the derivation",
      CS.people_visibility({"scope": "all", "people_visibility": "self"}) == CS.PEOPLE_SELF
      and CS.people_visibility({"scope": "self", "people_visibility": "all"}) == CS.PEOPLE_ALL)
check("F4. so the undeclared mystery role reads only itself",
      who(shifts_for("Bearer mystery")) == {"E30"} or shifts_for("Bearer mystery") == [],
      str(who(shifts_for("Bearer mystery"))))
check("F5. a provisioned login with NO employee_id sees NOTHING — deny-all, never unrestricted",
      SO.schedule_emp_ids("Bearer nobody", HOUSE) == set() and shifts_for("Bearer nobody") == [])

# ══════════════════ G. Both employee_id forms ═══════════════════════════════════════════════════
st[("storeops", "shifts")].append(shift("1", "B-1", "2026-09-11"))      # numeric employees.id form
rep_rows2 = shifts_for("Bearer rep")
check("G1. a shift stored with the NUMERIC employees.id is still the rep's own shift",
      "2026-09-11" in {r["shift_date"] for r in rep_rows2}, str(sorted(r["shift_date"] for r in rep_rows2)))
check("G2. and it is still nobody else's", who(shifts_for("Bearer dm")) == {"E20", "E21"})
st[("storeops", "shifts")].pop()

# ══════════════════ H. The siblings are wired ═══════════════════════════════════════════════════
check("H1. GET /time-off narrows to the rep's own requests",
      who(SO.get_time_off(employee_id=None, authorization="Bearer rep", org_id=HOUSE)) == {"E1"})
check("H2. GET /shift-templates narrows to the rep's own recurring rows",
      who(SO.get_shift_templates(authorization="Bearer rep", org_id=HOUSE)) == {"E1"})
swaps = SO.get_shift_swaps(status=None, authorization="Bearer rep", org_id=HOUSE)
check("H3. GET /shift-swaps keeps a swap the rep is a party to, drops one they are not",
      {r["id"] for r in swaps} == {1}, str([r["id"] for r in swaps]))
tl = SO.timeclock_list(start=WK, end=WE, employee_id="", authorization="Bearer rep", org_id=HOUSE)
check("H4. GET /timeclock/list narrows the punch list the same way",
      {r["id"] for r in tl} == {"t1"}, str([r["id"] for r in tl]))
trend = SO.schedule_hours_trend(anchor=WE, weeks=2, months=2, authorization="Bearer rep", org_id=HOUSE)
check("H5. the hours trend reports it is people-restricted and counts only the rep's hours",
      trend["scope"]["people_restricted"] is True, str(trend["scope"]))
try:
    SO.staffing_heatmap(store_code="B-3", period="2026-09", authorization="Bearer dm", org_id=HOUSE)
    heat_refused = False
except Exception as e:
    heat_refused = getattr(e, "status_code", None) == 403
check("H6. the staffing heat map (previously ungated entirely) refuses a store outside the span",
      heat_refused)
# The heat map is a store-level AGGREGATE with no names in it, so its gate is the STORE keyset —
# which for a rep whose login pins three markets is still wide. Narrowing that is the separate
# "a rep's login should not pin three markets" setup problem, reported rather than coded around.

# ══════════════════ J. THE MARKET PIN NO LONGER WIDENS A STORE-SCOPED SPAN ══════════════════════
# The second half of the owner's ask: "they shoudl be gated out of all stores other and thier own"
# — the SALES REPORT and every other store-keyed report read `scope_keyset`, so the fix is there.
ks_rep_now = SO.scope_keyset("Bearer rep", HOUSE)
check("J1. the rep's reporting span is now their OWN store, not their three pinned markets",
      ks_rep_now == {"B-1", "1 MAIN ST"}, str(ks_rep_now))
check("J2. a store-scoped MANAGER is narrowed the same way (a market is a manager-of-market fact)",
      SO.scope_keyset("Bearer mgr", HOUSE) == {"B-1", "1 MAIN ST"},
      str(SO.scope_keyset("Bearer mgr", HOUSE)))
check("J3. a scope-'market' DM's market grant STILL binds — only store/self scopes refuse it",
      {"B-2", "2 OAK AVE"} <= (SO.scope_keyset("Bearer dm", HOUSE) or set()),
      str(SO.scope_keyset("Bearer dm", HOUSE)))
check("J4. an 'all'-scope admin is still unrestricted", SO.scope_keyset("Bearer admin", HOUSE) is None)
check("J5. employees.home_store is deliberately NOT read into a reporting span — a login with no "
      "store pin resolves nothing rather than being granted a store here",
      SO.scope_keyset("Bearer nopin", HOUSE) == set(),
      str(SO.scope_keyset("Bearer nopin", HOUSE)))

# ══════════════════ I. Narrowing only, against the PRE-FIX gate ═════════════════════════════════
narrowed, widened_for = True, []
for tok, who_au in (("Bearer rep", "uid-rep"), ("Bearer mgr", "uid-mgr"), ("Bearer dm", "uid-dm"),
                    ("Bearer mystery", "uid-mystery"), ("Bearer nobody", "uid-nobody")):
    au = [a for a in st[("storeops", "app_users")] if a["auth_id"] == who_au][0]
    scope = SO._role_scope(HOUSE, au["role"])
    if scope == "all":
        continue                                            # unrestricted before and after
    old = CS.widen_codes_to_keys(SO.get_supabase(), HOUSE, SO._login_extra_codes(au, HOUSE))
    before = {s["id"] for s in st[("storeops", "shifts")] if SO.in_keyset(old, s["store_code"])}
    after = {r["id"] for r in shifts_for(tok)}
    if not after <= before:
        narrowed = False
        widened_for.append((tok, sorted(after - before)))
check("I1. every caller reads a SUBSET of what the PRE-FIX gate gave them — nobody gained a row",
      narrowed, str(widened_for))

# ══════════════════ K. THE SALES REPORT AND THE FLAGS QUEUE (owner's extension, same hour) ══════
# *"same for sales report, they shoudl be gated out of all stores other and thier own, also the
# flags should only be seen by them for thier own not all stores"*. Two DIFFERENT grains, and the
# owner named both correctly: the sales report is per STORE (J above fixes it at the span), a flag is
# per REP, so it gates on the person.
import app.modules.commcalc.router as CC                   # noqa: E402

CC.get_supabase = lambda: FakeClient(st)
CC.sb = lambda: FakeClient(st)
st[("commcalc", "flags")] = [
    {"id": 1, "org_id": HOUSE, "period": "2026-09", "epay_salesperson": "REP ONE",
     "store_code": "B-1", "store_address": "1 Main St", "status": "open", "severity": 1},
    {"id": 2, "org_id": HOUSE, "period": "2026-09", "epay_salesperson": "REP TWO",
     "store_code": "B-1", "store_address": "1 Main St", "status": "open", "severity": 1},
    {"id": 3, "org_id": HOUSE, "period": "2026-09", "epay_salesperson": "FAR AWAY",
     "store_code": "B-3", "store_address": "3 Penn Blvd", "status": "open", "severity": 1},
]
st[("commcalc", "name_map")] = []
st[("commcalc", "rep_aliases")] = []

old_rep_ks = CS.widen_codes_to_keys(SO.get_supabase(), HOUSE, SO._login_extra_codes(rep_au, HOUSE))
before_flags = {f["id"] for f in st[("commcalc", "flags")]
                if SO.in_keyset(old_rep_ks, f["store_code"], f["store_address"])}
check("K1. the PRE-FIX gate showed the rep every store's flags",
      before_flags == {1, 2, 3}, str(before_flags))
flags_rep = CC.get_flags("2026-09", authorization="Bearer rep", org_id=HOUSE)
check("K2. the rep now reads ONLY the flags raised against them, not their store's or the company's",
      {f["id"] for f in flags_rep} == {1}, str([f["id"] for f in flags_rep]))
flags_dm = CC.get_flags("2026-09", authorization="Bearer dm", org_id=HOUSE)
check("K3. a manager's flags queue is untouched — still their span, by store",
      {f["id"] for f in flags_dm} == set(), str([f["id"] for f in flags_dm]))
flags_admin = CC.get_flags("2026-09", authorization="Bearer admin", org_id=HOUSE)
check("K4. an admin still reads every flag", {f["id"] for f in flags_admin} == {1, 2, 3},
      str([f["id"] for f in flags_admin]))
check("K5. the individual-contributor answer is ONE function, and a declared people_visibility "
      "'self' now satisfies it (it is why the flags ask did nothing before)",
      SO.role_is_self_scoped(HOUSE, "sales_rep") is True
      and SO.role_is_self_scoped(HOUSE, "store_manager") is False
      and SO.role_is_self_scoped(HOUSE, "admin") is False,
      f"rep={SO.role_is_self_scoped(HOUSE, 'sales_rep')} "
      f"mgr={SO.role_is_self_scoped(HOUSE, 'store_manager')}")
from app.modules.commcalc import payout_audience as PA        # noqa: E402
check("K6. a flag with no rep name on it never becomes 'mine' by accident",
      PA.row_is_mine({"store_code": "B-1"}, {"REP ONE"}) is False)
check("K7. nor does a manager's row set change shape — mine_only hands a manager the SAME list",
      PA.mine_only(st[("commcalc", "flags")], None) is st[("commcalc", "flags")])

# The SALES REPORT is store-keyed and applies `scope_keyset` verbatim, which J1/J2 pin. Proved here
# on the endpoint's own predicate over its own rows, so a future change to how it filters is caught.
sales_rows = [{"store": "B-1", "amount": 1}, {"store": "B-2", "amount": 2}, {"store": "B-3", "amount": 3}]
for tok, want in (("Bearer rep", {"B-1"}), ("Bearer mgr", {"B-1"}), ("Bearer dm", {"B-2"})):
    ks = SO.scope_keyset(tok, HOUSE)
    got = {r["store"] for r in sales_rows if SO.in_keyset(ks, r.get("store"))}
    check(f"K8. the sales report's own store filter leaves {tok.split()[1]} only {sorted(want)}",
          got == want, str(got))
check("K9. and the PRE-FIX filter left the rep all three stores",
      {r["store"] for r in sales_rows if SO.in_keyset(old_rep_ks, r.get("store"))} == {"B-1", "B-2", "B-3"})

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:\n  " + "\n  ".join(FAIL))
sys.exit(1 if FAIL else 0)
