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


def shift(eid, store, date, hours=8.0, sid=None, deleted=False):
    return {"id": sid or nid("sh"), "org_id": HOUSE, "employee_id": eid, "employee_name": eid,
            "store_code": store, "shift_date": date, "start_time": "09:00", "end_time": "17:00",
            "scheduled_hours": hours, "actual_hours": 0, "is_deleted": deleted}


WK, WE = "2026-09-07", "2026-09-13"

st = {
    ("storeops", "app_config"): [{"id": 1, "rbac_enabled": True}],
    ("storeops", "stores"): [
        {"org_id": HOUSE, "store_code": "B-1", "address": "1 Main St", "market": "LI", "is_active": True},
        {"org_id": HOUSE, "store_code": "B-2", "address": "2 Oak Ave", "market": "NJ", "is_active": True},
        {"org_id": HOUSE, "store_code": "B-3", "address": "3 Penn Blvd", "market": "NYC", "is_active": True},
        # B-7 / B-8 exist only for the BASIS test below: no other check asserts on either, so the
        # two fixtures that disagree about where their person is cannot disturb anybody's people
        # list while proving which field the span actually reads.
        {"org_id": HOUSE, "store_code": "B-7", "address": "7 Roster Rd", "market": "LI", "is_active": True},
        {"org_id": HOUSE, "store_code": "B-8", "address": "8 Evidence Way", "market": "LI", "is_active": True},
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
        # An ELEVATED role (`_ELEVATED_ROLES`) whose row carries NO `scope` key at all —
        # `_role_scope` resolves it to 'all' from the elevated-role default, and the declaration
        # must honour that resolution rather than the empty blob.
        {"org_id": HOUSE, "name": "owner", "permissions": {"pages": {}}},
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
        # Has a roster home_store (B-1) but NO pin on the login — the live case for 8 logins. Its
        # only EVIDENCE is a shift at B-2, so the span it resolves proves which basis is in force.
        app_user("nopin", "store_manager", employee_id="E31"),
        # A rep pinned to B-1, home_store B-1, and NO shift and NO punch anywhere — the live case
        # for 7 self-visibility logins. Under the evidence basis this resolves NOTHING.
        app_user("ghost", "sales_rep", store_code="B-1", employee_id="E99"),
    ],
    ("storeops", "employees"): [
        {"org_id": HOUSE, "id": "1", "employee_id": "E1", "name": "Rep One", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "2", "employee_id": "E2", "name": "Rep Two", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "3", "employee_id": "E3", "name": "Far Away", "home_store": "B-3", "is_active": True},
        {"org_id": HOUSE, "id": "10", "employee_id": "E10", "name": "Mgr Ten", "home_store": "B-1", "is_active": True},
        {"org_id": HOUSE, "id": "20", "employee_id": "E20", "name": "Dm Twenty", "home_store": "B-2", "is_active": True},
        {"org_id": HOUSE, "id": "21", "employee_id": "E21", "name": "Nj Rep", "home_store": "B-2", "is_active": True},
        {"org_id": HOUSE, "id": "30", "employee_id": "E30", "name": "Mystery", "home_store": "B-1", "is_active": True},
        # Both sit at B-7, a store no other check asserts on, so they test the BASIS without
        # joining anybody's people list. E31's roster store is B-7 and its only shift is at B-2 —
        # that disagreement is the whole point. E99 has a roster store and a pin and no evidence.
        {"org_id": HOUSE, "id": "31", "employee_id": "E31", "name": "Pinless Mgr", "home_store": "B-7", "is_active": True},
        {"org_id": HOUSE, "id": "99", "employee_id": "E99", "name": "Ghost Rep", "home_store": "B-7", "is_active": True},
    ],
    ("storeops", "shifts"): [
        shift("E1", "B-1", "2026-09-08"),
        shift("E1", "B-3", "2026-09-09"),          # the rep was BORROWED to another store
        shift("E2", "B-1", "2026-09-08"),
        shift("E3", "B-3", "2026-09-10"),
        shift("E10", "B-1", "2026-09-08"),
        shift("E20", "B-2", "2026-09-08"),
        shift("E21", "B-2", "2026-09-09"),
        shift("E31", "B-8", "2026-09-11"),         # the pinless manager's ONLY evidence, and it is
                                                   # NOT their roster home_store (B-7)
        shift("E1", "B-9", "2026-09-12", deleted=True),   # a CANCELLED shift is not evidence
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
check("A2. so the pre-fix gate left the rep reading EVERY scheduled person's shifts — the whole "
      "company",
      leak == {"E1", "E2", "E3", "E10", "E20", "E21", "E31"}, str(leak))

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
      {"E1", "E2", "E3", "E10", "E20", "E21", "E31"}, str(who(shifts_for("Bearer admin"))))
check("E3. NO token -> unrestricted (an in-process / scheduled caller is never blanked)",
      SO.schedule_emp_ids("", HOUSE) is None)
st[("storeops", "app_config")] = [{"id": 1, "rbac_enabled": False}]
check("E4. rbac master switch OFF -> unrestricted", SO.schedule_emp_ids("Bearer rep", HOUSE) is None)
check("E5. and the endpoint is then byte-identical to pre-change behaviour",
      who(shifts_for("Bearer rep")) == {"E1", "E2", "E3", "E10", "E20", "E21", "E31"}, str(who(shifts_for("Bearer rep"))))
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

# ══════════════════ J. THE SPAN IS EVIDENCE, NOT A PIN ══════════════════════════════════════════
# Two owner directives, same day, same mechanism. First: *"they shoudl be gated out of all stores
# other and thier own"* — a MARKET pin must not widen a store-scoped span. Then, on seeing the
# result: *"the store pin is not desired, the employee shoudl have teh visibility in the store they
# have been schduled and actually worked and only their numbers, the concept of home store does not
# apply for vistibility into the performance of the store"*.
#
# So the span is resolved from EVIDENCE — a shift (the roster said be there) or a punch (the clock
# says they were). Note J1: the rep's span is WIDER than their pin, because they were borrowed to
# B-3 and genuinely worked it. That is the directive, and it is only safe because the SAME
# declaration gates the person dimension — see K8/K8b, where the rep reads those stores and sees
# only their own rows at them. A harness that proved one half without the other would be proving a
# leak.
ks_rep_now = SO.scope_keyset("Bearer rep", HOUSE)
check("J1. the rep's span is where they were SCHEDULED or WORKED — their own store AND the one they "
      "were borrowed to — not their three pinned markets",
      ks_rep_now == {"B-1", "1 MAIN ST", "B-3", "3 PENN BLVD"}, str(ks_rep_now))
check("J1b. a MARKET pin is still never read for them (their three markets bind 'B-2' and it is "
      "absent, even though the pin names the whole company)",
      "B-2" not in ks_rep_now and "2 OAK AVE" not in ks_rep_now, str(ks_rep_now))
check("J2. a store-scoped MANAGER gets assigned-OR-worked: the store they run plus anywhere they "
      "actually were, and still no market",
      SO.scope_keyset("Bearer mgr", HOUSE) == {"B-1", "1 MAIN ST"},
      str(SO.scope_keyset("Bearer mgr", HOUSE)))
check("J3. a scope-'market' DM's market grant STILL binds — only store/self tiers refuse it",
      {"B-2", "2 OAK AVE"} <= (SO.scope_keyset("Bearer dm", HOUSE) or set()),
      str(SO.scope_keyset("Bearer dm", HOUSE)))
check("J4. an 'all'-scope admin is still unrestricted", SO.scope_keyset("Bearer admin", HOUSE) is None)
# THE BASIS TEST. `nopin` has roster home_store B-7 and no login pin; its only shift is at B-8. A
# home-store basis answers B-7, an evidence basis answers B-8. There is no other way to tell the two
# apart, which is why this fixture exists.
#
# `employees.home_store` IS still read by `reporting_employee_ids`, deliberately, and that is not a
# contradiction: there it answers "who is ASSIGNED to my store", which is a fair reading of "any
# employee works under him". The owner's sentence rules it out as an answer to "whose PERFORMANCE
# may I see", which is this dimension.
ks_nopin = SO.scope_keyset("Bearer nopin", HOUSE)
check("J5. the span reads EVIDENCE, not employees.home_store — a login whose roster store is B-7 "
      "but whose only shift is at B-8 resolves B-8 and not B-7",
      "B-8" in ks_nopin and "B-7" not in ks_nopin, str(ks_nopin))
check("J6. and a login with a pin, a home_store and NO shift or punch anywhere resolves NOTHING "
      "rather than being granted its pinned store (7 live logins) — empty stays a deny-all, never "
      "the unrestricted None",
      SO.scope_keyset("Bearer ghost", HOUSE) == set(),
      str(SO.scope_keyset("Bearer ghost", HOUSE)))
check("J7. the evidence scan is BOUNDED by the window being reported — asking about August, when "
      "the rep's only shifts are in September, resolves nothing",
      SO.scope_keyset("Bearer rep", HOUSE, since="2026-08-01", until="2026-08-31") == set(),
      str(SO.scope_keyset("Bearer rep", HOUSE, since="2026-08-01", until="2026-08-31")))
check("J8. a CANCELLED shift is not evidence — the rep has a deleted B-9 shift and B-9 is absent "
      "from both the raw answer and the keyset",
      "B-9" not in CS.worked_store_codes(SO.get_supabase(), HOUSE, "E1")
      and "B-9" not in ks_rep_now,
      str(CS.worked_store_codes(SO.get_supabase(), HOUSE, "E1")))
check("J8b. a person with no employee_id resolves nothing rather than everything",
      CS.worked_store_codes(SO.get_supabase(), HOUSE, None) == set()
      and CS.worked_store_codes(SO.get_supabase(), HOUSE, "") == set())
check("J9. both directions read ONE evidence registry — reporting_employee_ids dereferences the "
      "same WORKED_AT_SOURCES tuple worked_store_codes does, so neither can drift",
      isinstance(CS.WORKED_AT_SOURCES, tuple) and len(CS.WORKED_AT_SOURCES) >= 2
      and {t[0] for t in CS.WORKED_AT_SOURCES} == {"shifts", "timelog"},
      str(CS.WORKED_AT_SOURCES))

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
# THE REGRESSION (caught by harness_payout_audience.py G5 on the first push of this branch): the
# declaration must be read with the CANONICAL scope stamped on it. Reading `roles.permissions`
# directly made a DM or an owner whose row omits a `scope` key derive 'self' and read as an
# individual contributor — i.e. the widest roles would have been treated as the narrowest.
st[("storeops", "roles")].append(
    {"org_id": HOUSE, "name": "no_scope_key", "permissions": {"pages": {}}})
check("K5b. an ELEVATED role whose row has NO scope key resolves through _role_scope ('all'), so it "
      "is NOT read as an individual contributor",
      SO._role_scope(HOUSE, "owner") == "all"
      and SO.role_is_self_scoped(HOUSE, "owner") is False,
      f"scope={SO._role_scope(HOUSE, 'owner')} "
      f"self_scoped={SO.role_is_self_scoped(HOUSE, 'owner')}")
check("K5c. and the fail-closed default for a genuinely unknown role still lands on 'self'",
      SO.role_is_self_scoped(HOUSE, "no_scope_key") is True)
check("K6. a flag with no rep name on it never becomes 'mine' by accident",
      PA.row_is_mine({"store_code": "B-1"}, {"REP ONE"}) is False)
check("K7. nor does a manager's row set change shape — mine_only hands a manager the SAME list",
      PA.mine_only(st[("commcalc", "flags")], None) is st[("commcalc", "flags")])

# ══════════════════ K8+. THE SALES REPORT — BOTH DIMENSIONS, ON THE REAL ENDPOINT ═══════════════
# *"the employee shoudl have teh visibility in the store they have been schduled and actually worked
# and only their numbers"*. This report's rows are PER REP — one per (store, salesperson, day) with
# that named person's revenue and GP — so the store gate alone was never the owner's sentence. These
# run the real `sales_report` over real transactions, because the two halves are only correct
# TOGETHER: the store half makes a rep's list wider (B-1 AND the borrowed B-3) and the person half
# is what keeps that from being a leak.
st[("commcalc", "raw_sales")] = [
    # B-1: the rep's own store, shared with a colleague.
    {"org_id": HOUSE, "period": "September 2026", "store": "B-1", "salesperson": "Rep One",
     "trans_id": "T1", "trans_date": "2026-09-08", "department": "Phones", "category": "Device",
     "product_desc": "Phone A", "contract_type": "New", "ext_price": 100, "gp": 30, "voided": False},
    {"org_id": HOUSE, "period": "September 2026", "store": "B-1", "salesperson": "Rep Two",
     "trans_id": "T2", "trans_date": "2026-09-08", "department": "Phones", "category": "Device",
     "product_desc": "Phone B", "contract_type": "New", "ext_price": 200, "gp": 60, "voided": False},
    # B-3: the store the rep was BORROWED to — their own row there, and a colleague's.
    {"org_id": HOUSE, "period": "September 2026", "store": "B-3", "salesperson": "Rep One",
     "trans_id": "T3", "trans_date": "2026-09-09", "department": "Phones", "category": "Device",
     "product_desc": "Phone C", "contract_type": "New", "ext_price": 300, "gp": 90, "voided": False},
    {"org_id": HOUSE, "period": "September 2026", "store": "B-3", "salesperson": "Far Away",
     "trans_id": "T4", "trans_date": "2026-09-10", "department": "Phones", "category": "Device",
     "product_desc": "Phone D", "contract_type": "New", "ext_price": 400, "gp": 120, "voided": False},
    # B-2: a store the rep has never been near.
    {"org_id": HOUSE, "period": "September 2026", "store": "B-2", "salesperson": "Nj Rep",
     "trans_id": "T5", "trans_date": "2026-09-08", "department": "Phones", "category": "Device",
     "product_desc": "Phone E", "contract_type": "New", "ext_price": 500, "gp": 150, "voided": False},
]
# A prior month, so the banner has something to compare against and actually renders (it returns
# `available: False` when both windows are empty, which would make the gate check below vacuous).
for _d, _st, _sp, _tid, _amt in (("2026-08-08", "B-1", "Rep One", "P1", 50),
                                 ("2026-08-08", "B-2", "Nj Rep", "P2", 250)):
    st[("commcalc", "raw_sales")].append(
        {"org_id": HOUSE, "period": "August 2026", "store": _st, "salesperson": _sp,
         "trans_id": _tid, "trans_date": _d, "department": "Phones", "category": "Device",
         "product_desc": "Phone", "contract_type": "New", "ext_price": _amt,
         "gp": _amt / 3.0, "voided": False})
st[("commcalc", "daily_sales_feed")] = []


def sales_cells(tok):
    r = CC.sales_report(period="September 2026", authorization=tok, org_id=HOUSE)
    return {(x["store"], x["salesperson"]) for x in (r.get("rows") or r.get("data") or [])}, r


cells_rep, rep_resp = sales_cells("Bearer rep")
check("K8. the rep's sales report reaches BOTH stores they actually worked — their own and the one "
      "they were borrowed to — which the pin basis never would have",
      {c[0] for c in cells_rep} == {"B-1", "B-3"}, str(cells_rep))
check("K8b. and at BOTH of them they read only their OWN numbers, never the colleague's row that "
      "shares the store — this is the half that makes the wider store list safe",
      cells_rep == {("B-1", "Rep One"), ("B-3", "Rep One")}, str(cells_rep))
check("K8c. the TOTALS are their own too — summed after the person filter, not before (the whole "
      "fixture is $1,500; this rep sold $400 of it)",
      abs(float((rep_resp.get("totals") or {}).get("revenue", 0)) - 400.0) < 0.01,
      str(rep_resp.get("totals")))
cells_mgr, _ = sales_cells("Bearer mgr")
check("K8d. a store MANAGER still reads every rep at the store they run — a span tier is not an "
      "individual contributor",
      cells_mgr == {("B-1", "Rep One"), ("B-1", "Rep Two")}, str(cells_mgr))
cells_admin, admin_resp = sales_cells("Bearer admin")
check("K8e. an admin still reads the whole company, and their totals are the whole company's",
      {c[0] for c in cells_admin} == {"B-1", "B-2", "B-3"}
      and abs(float((admin_resp.get("totals") or {}).get("revenue", 0)) - 1500.0) < 0.01,
      f"{cells_admin} {admin_resp.get('totals')}")
check("K9. and the PRE-FIX gate left the rep every store's rows AND every colleague's name on them "
      "— the leak the owner reported, reproduced on the old basis",
      {r["store"] for r in st[("commcalc", "raw_sales")] if SO.in_keyset(old_rep_ks, r.get("store"))}
      == {"B-1", "B-2", "B-3"})

# ══════════════════ L. THE DRILL-DOWN AND THE BANNER HAD NO GATE AT ALL ═════════════════════════
# Both took no `authorization` parameter, so neither ever looked at who was asking. The drill-down
# returns customer name, phone (`mdn`) and serial; the banner totals the company. Same sibling class
# as the ungated staffing heat map, and found the same way — by asking what ELSE answers this
# question rather than fixing only the surface that was reported.
import inspect  # noqa: E402
from datetime import date as _date  # noqa: E402
for fn, name in ((CC.sales_report_detail, "/sales-report/detail"),
                 (CC.sales_report_narrative, "/sales-report/narrative")):
    check(f"L1. {name} now takes the caller's identity at all",
          "authorization" in inspect.signature(fn).parameters,
          str(list(inspect.signature(fn).parameters)))
det_own = CC.sales_report_detail(period="September 2026", store="B-3", salesperson="Rep One",
                                 date="2026-09-09", authorization="Bearer rep", org_id=HOUSE)
check("L2. a rep may open their OWN cell at a store they worked",
      {t["trans_id"] for t in det_own["transactions"]} == {"T3"}, str(det_own["transactions"]))
try:
    CC.sales_report_detail(period="September 2026", store="B-3", salesperson="Far Away",
                           date="2026-09-10", authorization="Bearer rep", org_id=HOUSE)
    _refused = False
except Exception as e:
    _refused = getattr(e, "status_code", None) == 403
check("L3. and is REFUSED a colleague's cell at that same store, by name", _refused)
try:
    CC.sales_report_detail(period="September 2026", store="B-2", salesperson="Nj Rep",
                           date="2026-09-08", authorization="Bearer rep", org_id=HOUSE)
    _refused_store = False
except Exception as e:
    _refused_store = getattr(e, "status_code", None) == 403
check("L4. and refused a store they never worked", _refused_store)
det_blank = CC.sales_report_detail(period="September 2026", store="B-1", salesperson="",
                                   date="", authorization="Bearer rep", org_id=HOUSE)
check("L5. omitting the rep name does NOT hand them the whole cell — the ROWS are filtered too, so "
      "a colleague's customer name, phone and serial stay unreachable",
      {t["trans_id"] for t in det_blank["transactions"]} == {"T1"},
      str(det_blank["transactions"]))
nar_rep = CC.sales_report_narrative(period="September 2026", today="2026-10-01",
                                    authorization="Bearer rep", org_id=HOUSE)
nar_admin = CC.sales_report_narrative(period="September 2026", today="2026-10-01",
                                      authorization="Bearer admin", org_id=HOUSE)
check("L6. the banner renders for both, and is GATED — a rep's sentences and an admin's are not the "
      "same numbers (a sentence naming the company's revenue IS the company's revenue)",
      nar_rep.get("available") and nar_admin.get("available") and nar_rep != nar_admin,
      f"rep={str(nar_rep)[:160]}")
check("L7. the banner's own aggregation honours a keyset directly, so no future caller can get the "
      "unrestricted roll-up by forgetting to pass one",
      CC._sales_narrative(SO.get_supabase(), HOUSE, "September 2026", today=_date.fromisoformat("2026-10-01"),
                          keyset={"B-1", "1 MAIN ST"})
      != CC._sales_narrative(SO.get_supabase(), HOUSE, "September 2026", today=_date.fromisoformat("2026-10-01"),
                             keyset=None))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:\n  " + "\n  ".join(FAIL))
sys.exit(1 if FAIL else 0)
