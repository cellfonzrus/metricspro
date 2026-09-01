"""HARNESS — MA TX → P&L booking (Phase B, owner spec 2026-09-01, mig 309).

"Merchant discount for each line item goes into the P&L as merchant discount, residual under
residual." Proves, with NO DB and stdlib only:

  A. the residual row-matcher UNION — product_name '%residual%' family OR configured order_type
     (case-insensitive, trimmed); a row matching BOTH books ONCE; neither ⇒ excluded.
  B. sign conventions — residual = −retail_cost (negative feed value = money to the dealer),
     merchant discount = +merchant_discount; a mixed row books both columns (different money).
  C. the own-line vs legacy-fold toggle — pl_merchant_discount_own_line TRUE (default) books to
     `ma_merchant_discount`, FALSE keeps the byte-identical legacy `atu_income` fold.
  D. adaptive config — missing table/column/row (pre-mig-309) ⇒ the defaults; malformed values
     keep the defaults; an explicit empty order-type list means "label family only".
  E. money-column guard — no new path references merchant_invoice (an invoice NUMBER stored as
     NUMERIC; see residual_subs._MA_IDENTIFIER_COLUMNS), and the guard itself still bites.
  F. end-to-end through the REAL coa.build_inputs with a genuinely-filtering stub client (the
     harness_ma_income_heads pattern) — default and opt-out orgs, period isolation, single-count.

Run:  cd backend && python3 harness_ma_tx_pnl.py
"""
import inspect
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

from app.modules.account import residual_subs as rs  # noqa: E402
from app.modules.account import coa  # noqa: E402

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


