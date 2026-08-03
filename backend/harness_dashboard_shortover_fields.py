"""Offline proof harness for retail-ops-24 (PACKAGE B, OWNER DIRECTIVE 2026-08-03 "Make one tile for
cash short and one tile for cash over"): `GET /closing/submissions` now surfaces two additive, structured
per-row fields — `cash_short_amount` / `cash_over_amount` — captured from the SAME `_money_issues` call
`closing_submissions` already makes to derive `gate_status`/`gate_reasons` (never a second/different
computation). These feed the two new dashboard tiles (frontend sums them over the currently-filtered
rows) and their drill-down (the same detail table, narrowed to rows where the relevant amount is > 0).

Same convention/fake-client as harness_closing_submissions.py: runs the REAL `closing_submissions`
function against a stateful fake Supabase client, monkeypatching only `_b2b_day`.

Run: `cd backend && python3 harness_dashboard_shortover_fields.py`

Proves:
  A. Exact match (declared == B2B) -> both amounts 0.0.
  B. Cash SHORT (declared < B2B, block) -> cash_short_amount = the block variance, cash_over_amount = 0.
  C. Cash OVER (declared > B2B, flag) -> cash_over_amount = the flag variance, cash_short_amount = 0.
  D. recon_pending (no B2B data) -> both amounts stay 0.0 (never a guessed number).
  E. A CREDIT-only issue (over/under) never populates either cash_* field (metric-scoped, not
     conflated with credit).
  F. Money secrecy: unauthenticated + DM (market-scope) callers get 0.0 for BOTH fields (same boundary
     as gate_reasons/b2b_cash) even though the row IS genuinely short/over; a company-wide caller sees
     the real amounts.
  G. Never netted: summing cash_short_amount and cash_over_amount independently across a mixed set of
     rows (some short, some over) gives the correct SEPARATE totals — short-total and over-total do not
     cancel each other out (this is the actual point of splitting the two tiles).
  H. Field is present at the exact key on every row (dashboard tile summation assumes `.cash_short_amount
     || 0` / `.cash_over_amount || 0` never throws on a missing key).
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
    def __init__(self, store, table):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
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
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store): self.store = store
    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name)


def fresh_store():
    return {"daily_closing": [], "daily_closing_verification": [], "stores": [], "app_users": [], "roles": []}


import app.modules.core.router as core            # noqa: E402
import app.modules.closing.router as cr            # noqa: E402

AUTH_NONE = ""
AUTH_GOOD = "Bearer good-token"


def wire(store):
    fake = FakeClient(store)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    core._uid_from_token = lambda a: None
    return fake


def role_row(org, name, perms):
    return {"id": nid("role"), "org_id": org, "name": name, "permissions": perms}


def as_dm(store):
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "market_manager", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "market_manager", {"scope": "market"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def as_company_wide(store):
    store["app_users"] = [{"id": nid(), "auth_id": "uid-1", "org_id": HOUSE, "role": "admin", "super_admin": False}]
    store["roles"] = [role_row(HOUSE, "admin", {"scope": "all"})]
    core._uid_from_token = lambda a: ("uid-1" if a == AUTH_GOOD else None)


def base_row(**kw):
    r = {"org_id": HOUSE, "close_date": "2026-07-15", "period": "2026-07",
         "submitted_at": "2026-07-15T20:00:00Z", "store_code": "S1", "store_address": "1 Main St",
         "store_name": "1 Main St", "employee_name": "Jane Rep", "source": "manual",
         "t_cash": 100.0, "t_credit": 50.0, "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0,
         "t_zelle": 0.0, "t_acima": 0.0, "acc_sale": 25.0, "epay_on_cash": 0.0, "epay_on_credit": 0.0,
         "epay_on_acima": 0.0, "upgrade_count": 1, "new_line_count": 2, "postpaid_count": 0,
         "expense_amount": 0.0, "expense_description": None, "expense_approved": False,
         "attempts": 1, "auto_accepted": False, "mgmt_flag": False, "released_at": None,
         "released_by": None, "correction_count": 0, "envelope_picture": None,
         "remarks": "", "tenders": None, "counts": None}
    r.update(kw)
    return r


def fake_b2b_day(client, org_id, date):
    days = {
        # exact match
        "2026-07-01": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 100.0, "card": 50.0, "acc_gross": 0, "total": 150.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        # cash SHORT: declared 100 vs b2b 150 -> block, variance -50
        "2026-07-02": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 150.0, "card": 50.0, "acc_gross": 0, "total": 200.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        # cash OVER: declared 100 vs b2b 40 -> flag, variance +60
        "2026-07-03": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 40.0, "card": 50.0, "acc_gross": 0, "total": 90.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
        # no B2B data -> recon_pending
        "2026-07-04": {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}},
        # CREDIT over only (cash exact) -> should not touch cash_short/cash_over
        "2026-07-05": {"has_data": True, "by_store": {}, "by_rep": {("S1", "jane rep"): {"cash": 100.0, "card": 20.0, "acc_gross": 0, "total": 120.0, "salesperson": "Jane Rep", "tenders_available": True}}, "counts": {}},
    }
    return days.get(date, {"has_data": False, "by_store": {}, "by_rep": {}, "counts": {}})


st = fresh_store(); wire(st)
cr._b2b_day = fake_b2b_day
st["daily_closing"] = [
    base_row(id="exact", close_date="2026-07-01", t_cash=100.0, t_credit=50.0),
    base_row(id="short", close_date="2026-07-02", t_cash=100.0, t_credit=50.0),
    base_row(id="over", close_date="2026-07-03", t_cash=100.0, t_credit=50.0),
    base_row(id="pending", close_date="2026-07-04", t_cash=100.0, t_credit=50.0),
    base_row(id="credit_over_only", close_date="2026-07-05", t_cash=100.0, t_credit=50.0),
]
resp = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-05", authorization=AUTH_NONE, org_id=HOUSE)
by_id = {r["id"]: r for r in resp["rows"]}

as_company_wide(st)
resp_admin = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-05", authorization=AUTH_GOOD, org_id=HOUSE)
admin_by_id = {r["id"]: r for r in resp_admin["rows"]}

# ═══════════════════════════ A. Exact match -> both 0 ═══════════════════════════
check("A1. exact match: cash_short_amount == 0.0", admin_by_id["exact"]["cash_short_amount"] == 0.0)
check("A2. exact match: cash_over_amount == 0.0", admin_by_id["exact"]["cash_over_amount"] == 0.0)

# ═══════════════════════════ B. Cash SHORT (company-wide caller — real amounts visible) ═════════
check("B1. short row: gate_status blocked", admin_by_id["short"]["gate_status"] == "blocked", admin_by_id["short"]["gate_status"])
check("B2. short row: cash_short_amount == 50.0 (declared 100 vs b2b 150)",
      admin_by_id["short"]["cash_short_amount"] == 50.0, str(admin_by_id["short"]["cash_short_amount"]))
check("B3. short row: cash_over_amount stays 0.0", admin_by_id["short"]["cash_over_amount"] == 0.0)

# ═══════════════════════════ C. Cash OVER (company-wide caller) ═════════════════════════════════
check("C1. over row: gate_status flagged", admin_by_id["over"]["gate_status"] == "flagged", admin_by_id["over"]["gate_status"])
check("C2. over row: cash_over_amount == 60.0 (declared 100 vs b2b 40)",
      admin_by_id["over"]["cash_over_amount"] == 60.0, str(admin_by_id["over"]["cash_over_amount"]))
check("C3. over row: cash_short_amount stays 0.0", admin_by_id["over"]["cash_short_amount"] == 0.0)

# ═══════════════════════════ D. recon_pending -> both 0, never guessed ══════════
check("D1. pending row: gate_status recon_pending", admin_by_id["pending"]["gate_status"] == "recon_pending")
check("D2. pending row: cash_short_amount == 0.0 (no data, never a fabricated number)",
      admin_by_id["pending"]["cash_short_amount"] == 0.0)
check("D3. pending row: cash_over_amount == 0.0", admin_by_id["pending"]["cash_over_amount"] == 0.0)

# ═══════════════════════════ E. Credit-only issue never touches cash_* fields ═══
check("E1. credit-over-only row: gate_status blocked (credit over = block)",
      admin_by_id["credit_over_only"]["gate_status"] == "blocked", admin_by_id["credit_over_only"]["gate_status"])
check("E2. credit-over-only row: cash_short_amount stays 0.0 (cash matched exactly)",
      admin_by_id["credit_over_only"]["cash_short_amount"] == 0.0)
check("E3. credit-over-only row: cash_over_amount stays 0.0 (metric-scoped, not conflated with credit)",
      admin_by_id["credit_over_only"]["cash_over_amount"] == 0.0)

# ═══════════════════════════ F. Money-secrecy boundary ══════════════════════════
check("F1. unauthenticated caller: short row's cash_short_amount hidden (0.0), even though it's really short",
      by_id["short"]["cash_short_amount"] == 0.0 and resp["can_review"] is False)

as_dm(st)
resp_dm = cr.closing_submissions(date_from="2026-07-01", date_to="2026-07-05", authorization=AUTH_GOOD, org_id=HOUSE)
dm_short = {r["id"]: r for r in resp_dm["rows"]}["short"]
check("F2. DM (market-scope) caller ALSO gets 0.0 for both fields",
      dm_short["cash_short_amount"] == 0.0 and dm_short["cash_over_amount"] == 0.0 and resp_dm["can_review"] is False)

check("F3. company-wide caller sees the REAL cash_short_amount (50.0)",
      admin_by_id["short"]["cash_short_amount"] == 50.0 and resp_admin["can_review"] is True,
      str(admin_by_id["short"]))
check("F4. company-wide caller sees the REAL cash_over_amount (60.0)",
      admin_by_id["over"]["cash_over_amount"] == 60.0, str(admin_by_id["over"]))

# ═══════════════════════════ G. Never netted — sums stay separate ═══════════════
total_short = round(sum(r.get("cash_short_amount") or 0.0 for r in admin_by_id.values()), 2)
total_over = round(sum(r.get("cash_over_amount") or 0.0 for r in admin_by_id.values()), 2)
check("G1. total short across all rows == 50.0 (only the short row contributes)", total_short == 50.0, str(total_short))
check("G2. total over across all rows == 60.0 (only the over row contributes) — NOT netted against the "
      "50.0 short into some combined 10.0 or -10.0 figure", total_over == 60.0, str(total_over))
check("G3. short-total and over-total are independent (both non-zero simultaneously)",
      total_short > 0 and total_over > 0 and total_short != total_over)

# ═══════════════════════════ H. Field always present (never a missing key) ══════
check("H1. every row (all 5 statuses) carries both keys",
      all("cash_short_amount" in r and "cash_over_amount" in r for r in resp_admin["rows"]))

cr._b2b_day = cr._b2b_day  # no-op restore marker (module-level monkeypatch is harness-local; fine as-is)

# ── Summary ──────────────────────────────────────────────────────────────────────────────────────
print(f"\n{len(PASS)}/{len(PASS) + len(FAIL)} checks passed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
