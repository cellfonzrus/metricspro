"""Offline proof harness — DM daily-envelope visibility on the pickup pages (OWNER BUG REPORT
2026-09-02, verbatim: "the cash pick up and the bill pay pick up for the district managers is not
showing the daily envelopes in their menu, whereas admin i can see all, there is no permission
where they are gated out also").

ROOT CAUSE (evidence-first, live LuxeLink org 854f6d7b… on 2026-09-02: 12 envelopes; DM E189 saw 0,
all 12 dropped by the MARKET filter, 0 dropped by her keyset — her span was fine): a scope-'market'
login's `app_users.market` is a COMMA-JOINED multi-market grant ("Chicago, NY" = Chicago AND NY —
`core.scope.login_grant_breakdown` has comma-split it since ruling #6). Both pickup pages
auto-apply that raw grant as the singular `market=` param (pickup/page.tsx + billpay-pickup/
page.tsx useEffect, scope==='market'), and GET /closing/pickups + /billpay-pickups compared it as
ONE exact casefolded string against each store's resolved market ("Chicago" or "NY") — so EVERY
resolved-market envelope was dropped; the deliberate blank-market-lenient bypass never fired
because every LuxeLink store resolves a market. Admin (scope 'all') gets no auto market → saw all.
PRE-EXISTING on the cash side (the exact-match predates 2026-09-02) and faithfully mirrored into
the new billpay page; NOT a regression from commits 34acb66/b4b503a (neither touched the market
compare). The DM dashboard/DM-Verify pages never broke because they send the CSV `markets=` param,
which `_resolve_market_filter` comma-splits — the decomposition the singular param lacked.

THE FIX (shared source, duplicate-check gate: REUSE the working pages' mechanism): both pickup
endpoints now resolve `market=` through the SAME `_resolve_market_filter` helper the rollup/
summary/submissions endpoints use, and that helper's singular arm now admits the comma-split
components ALONGSIDE the whole string — so "Chicago, NY" matches both markets, while a market
whose canonical name genuinely contains a comma still matches whole. Pure widening; the keyset
(span) gate, the blank-market leniency, and the empty-span fail-closed are all untouched.
/closing/recon (same auto-apply, same exact-match class) is refit to the same helper.

Run: `cd backend && python3 harness_pickup_market_span.py`
No live DB/network — same fake-Supabase-chain-client convention as harness_cash_pickup.py /
harness_billpay_pickup.py, driving the REAL `closing_pickups` / `billpay_pickups` functions with
the keyset stubbed per persona (admin / DM / rep) exactly as `scope_keyset` would return it.
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("PASS" if cond else "FAIL") + f"  {name}" + (f"  [{detail}]" if detail else ""))


HOUSE = "00000000-0000-0000-0000-000000000001"


class Q:
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def order(self, *a, **k): return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
        return True

    def execute(self):
        rows = self.s.setdefault(self.t, [])
        matched = [dict(r) for r in rows if self._match(r)]
        if self._limit is not None:
            matched = matched[: self._limit]
        return SimpleNamespace(data=matched)


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


import app.modules.closing.router as cr        # noqa: E402
import app.modules.storeops.router as sor      # noqa: E402


def wire(store, keyset):
    """Wire the fake DB AND pin the caller's span keyset — the exact value scope_keyset() would
    return: None = unrestricted (admin/'all'), a set of UPPER store keys = a manager's span,
    empty set = fail-closed (identified caller, no span)."""
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    cr._signed_envelope = lambda path: (f"signed://{path}" if path else None)
    sor.scope_keyset = lambda authorization, org_id=HOUSE: (None if keyset is None else set(keyset))
    return fake


# The LuxeLink shape in miniature: two real markets, every store resolves one (so the
# blank-market-lenient bypass CANNOT mask the bug, exactly like production on 2026-09-02).
DAY = "2026-09-02"


def org_store():
    return {
        "stores": [
            {"org_id": HOUSE, "store_code": "CHI1", "address": "1 Chicago Ave", "market": "Chicago", "is_active": True},
            {"org_id": HOUSE, "store_code": "CHI2", "address": "2 Chicago Ave", "market": "Chicago", "is_active": True},
            {"org_id": HOUSE, "store_code": "NY1", "address": "1 Brooklyn Ave", "market": "NY", "is_active": True},
        ],
        "daily_closing": [
            {"org_id": HOUSE, "close_date": DAY, "store_code": "CHI1", "employee_name": "Rep A",
             "store_cash": 100.0, "epay_cash": 0.0, "t_cash": 100.0, "epay_on_cash": 40.0,
             "epay_on_credit": 0.0, "envelope_picture": None},
            {"org_id": HOUSE, "close_date": DAY, "store_code": "CHI2", "employee_name": "Rep B",
             "store_cash": 200.0, "epay_cash": 0.0, "t_cash": 200.0, "epay_on_cash": 50.0,
             "epay_on_credit": 0.0, "envelope_picture": None},
            {"org_id": HOUSE, "close_date": DAY, "store_code": "NY1", "employee_name": "Rep C",
             "store_cash": 300.0, "epay_cash": 0.0, "t_cash": 300.0, "epay_on_cash": 60.0,
             "epay_on_credit": 0.0, "envelope_picture": None},
        ],
    }


ALL_KEYS = {"CHI1", "CHI2", "NY1", "1 CHICAGO AVE", "2 CHICAGO AVE", "1 BROOKLYN AVE"}
CHI_KEYS = {"CHI1", "CHI2", "1 CHICAGO AVE", "2 CHICAGO AVE"}


def codes(resp):
    return sorted(e["store_code"] for e in resp["envelopes"])


# ═══ 0. The pure resolver truth table (the shared fix itself) ══════════════════════════════════
r = cr._resolve_market_filter
check("0a. no filter at all -> None (nothing dropped)", r(None, None) is None and r("", "") is None)
check("0b. singular single market unchanged", r("Chicago", None) == {"chicago"})
check("0c. singular COMMA-JOINED GRANT now admits its components AND keeps the whole string",
      r("Chicago, NY", None) == {"chicago, ny", "chicago", "ny"})
check("0d. CSV markets= unchanged (already split)", r(None, "Chicago, NY") == {"chicago", "ny"})
check("0e. markets= still wins over market= when both sent", r("Chicago", "NY") == {"ny"})

# ═══ 1. THE BUG, reproduced + pinned fixed: multi-market DM on /closing/pickups ════════════════
# Old predicate (pre-fix): mk.casefold() != "chicago, ny" -> every resolved store dropped -> 0.
old = [s for s in ("Chicago", "Chicago", "NY") if s.casefold() == "Chicago, NY".casefold()]
check("1a. old exact-match semantics dropped ALL resolved-market envelopes (the reproduced 0/12)",
      len(old) == 0)
wire(org_store(), ALL_KEYS)   # janet: span = both markets' stores (RPC empty; login grants resolve)
resp = cr.closing_pickups(date=DAY, market="Chicago, NY", org_id=HOUSE)
check("1b. FIX: DM with multi-market grant 'Chicago, NY' (auto-applied verbatim) sees ALL 3 "
      "in-span envelopes", codes(resp) == ["CHI1", "CHI2", "NY1"], str(codes(resp)))

# ═══ 2. Span truth table on /closing/pickups ═══════════════════════════════════════════════════
wire(org_store(), None)       # admin / scope-'all': keyset None, frontend applies no auto market
resp = cr.closing_pickups(date=DAY, org_id=HOUSE)
check("2a. admin (scope 'all', no market) sees every envelope", codes(resp) == ["CHI1", "CHI2", "NY1"])

wire(org_store(), CHI_KEYS)   # luis: single-market DM, market='Chicago'
resp = cr.closing_pickups(date=DAY, market="Chicago", org_id=HOUSE)
check("2b. single-market DM sees exactly their market's envelopes", codes(resp) == ["CHI1", "CHI2"])

wire(org_store(), CHI_KEYS)   # keyset still gates even when the market filter would admit more
resp = cr.closing_pickups(date=DAY, market="Chicago, NY", org_id=HOUSE)
check("2c. KEYSET STILL GATES: a Chicago-span DM sending 'Chicago, NY' gets NO out-of-span NY row",
      codes(resp) == ["CHI1", "CHI2"], str(codes(resp)))

wire(org_store(), set())      # identified caller, empty span (e.g. rep-level / no grants)
resp = cr.closing_pickups(date=DAY, org_id=HOUSE)
check("2d. FAIL-CLOSED unchanged: empty span -> zero envelopes", codes(resp) == [])

# ═══ 3. Same truth table on /closing/billpay-pickups (the shared-source proof) ═════════════════
wire(org_store(), ALL_KEYS)
resp = cr.billpay_pickups(date=DAY, market="Chicago, NY", org_id=HOUSE)
check("3a. FIX mirrors: multi-market DM sees ALL 3 bill-pay envelopes", codes(resp) == ["CHI1", "CHI2", "NY1"],
      str(codes(resp)))
wire(org_store(), None)
resp = cr.billpay_pickups(date=DAY, org_id=HOUSE)
check("3b. admin sees all bill-pay envelopes", codes(resp) == ["CHI1", "CHI2", "NY1"])
wire(org_store(), CHI_KEYS)
resp = cr.billpay_pickups(date=DAY, market="Chicago, NY", org_id=HOUSE)
check("3c. keyset still gates the bill-pay mirror", codes(resp) == ["CHI1", "CHI2"])
wire(org_store(), set())
resp = cr.billpay_pickups(date=DAY, org_id=HOUSE)
check("3d. fail-closed unchanged on the bill-pay mirror", codes(resp) == [])

# ═══ 4. Leniency + literal-comma market preserved ══════════════════════════════════════════════
st = org_store()
st["stores"].append({"org_id": HOUSE, "store_code": "ORPH", "address": "9 Orphan Rd",
                     "market": "", "is_active": True})
st["daily_closing"].append({"org_id": HOUSE, "close_date": DAY, "store_code": "ORPH",
                            "employee_name": "Rep O", "store_cash": 50.0, "epay_cash": 0.0,
                            "t_cash": 50.0, "epay_on_cash": 10.0, "epay_on_credit": 0.0,
                            "envelope_picture": None})
wire(st, ALL_KEYS | {"ORPH", "9 ORPHAN RD"})
resp = cr.closing_pickups(date=DAY, market="Chicago, NY", org_id=HOUSE)
check("4a. blank-market envelope STILL bypasses the filter (cash-collection leniency untouched)",
      "ORPH" in codes(resp), str(codes(resp)))

st = org_store()
st["stores"] = [{"org_id": HOUSE, "store_code": "DFW", "address": "1 Dallas Rd",
                 "market": "Dallas, TX", "is_active": True}]
st["daily_closing"] = [{"org_id": HOUSE, "close_date": DAY, "store_code": "DFW",
                        "employee_name": "Rep D", "store_cash": 75.0, "epay_cash": 0.0,
                        "t_cash": 75.0, "epay_on_cash": 5.0, "epay_on_credit": 0.0,
                        "envelope_picture": None}]
wire(st, None)
resp = cr.closing_pickups(date=DAY, market="Dallas, TX", org_id=HOUSE)
check("4b. a market whose CANONICAL NAME contains a comma still matches whole (never narrowed)",
      codes(resp) == ["DFW"], str(codes(resp)))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