def approx(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


def booked(rows, cfg=None):
    """Σ per line from the pure bookings helper."""
    out = {}
    for line, amt in rs.ma_tx_pnl_bookings(rows, cfg):
        out[line] = round(out.get(line, 0.0) + amt, 2)
    return out


print("── A. the residual UNION matcher ──")
m = rs.ma_residual_row_matcher(rs.default_ma_pnl_config())
check("product-name family hit ('Trac Autopay Residual')",
      m("Trac Autopay Residual", "Activation Order"))
check("product-name family hit, bare 'Residual'", m("Residual", None))
check("order-type hit (default 'Postpaid Residual Order'), name lacks the word",
      m("Trac Autopay", "Postpaid Residual Order"))
check("neither ⇒ excluded", not m("Total Wireless 5G Unlimited RTR $55", "Activation Order"))
check("case-insensitive product name", m("TRAC AUTOPAY RESIDUAL", None))
check("case/trim-insensitive order type", m("Airtime", "  postpaid residual order  "))
check("no order_type at all (older schema row) still matches the label family",
      m("Monthly Residual") and not m("Airtime"))
m2 = rs.ma_residual_row_matcher({"residual_order_types": ["Custom Residual Type"]})
check("configured order types replace the default (RULE TWO — config, never code)",
      m2("x", "custom residual type") and not m2("x", "Postpaid Residual Order"))
m3 = rs.ma_residual_row_matcher({"residual_order_types": []})
check("explicit EMPTY list ⇒ label family only (the pre-309 filter)",
      m3("Trac Autopay Residual", "z") and not m3("x", "Postpaid Residual Order"))

print("── B. sign conventions + single-count ──")
ROWS = [
    # label-family residual: money in retail_cost (negative = paid TO the dealer), $0 discount
    {"product_name": "Trac Autopay Residual", "order_type": "Postpaid Residual Order",
     "retail_cost": -70.0, "merchant_discount": 0.0},           # matches BOTH criteria
    {"product_name": "Residual", "order_type": "Adjustment",
     "retail_cost": -30.0, "merchant_discount": 0.0},           # label only
    {"product_name": "Total Bronze $25", "order_type": "Postpaid Residual Order",
     "retail_cost": -12.5, "merchant_discount": 0.0},           # order type only
    # activation/airtime rows: money in merchant_discount (owner sample: 3.4 / 2.47)
    {"product_name": "Total 5G $55", "order_type": "Activation Order",
     "retail_cost": 55.0, "merchant_discount": 3.4},
    {"product_name": "Total Starter $40", "order_type": "Activation Order",
     "retail_cost": 40.0, "merchant_discount": 2.47},
    # a MIXED row: order-type residual THAT ALSO carries airtime margin — both book (different money)
    {"product_name": "Total Bronze $25", "order_type": "Postpaid Residual Order",
     "retail_cost": -10.0, "merchant_discount": 1.0},
]
b = booked(ROWS)  # default config (own line)
check("residual = Σ −retail_cost over matched rows, each ONCE: 70+30+12.5+10 = 122.50",
      approx(b.get("mi_income", 0), 122.50), b)
check("a both-criteria row books ONCE (would be 192.50 if double-booked)",
      not approx(b.get("mi_income", 0), 192.50))
check("merchant discount = +Σ merchant_discount: 3.4+2.47+1.0 = 6.87",
      approx(b.get("ma_merchant_discount", 0), 6.87), b)
check("non-residual rows' retail_cost NEVER books (55/40 stay off the books)",
      approx(sum(b.values()), 122.50 + 6.87), b)
one_row = [r for _, r in enumerate(ROWS) if _ == 0]
entries = rs.ma_tx_pnl_bookings(one_row)
check("bookings for one both-criteria row: exactly one residual entry",
      sum(1 for k, _ in entries if k == "mi_income") == 1, entries)
check("positive retail_cost on a matched row books NEGATIVE (a clawback stays honest)",
      approx(booked([{"product_name": "Residual", "retail_cost": 5.0,
                      "merchant_discount": 0.0}]).get("mi_income", 0), -5.0))

print("── C. own-line vs legacy-fold toggle ──")
legacy_cfg = dict(rs.default_ma_pnl_config(), merchant_discount_own_line=False)
bl = booked(ROWS, legacy_cfg)
check("toggle FALSE folds into atu_income (legacy), same dollars",
      approx(bl.get("atu_income", 0), 6.87) and "ma_merchant_discount" not in bl, bl)
check("toggle FALSE leaves residual untouched", approx(bl.get("mi_income", 0), 122.50))
check("cfg=None ⇒ the mig-309 default (own line)",
      approx(booked(ROWS, None).get("ma_merchant_discount", 0), 6.87))
check("legacy fold is amount-identical to the own-line booking (only the line moves)",
      approx(bl.get("atu_income", 0), b.get("ma_merchant_discount", 0)))

print("── D. adaptive config resolution ──")


class _Raising:
    def schema(self, *_a):
        raise RuntimeError("commission_org_config not reachable (pre-mig-309)")


class _RowClient:
    """Returns one commission_org_config row (or raises when told the column is missing)."""
    def __init__(self, row=None, missing_column=False):
        self.row, self.missing_column = row, missing_column

    def schema(self, _n):
        return self

    def table(self, _n):
        return self

    def select(self, cols):
        if self.missing_column and "pl_merchant_discount_own_line" in cols:
            raise RuntimeError('column "pl_merchant_discount_own_line" does not exist')
        return self

    def eq(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        return SimpleNamespace(data=([self.row] if self.row is not None else []))


DEF = rs.default_ma_pnl_config()
check("client raising entirely ⇒ defaults (never raises)",
      rs.load_ma_pnl_config(_Raising(), "ORG") == DEF)
check("mig 309 not run (missing column) ⇒ defaults",
      rs.load_ma_pnl_config(_RowClient(missing_column=True), "ORG") == DEF)
check("no org row ⇒ defaults", rs.load_ma_pnl_config(_RowClient(row=None), "ORG") == DEF)
got = rs.load_ma_pnl_config(_RowClient(
    row={"pl_merchant_discount_own_line": False,
         "pl_ma_residual_order_types": [" Postpaid Residual Order ", "Custom", "", "  "]}), "ORG")
check("org row applies: toggle False + trimmed, blank-free order types",
      got == {"merchant_discount_own_line": False,
              "residual_order_types": ["Postpaid Residual Order", "Custom"]}, got)
got = rs.load_ma_pnl_config(_RowClient(
    row={"pl_merchant_discount_own_line": "yes", "pl_ma_residual_order_types": "Postpaid"}), "ORG")
check("malformed values (non-bool / non-list) keep the defaults, never guess", got == DEF, got)
got = rs.load_ma_pnl_config(_RowClient(
    row={"pl_merchant_discount_own_line": True, "pl_ma_residual_order_types": []}), "ORG")
check("explicit empty order-type list is HONORED (label family only), not defaulted",
      got["residual_order_types"] == [], got)
check("default order types mirror mig 309's column default",
      DEF["residual_order_types"] == ["Postpaid Residual Order"])

print("── E. money-column guard — merchant_invoice can never be summed ──")


def code_strings(fn):
    """Every string LITERAL the function's code actually carries (column lists, .get() keys, …),
    docstrings excluded — comments never reach the AST, so a doc mention can't false-alarm and a
    real `select(\"...merchant_invoice...\")` can't hide."""
    import ast
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                body[0].value.value = ""          # blank the docstring in place
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


_new_lits = [s for f in (rs.load_ma_pnl_config, rs.ma_residual_row_matcher,
                         rs.ma_tx_pnl_bookings, rs.default_ma_pnl_config)
             for s in code_strings(f)]
check("no new residual_subs path touches merchant_invoice (code literals, not comments)",
      all("merchant_invoice" not in s for s in _new_lits))
check("coa.build_inputs touches no identifier column as money (merchant_invoice absent from code)",
      all("merchant_invoice" not in s for s in code_strings(coa.build_inputs)))
check("the P&L money columns pass the guard",
      rs._MA_PNL_MONEY_COLUMNS == ["merchant_discount", "retail_cost"])
try:
    rs.assert_money_columns(["merchant_discount", "merchant_invoice"], "harness")
    check("assert_money_columns still bites on merchant_invoice", False, "no exception")
except ValueError as e:
    check("assert_money_columns still bites on merchant_invoice", "merchant_invoice" in str(e))

print("── F. end-to-end through the REAL coa.build_inputs (filtering stub client) ──")
MA_DAILY_TX = [dict(r, org_id="ORG", period="July 2026") for r in ROWS] + [
    # a DIFFERENT period — must never leak into July's figures
    {"org_id": "ORG", "period": "June 2026", "product_name": "Residual",
     "order_type": "Postpaid Residual Order", "retail_cost": -888.0, "merchant_discount": 9.0},
]


class _Q:
    def __init__(self, rows):
        self.rows = list(rows)

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        return _Q([r for r in self.rows if r.get(col) == val])

    def in_(self, col, vals):
        vs = set(vals)
        return _Q([r for r in self.rows if r.get(col) in vs])

    def ilike(self, col, pattern):
        pat = pattern.strip("%").lower()
        return _Q([r for r in self.rows if pat in str(r.get(col) or "").lower()])

    def gte(self, *_a):
        return self

    def lte(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        return _Q(self.rows[:n])

    def range(self, lo, hi):
        return _Q(self.rows[lo:hi + 1])

    def execute(self):
        return SimpleNamespace(data=list(self.rows))


class StubClient:
    def __init__(self, tables):
        self.tables = tables

    def schema(self, _name):
        return self

    def table(self, name):
        return _Q(self.tables.get(name, []))

    def rpc(self, *_a, **_k):
        return _Q([])


def cw(tables):
    L = coa.build_inputs(StubClient(tables), "ORG", "July 2026")
    return {k: round(v["company_wide"], 2)
            for k, v in L.items() if isinstance(v, dict) and v.get("company_wide")}


# the stub must FILTER, or every assertion below is vacuous (harness_ma_income_heads lesson)
_july = StubClient({"raw_ma_daily_tx": MA_DAILY_TX}).schema("commcalc") \
    .table("raw_ma_daily_tx").in_("period", ["July 2026"]).execute().data
check("stub client genuinely filters (June excluded from a July query)", len(_july) == 6)

no_cfg = cw({"raw_ma_daily_tx": MA_DAILY_TX})
check("E2E default (no config table at all): merchant discount on its OWN line",
      approx(no_cfg.get("ma_merchant_discount", 0), 6.87), no_cfg)
check("E2E default: residual union on mi_income, each row once, June excluded",
      approx(no_cfg.get("mi_income", 0), 122.50), no_cfg)
check("E2E default: atu_income carries nothing", no_cfg.get("atu_income") is None, no_cfg)

opt_out = cw({"raw_ma_daily_tx": MA_DAILY_TX,
              "commission_org_config": [{"org_id": "ORG",
                                         "pl_merchant_discount_own_line": False,
                                         "pl_ma_residual_order_types": ["Postpaid Residual Order"]}]})
check("E2E opt-out org: byte-identical legacy fold into atu_income",
      approx(opt_out.get("atu_income", 0), 6.87) and "ma_merchant_discount" not in opt_out, opt_out)
check("E2E opt-out org: residual booking unchanged", approx(opt_out.get("mi_income", 0), 122.50))

other_org = cw({"raw_ma_daily_tx": MA_DAILY_TX,
                "commission_org_config": [{"org_id": "OTHER-ORG",
                                           "pl_merchant_discount_own_line": False}]})
check("E2E org-scoped: ANOTHER org's opt-out does not leak (this org keeps the default)",
      approx(other_org.get("ma_merchant_discount", 0), 6.87), other_org)

check("PL_SPEC carries the new line with atu_income's section/sign semantics",
      next((s for k, _l, s, kind, scope in coa.PL_SPEC if k == "ma_merchant_discount"), None)
      == "revenue"
      and coa.PL_LABEL.get("ma_merchant_discount") == "Merchant discount")
check("mi_income's label of record names residual (why no separate Residual line was added)",
      "residual" in coa.PL_LABEL["mi_income"].lower(), coa.PL_LABEL["mi_income"])

print()
print(f"{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
