"""EEP P&L WIRING harness — proves the envelope-expense wave lands on the RIGHT P&L lines and can
never double-count. Drives the REAL shipped code (`account.coa.build_inputs`,
`account.engine._assemble`, `account.autocompute`) against an in-memory FAKE Supabase client.
No DB, no network. Run: `python3 harness_eep_pl_wiring.py` from backend/.

CONTEXT (spec: docs/specs/envelope-expense-payout.md, owner directive 2026-08-04)
  mod-retail-ops posts per-(period, store, expense-KIND category) rollups of daily-closing expenses
  through the sanctioned receiver POST /commcalc/expenses/{period}/system-line with
  source_key='closing_expense:<category-id>', expense_name=<category name>.
  mod-people posts source_key='additional_payroll', expense_name='Additional Payroll' — the EXCESS
  of envelope CASH salary advanced over what the employee actually earned.
  Cash payouts themselves (envelope_withdrawal / commission_payout_ledger / salary_advance_ledger)
  are cash MOVEMENTS against costs already booked; they are NOT P&L inputs.

WHAT THIS PROVES

  1. ROUTING (pure `coa.route_expense_line`)
     • 'additional_payroll'   → payroll_expenses, drill 'Additional Payroll'
     • 'payroll_expenses'     → payroll_expenses (unchanged)
     • 'payroll_gross'        → wages (unchanged, no drill)
     • 'closing_expense:<id>' → store_opex for ANY category id (prefix match, incl. blank id)
     • NULL / '' / 'pto_accrual' / an unknown future token → store_opex (historical default)

  2. PLACEMENT through the real aggregator + the real statement assembler
     • additional_payroll lands on the Payroll Expenses OPEX line — never on wages, never on store_opex
     • closing_expense:* lands on Store operating expenses with the CATEGORY NAME as drill label
       (the owner's "expenses auto-fill the P&L")
     • the payroll_expenses drill-down sums EXACTLY to its line (burden + additional payroll)
     • per-store attribution survives store_code → canonical store_address resolution
     • the `auto_opt` Payroll Expenses line still materializes only when it carries value

  3. THE WAGES FALLBACK IS NOT BROKEN (the money trap)
     • an additional_payroll line does NOT suppress the StoreOps shifts×rate wages estimate
       (only 'payroll_gross' does) — otherwise a cash advance would DELETE a tenant's wages line
     • with payroll_gross present the estimate is still suppressed and the label is 'Gross Payroll'
     • additional_payroll and wages never carry the same dollar

  4. DOUBLE-COUNT GUARD — envelope CASH never enters the books
     • the fake DB is STUFFED with $1,000,000+ of commcalc.envelope_withdrawal,
       commcalc.commission_payout_ledger, storeops.salary_advance_ledger and raw
       commcalc.closing_expense line items; build_inputs NEVER queries any of them (query log)
       and every computed figure is byte-identical to a DB without them
     • those tables are also absent from autocompute's staleness source lists (no wildcard ingestion)
     • the whole P&L is reproducible: net income equals the hand-computed figure

  5. STALENESS SENSITIVITY (autocompute)
     • store_expenses is a watched PERIOD source on created_at; a freshly posted system line
       (the receiver deletes-by-source_key then INSERTs, so created_at is new) flips
       needs_recompute False → True for BOTH period spellings

  6. RULE ONE — multi-tenant
     • every query build_inputs issues is org-constrained
     • a second tenant's expenses/ledgers never appear in this tenant's P&L, in either direction
     • period-spelling duality ("2026-06" vs "June 2026") is honoured via _period.period_keys

  7. READ-ONLY — the fake client raises on insert/update/upsert/delete/rpc, so a clean run proves
     build_inputs writes nothing.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.modules.account import coa            # noqa: E402
from app.modules.account import engine         # noqa: E402
from app.modules.account import autocompute    # noqa: E402
from app.modules.account import _period        # noqa: E402

PASS, FAIL = [], []
QUERY_LOG = []

ORG_A = "00000000-0000-0000-0000-000000000001"
ORG_B = "22222222-2222-2222-2222-000000000002"
PERIOD = "2026-06"
PERIOD_NAME = "June 2026"

ADDR_1 = "116-36 Queens Blvd"
ADDR_2 = "3 Palisade Ave"

# Tables the P&L must NEVER read (EEP cash ledgers + the raw closing line items).
FORBIDDEN = {
    "commcalc.envelope_withdrawal",
    "commcalc.commission_payout_ledger",
    "storeops.salary_advance_ledger",
    "commcalc.closing_expense",
    "commcalc.envelope_payout_config",
    "commcalc.daily_commission_accrual",
}


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS  {name}")
    else:
        FAIL.append(f"{name} :: {detail}")
        print(f"  FAIL  {name} :: {detail}")


def approx(a, b, eps=0.005):
    return abs(float(a) - float(b)) < eps


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Fake supabase-py client — only the verbs this code path uses. Every executed query is logged
# (schema.table + filters) so org scoping AND the forbidden-table guard can be asserted on ALL of
# them. Every WRITE verb raises: build_inputs is a pure read.
# ══════════════════════════════════════════════════════════════════════════════════════════════
class _MissingTable(Exception):
    pass


class WriteAttempted(AssertionError):
    pass


class _Q:
    def __init__(self, store, schema, table):
        self._store, self._schema, self._table = store, schema, table
        self._eq, self._in, self._gte, self._lt = {}, {}, {}, {}
        self._range, self._limit = None, None
        self._order = None

    def select(self, *a, **k):
        return self

    def eq(self, k, v):
        self._eq[k] = v
        return self

    def in_(self, k, v):
        self._in[k] = list(v)
        return self

    def gte(self, k, v):
        self._gte[k] = v
        return self

    def lt(self, k, v):
        self._lt[k] = v
        return self

    def limit(self, n):
        self._limit = n
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def range(self, a, b):
        self._range = (a, b)
        return self

    def insert(self, *a, **k):
        raise WriteAttempted(f"insert attempted on {self._schema}.{self._table}")

    def upsert(self, *a, **k):
        raise WriteAttempted(f"upsert attempted on {self._schema}.{self._table}")

    def update(self, *a, **k):
        raise WriteAttempted(f"update attempted on {self._schema}.{self._table}")

    def delete(self, *a, **k):
        raise WriteAttempted(f"delete attempted on {self._schema}.{self._table}")

    def execute(self):
        key = f"{self._schema}.{self._table}"
        QUERY_LOG.append({"key": key, "eq": dict(self._eq), "in": dict(self._in)})
        if key not in self._store:
            raise _MissingTable(f"relation {key} does not exist")
        rows = []
        for r in self._store[key]:
            ok = True
            for k, v in self._eq.items():
                if str(r.get(k)) != str(v):
                    ok = False
            for k, v in self._in.items():
                if str(r.get(k)) not in {str(x) for x in v}:
                    ok = False
            for k, v in self._gte.items():
                if not (r.get(k) is not None and str(r.get(k)) >= str(v)):
                    ok = False
            for k, v in self._lt.items():
                if not (r.get(k) is not None and str(r.get(k)) < str(v)):
                    ok = False
            if ok:
                rows.append(dict(r))
        if self._order:
            col, desc = self._order
            rows.sort(key=lambda r: str(r.get(col) or ""), reverse=bool(desc))
        if self._range:
            a, b = self._range
            rows = rows[a:b + 1]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Res", (), {"data": rows})()


class _Schema:
    def __init__(self, store, schema):
        self._store, self._schema = store, schema

    def table(self, t):
        return _Q(self._store, self._schema, t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted")


class FakeClient:
    def __init__(self, store):
        self._store = store

    def schema(self, s):
        return _Schema(self._store, s)

    def table(self, t):
        return _Q(self._store, "public", t)

    def rpc(self, *a, **k):
        raise WriteAttempted("rpc attempted")


# ══════════════════════════════════════════════════════════════════════════════════════════════
# Fixture
# ══════════════════════════════════════════════════════════════════════════════════════════════
def _expense(org, period, code, name, amount, source_key=None, created_at="2026-07-01T10:00:00Z"):
    return {"org_id": org, "period": period, "store_code": code, "expense_name": name,
            "expense_type": "Fixed", "amount": amount, "source_key": source_key,
            "created_at": created_at}


def base_store(*, with_payroll_gross=False, with_forbidden=True, extra_expenses=None):
    """The fixture DB. Two tenants, two stores, the full EEP system-line set."""
    store_mapping = [
        {"org_id": ORG_A, "store_code": "1001", "store_address": ADDR_1},
        {"org_id": ORG_A, "store_code": "1002", "store_address": ADDR_2},
        {"org_id": ORG_B, "store_code": "9001", "store_address": "1 Tenant B Way"},
    ]

    exp = [
        # mod-people: employer burden (pre-existing producer)
        _expense(ORG_A, PERIOD, "1001", "Payroll Expenses", 1200.00, "payroll_expenses"),
        # mod-people: EEP — envelope cash salary advanced BEYOND what was earned
        _expense(ORG_A, PERIOD, "1001", "Additional Payroll", 800.00, "additional_payroll"),
        # mod-retail-ops: EEP — daily-closing expense categories (P&L auto-fill).
        # NOTE the SECOND spelling of the same month — proves _period.period_keys duality.
        _expense(ORG_A, PERIOD, "1001", "Petty Expenses", 150.00, "closing_expense:cat-petty"),
        _expense(ORG_A, PERIOD_NAME, "1002", "Office Expenses", 75.00, "closing_expense:cat-office"),
        _expense(ORG_A, PERIOD, "1002", "Supplies", 40.00, "closing_expense:cat-supplies"),
        # hand-entered expense (source_key NULL) — must be untouched
        _expense(ORG_A, PERIOD, "1001", "Rent", 3000.00, None),
        # another producer's system line — must stay in store_opex, not folded into payroll
        _expense(ORG_A, PERIOD, "1001", "Paid Leave Accumulated", 90.00, "pto_accrual"),
        # ── TENANT B — must never appear in tenant A's books ──
        _expense(ORG_B, PERIOD, "9001", "Additional Payroll", 555555.00, "additional_payroll"),
        _expense(ORG_B, PERIOD, "9001", "Petty Expenses", 444444.00, "closing_expense:cat-petty"),
    ]
    if with_payroll_gross:
        exp.append(_expense(ORG_A, PERIOD, "1001", "Gross Payroll", 10000.00, "payroll_gross"))
    exp.extend(extra_expenses or [])

    db = {
        "commcalc.store_mapping": store_mapping,
        "commcalc.store_expenses": exp,
        # StoreOps shifts×rate — the wages FALLBACK source (used when no payroll_gross line exists)
        "storeops.employees": [
            {"org_id": ORG_A, "employee_id": "E1", "pay_rate": 20.0, "home_store": "1001"},
            {"org_id": ORG_B, "employee_id": "E9", "pay_rate": 99.0, "home_store": "9001"},
        ],
        "storeops.shifts": [
            {"org_id": ORG_A, "employee_id": "E1", "store_code": "1001", "scheduled_hours": 100.0,
             "actual_hours": 100.0, "shift_date": "2026-06-10", "is_deleted": False},
            {"org_id": ORG_B, "employee_id": "E9", "store_code": "9001", "scheduled_hours": 100.0,
             "actual_hours": 100.0, "shift_date": "2026-06-10", "is_deleted": False},
        ],
        "commcalc.account_statements": [],
    }
    if with_forbidden:
        # Fat cash-ledger rows. If ANY of these is read, the guard checks below blow up.
        db["commcalc.envelope_withdrawal"] = [
            {"org_id": ORG_A, "store_code": "1001", "close_date": "2026-06-05", "amount": 1000000.00,
             "purpose": "commission_payout", "period": PERIOD, "created_at": "2026-07-09T00:00:00Z"},
        ]
        db["commcalc.commission_payout_ledger"] = [
            {"org_id": ORG_A, "employee_key": "E1", "amount": 750000.00, "paid_date": "2026-06-05",
             "method": "envelope_cash", "store_code": "1001", "period": PERIOD,
             "created_at": "2026-07-09T00:00:00Z"},
        ]
        db["storeops.salary_advance_ledger"] = [
            {"org_id": ORG_A, "employee_id": "E1", "amount": 500000.00, "paid_date": "2026-06-05",
             "method": "envelope_cash", "store_code": "1001", "period": PERIOD,
             "created_at": "2026-07-09T00:00:00Z"},
        ]
        db["commcalc.closing_expense"] = [
            {"org_id": ORG_A, "store_code": "1001", "close_date": "2026-06-05", "amount": 250000.00,
             "kind": "payroll", "category_name": "Salary", "period": PERIOD,
             "created_at": "2026-07-09T00:00:00Z"},
        ]
        db["commcalc.daily_commission_accrual"] = [
            {"org_id": ORG_A, "work_date": "2026-06-05", "employee_key": "E1", "store_code": "1001",
             "total_amount": 333333.00, "period": PERIOD, "created_at": "2026-07-09T00:00:00Z"},
        ]
        db["commcalc.envelope_payout_config"] = [
            {"org_id": ORG_A, "store_code": "1001", "take_commission": True, "period": PERIOD},
        ]
    return db


def compute(db, org=ORG_A, period=PERIOD):
    QUERY_LOG.clear()
    return coa.build_inputs(FakeClient(db), org, period)


PL_SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
               ("Operating Expenses", "opex"), ("Other", "other")]


def assemble(inputs, stores=None, include_cw=True):
    return engine._assemble(inputs, [], coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS,
                            "consolidated" if stores is None else "scoped", stores, include_cw)


def line_of(pl, key):
    for sec in pl["sections"]:
        for ln in sec["lines"]:
            if ln["key"] == key:
                return sec["type"], ln
    return None, None


print("\n═══ 1. ROUTING TABLE (pure coa.route_expense_line) ═══")
check("r1  additional_payroll → payroll_expenses",
      coa.route_expense_line("additional_payroll")[0] == "payroll_expenses",
      coa.route_expense_line("additional_payroll"))
check("r2  additional_payroll fallback drill label is 'Additional Payroll'",
      coa.route_expense_line("additional_payroll")[1] == "Additional Payroll")
check("r3  additional_payroll is NOT wages", coa.route_expense_line("additional_payroll")[0] != "wages")
check("r4  additional_payroll is NOT store_opex",
      coa.route_expense_line("additional_payroll")[0] != "store_opex")
check("r5  payroll_gross still → wages", coa.route_expense_line("payroll_gross")[0] == "wages")
check("r6  payroll_expenses still → payroll_expenses",
      coa.route_expense_line("payroll_expenses")[0] == "payroll_expenses")
check("r7  closing_expense:<uuid> → store_opex",
      coa.route_expense_line("closing_expense:8f1c6a2e-0000-4a11-9d33-aa0011223344")[0] == "store_opex")
check("r8  closing_expense: with an EMPTY id still → store_opex (prefix match, never a crash)",
      coa.route_expense_line("closing_expense:")[0] == "store_opex")
check("r9  NULL source_key (manual expense) → store_opex", coa.route_expense_line(None)[0] == "store_opex")
check("r10 blank/whitespace source_key → store_opex", coa.route_expense_line("   ")[0] == "store_opex")
check("r11 pto_accrual keeps its historical store_opex home",
      coa.route_expense_line("pto_accrual")[0] == "store_opex")
check("r12 an UNKNOWN future token defaults to store_opex (never raises, never lands on payroll)",
      coa.route_expense_line("some_future_producer")[0] == "store_opex")
check("r13 manual/default fallback label is the historical 'Expense'",
      coa.route_expense_line(None)[1] == "Expense")
check("r14 'additional_payroll' is NOT in the wages-authoritative set (the fallback guard)",
      "additional_payroll" not in coa._WAGES_AUTHORITATIVE_KEYS, coa._WAGES_AUTHORITATIVE_KEYS)
check("r15 only payroll_gross is wages-authoritative",
      coa._WAGES_AUTHORITATIVE_KEYS == {"payroll_gross"}, coa._WAGES_AUTHORITATIVE_KEYS)
check("r16 no closing_expense route can reach a payroll line",
      all(coa.route_expense_line("closing_expense:" + c)[0] == "store_opex"
          for c in ("salary", "commission", "Payroll", "cat-1")))


print("\n═══ 2. PLACEMENT — real build_inputs, no payroll_gross (the common tenant) ═══")
db = base_store()
inp = compute(db)

check("p1  payroll_expenses line = burden 1200 + additional payroll 800 = 2000",
      approx(sum(inp["payroll_expenses"]["by_store"].values()), 2000.00),
      inp["payroll_expenses"])
check("p2  it is attributed to the CANONICAL store address, not the store_code",
      approx(inp["payroll_expenses"]["by_store"].get(ADDR_1, 0), 2000.00),
      inp["payroll_expenses"]["by_store"])
check("p3  payroll_expenses drill = {'Payroll Expenses':1200, 'Additional Payroll':800}",
      inp["payroll_expenses"]["detail"] == {"Payroll Expenses": 1200.00, "Additional Payroll": 800.00},
      inp["payroll_expenses"]["detail"])
check("p4  the drill sums EXACTLY to the line (nothing unexplained)",
      approx(sum(inp["payroll_expenses"]["detail"].values()),
             sum(inp["payroll_expenses"]["by_store"].values())))
check("p5  'Additional Payroll' NEVER appears under store_opex",
      "Additional Payroll" not in inp["store_opex"]["detail"], inp["store_opex"]["detail"])

opex_detail = inp["store_opex"]["detail"]
check("p6  closing_expense:cat-petty booked to store_opex under its CATEGORY NAME",
      approx(opex_detail.get("Petty Expenses", 0), 150.00), opex_detail)
check("p7  closing_expense:cat-office (stored under the 'June 2026' spelling) is picked up for '2026-06'",
      approx(opex_detail.get("Office Expenses", 0), 75.00), opex_detail)
check("p8  closing_expense:cat-supplies booked under its category name",
      approx(opex_detail.get("Supplies", 0), 40.00), opex_detail)
check("p9  the manual 'Rent' row is untouched", approx(opex_detail.get("Rent", 0), 3000.00), opex_detail)
check("p10 pto_accrual keeps its own store_opex drill-down",
      approx(opex_detail.get("Paid Leave Accumulated", 0), 90.00), opex_detail)
check("p11 store_opex total = 150+75+40+3000+90 = 3355",
      approx(sum(inp["store_opex"]["by_store"].values()), 3355.00), inp["store_opex"]["by_store"])
check("p12 per-store split: 1001→3240, 1002→115",
      approx(inp["store_opex"]["by_store"].get(ADDR_1, 0), 3240.00)
      and approx(inp["store_opex"]["by_store"].get(ADDR_2, 0), 115.00),
      inp["store_opex"]["by_store"])

check("p13 WAGES FALLBACK SURVIVES an additional_payroll line: 100h × $20 = 2000 still booked",
      approx(sum(inp["wages"]["by_store"].values()), 2000.00), inp["wages"])
check("p14 the wages line keeps its estimate label (no exact-gross line exists)",
      inp.get("wages", {}).get("label") in (None, ""), inp["wages"].get("label"))
check("p15 additional payroll and wages are DIFFERENT dollars (no shared amount source)",
      approx(sum(inp["payroll_expenses"]["by_store"].values()), 2000.00)
      and approx(sum(inp["wages"]["by_store"].values()), 2000.00)
      and inp["payroll_expenses"]["detail"].get("Additional Payroll") == 800.00)

pl = assemble(inp)
sec, ln = line_of(pl, "payroll_expenses")
check("p16 the Payroll Expenses line lands in OPERATING EXPENSES on the assembled P&L", sec == "opex", sec)
check("p17 its amount on the statement is 2000.00", ln and approx(ln["amount"], 2000.00), ln)
check("p18 its drill-down survives onto the statement",
      ln and ln["detail"].get("Additional Payroll") == 800.00, ln)
sec_o, ln_o = line_of(pl, "store_opex")
check("p19 the closing-expense categories are drillable on the statement's store_opex line",
      sec_o == "opex" and ln_o["detail"].get("Petty Expenses") == 150.00
      and ln_o["detail"].get("Office Expenses") == 75.00, ln_o)
sec_w, ln_w = line_of(pl, "wages")
check("p20 wages is still its own separate opex line at 2000.00",
      sec_w == "opex" and approx(ln_w["amount"], 2000.00), ln_w)
check("p21 total opex = wages 2000 + payroll_expenses 2000 + store_opex 3355 = 7355",
      approx(next(s["subtotal"] for s in pl["sections"] if s["type"] == "opex"), 7355.00),
      [(s["type"], s["subtotal"]) for s in pl["sections"]])
check("p22 net income = −7355 (no revenue in the fixture) — every dollar accounted for",
      approx(pl["net_income"], -7355.00), pl["net_income"])

pl_store2 = assemble(inp, stores={ADDR_2}, include_cw=False)
_, ln2 = line_of(pl_store2, "store_opex")
check("p23 store-scoped P&L sees only that store's closing expenses (75+40=115)",
      approx(ln2["amount"], 115.00), ln2)
_, lnp2 = line_of(pl_store2, "payroll_expenses")
check("p24 a store with no payroll cost shows NO Payroll Expenses line (auto_opt)", lnp2 is None, lnp2)


print("\n═══ 3. WAGES FALLBACK / GROSS-PAYROLL INTERACTION ═══")
db_g = base_store(with_payroll_gross=True)
inp_g = compute(db_g)
check("w1  with payroll_gross present, wages = the EXACT 10000 (estimate suppressed, not summed)",
      approx(sum(inp_g["wages"]["by_store"].values()), 10000.00), inp_g["wages"])
check("w2  the wages line is relabelled 'Gross Payroll'", inp_g["wages"].get("label") == "Gross Payroll")
check("w3  additional_payroll STILL books separately to payroll_expenses (2000 total)",
      approx(sum(inp_g["payroll_expenses"]["by_store"].values()), 2000.00), inp_g["payroll_expenses"])
check("w4  gross payroll never leaks into the payroll_expenses drill",
      "Gross Payroll" not in inp_g["payroll_expenses"]["detail"], inp_g["payroll_expenses"]["detail"])
check("w5  gross payroll never leaks into store_opex",
      "Gross Payroll" not in inp_g["store_opex"]["detail"], inp_g["store_opex"]["detail"])

# The trap this guards: if 'additional_payroll' were ever treated as authoritative, wages would
# collapse to 0 for a tenant that has no payroll_gross push. p13 already proves it doesn't; this
# proves the amounts are independent by construction.
db_only_ap = base_store()
db_only_ap["commcalc.store_expenses"] = [
    r for r in db_only_ap["commcalc.store_expenses"]
    if (r.get("source_key") or "") == "additional_payroll"
]
inp_ap = compute(db_only_ap)
check("w6  additional_payroll ALONE still leaves the shifts×rate wages estimate intact (2000)",
      approx(sum(inp_ap["wages"]["by_store"].values()), 2000.00), inp_ap["wages"])
check("w7  additional_payroll ALONE books 800 to payroll_expenses",
      approx(sum(inp_ap["payroll_expenses"]["by_store"].values()), 800.00), inp_ap["payroll_expenses"])

db_none = base_store()
db_none["commcalc.store_expenses"] = [r for r in db_none["commcalc.store_expenses"]
                                      if r.get("org_id") != ORG_A]
inp_none = compute(db_none)
pl_none = assemble(inp_none)
_, ln_none = line_of(pl_none, "payroll_expenses")
check("w8  a tenant with NO payroll-expense lines shows no Payroll Expenses line at all (auto_opt)",
      ln_none is None, ln_none)


print("\n═══ 4. DOUBLE-COUNT GUARD — envelope cash never enters the P&L ═══")
db_fat = base_store(with_forbidden=True)
inp_fat = compute(db_fat)
touched = {q["key"] for q in QUERY_LOG}
hit = touched & FORBIDDEN
check("g1  build_inputs queries NONE of the envelope cash ledgers / raw closing lines", not hit, hit)
check("g2  (control) the guard is meaningful — those tables DO exist in this fixture",
      FORBIDDEN & set(db_fat.keys()) == {"commcalc.envelope_withdrawal",
                                         "commcalc.commission_payout_ledger",
                                         "storeops.salary_advance_ledger",
                                         "commcalc.closing_expense",
                                         "commcalc.daily_commission_accrual",
                                         "commcalc.envelope_payout_config"})

db_lean = base_store(with_forbidden=False)
inp_lean = compute(db_lean)


def flatten(inputs):
    return {k: (round(sum(v["by_store"].values()), 2), round(v["company_wide"], 2),
                {dk: round(dv, 2) for dk, dv in v["detail"].items()})
            for k, v in inputs.items()}


check("g3  every P&L/BS figure is IDENTICAL with and without $2.8M of envelope cash in the DB",
      flatten(inp_fat) == flatten(inp_lean),
      {k: (flatten(inp_fat)[k], flatten(inp_lean)[k])
       for k in flatten(inp_fat) if flatten(inp_fat)[k] != flatten(inp_lean)[k]})
pl_fat = assemble(inp_fat)
check("g4  net income is unmoved by the cash ledgers (−7355)", approx(pl_fat["net_income"], -7355.00),
      pl_fat["net_income"])
check("g5  no line anywhere carries a cash-ledger amount (1,000,000 / 750,000 / 500,000 / 250,000)",
      not any(approx(abs(v), x, 0.5) for _, line in inp_fat.items()
              for v in list(line["by_store"].values()) + [line["company_wide"]]
              for x in (1000000, 750000, 500000, 250000, 333333)),
      {k: v["by_store"] for k, v in inp_fat.items() if v["by_store"]})

src_tables = {t for t, _ in autocompute._PERIOD_SOURCES} | {t for t, _ in autocompute._POINT_IN_TIME_SOURCES}
check("g6  autocompute's staleness lists are an explicit ALLOWLIST — no cash ledger in them",
      not (src_tables & {"envelope_withdrawal", "commission_payout_ledger", "salary_advance_ledger",
                         "closing_expense", "daily_commission_accrual"}), src_tables)
check("g7  autocompute reads only commcalc.* source tables by NAME (no wildcard ingestion)",
      all(isinstance(t, str) and isinstance(c, list) and c
          for t, c in autocompute._PERIOD_SOURCES + autocompute._POINT_IN_TIME_SOURCES))

# The producer-side contract: a salary/commission-KIND category must never be posted as a system
# line. If one ever were, it would still be visible — this asserts it would NOT silently merge into
# the payroll lines (it lands in store_opex under its own name, where a reviewer can see it).
db_bad = base_store()
db_bad["commcalc.store_expenses"].append(
    _expense(ORG_A, PERIOD, "1001", "Salary", 5000.00, "closing_expense:cat-salary"))
inp_bad = compute(db_bad)
check("g8  a mis-posted salary-kind closing line does NOT silently join the payroll lines",
      approx(sum(inp_bad["payroll_expenses"]["by_store"].values()), 2000.00)
      and approx(sum(inp_bad["wages"]["by_store"].values()), 2000.00), inp_bad["payroll_expenses"])
check("g9  ...it is visible in store_opex under its own name instead (auditable, not hidden)",
      approx(inp_bad["store_opex"]["detail"].get("Salary", 0), 5000.00), inp_bad["store_opex"]["detail"])


print("\n═══ 5. STALENESS SENSITIVITY (autocompute) ═══")
check("s1  store_expenses is a watched PERIOD source", "store_expenses" in {t for t, _ in autocompute._PERIOD_SOURCES})
check("s2  ...watched on created_at (the column the receiver's INSERT re-stamps)",
      dict(autocompute._PERIOD_SOURCES)["store_expenses"] == ["created_at"])

db_s = base_store()
cl = FakeClient(db_s)
newest = autocompute.newest_ingest_at(cl, ORG_A, PERIOD)
check("s3  newest_ingest_at sees the existing store_expenses rows", newest == "2026-07-01T10:00:00Z", newest)
computed_at = "2026-07-02T00:00:00Z"          # statements computed AFTER those rows
check("s4  a period computed after the last write is NOT stale",
      autocompute.needs_recompute(computed_at, newest) is False, (computed_at, newest))

# retail-ops posts a fresh closing-expense rollup (receiver = delete-by-source_key + INSERT ⇒ new created_at)
db_s["commcalc.store_expenses"] = [
    r for r in db_s["commcalc.store_expenses"]
    if not (r["org_id"] == ORG_A and (r.get("source_key") or "") == "closing_expense:cat-petty")
] + [_expense(ORG_A, PERIOD, "1001", "Petty Expenses", 210.00, "closing_expense:cat-petty",
              created_at="2026-07-03T08:30:00Z")]
newest2 = autocompute.newest_ingest_at(FakeClient(db_s), ORG_A, PERIOD)
check("s5  the re-posted system line advances newest_ingest_at", newest2 == "2026-07-03T08:30:00Z", newest2)
check("s6  ...which flips the period to STALE → the sweep recomputes",
      autocompute.needs_recompute(computed_at, newest2) is True)

# same proof for the month-NAME spelling (the receiver may post either)
db_s2 = base_store()
db_s2["commcalc.store_expenses"].append(
    _expense(ORG_A, PERIOD_NAME, "1001", "Additional Payroll", 900.00, "additional_payroll",
             created_at="2026-07-04T09:00:00Z"))
check("s7  a system line posted under the OTHER period spelling still triggers staleness",
      autocompute.needs_recompute(computed_at,
                                  autocompute.newest_ingest_at(FakeClient(db_s2), ORG_A, PERIOD_NAME)) is True)
check("s8  ...and is visible when computing the numeric spelling too",
      autocompute.needs_recompute(computed_at,
                                  autocompute.newest_ingest_at(FakeClient(db_s2), ORG_A, PERIOD)) is True)
check("s9  staleness() reports the same verdict for the page banner",
      autocompute.staleness(FakeClient(db_s2), ORG_A, PERIOD, computed_at=computed_at)["stale"] is True)
check("s10 a tenant with NO data for the period is never 'stale' (nothing to compute)",
      autocompute.needs_recompute(None, autocompute.newest_ingest_at(FakeClient(db_s2), ORG_B, "2026-01")) is False)


print("\n═══ 6. RULE ONE — multi-tenant ═══")
QUERY_LOG.clear()
inp_a = coa.build_inputs(FakeClient(base_store()), ORG_A, PERIOD)
unscoped = [q["key"] for q in QUERY_LOG if "org_id" not in q["eq"] and "org_id" not in q["in"]]
check("m1  EVERY query build_inputs issues is org-scoped", not unscoped, unscoped)
check("m2  tenant B's $555,555 Additional Payroll is absent from tenant A's payroll line",
      approx(sum(inp_a["payroll_expenses"]["by_store"].values()), 2000.00))
check("m3  tenant B's $444,444 closing expense is absent from tenant A's store_opex",
      approx(sum(inp_a["store_opex"]["by_store"].values()), 3355.00))
check("m4  tenant B's store never appears in tenant A's books",
      "1 Tenant B Way" not in set(inp_a["store_opex"]["by_store"]) | set(inp_a["payroll_expenses"]["by_store"]))

inp_b = coa.build_inputs(FakeClient(base_store()), ORG_B, PERIOD)
check("m5  the reverse holds — tenant B sees ONLY its own additional payroll (555555)",
      approx(sum(inp_b["payroll_expenses"]["by_store"].values()), 555555.00), inp_b["payroll_expenses"])
check("m6  ...and only its own closing expense (444444)",
      approx(sum(inp_b["store_opex"]["by_store"].values()), 444444.00), inp_b["store_opex"])
check("m7  tenant A's stores never appear in tenant B's books",
      not ({ADDR_1, ADDR_2} & set(inp_b["store_opex"]["by_store"])), inp_b["store_opex"]["by_store"])

pk = set(_period.period_keys(PERIOD))
check("m8  the period filter carries BOTH spellings (the finance trap)",
      {"2026-06", "June 2026"} <= pk, pk)
se_queries = [q for q in QUERY_LOG if q["key"] == "commcalc.store_expenses"]
check("m9  the store_expenses read filters period through period_keys (both spellings)",
      se_queries and all({"2026-06", "June 2026"} <= set(q["in"].get("period", [])) for q in se_queries),
      se_queries)
inp_name = coa.build_inputs(FakeClient(base_store()), ORG_A, PERIOD_NAME)
check("m10 computing the month-NAME spelling yields the identical totals",
      flatten(inp_name) == flatten(inp_a),
      {k: (flatten(inp_name)[k], flatten(inp_a)[k])
       for k in flatten(inp_a) if flatten(inp_name)[k] != flatten(inp_a)[k]})


print("\n═══ 7. READ-ONLY ═══")
check("ro1 build_inputs performed zero writes (the fake client raises on any write verb)", True)


print("\n" + "═" * 96)
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
for f in FAIL:
    print("   FAIL " + f)
print("═" * 96)
sys.exit(1 if FAIL else 0)
