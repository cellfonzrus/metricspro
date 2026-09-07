"""Offline proof harness for the Envelope Expense Management + Envelope Payouts (EEP) package —
retail-ops lead (migs 506/507). Spec: /workspaces/commcalc/docs/specs/envelope-expense-payout.md.

Same stateful-fake-Supabase-client convention as harness_closing_hardening.py /
harness_closing_reports_span_scope.py — runs the REAL router functions, no live DB/network.
`requests.get`/`requests.post` (the cross-module sibling calls to mod-commission/mod-people, which are
being built in parallel in sibling worktrees and may not exist yet) are monkeypatched to canned
responses/404s so this harness is self-contained.

Run: `cd backend && python3 harness_eep_retail_ops.py`

Proves:
  A. expense_config.load_categories lazy-seeds the 5 presets on first read (and persists them) —
     matches the spec's Salary/Commission/Petty/Office/Supplies list + kinds exactly.
  B. _validate_expense_line enforces: known category, amount>0, description required,
     employee required for payroll/commission-kind categories (not for expense-kind).
  C. POST /closing/row wiring: a valid `expense_lines` payload inserts commcalc.closing_expense rows
     tied to the new row's id; an INVALID line (e.g. missing description) rejects the WHOLE submit
     with nothing written to either table (all-or-nothing, no partial money loss).
  D. Netting is byte-identical to today when closing_expense/envelope_withdrawal are empty (pre-
     migration / no data), and correctly nets APPROVED expenses + withdrawals when present, across
     all three surfaces the spec names: _bank_deposit_declared, GET /closing/pickups (ready_cash),
     GET /closing/cash-position (cash_on_hand). bill_payment_cash target is NOT netted (separate ePay
     recon leg, not the physical envelope).
  E. Money doctrine: approving an 'expense'-kind category line triggers a P&L system-line push;
     approving a 'payroll' or 'commission'-kind line NEVER does (cash advance, not P&L).
  F. GET /closing/payout-due merges commission-accrued + salary-owed + approved-unpaid expenses,
     respecting cadence_due gating, and degrades to an empty section + note when a sibling 404s.
  G. GET /closing/envelope-plan picks the fewest envelopes to cover payout-due, using REAL netted
     `available` figures (not synthetic ones) — cross-checks against the already-proven
     select_envelopes algorithm (scratchpad/prove_envelope.py).
  H. POST /closing/envelope-withdrawal writes the ledger row, marks a linked expense line paid, and
     (best-effort) calls the sibling payout endpoints — captured, never raised, on a 404.
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


# ── stateful fake supabase client (copied convention) ───────────────────────────────────────────────
class Q:
    def __init__(self, store, table, poison_writes=False):
        self.s, self.t = store, table
        self.op, self.payload = "select", None
        self.filters = []
        self._limit = None
        self._order = None
        self._poison = poison_writes

    def select(self, *a, **k): self.op = "select"; return self
    def insert(self, rows, **k): self.op = "insert"; self.payload = rows; return self
    def update(self, patch, **k): self.op = "update"; self.payload = patch; return self
    def upsert(self, rows, **k): self.op = "upsert"; self.payload = rows; return self
    def delete(self, **k): self.op = "delete"; return self
    def eq(self, c, v): self.filters.append((c, "eq", v)); return self
    def in_(self, c, v): self.filters.append((c, "in", list(v))); return self
    def gte(self, c, v): self.filters.append((c, "gte", v)); return self
    def lte(self, c, v): self.filters.append((c, "lte", v)); return self
    def is_(self, c, v): self.filters.append((c, "is", v)); return self
    def ilike(self, c, v): self.filters.append((c, "ilike", v)); return self
    def order(self, col, desc=False, **k): self._order = (col, desc); return self
    def limit(self, n, *a, **k): self._limit = n; return self

    def _match(self, row):
        for c, kind, v in self.filters:
            rv = row.get(c)
            if kind == "eq" and rv != v: return False
            if kind == "in" and rv not in v: return False
            if kind == "gte" and not (rv is not None and str(rv) >= str(v)): return False
            if kind == "lte" and not (rv is not None and str(rv) <= str(v)): return False
            if kind == "is" and v == "null" and rv is not None: return False
            if kind == "ilike" and str(rv or "").lower() != str(v or "").lower(): return False
        return True

    def execute(self):
        if self._poison and self.op in ("insert", "update", "delete", "upsert"):
            raise AssertionError(f"UNEXPECTED WRITE ({self.op}) on {self.t}")
        rows = self.s.setdefault(self.t, [])
        if self.op == "select":
            matched = [dict(r) for r in rows if self._match(r)]
            if self._order:
                col, desc = self._order
                matched.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
            if self._limit is not None:
                matched = matched[: self._limit]
            return SimpleNamespace(data=matched)
        if self.op in ("insert", "upsert"):
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            out = []
            for r in payload:
                r = dict(r)
                if self.op == "upsert" and r.get("id"):
                    existing = next((x for x in rows if x.get("id") == r["id"]), None)
                    if existing:
                        existing.update(r); out.append(dict(existing)); continue
                r.setdefault("id", nid(self.t))
                rows.append(r); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "update":
            out = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload); out.append(dict(r))
            return SimpleNamespace(data=out)
        if self.op == "delete":
            keep = [r for r in rows if not self._match(r)]
            deleted = [r for r in rows if self._match(r)]
            self.s[self.t] = keep
            return SimpleNamespace(data=deleted)
        return SimpleNamespace(data=[])


class FakeClient:
    def __init__(self, store, poison_writes=False):
        self.store = store
        self.poison_writes = poison_writes

    def schema(self, _n): return self
    def table(self, name): return Q(self.store, name, poison_writes=self.poison_writes)


def fresh_store():
    return {"daily_closing": [], "stores": [], "closing_expense_category": [], "closing_expense": [],
            "envelope_withdrawal": [], "envelope_payout_config": [], "tenants": [], "cash_pickup": [],
            "bank_deposit": []}


import app.modules.core.router as core                # noqa: E402
import app.modules.storeops.router as storeops         # noqa: E402
import app.modules.closing.router as cr                # noqa: E402
from app.modules.closing import expense_config         # noqa: E402


def _body(model, d):
    """Build the request model FastAPI hands the handler, instead of a plain dict.

    These endpoints were migrated from `body: dict` to a declared pydantic model, so the handler
    reads `body.<field>`. A probe passing a dict dies with AttributeError BEFORE reaching the logic
    under test — the harness then reads as "failing" while proving nothing. `model_validate`
    reproduces FastAPI's own call shape, including which fields count as explicitly set
    (`model_fields_set`), which several handlers branch on.
    """
    return model.model_validate(d)


def wire(store, poison_writes=False, unrestricted_span=True, manager=True):
    fake = FakeClient(store, poison_writes=poison_writes)
    cr.sb = lambda: fake
    cr.get_supabase = lambda: fake
    core.get_supabase = lambda: fake
    if unrestricted_span:
        storeops.scope_keyset = lambda auth, org: None
    if manager:
        cr._caller_perms = lambda client, auth: {"__super_admin": True, "__resolved": True}
        cr._caller_email = lambda client, auth: "dm@test.com"
    return fake


# ── fake requests (sibling HTTP calls) ───────────────────────────────────────────────────────────────
class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.content = b"1" if json_data is not None else b""

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 404:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeRequests:
    """Route by URL substring -> canned response. `calls` records every call for assertions."""
    def __init__(self):
        self.routes = {}   # substring -> FakeResp
        self.calls = []

    def route(self, substr, resp):
        self.routes[substr] = resp

    def _resolve(self, url):
        for substr, resp in self.routes.items():
            if substr in url:
                return resp
        return FakeResp(404)

    # **kw, not a fixed signature. The real caller now sends `headers=_sib_headers(authorization)`
    # on the internal system-line push; a double that only accepted (url, params, json, timeout)
    # raised TypeError inside the push's own try/except, which swallowed it as
    # {"pushed": False, "note": "push failed (TypeError: ...)"}. The P&L-push assertions then failed
    # while the product was fine — and worse, they would have kept "failing correctly" if the push
    # ever genuinely broke, so the signal was useless in both directions.
    def get(self, url, params=None, timeout=None, **kw):
        self.calls.append(("GET", url, params, None, kw))
        return self._resolve(url)

    def post(self, url, params=None, json=None, timeout=None, **kw):
        self.calls.append(("POST", url, params, json, kw))
        return self._resolve(url)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# A. expense_config lazy-seed
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("== A. expense_config lazy-seed ==")
store = fresh_store()
fake = wire(store)
cats = expense_config.load_categories(fake, HOUSE)
check("seeds exactly 5 presets", len(cats) == 5, str(len(cats)))
names = {c["name"] for c in cats}
check("preset names match the spec exactly",
      names == {"Salary", "Commission", "Petty Expenses", "Office Expenses", "Supplies"}, str(names))
kinds = {c["name"]: c["kind"] for c in cats}
check("Salary is payroll-kind", kinds.get("Salary") == "payroll")
check("Commission is commission-kind", kinds.get("Commission") == "commission")
check("Petty/Office/Supplies are expense-kind",
      all(kinds.get(n) == "expense" for n in ("Petty Expenses", "Office Expenses", "Supplies")))
check("persisted to the fake table (not just returned)", len(store["closing_expense_category"]) == 5)
cats2 = expense_config.load_categories(fake, HOUSE)
check("second read does NOT re-seed (no duplicate rows)", len(store["closing_expense_category"]) == 5)

# Degrade pre-migration: table select raises -> coded defaults, never crashes.
class PoisonSelectClient(FakeClient):
    def table(self, name):
        if name == expense_config.TABLE:
            class Boom:
                def select(self, *a, **k): raise Exception("relation does not exist")
            return Boom()
        return super().table(name)

cats3 = expense_config.load_categories(PoisonSelectClient(fresh_store()), HOUSE)
check("pre-migration degrade: still returns 5 coded-default categories", len(cats3) == 5)
check("pre-migration degrade: source='default'", all(c.get("source") == "default" for c in cats3))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# B. _validate_expense_line
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== B. _validate_expense_line ==")
store = fresh_store()
fake = wire(store)
cats = expense_config.load_categories(fake, HOUSE)
salary_id = next(c["id"] for c in cats if c["name"] == "Salary")
petty_id = next(c["id"] for c in cats if c["name"] == "Petty Expenses")

try:
    cr._validate_expense_line(fake, HOUSE, {"category_id": "nope", "amount": 10, "description": "x"})
    check("unknown category rejected", False)
except Exception as e:
    check("unknown category rejected", "HTTPException" in type(e).__name__ or getattr(e, "status_code", None) == 400)

try:
    cr._validate_expense_line(fake, HOUSE, {"category_id": petty_id, "amount": 0, "description": "x"})
    check("zero amount rejected", False)
except Exception:
    check("zero amount rejected", True)

try:
    cr._validate_expense_line(fake, HOUSE, {"category_id": petty_id, "amount": 10})
    check("missing description rejected", False)
except Exception:
    check("missing description rejected", True)

try:
    cr._validate_expense_line(fake, HOUSE, {"category_id": salary_id, "amount": 100, "description": "wk pay"})
    check("payroll-kind without employee_id rejected", False)
except Exception:
    check("payroll-kind without employee_id rejected", True)

clean = cr._validate_expense_line(fake, HOUSE, {"category_id": salary_id, "amount": 100,
                                                "description": "wk pay", "employee_id": "emp-1",
                                                "employee_name": "Jane Rep"})
check("payroll-kind WITH employee_id passes", clean["employee_id"] == "emp-1")
check("category kind/name snapshotted", clean["category_kind"] == "payroll" and clean["category_name"] == "Salary")

clean2 = cr._validate_expense_line(fake, HOUSE, {"category_id": petty_id, "amount": 12.5, "description": "tape"})
check("expense-kind without employee_id passes (not required)", clean2["employee_id"] is None)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# C. POST /closing/row wiring (expense_lines)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== C. POST /closing/row expense_lines wiring ==")


async def _submit(fake, payload):
    return await cr.create_row(payload, org_id=HOUSE)


import asyncio  # noqa: E402

store = fresh_store()
fake = wire(store)
cats = expense_config.load_categories(fake, HOUSE)
petty_id = next(c["id"] for c in cats if c["name"] == "Petty Expenses")
salary_id = next(c["id"] for c in cats if c["name"] == "Salary")

payload = {
    "close_date": "2026-08-01", "store_code": "S1", "store_name": "1 Main St",
    "employee_name": "Jane Rep", "t_cash": "500", "t_credit": "0",
    "expense_lines": [
        {"category_id": petty_id, "amount": 20, "description": "tape + labels"},
        {"category_id": salary_id, "amount": 50, "description": "advance", "employee_id": "emp-1",
         "employee_name": "Jane Rep"},
    ],
}
resp = asyncio.run(_submit(fake, payload))
check("submit accepted (no B2B loaded -> recon_pending, never blocks)", resp.get("accepted") is True)
check("2 expense lines inserted", len(resp.get("expense_lines") or []) == 2)
check("expense lines tied to the new row's closing_row_id",
      all(e.get("closing_row_id") == resp.get("id") for e in resp["expense_lines"]))
check("expense lines persisted to the fake table", len(store["closing_expense"]) == 2)
check("legacy expense_amount/description untouched (both 0/None on this submit)",
      resp.get("expense_amount") == 0 and resp.get("expense_description") is None)

# Bad line (missing description) -> whole submit rejected, nothing written anywhere.
store2 = fresh_store()
fake2 = wire(store2)
expense_config.load_categories(fake2, HOUSE)
bad_payload = dict(payload)
bad_payload["employee_name"] = "Bad Rep"
bad_payload["expense_lines"] = [{"category_id": petty_id, "amount": 20}]  # no description
try:
    asyncio.run(_submit(fake2, bad_payload))
    check("invalid expense line rejects the whole submit", False)
except Exception as e:
    check("invalid expense line rejects the whole submit",
          getattr(e, "status_code", None) == 400 or "400" in str(e))
check("all-or-nothing: no daily_closing row written on a rejected submit", len(store2["daily_closing"]) == 0)
check("all-or-nothing: no closing_expense row written on a rejected submit", len(store2["closing_expense"]) == 0)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# D. Netting — _bank_deposit_declared / pickups / cash-position
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== D. Netting (envelope math) ==")

store = fresh_store()
fake = wire(store)
store["daily_closing"].append({"id": "row-1", "org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01",
                               "t_cash": 500.0, "store_cash": 500.0, "epay_on_cash": 0.0, "epay_cash": 0.0,
                               "employee_name": "Jane Rep", "store_name": "1 Main St", "store_address": "1 Main St"})

# Pre-migration / no data: byte-identical to gross.
amt, n = cr._bank_deposit_declared(fake, HOUSE, "S1", "2026-08-01", "total_cash")
check("_bank_deposit_declared: no expenses/withdrawals -> byte-identical gross", amt == 500.0)

store["closing_expense"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01",
                                 "closing_row_id": "row-1", "amount": 40.0, "status": "approved"})
store["envelope_withdrawal"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01",
                                     "closing_row_id": "row-1", "amount": 60.0})
amt, n = cr._bank_deposit_declared(fake, HOUSE, "S1", "2026-08-01", "total_cash")
check("_bank_deposit_declared: total_cash nets expense+withdrawal", amt == 400.0, str(amt))

amt_bp, _ = cr._bank_deposit_declared(fake, HOUSE, "S1", "2026-08-01", "bill_payment_cash")
check("_bank_deposit_declared: bill_payment_cash target is NOT netted (separate ePay leg)", amt_bp == 0.0)

# A pending (not approved) line must NOT net.
store["closing_expense"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01",
                                 "closing_row_id": "row-1", "amount": 999.0, "status": "pending"})
amt, n = cr._bank_deposit_declared(fake, HOUSE, "S1", "2026-08-01", "total_cash")
check("_bank_deposit_declared: PENDING expense lines are NOT netted", amt == 400.0, str(amt))

# GET /closing/pickups — per-envelope ready_cash nets.
r = cr.closing_pickups(date="2026-08-01", org_id=HOUSE, authorization="")
env = next(e for e in r["envelopes"] if e["store_code"] == "S1")
check("GET /closing/pickups: per-envelope cash netted", env["cash"] == 400.0, str(env["cash"]))
check("GET /closing/pickups: ready_cash reflects the netted total", r["ready_cash"] == 400.0, str(r["ready_cash"]))

# GET /closing/cash-position — single-day cash_on_hand nets.
r = cr.cash_position(date="2026-08-01", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "S1")
check("GET /closing/cash-position: cash_on_hand netted", row["cash_on_hand"] == 400.0, str(row["cash_on_hand"]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# E. Money doctrine: expense-kind approval pushes P&L; payroll/commission-kind never does
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== E. Money doctrine (P&L push gating) ==")

store = fresh_store()
fake = wire(store)
cats = expense_config.load_categories(fake, HOUSE)
petty = next(c for c in cats if c["name"] == "Petty Expenses")
salary = next(c for c in cats if c["name"] == "Salary")

fr = FakeRequests()
cr.requests = fr
fr.route("/system-line", FakeResp(200, {"ok": True}))

exp_row = fake.table("closing_expense").insert({
    "org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1",
    "category_id": petty["id"], "category_kind": "expense", "category_name": "Petty Expenses",
    "amount": 33.0, "description": "batteries", "status": "pending"}).execute().data[0]
resp = cr.decide_expense_line(exp_row["id"], _body(cr.DecideExpenseLineIn, {"status": "approved"}), org_id=HOUSE, authorization="")
check("expense-kind approval succeeds", resp["status"] == "approved")
check("expense-kind approval triggers a P&L system-line push",
      resp["pl_push"] is not None and resp["pl_push"].get("pushed") is True)
pl_calls = [c for c in fr.calls if "system-line" in c[1]]
check("exactly one system-line push call made", len(pl_calls) == 1, str(len(pl_calls)))
check("push uses source_key closing_expense:<category-id>",
      pl_calls[0][3]["source_key"] == f"closing_expense:{petty['id']}")
check("push label == category name", pl_calls[0][3]["label"] == "Petty Expenses")

fr.calls.clear()
sal_row = fake.table("closing_expense").insert({
    "org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1",
    "category_id": salary["id"], "category_kind": "payroll", "category_name": "Salary",
    "amount": 100.0, "description": "advance", "employee_id": "emp-1", "status": "pending"}).execute().data[0]
resp2 = cr.decide_expense_line(sal_row["id"], _body(cr.DecideExpenseLineIn, {"status": "approved"}), org_id=HOUSE, authorization="")
check("payroll-kind approval succeeds", resp2["status"] == "approved")
check("payroll-kind approval NEVER triggers a P&L push (money doctrine)", resp2["pl_push"] is None)
check("no system-line HTTP call made for a payroll-kind approval",
      not any("system-line" in c[1] for c in fr.calls))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# F. GET /closing/payout-due
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== F. GET /closing/payout-due ==")

store = fresh_store()
fake = wire(store)
fr = FakeRequests()
cr.requests = fr
fr.route("/commcalc/payout/accrued", FakeResp(200, {
    "employees": [{"employee_key": "e1", "name": "Jane Rep", "unpaid_balance": 250.0, "store_codes": ["S1"]}],
    "as_of": "2026-08-04"}))
fr.route("/storeops/salary-owed", FakeResp(200, {
    "employees": [{"employee_id": "emp-1", "name": "Jane Rep", "balance": 80.0}]}))

r = cr.payout_due(store_code="S1", as_of="2026-08-04", org_id=HOUSE, authorization="")
check("commission_due picked up from the sibling (daily cadence default -> always due)",
      r["commission_due"] == 250.0, str(r["commission_due"]))
check("salary_due picked up from the sibling", r["salary_due"] == 80.0, str(r["salary_due"]))
check("total_cash_required sums all 3 legs", r["total_cash_required"] == 330.0, str(r["total_cash_required"]))
check("no error notes when both siblings answer", r["notes"] == [], str(r["notes"]))

# Weekly cadence gating: only due on the anchor weekday.
cr.put_envelope_config(_body(cr.PutEnvelopeConfigIn, {"take_commission": True, "take_salary": True, "take_expenses": True,
                        "commission_cadence": "weekly", "commission_anchor": 0,   # Monday
                        "salary_cadence": "weekly", "salary_anchor": 0}),
                       org_id=HOUSE, authorization="")
r2 = cr.payout_due(store_code="S1", as_of="2026-08-04", org_id=HOUSE, authorization="")  # Tuesday
check("weekly cadence NOT due off the anchor weekday -> commission_due 0", r2["commission_due"] == 0.0)
check("weekly cadence NOT due off the anchor weekday -> salary_due 0", r2["salary_due"] == 0.0)
r3 = cr.payout_due(store_code="S1", as_of="2026-08-03", org_id=HOUSE, authorization="")  # Monday
check("weekly cadence DUE on the anchor weekday", r3["commission_due"] == 250.0 and r3["salary_due"] == 80.0)

# Sibling 404 degrades gracefully (empty section + note, never a raise).
fr2 = FakeRequests()   # no routes registered -> everything 404s
cr.requests = fr2
r4 = cr.payout_due(store_code="S1", as_of="2026-08-03", org_id=HOUSE, authorization="")
check("sibling 404 -> commission_due degrades to 0, not a crash", r4["commission_due"] == 0.0)
check("sibling 404 -> a note is surfaced", any("commission" in n for n in r4["notes"]))
check("sibling 404 -> a note is surfaced for salary too", any("salary" in n for n in r4["notes"]))

# F2. mod-commission cross-module contract update (agent/commission/accrual-owner-answers, 2026-08-04):
# `due_now` (Q19 cycle-reset + Q14 auto-net-aware) wins over the legacy `unpaid_balance` when the
# sibling sends both; an older/degraded sibling response with only `unpaid_balance` still works
# (fallback, byte-identical to pre-contract-update behaviour).
store_f2 = fresh_store()
fake_f2 = wire(store_f2)
fr_f2 = FakeRequests()
cr.requests = fr_f2
fr_f2.route("/commcalc/payout/accrued", FakeResp(200, {
    "employees": [{"employee_key": "e1", "name": "Jane Rep", "unpaid_balance": 250.0, "due_now": 90.0,
                   "payable_field": "due_now", "consumer_note": "net of prior over-advance",
                   "store_codes": ["S1"]}],
    "as_of": "2026-08-04"}))
fr_f2.route("/storeops/salary-owed", FakeResp(404))
r_f2 = cr.payout_due(store_code="S1", as_of="2026-08-04", org_id=HOUSE, authorization="")
check("due_now present + less than unpaid_balance -> payout-due uses due_now (90, not 250)",
      r_f2["commission_due"] == 90.0, str(r_f2["commission_due"]))
check("due_now present -> the employee's OWN amount reflects due_now too",
      r_f2["commission_employees"][0]["amount"] == 90.0, str(r_f2["commission_employees"]))

fr_f2b = FakeRequests()
cr.requests = fr_f2b
fr_f2b.route("/commcalc/payout/accrued", FakeResp(200, {
    "employees": [{"employee_key": "e1", "name": "Jane Rep", "unpaid_balance": 250.0, "store_codes": ["S1"]}],
    "as_of": "2026-08-04"}))   # no due_now at all -> older/degraded sibling response
fr_f2b.route("/storeops/salary-owed", FakeResp(404))
r_f2b = cr.payout_due(store_code="S1", as_of="2026-08-04", org_id=HOUSE, authorization="")
check("due_now ABSENT -> falls back to unpaid_balance (250) unchanged", r_f2b["commission_due"] == 250.0, str(r_f2b["commission_due"]))


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# G. GET /closing/envelope-plan (fewest envelopes, real netted availability)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== G. GET /closing/envelope-plan ==")

store = fresh_store()
fake = wire(store)
fr = FakeRequests()
cr.requests = fr
fr.route("/commcalc/payout/accrued", FakeResp(404))
fr.route("/storeops/salary-owed", FakeResp(404))
store["daily_closing"] += [
    {"id": "d1", "org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-28", "t_cash": 300.0,
     "store_cash": 300.0, "employee_name": "A", "store_address": "1 Main St"},
    {"id": "d2", "org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-29", "t_cash": 250.0,
     "store_cash": 250.0, "employee_name": "B", "store_address": "1 Main St"},
    {"id": "d3", "org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-30", "t_cash": 100.0,
     "store_cash": 100.0, "employee_name": "C", "store_address": "1 Main St"},
]
# d1 partially drained by an approved expense -> available 250 (not 300).
store["closing_expense"].append({"org_id": HOUSE, "store_code": "S1", "close_date": "2026-07-28",
                                 "closing_row_id": "d1", "amount": 50.0, "status": "approved"})
r = cr.envelope_plan(store_code="S1", as_of="2026-08-04", required_amount=500.0, org_id=HOUSE, authorization="")
check("envelope-plan picks fewest envelopes (2, not 3)", len(r["picks"]) == 2, str(len(r["picks"])))
check("envelope-plan drains largest-available first (d1 netted to 250, tied w/ d2's 250 -> oldest d1 first)",
      r["picks"][0]["closing_row_id"] == "d1", str(r["picks"]))
check("envelope-plan total_taken == required, no shortfall", r["total_taken"] == 500.0 and r["shortfall"] == 0.0)

# required_amount omitted -> falls back to GET /closing/payout-due for the same store/date (both
# siblings 404 above -> commission/salary both 0, so requirement is whatever approved-unpaid expenses
# exist; none here -> required 0 -> no picks).
r2 = cr.envelope_plan(store_code="S1", as_of="2026-08-04", org_id=HOUSE, authorization="")
check("envelope-plan with no required_amount falls back to payout-due (0 here -> no picks)",
      r2["picks"] == [] and r2["required_amount"] == 0.0)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# H. POST /closing/envelope-withdrawal
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== H. POST /closing/envelope-withdrawal ==")

store = fresh_store()
fake = wire(store)
fr = FakeRequests()
cr.requests = fr

# H1: purpose='expense' marks the linked closing_expense row paid. (The netting-stays-correct-
# afterward half of this scenario — does the envelope get double-subtracted now that the SAME dollar
# is both an approved expense line AND a withdrawal row? — is proven separately in section J, which
# reproduces this exact shape across all three netting surfaces; kept split out so this section stays
# focused on the write/sibling-call contract.)
exp_row = fake.table("closing_expense").insert({
    "org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1",
    "amount": 40.0, "status": "approved", "paid": False}).execute().data[0]
resp = cr.record_envelope_withdrawal(_body(cr.RecordEnvelopeWithdrawalIn, {
    "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1", "amount": 40.0,
    "purpose": "expense", "expense_id": exp_row["id"], "remaining_after": 460.0}),
    org_id=HOUSE, authorization="")
check("withdrawal row written", resp["ok"] is True and resp["withdrawal"]["amount"] == 40.0)
linked = fake.table("closing_expense").select("*").eq("id", exp_row["id"]).execute().data[0]
check("linked expense line marked paid", linked["paid"] is True and linked.get("withdrawal_id") == resp["withdrawal"]["id"])

# H2: purpose='commission_payout' calls the sibling; a 404 is captured, never raised.
fr.route("/commcalc/payout/record", FakeResp(404))
resp2 = cr.record_envelope_withdrawal(_body(cr.RecordEnvelopeWithdrawalIn, {
    "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1", "amount": 100.0,
    "purpose": "commission_payout", "employee_id": "e1", "employee_name": "Jane Rep"}),
    org_id=HOUSE, authorization="")
check("commission_payout withdrawal persists despite a 404 sibling", resp2["ok"] is True)
check("sibling_call reports the 404, not posted", resp2["sibling_call"]["posted"] is False)

# H3: purpose='salary_payout' with a sibling that succeeds -> payout_ref backfilled.
fr.route("/storeops/salary-advance/record", FakeResp(200, {"id": "ledger-row-1"}))
resp3 = cr.record_envelope_withdrawal(_body(cr.RecordEnvelopeWithdrawalIn, {
    "store_code": "S1", "close_date": "2026-08-01", "closing_row_id": "row-1", "amount": 60.0,
    "purpose": "salary_payout", "employee_id": "emp-1", "employee_name": "Jane Rep"}),
    org_id=HOUSE, authorization="")
check("salary_payout sibling call succeeds", resp3["sibling_call"]["posted"] is True)
saved = fake.table("envelope_withdrawal").select("*").eq("id", resp3["withdrawal"]["id"]).execute().data[0]
check("payout_ref backfilled from the sibling's ledger row id", saved.get("payout_ref") == "ledger-row-1")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# I. GET /closing/summary (DM Verify) attaches _expense_lines per rep row
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== I. GET /closing/summary attaches expense lines ==")
store = fresh_store()
fake = wire(store)
store["daily_closing"].append({"id": "row-x", "org_id": HOUSE, "close_date": "2026-08-01", "period": "2026-08",
                               "store_code": "S1", "store_name": "1 Main St", "store_address": "1 Main St",
                               "employee_name": "Jane Rep", "store_cash": 0, "store_cc": 0, "epay_cash": 0,
                               "epay_cc": 0, "acc_sale": 0, "other_account": 0, "upgrade_count": 0,
                               "new_line_count": 0, "postpaid_count": 0, "t_cash": 500.0, "t_credit": 0.0,
                               "t_ext_cc": 0.0, "t_gift": 0.0, "t_store_acct": 0.0, "t_zelle": 0.0, "t_acima": 0.0})
store["closing_expense"].append({"id": "exp-x", "org_id": HOUSE, "store_code": "S1", "close_date": "2026-08-01",
                                 "closing_row_id": "row-x", "category_name": "Petty Expenses",
                                 "category_kind": "expense", "amount": 15.0, "status": "pending"})
r = cr.closing_summary(date="2026-08-01", org_id=HOUSE, authorization="")
s1 = next(s for s in r["stores"] if s["store_code"] == "S1")
rep = s1["reps"][0]
check("rep row carries _expense_lines", len(rep.get("_expense_lines") or []) == 1)
check("_expense_lines row matches the inserted expense", rep["_expense_lines"][0]["id"] == "exp-x")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════
# J. GATE-1 FIX (2026-08-04 coordinator finding): each expense dollar nets an envelope EXACTLY ONCE.
#    Before the fix: an approved closing_expense line netted once as "approved", then AGAIN when the
#    DM paid it out via POST /closing/envelope-withdrawal (purpose='expense', expense_id set) — a
#    double-subtraction. Proven here across ALL THREE netting surfaces, plus the two related cases
#    the coordinator called out (pending-but-paid nets once; payout-due drops a paid line).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════
print("\n== J. GATE-1 FIX: expense-paid-via-withdrawal nets ONCE, not twice ==")

store = fresh_store()
fake = wire(store)
store["daily_closing"].append({"id": "row-j1", "org_id": HOUSE, "store_code": "SJ", "close_date": "2026-08-01",
                               "t_cash": 500.0, "store_cash": 500.0, "epay_on_cash": 0.0, "epay_cash": 0.0,
                               "employee_name": "Jane Rep", "store_name": "J Store", "store_address": "J Store"})
exp_row = fake.table("closing_expense").insert({
    "org_id": HOUSE, "store_code": "SJ", "close_date": "2026-08-01", "closing_row_id": "row-j1",
    "amount": 40.0, "status": "approved", "paid": False, "category_name": "Petty Expenses",
    "category_kind": "expense", "description": "batteries"}).execute().data[0]

# Sanity: approved-but-unpaid nets once (the pre-existing, already-proven path) — 500-40=460.
amt, _ = cr._bank_deposit_declared(fake, HOUSE, "SJ", "2026-08-01", "total_cash")
check("J1: approved-unpaid expense nets once (460, not less)", amt == 460.0, str(amt))

# The DM pays that SAME line out via the withdrawal flow (H1's exact path) — this used to ALSO
# subtract another 40 (net 420 = wrong; correct is still 460, since the same $40 is now represented
# by amount=40+paid=True on ONE side and excluded on the withdrawal side).
resp = cr.record_envelope_withdrawal(_body(cr.RecordEnvelopeWithdrawalIn, {
    "store_code": "SJ", "close_date": "2026-08-01", "closing_row_id": "row-j1", "amount": 40.0,
    "purpose": "expense", "expense_id": exp_row["id"], "remaining_after": 460.0}),
    org_id=HOUSE, authorization="")
check("J2: withdrawal recorded + linked expense marked paid", resp["ok"] is True)
linked = fake.table("closing_expense").select("*").eq("id", exp_row["id"]).execute().data[0]
check("J2: expense line is now paid=true", linked["paid"] is True)

amt, _ = cr._bank_deposit_declared(fake, HOUSE, "SJ", "2026-08-01", "total_cash")
check("J3: _bank_deposit_declared nets ONCE after payout (460, NOT 420 double-subtracted)",
      amt == 460.0, str(amt))

r = cr.closing_pickups(date="2026-08-01", org_id=HOUSE, authorization="")
env = next(e for e in r["envelopes"] if e["store_code"] == "SJ")
check("J4: GET /closing/pickups nets ONCE after payout (460)", env["cash"] == 460.0, str(env["cash"]))

r = cr.cash_position(date="2026-08-01", org_id=HOUSE, authorization="")
row = next(x for x in r["rows"] if x["store_code"] == "SJ")
check("J5: GET /closing/cash-position nets ONCE after payout (460)", row["cash_on_hand"] == 460.0, str(row["cash_on_hand"]))

# J6: a commission/salary/other withdrawal (no expense_id) is UNAFFECTED by the fix — still nets in
# full, on top of the (unrelated) expense line above: 460 - 25 = 435.
cr.record_envelope_withdrawal(_body(cr.RecordEnvelopeWithdrawalIn, {
    "store_code": "SJ", "close_date": "2026-08-01", "closing_row_id": "row-j1", "amount": 25.0,
    "purpose": "other"}), org_id=HOUSE, authorization="")
amt, _ = cr._bank_deposit_declared(fake, HOUSE, "SJ", "2026-08-01", "total_cash")
check("J6: a non-expense-linked withdrawal (purpose='other') still nets normally (435)", amt == 435.0, str(amt))

# J7: pending-but-paid line nets once (coordinator's explicit case — a line paid while still
# 'pending', which POST /closing/envelope-withdrawal's expense path doesn't itself forbid).
store2 = fresh_store()
fake2 = wire(store2)
store2["daily_closing"].append({"id": "row-j2", "org_id": HOUSE, "store_code": "SK", "close_date": "2026-08-01",
                                "t_cash": 300.0, "store_cash": 300.0, "epay_on_cash": 0.0, "epay_cash": 0.0,
                                "employee_name": "Sam Rep", "store_name": "K Store", "store_address": "K Store"})
store2["closing_expense"].append({"org_id": HOUSE, "store_code": "SK", "close_date": "2026-08-01",
                                  "closing_row_id": "row-j2", "amount": 30.0, "status": "pending", "paid": True})
amt, _ = cr._bank_deposit_declared(fake2, HOUSE, "SK", "2026-08-01", "total_cash")
check("J7: a pending-but-paid line nets ONCE (270), not zero and not double", amt == 270.0, str(amt))

# J8: GET /closing/payout-due drops a paid line from expenses_due (approved-and-paid is no longer due).
store3 = fresh_store()
fake3 = wire(store3)
cr.requests = FakeRequests()   # both siblings 404 -> isolates this check to the expenses leg
store3["closing_expense"] += [
    {"org_id": HOUSE, "store_code": "SL", "close_date": "2026-08-01", "amount": 50.0,
     "status": "approved", "paid": False, "category_name": "Petty Expenses"},
    {"org_id": HOUSE, "store_code": "SL", "close_date": "2026-08-01", "amount": 75.0,
     "status": "approved", "paid": True, "category_name": "Office Expenses"},
]
r = cr.payout_due(store_code="SL", as_of="2026-08-01", org_id=HOUSE, authorization="")
check("J8: payout-due expenses_due excludes the PAID line (only the 50 unpaid one)",
      r["expenses_due"] == 50.0, str(r["expenses_due"]))
check("J8: payout-due expense_lines list also excludes the paid line", len(r["expense_lines"]) == 1)


print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
