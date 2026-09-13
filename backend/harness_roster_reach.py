"""Harness — ROSTER REACH: "whom may this login pick a name from?" (app/core/scope.py).

THE DEFECT THIS PINS (owner-reported 2026-09-13, production outage the night of 09-12)
─────────────────────────────────────────────────────────────────────────────────────
  "employees were not able to close last night since on teh closing sheet it asks for teh employee
   name which the user was not abel to pick from the drop down menu, i made a little change to
   permissions yesterday and swictched back to view thier won store as per thier role but i think
   that interfered"

The house org's `sales_rep` / `store_manager` roles moved from `scheduling_reach='org'` to `'span'`.
`GET /storeops/employees?all_company=true` then fell through to the REPORTING span
(`storeops.caller_scope`), which resolves from `storeops.app_users` ALONE. Four active reps have no
store pinned on their LOGIN but a real one on their EMPLOYEE record, so their span resolved EMPTY —
and an empty span is a deny-all. Zero names in a required picker = the store cannot close.

Proves, WITHOUT a database:
  A. roster_reach() maps the owner's matrix exactly, and the DEFAULT ('org' / absent / garbage) is
     byte-identical to today's unconditional org-wide roster — no tenant that has not opted in moves.
  B. REGRESSION, the four live logins: a rep with store_code=NULL, store_codes=NULL/[] and a real
     `employees.home_store` resolves to THAT STORE — not to the empty set that caused the outage.
  C. roster_keyset() NEVER returns an empty set. An unresolvable reach degrades to unrestricted
     (a picker is not a security boundary; an empty one stops money being counted) and says why.
  D. The over-wide half of the same defect: a scope-'store' rep carrying a MARKET grant gets their
     own store(s), not the market's 13 stores — `self_store_codes` refuses to read market grants.
  E. A DM (scope 'market') still sees everybody in their market, and an admin (scope 'all') keeps
     the whole roster.
  F. roster_visible() keeps the CALLER themselves even when their home_store is outside the keyset
     ("thier anme should default" — a default the picker does not contain is not a default).
  G. roster_visible() keeps an employee whose `home_store` is BLANK. Unassigned is NOT MEASURED,
     not "belongs to no store"; 8 of the 52 active people in the house org are in that state and
     silently dropping them is the silent-zero pattern. WITH the negative control that an employee
     assigned to a DIFFERENT store is still excluded.
  H. Org scoping: every read this path makes is filtered by org_id, and a same-code store in
     another tenant never enters the keyset.

Run: python3 backend/harness_roster_reach.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core import scope as S   # noqa: E402

PASS = FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {extra}")


# ── Fake supabase client (records every read so org scoping is assertable) ──────────────────────
class _Q:
    def __init__(self, rows, log, schema, table):
        self._rows, self._log, self._schema, self._table = rows, log, schema, table
        self._filters = {}

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, _n):
        return self

    def execute(self):
        self._log.append({"schema": self._schema, "table": self._table, "filters": dict(self._filters)})
        rows = self._rows
        for k, v in self._filters.items():
            rows = [r for r in rows if r.get(k) == v]
        return type("R", (), {"data": rows})()


class _Schema:
    def __init__(self, data, log, name):
        self._data, self._log, self._name = data, log, name

    def table(self, t):
        if t not in self._data.get(self._name, {}):
            raise RuntimeError(f"relation {self._name}.{t} does not exist")
        return _Q(list(self._data[self._name][t]), self._log, self._name, t)


class FakeClient:
    def __init__(self, data):
        self.data, self.log = data, []

    def schema(self, name):
        return _Schema(self.data, self.log, name)


ORG = "00000000-0000-0000-0000-000000000001"
OTHER = "00000000-0000-0000-0000-0000000000ff"

# Shaped on the live house org: store CODES in home_store, a Chicago market binding many stores.
DATA = {
    "storeops": {
        "stores": [
            {"org_id": ORG, "store_code": "B-117",  "address": "117 Main St",   "market": "Chicago"},
            {"org_id": ORG, "store_code": "B-418",  "address": "418 North Ave", "market": "Chicago"},
            {"org_id": ORG, "store_code": "B-1800", "address": "1800 Grand",    "market": "Chicago"},
            {"org_id": ORG, "store_code": "B-4712", "address": "4712 Cermak",   "market": "Chicago"},
            {"org_id": ORG, "store_code": "B-2612", "address": "2612 Kedzie",   "market": "Chicago"},
            {"org_id": ORG, "store_code": "B-509",  "address": "509 Utica",     "market": "NYC"},
            # same CODE in another tenant — must never leak into this org's keyset
            {"org_id": OTHER, "store_code": "B-117", "address": "elsewhere",    "market": "Chicago"},
        ],
        "employees": [],
    },
    "commcalc": {
        "store_mapping": [],
        "store_aliases": [],
    },
}


def client():
    S._market_cache.clear()          # the index is TTL-cached per org; start every case cold
    return FakeClient(DATA)


def keys(c, org, perms, au, home=None, units=None):
    ks, why = S.roster_keyset(c, org, role_perms=perms, app_user=au,
                              employee_home_store=home, org_unit_codes=units)
    return ks, why


SPAN = {"scheduling_reach": "span"}
REP = dict(SPAN, scope="store")
MGR = dict(SPAN, scope="market")
ADMIN = dict(SPAN, scope="all")

print("\nA. roster_reach() — the owner's matrix, and an unchanged default")
ok("default (no perms) -> ALL", S.roster_reach({}) == S.ROSTER_ALL)
ok("reach 'org' -> ALL even for a store scope",
   S.roster_reach({"scope": "store", "scheduling_reach": "org"}) == S.ROSTER_ALL)
ok("garbage reach -> ALL", S.roster_reach({"scope": "store", "scheduling_reach": "banana"}) == S.ROSTER_ALL)
ok("span + scope 'store' -> OWN STORE", S.roster_reach(REP) == S.ROSTER_OWN_STORE)
ok("span + scope 'self'  -> OWN STORE", S.roster_reach(dict(SPAN, scope="self")) == S.ROSTER_OWN_STORE)
ok("span + scope 'market' -> SPAN", S.roster_reach(MGR) == S.ROSTER_SPAN)
ok("span + scope 'all' -> ALL (market manager and above see everybody)",
   S.roster_reach(ADMIN) == S.ROSTER_ALL)
ok("None perms never raises", S.roster_reach(None) == S.ROSTER_ALL)

print("\nB. REGRESSION — the four live logins that could not close on 2026-09-12")
LIVE = [("ennio.rodas@gmail.com", "E253", None, None, "B-117"),
        ("l2127martinez@gmail.com", "E170", None, [], "B-1800"),
        ('"akberawais@icloud.com" <akberawais@icloud.com>', "E252", None, None, "B-418"),
        ("junooshaik1@gmail.com", "E242", None, None, "B-4712")]
for email, eid, sc, scs, home in LIVE:
    au = {"role": "sales_rep", "employee_id": eid, "market": None,
          "store_code": sc, "store_codes": scs}
    ks, why = keys(client(), ORG, REP, au, home=home)
    ok(f"{email[:28]:<28} -> {home} (was: EMPTY = zero-name dropdown)",
       ks is not None and home.upper() in ks, f"got {ks!r}")

print("\nC. roster_keyset() never hands back an empty deny-all")
au_nothing = {"role": "sales_rep", "employee_id": "E999", "market": None,
              "store_code": None, "store_codes": None}
ks, why = keys(client(), ORG, REP, au_nothing, home="")
ok("no pin and no home_store -> UNRESTRICTED, not empty", ks is None)
ok("...and the misconfiguration is named in `why`", "NO RESOLVABLE STORE" in why, why)
ks2, _ = keys(client(), ORG, MGR, {"role": "district_manager", "employee_id": "E1",
                                   "market": None, "store_code": None, "store_codes": None}, units=[])
ok("a manager with no market and no org unit -> UNRESTRICTED, not empty", ks2 is None)

print("\nD. The over-wide half — a market grant must NOT widen a store-scoped rep")
au_market = {"role": "sales_rep", "employee_id": "E011", "market": "Chicago",
             "store_code": None, "store_codes": None}
ks, why = keys(client(), ORG, REP, au_market, home="B-2612")
ok("rep with market=Chicago gets ONLY their own store", ks is not None and "B-2612" in ks, f"{ks!r}")
for c in ("B-117", "B-418", "B-1800", "B-4712"):
    ok(f"  ...and NOT {c} (the market's other stores)", ks is not None and c not in ks)
ok("store manager pinned to two stores gets both",
   (lambda k: k is not None and {"B-117", "B-418"} <= k)(
       keys(client(), ORG, REP, {"role": "store_manager", "employee_id": "E5", "market": "Chicago",
                                 "store_code": "B-117", "store_codes": ["B-418"]}, home="B-117")[0]))

print("\nE. DM sees their market; admin sees everybody")
ks, why = keys(client(), ORG, MGR, {"role": "district_manager", "employee_id": "E7",
                                    "market": "Chicago", "store_code": None, "store_codes": None},
               units=[])
ok("DM keyset covers every Chicago store",
   ks is not None and {"B-117", "B-418", "B-1800", "B-4712", "B-2612"} <= ks, f"{ks!r}")
ok("...and excludes the NYC store", ks is not None and "B-509" not in ks)
ok("admin (scope 'all') -> UNRESTRICTED", keys(client(), ORG, ADMIN, {"role": "admin"})[0] is None)

print("\nF/G. roster_visible() — the caller, and the unassigned, always survive")
ROWS = [{"employee_id": "E253", "name": "Ennio Rodas", "home_store": "B-117"},
        {"employee_id": "E011", "name": "Abdul Kakar", "home_store": "B-117"},
        {"employee_id": "E266", "name": "Satish Onteru", "home_store": ""},      # UNASSIGNED
        {"employee_id": "E267", "name": "Miguel Ospina", "home_store": None},    # UNASSIGNED
        {"employee_id": "E021", "name": "A Ramos", "home_store": "B-509"}]       # another store
vis = S.roster_visible(ROWS, {"B-117"}, my_employee_id="E253")
names = {r["name"] for r in vis}
ok("own-store colleagues are pickable", {"Ennio Rodas", "Abdul Kakar"} <= names)
ok("blank home_store kept (not measured, not 'no store')", "Satish Onteru" in names)
ok("None home_store kept", "Miguel Ospina" in names)
ok("NEGATIVE CONTROL: a different store's employee is excluded", "A Ramos" not in names, names)
borrowed = S.roster_visible(ROWS, {"B-999"}, my_employee_id="E253")
ok("the CALLER survives a keyset that excludes their own home store",
   {r["employee_id"] for r in borrowed} >= {"E253"})
ok("...without dragging their colleague in",
   "Abdul Kakar" not in {r["name"] for r in borrowed})
ok("keyset None -> untouched list", len(S.roster_visible(ROWS, None)) == len(ROWS))
ok("empty keyset still filters (only caller + unassigned)",
   {r["name"] for r in S.roster_visible(ROWS, set(), my_employee_id="E253")} ==
   {"Ennio Rodas", "Satish Onteru", "Miguel Ospina"})

print("\nH. Org scoping")
c = client()
keys(c, ORG, MGR, {"role": "district_manager", "employee_id": "E7", "market": "Chicago",
                   "store_code": None, "store_codes": None}, units=[])
reads = [r for r in c.log if r["table"] in ("stores", "store_mapping", "store_aliases")]
ok("every store/market read is org-filtered", reads and all(r["filters"].get("org_id") == ORG for r in reads),
   [r["filters"] for r in reads])
ks, _ = keys(client(), ORG, REP, {"role": "sales_rep", "employee_id": "E1", "market": None,
                                  "store_code": "B-117", "store_codes": None}, home="B-117")
ok("the OTHER tenant's same-code store address never enters the keyset",
   ks is not None and "ELSEWHERE" not in ks, f"{ks!r}")

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
