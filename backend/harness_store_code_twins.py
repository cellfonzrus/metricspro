"""Harness — GET /closing/stores must offer ONE option per physical store, and every internal
sibling call must carry the caller's bearer token.

Reproduces BOTH live defects first, then proves the fix:

  1. STORE TWINS. luxelink's commcalc.store_mapping carries 39 rows for 20 addresses — a 2026-08-05
     bulk insert added 19 structured 'LUX-*' codes for stores already keyed '957' / 'Cicero' / 'QV'.
     closing_stores keyed purely on store_code, so the picker listed every store TWICE. 29 closings /
     $9,413.16 landed on the twin nobody else reads.

  2. HEADER-LESS SELF-CALLS. tenant_middleware 401s an unauthenticated request before it reaches the
     handler, so closing's sibling calls (payout/accrued, salary-owed, payout/record,
     salary-advance/record, expenses system-line) all failed — surfacing as $0, not as an error.

Read-only: the fake client raises on every write verb.
"""
import sys, types

sys.path.insert(0, __import__("os").path.dirname(__file__))

PASS, FAIL = [], []


def ok(cond, what):
    (PASS if cond else FAIL).append(what)
    print(("  PASS " if cond else "  FAIL ") + what)


# ── fake supabase ──────────────────────────────────────────────────────────────────────────────────
class _Q:
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink
        self._filters = []

    def select(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def execute(self):
        rows = self._rows
        for col, val in self._filters:
            rows = [r for r in rows if r.get(col) == val]
        self._sink.append(list(self._filters))
        return types.SimpleNamespace(data=rows)

    def _write(self, *a, **k):
        raise AssertionError("harness is READ-ONLY — a write verb was called")

    insert = update = upsert = delete = _write


class _Schema:
    def __init__(self, tables, sink, raise_on=()):
        self._t, self._sink, self._raise_on = tables, sink, raise_on

    def table(self, name):
        if name in self._raise_on:
            raise RuntimeError(f"simulated: relation {name} does not exist")
        return _Q(list(self._t.get(name, [])), self._sink)


class FakeClient:
    def __init__(self, tables, raise_on=()):
        self.tables, self.filters, self._raise_on = tables, [], raise_on

    def schema(self, name):
        return _Schema(self.tables.get(name, {}), self.filters, self._raise_on)


ORG = "854f6d7b-6590-4e4d-88ab-646f560d4f4c"
OTHER = "00000000-0000-0000-0000-000000000001"


def luxelink_tables(with_aliases=True, with_twins=True):
    """The REAL shape, measured from prod 2026-08-11 (trimmed to 4 stores + 1 twin-free control)."""
    sm = [
        {"org_id": ORG, "store_code": "957", "store_address": "957 Pennsylvania Avenue", "salesforce_id": None},
        {"org_id": ORG, "store_code": "Cicero", "store_address": "2317 S Cicero Ave STE A", "salesforce_id": None},
        {"org_id": ORG, "store_code": "QV", "store_address": "218-80 Hempstead Avenue", "salesforce_id": None},
        {"org_id": ORG, "store_code": "Utica", "store_address": "531 Utica Ave", "salesforce_id": None},
    ]
    if with_twins:
        sm += [
            {"org_id": ORG, "store_code": "LUX-NY-PENN", "store_address": "957 Pennsylvania Avenue", "salesforce_id": None},
            {"org_id": ORG, "store_code": "LUX-CHI-CICERO", "store_address": "2317 S Cicero Ave STE A", "salesforce_id": None},
            {"org_id": ORG, "store_code": "LUX-NY-HEMPSTEAD", "store_address": "218-80 Hempstead Avenue", "salesforce_id": None},
            {"org_id": ORG, "store_code": "LUX-NY-UTICA", "store_address": "531 Utica Ave", "salesforce_id": None},
        ]
    sm.append({"org_id": OTHER, "store_code": "OTHER-1", "store_address": "1 Other St", "salesforce_id": None})
    stores = [
        {"org_id": ORG, "store_code": "957", "address": "957 Pennsylvania Ave", "market": "NY"},
        {"org_id": ORG, "store_code": "Cicero", "address": "2317 S Cicero Ave STE A", "market": "Chicago"},
        {"org_id": ORG, "store_code": "QV", "address": "218-80 Hempstead Avenue", "market": "NY"},
        {"org_id": ORG, "store_code": "Utica", "address": "531 Utica Ave", "market": "NY"},
        {"org_id": OTHER, "store_code": "OTHER-1", "address": "1 Other St", "market": "X"},
    ]
    aliases = []
    if with_aliases:
        aliases = [{"org_id": ORG, "alias": "957 Pennsylvania Avenue", "store_code": "957"}]
    return {"commcalc": {"store_mapping": sm, "store_aliases": aliases},
            "storeops": {"stores": stores}}


import app.modules.closing.router as R  # noqa: E402


def call_stores(tables, raise_on=()):
    fake = FakeClient(tables, raise_on=raise_on)
    R.sb = lambda: fake
    return R.closing_stores(org_id=ORG), fake


print("\n§1 · THE BUG: twins produce two options for one store")
opts, _ = call_stores(luxelink_tables())
codes = sorted(o["store_code"] for o in opts)
ok(len(opts) == 4, f"4 physical stores -> 4 options (got {len(opts)}: {codes})")
ok(not any(c.startswith("LUX-") for c in codes),
   "no LUX-* twin is offered as its own store")
penn = [o for o in opts if o["store_address"] == "957 Pennsylvania Avenue"]
ok(len(penn) == 1, "957 Pennsylvania Avenue appears exactly once")
ok(penn and penn[0]["store_code"] == "957",
   f"the survivor is the STORE MASTER's code '957', not the twin (got {penn[0]['store_code'] if penn else None})")
ok(penn and penn[0].get("aliases") == ["LUX-NY-PENN"],
   "the absorbed spelling is reported in `aliases`, not silently dropped")
ok(penn and penn[0]["market"] == "NY", "market survives the collapse")

print("\n§2 · NEGATIVE CONTROL: without twins the output is unchanged")
opts_clean, _ = call_stores(luxelink_tables(with_twins=False))
ok(len(opts_clean) == 4, "4 stores -> 4 options")
ok(all("aliases" not in o for o in opts_clean),
   "nothing collapsed ⇒ no `aliases` key added (byte-identical shape for a clean tenant)")

print("\n§3 · TENANT ISOLATION (RULE ONE)")
ok(all(o["store_code"] != "OTHER-1" for o in opts),
   "the other tenant's store never appears")
_, fake = call_stores(luxelink_tables())
ok(all(any(c == "org_id" and v == ORG for c, v in f) for f in fake.filters),
   f"every read filtered on org_id ({len(fake.filters)} queries)")

print("\n§4 · TWO REAL STORES AT ONE ADDRESS ARE NOT COLLAPSED")
t = luxelink_tables(with_twins=False)
t["commcalc"]["store_mapping"].append(
    {"org_id": ORG, "store_code": "957-B", "store_address": "957 Pennsylvania Avenue", "salesforce_id": None})
t["storeops"]["stores"].append(
    {"org_id": ORG, "store_code": "957-B", "address": "957 Pennsylvania Ave", "market": "NY"})
opts2, _ = call_stores(t)
both = sorted(o["store_code"] for o in opts2 if o["store_address"] == "957 Pennsylvania Avenue")
ok(both == ["957", "957-B"],
   f"both master-known codes survive a shared address (got {both}) — a suite split is not a twin")

print("\n§5 · EXPLICIT ALIAS COLLAPSES EVEN WHEN ADDRESSES DIFFER")
t = luxelink_tables(with_twins=False)
t["commcalc"]["store_mapping"].append(
    {"org_id": ORG, "store_code": "PENN-OLD", "store_address": "957 Penn Av (old spelling)", "salesforce_id": None})
t["commcalc"]["store_aliases"].append({"org_id": ORG, "alias": "PENN-OLD", "store_code": "957"})
opts3, _ = call_stores(t)
ok(all(o["store_code"] != "PENN-OLD" for o in opts3),
   "an admin-confirmed alias never gets its own option, even with a different address string")
penn3 = [o for o in opts3 if o["store_code"] == "957"]
ok(penn3 and "PENN-OLD" in (penn3[0].get("aliases") or []),
   "the aliased spelling rides along on the survivor")

print("\n§6 · DEGRADES, NEVER 500s (store_aliases missing entirely)")
try:
    opts4, _ = call_stores(luxelink_tables(), raise_on=("store_aliases",))
    ok(len(opts4) == 4, "a missing store_aliases table still collapses by address (4 options)")
except Exception as e:
    ok(False, f"raised instead of degrading: {e}")

print("\n§7 · SIBLING CALLS FORWARD THE CALLER'S BEARER TOKEN")
sent = {}


class _Resp:
    def __init__(self, code=200, payload=None):
        self.status_code, self._p, self.content = code, (payload or {}), b"{}"

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTPError: {self.status_code}")


def _cap(verb):
    def f(url, **kw):
        sent[verb] = {"url": url, "headers": kw.get("headers") or {}}
        return _Resp(200, {"employees": []})
    return f


R.requests = types.SimpleNamespace(get=_cap("get"), post=_cap("post"))

TOKEN = "Bearer eyJhbGciOiJIUzI1NiJ9.dm-token.sig"
R._get_commission_accrued(ORG, "2026-08-10", store_code="957", authorization=TOKEN)
ok(sent["get"]["headers"].get("Authorization") == TOKEN,
   "payout/accrued carries the caller's Authorization header")
R._get_salary_owed(ORG, "2026-06-11", "2026-08-10", store_code="957", authorization=TOKEN)
ok(sent["get"]["headers"].get("Authorization") == TOKEN,
   "salary-owed carries the caller's Authorization header")
R._post_commission_payout(ORG, "E1", 10.0, "2026-08-11", "957", "w1", "dm", authorization=TOKEN)
ok(sent["post"]["headers"].get("Authorization") == TOKEN,
   "payout/record carries the caller's Authorization header")
R._post_salary_advance(ORG, "E1", 10.0, "2026-08-11", "957", "w1", "dm", authorization=TOKEN)
ok(sent["post"]["headers"].get("Authorization") == TOKEN,
   "salary-advance/record carries the caller's Authorization header")
R._push_expense_category_pl(FakeClient({"commcalc": {"closing_expense": []}}), ORG, "2026-08", "c1", "Rent",
                            authorization=TOKEN)
ok(sent["post"]["headers"].get("Authorization") == TOKEN,
   "expenses system-line carries the caller's Authorization header")

print("\n§8 · A 401 IS REPORTED AS UNKNOWN, NEVER AS $0")
R.requests = types.SimpleNamespace(get=lambda url, **kw: _Resp(401), post=lambda url, **kw: _Resp(401))
data, err = R._get_commission_accrued(ORG, "2026-08-10", authorization="")
ok(data is None and err and "401" in err, f"401 surfaces the status in the note ({err!r})")
ok(err and "UNKNOWN" in err, "the note says the figure is UNKNOWN, not zero")
res = R._post_commission_payout(ORG, "E1", 10.0, "2026-08-11", "957", "w1", "dm")
ok(res["posted"] is False and "reconcile before re-paying" in res["note"],
   "a refused payout POST warns that cash left the envelope unrecorded (double-pay exposure)")
res = R._push_expense_category_pl(FakeClient({"commcalc": {"closing_expense": []}}), ORG, "2026-08", "c1", "Rent")
ok(res["pushed"] is False and "NOT on the P&L" in res["note"],
   "a refused system-line push says the category is missing from the P&L")

print("\n§9 · NO TOKEN ⇒ NO HEADER (never a forged/empty Authorization)")
ok(R._sib_headers("") == {} and R._sib_headers(None) == {},
   "an absent token sends no Authorization header at all")
ok(R._sib_headers(TOKEN) == {"Authorization": TOKEN}, "a present token is forwarded verbatim")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
sys.exit(1 if FAIL else 0)
