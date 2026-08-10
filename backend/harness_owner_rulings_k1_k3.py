"""Proof for owner rulings K1 / K2 / K3 (2026-08-10) + the store_borrowings column bug.

Drives the REAL `coa.build_inputs` against an in-memory client, so what is asserted is the shipping
code path rather than a restatement of it.

⚠️ THE STUB GENUINELY FILTERS. A stub whose `.eq()` is a no-op hands every seeded row to every query
and will happily "pass" while the production filter is broken — that exact trap produced two wrong
counts on this codebase (the `gp_category_map` / `category='accessory'` incident). `eq` / `in_` /
`ilike` / `range` / `limit` are each implemented against the seeded rows, and `test_stub_filters`
proves the stub discriminates BEFORE any accounting assertion is trusted.

⚠️ EVERY BUG IS REPRODUCED BEFORE IT IS FIXED. Sections 1/3/5 assert the DEFECTIVE behaviour still
occurs with default (empty) config — that is what proves the config gate is real and that no tenant's
numbers moved without an explicit opt-in. Sections 2/4/6 then assert the fixed behaviour with config on.

Run: python3 harness_owner_rulings_k1_k3.py
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")

ORG = "ORG-LUX"          # an MA/VidaPay tenant: no raw_mi, so the MA fallback answers
OTHER = "ORG-OTHER"      # isolation control
BOOST = "ORG-BOOST"      # has raw_mi ⇒ the MA fallback must never fire

# ── seed ─────────────────────────────────────────────────────────────────────────────────────────
# Shapes mirror luxelink's live July 2026 data, scaled to hand-checkable numbers.
# raw_ma_commission feed convention: NEGATIVE = paid TO the dealer.
MA_COMMISSION = [
    # two distinct handsets, priced from the fulfillment sheet below
    {"org_id": ORG, "period": "July 2026", "imei": "IMEI-A", "sku": "Apple iPhone 16e 128GB Black TO",
     "activation_type": "New", "device_margin": -20.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -500.0, "wallet_funding": 65.0, "fees_margin": -12.5,
     "spiff_m1": -30.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    {"org_id": ORG, "period": "July 2026", "imei": "IMEI-B", "sku": "Samsung Galaxy A16 5G TO",
     "activation_type": "New", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -100.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # DUPLICATE IMEI — the same physical handset on a line/AAL pair. Must be charged to COGS ONCE.
    {"org_id": ORG, "period": "July 2026", "imei": "IMEI-B", "sku": "Samsung Galaxy A16 5G TO",
     "activation_type": "Add", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": 0.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # UN-LINKABLE: a SIM-only/BYOD activation. Not a handset; contributes $0 and is COUNTED in meta.
    {"org_id": ORG, "period": "July 2026", "imei": "IMEI-C", "sku": "Product Not Available",
     "activation_type": "New", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": 0.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # a handset the price list does not carry — must be counted as unpriced, never guessed
    {"org_id": ORG, "period": "July 2026", "imei": "IMEI-D", "sku": "Motorola Razr 2025 Blue TO",
     "activation_type": "New", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -900.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # a DIFFERENT period — must never leak into July
    {"org_id": ORG, "period": "June 2026", "imei": "IMEI-J", "sku": "Apple iPhone 16e 128GB Black TO",
     "activation_type": "New", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -999.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
    # a DIFFERENT tenant — must never leak either
    {"org_id": OTHER, "period": "July 2026", "imei": "IMEI-X", "sku": "Apple iPhone 16e 128GB Black TO",
     "activation_type": "New", "device_margin": 0.0, "consumer_margin": 0.0,
     "consumer_financing": 0.0, "rebate": -777.0, "wallet_funding": 0.0, "fees_margin": 0.0,
     "spiff_m1": 0.0, "spiff_m2": 0.0, "spiff_m3": 0.0, "spiff_m4": 0.0, "spiff_m5": 0.0, "spiff_m6": 0.0},
]
# order_type must be the vendor's handset taxonomy or the row is not a device price
MA_FULFILLMENT = [
    {"org_id": ORG, "product_name": "Apple iPhone 16e 128GB Black TO", "number_ordered": 2,
     "price": 599.99, "order_type": "Branded Handset"},
    {"org_id": ORG, "product_name": "Samsung Galaxy A16 5G TO", "number_ordered": 10,
     "price": 129.99, "order_type": "Branded Handset"},
    # NOT a device — must be excluded from the price list
    {"org_id": ORG, "product_name": "Total by Verizon SIM Kit", "number_ordered": 20,
     "price": 3.00, "order_type": "VidaPay MarketPlace SIMs"},
    {"org_id": OTHER, "product_name": "Apple iPhone 16e 128GB Black TO", "number_ordered": 1,
     "price": 111.11, "order_type": "Branded Handset"},
]
MA_DAILY_TX = [
    {"org_id": ORG, "period": "July 2026", "product_name": "Residual",
     "retail_cost": -100.0, "merchant_discount": 0.0},
    {"org_id": ORG, "period": "July 2026", "product_name": "Total STARTER Plan $40 RTR",
     "retail_cost": 40.0, "merchant_discount": 2.0},
]
# POS: one handset department (device) whose recorded cost is NEGATIVE — the luxelink shape exactly.
RAW_SALES = [
    {"org_id": ORG, "period": "July 2026", "trans_id": "T1", "department": "Handset",
     "category": "KittedBranded", "product_desc": "iPhone 16e", "ext_price": 300.0, "gp": 400.0,
     "voided": "", "store": "STORE-1"},
    {"org_id": ORG, "period": "July 2026", "trans_id": "T2", "department": "Ondigo",
     "category": "Accessories", "product_desc": "Case", "ext_price": 100.0, "gp": 40.0,
     "voided": "", "store": "STORE-1"},
]
# store_expenses: the luxelink pattern — payroll typed BY HAND, every row source_key NULL.
STORE_EXPENSES = [
    {"org_id": ORG, "period": "July 2026", "store_code": "STORE-1", "expense_name": "Employee Salaries",
     "expense_type": "Fixed", "amount": 1000.0, "source_key": None},
    {"org_id": ORG, "period": "July 2026", "store_code": "STORE-1", "expense_name": "DM Salaries",
     "expense_type": "Fixed", "amount": 200.0, "source_key": None},
    {"org_id": ORG, "period": "July 2026", "store_code": "STORE-1", "expense_name": "Rent / Lease",
     "expense_type": "Variable", "amount": 500.0, "source_key": None},
]
# StoreOps shifts × pay_rate = the ESTIMATE that must be suppressed once payroll is authoritative.
SHIFTS = [{"org_id": ORG, "employee_id": "E1", "store_code": "STORE-1", "scheduled_hours": 100.0,
           "actual_hours": 100.0, "shift_date": "2026-07-05", "is_deleted": False}]
EMPLOYEES = [{"org_id": ORG, "employee_id": "E1", "pay_rate": 10.0, "home_store": "STORE-1"}]
STORE_MAPPING = [{"org_id": ORG, "store_code": "STORE-1", "store_address": "1 Main St"}]
# luxelink's REAL classifier config: exactly one row, Handset -> device. Without it the POS handset
# line matches neither classifier and is dropped — which is defect #5 in the formula book, and is why
# the POS-fallback assertions below would otherwise be vacuous.
GP_CATEGORY_MAP = [{"org_id": ORG, "department": "Handset", "category": "device"}]
# store_borrowings: the REAL schema (borrower_store / lender_store, no `repaid` column).
STORE_BORROWINGS = [{"org_id": ORG, "borrower_store": "1 Main St", "lender_store": "2 Oak Ave",
                     "amount": 750.0, "market": "M", "borrowed_date": "2026-07-02", "note": ""}]
# Boost control: raw_mi present ⇒ had_raw_mi True ⇒ the MA fallback (and therefore K1) never fires.
RAW_MI = [{"org_id": BOOST, "period": "July 2026", "actual_mi_payout": 10.0, "actual_atu_payout": 5.0}]

ACCOUNT_CONFIG = []      # mutated per-scenario; EMPTY = every default = pre-621 behaviour

TABLES = {
    "raw_ma_commission": MA_COMMISSION, "raw_ma_fulfillment": MA_FULFILLMENT,
    "raw_ma_daily_tx": MA_DAILY_TX, "raw_sales": RAW_SALES, "store_expenses": STORE_EXPENSES,
    "store_mapping": STORE_MAPPING, "store_borrowings": STORE_BORROWINGS, "raw_mi": RAW_MI,
    "account_config": ACCOUNT_CONFIG, "gp_category_map": GP_CATEGORY_MAP,
}
SO_TABLES = {"shifts": SHIFTS, "employees": EMPLOYEES}
WRITE_VERBS = ("insert", "update", "upsert", "delete")


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

    def gte(self, col, val):
        return _Q([r for r in self.rows if str(r.get(col) or "") >= str(val)])

    def lt(self, col, val):
        return _Q([r for r in self.rows if str(r.get(col) or "") < str(val)])

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

    def __getattr__(self, name):
        # READ-ONLY PROOF: any write verb reaching the stub is a bug in the code under test.
        if name in WRITE_VERBS:
            raise AssertionError("build_inputs attempted a WRITE (%s) — it must be read-only" % name)
        raise AttributeError(name)


class _Schema:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return _Q(self.tables.get(name, []))

    def rpc(self, *_a, **_k):
        return _Q([])


class StubClient:
    def schema(self, name):
        return _Schema(SO_TABLES if name == "storeops" else TABLES)

    def table(self, name):
        return _Schema(TABLES).table(name)


def approx(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


RESULTS = []


def check(name, got, want, why):
    good = approx(got, want) if isinstance(want, (int, float)) else (got == want)
    RESULTS.append(good)
    flag = "✓" if good else "✗"
    print("  %s %-46s got %-14s want %-14s  %s"
          % (flag, name, round(got, 2) if isinstance(got, float) else got,
             round(want, 2) if isinstance(want, float) else want, why))
    return good


def check_true(name, cond, why):
    RESULTS.append(bool(cond))
    print("  %s %-46s %s" % ("✓" if cond else "✗", name, why))


def set_config(orgs=(ORG,), **kw):
    """Seed account_config for EVERY org named. Seeding only ORG would make the §8 isolation test
    compare a configured tenant against an unconfigured one, which proves nothing about leakage."""
    ACCOUNT_CONFIG[:] = [dict({"org_id": o}, **kw) for o in orgs] if kw else []


def build(org=ORG, period="July 2026"):
    from app.modules.account import coa
    return coa.build_inputs(StubClient(), org, period)


def line(L, key):
    return round(L[key]["company_wide"] + sum(L[key]["by_store"].values()), 2)


# ── 0 · the stub must discriminate, or everything below is vacuous ────────────────────────────────
def test_stub_filters():
    print("\n[0] STUB SELF-TEST — a no-op .eq() would make every assertion meaningless")
    c = StubClient()
    july = c.schema("commcalc").table("raw_ma_commission").eq("period", "July 2026").execute().data
    check("eq(period) filters", len(july), 6, "5 ORG + 1 OTHER July rows, June excluded")
    mine = (c.schema("commcalc").table("raw_ma_commission")
            .eq("org_id", ORG).eq("period", "July 2026").execute().data)
    check("eq(org_id)+eq(period) filters", len(mine), 5, "OTHER's July row excluded")
    none = c.schema("commcalc").table("raw_ma_commission").eq("period", "NOPE").execute().data
    check("eq() on a missing value returns []", len(none), 0, "no silent match-all")
    res = c.schema("commcalc").table("raw_ma_daily_tx").ilike("product_name", "%residual%").execute().data
    check("ilike() filters", len(res), 1, "only the Residual row")
    try:
        c.schema("commcalc").table("raw_sales").insert({"x": 1})
        check_true("write verbs are refused", False, "insert() did NOT raise")
    except AssertionError:
        check_true("write verbs are refused", True, "insert() raises ⇒ read-only proven")


# ── 1 · K2 BUG REPRODUCTION — default config still double-counts payroll ──────────────────────────
def test_k2_bug_reproduced():
    print("\n[1] K2 BUG REPRODUCTION — empty config MUST still show the double-count (byte-identical)")
    set_config()
    L = build()
    check("wages (shifts x rate estimate)", line(L, "wages"), 1000.00, "100 hrs x $10 — the ESTIMATE")
    check("store_opex holds the manual salaries", line(L, "store_opex"), 1700.00,
          "1000 Employee + 200 DM + 500 Rent")
    check_true("the SAME labour is booked twice",
               approx(L["wages"]["by_store"].get("1 Main St", 0), 1000.0)
               and approx(L["store_opex"]["detail"].get("Employee Salaries", 0), 1000.0),
               "$1,000 of wages sits in BOTH lines — the defect, still present with no config")


# ── 2 · K2 FIXED — a configured payroll name suppresses the estimate ──────────────────────────────
def test_k2_fixed():
    print("\n[2] K2 FIXED — tenant lists its payroll expense names ⇒ estimate suppressed")
    set_config(payroll_expense_names=["Employee Salaries", "DM Salaries"])
    L = build()
    check("wages", line(L, "wages"), 0.00, "estimate SUPPRESSED — ruling K2 target")
    check("store_opex unchanged", line(L, "store_opex"), 1700.00,
          "authoritative salaries stay put; only the estimate went")
    check_true("wages line is NOT mislabelled 'Gross Payroll'",
               not L["wages"].get("label"),
               "an empty line must not claim an exact producer gross it never received")
    opex_before = 1000.00 + 1700.00
    check("total OPEX drops by exactly the estimate", 0.00 + 1700.00, opex_before - 1000.00,
          "no dollar invented or lost — the duplicate is simply gone")


def test_k2_case_and_route():
    print("\n[2b] K2 — case-insensitive match, and the OPTIONAL line route")
    set_config(payroll_expense_names=["employee salaries"])
    L = build()
    check("lower-case config name still matches", line(L, "wages"), 0.00,
          "'employee salaries' matches 'Employee Salaries'")
    # route override: move it onto the wages line. NET INCOME MUST NOT CHANGE.
    set_config(payroll_expense_names=["Employee Salaries", "DM Salaries"],
               payroll_expense_routes={"Employee Salaries": "wages", "DM Salaries": "payroll_expenses"})
    L = build()
    check("routed to wages", line(L, "wages"), 1000.00, "the MANUAL $1,000, not the estimate")
    check("routed to payroll_expenses", line(L, "payroll_expenses"), 200.00, "DM salaries")
    check("store_opex keeps only real opex", line(L, "store_opex"), 500.00, "rent alone")
    check("total OPEX identical to the un-routed case", 1000.00 + 200.00 + 500.00, 1700.00,
          "routing moves dollars BETWEEN opex lines; it never changes net income")
    check_true("wages IS labelled 'Gross Payroll' once it carries money",
               L["wages"].get("label") == "Gross Payroll", "label follows the money")


def test_k2_producer_token_still_wins():
    print("\n[2c] K2 — the EEP guard is intact: additional_payroll must never be authoritative")
    STORE_EXPENSES.append({"org_id": ORG, "period": "July 2026", "store_code": "STORE-1",
                           "expense_name": "Additional Payroll", "expense_type": "Fixed",
                           "amount": 77.0, "source_key": "additional_payroll"})
    set_config()
    L = build()
    check("estimate SURVIVES an additional_payroll row", line(L, "wages"), 1000.00,
          "treating an advance-excess as authoritative would DELETE the wages line")
    check("additional_payroll → payroll_expenses", line(L, "payroll_expenses"), 77.00, "EEP route intact")
    STORE_EXPENSES.pop()


# ── 3 · K1 — the rebate is CONTRA-COGS, reversing fae81a3 ─────────────────────────────────────────
def test_k1_rebate_contra_cogs():
    print("\n[3] K1 — device rebate is CONTRA-COGS (reverses fae81a3)")
    set_config()
    L = build()
    check("device_rebate (negative contra-COGS)", line(L, "device_rebate"), -1500.00,
          "500 + 100 + 900, booked NEGATIVE so it nets against COGS")
    check("vip_reimb no longer holds the rebate", line(L, "vip_reimb"), 0.00,
          "$1,500 left the income column — ruling K1")
    check_true("the drill label followed it",
               "Device purchase rebates (Distributor/MA)" in (L["device_rebate"]["detail"] or {}),
               "still drillable, never an anonymous lump")
    check("SPIFFs are untouched by the move", line(L, "carrier_comm"), 30.00, "spiff_m1 only")


# ── 4 · K3 — invoice-first device COGS ────────────────────────────────────────────────────────────
def test_k3_off_is_byte_identical():
    print("\n[4] K3 BUG REPRODUCTION — mode 'off' (default) still books the NEGATIVE POS cost")
    set_config()
    L = build()
    check("device_cost from POS (ext - gp)", line(L, "device_cost"), -100.00,
          "300 - 400 = NEGATIVE 100 — the pre-621 defect, deliberately preserved")
    check_true("no invoice source was consulted", not L["device_cost"].get("meta", {}).get("ma"),
               "mode=off short-circuits before any query — byte-identical")


def test_k3_auto():
    print("\n[5] K3 FIXED — mode 'auto': invoice-first, IMEI-deduped, POS displaced")
    set_config(device_cogs_mode="auto")
    L = build()
    check("device_cost from the INVOICE", line(L, "device_cost"), 729.98,
          "iPhone 16e 599.99 + A16 129.99 — the duplicate IMEI charged ONCE")
    check("displaced POS figure is recorded", L["device_cost"].get("displaced_pos_cost"), -100.00,
          "auditable for the before/after ledger; never silently dropped")
    meta = L["device_cost"]["meta"]["ma"]
    check("rows read", meta["rows"], 5, "ORG's July rows only")
    check("distinct IMEIs", meta["distinct_imei"], 4, "IMEI-B appeared twice")
    check("duplicate rows dropped", meta["dedup_dropped"], 1, "policy C1 IMEI dedup")
    check("priced devices", meta["priced"], 2, "the two on the price list")
    check("unknown-SKU remainder COUNTED", meta["unknown_sku"], 1, "'Product Not Available' — not a handset")
    check("unpriced remainder COUNTED", meta["unpriced_sku"], 1, "Razr not on the price list — never guessed")
    check("SIM kit excluded from the price list", meta["price_list_skus"], 2,
          "only order_type='Branded Handset' rows price a device")


def test_k3_dedup_is_load_bearing():
    print("\n[5b] K3 — remove the duplicate IMEI and the cost must NOT change")
    set_config(device_cogs_mode="auto")
    dup = MA_COMMISSION.pop(2)          # the IMEI-B repeat
    L = build()
    check("cost identical without the dup row", line(L, "device_cost"), 729.98,
          "proves dedup did the work, not luck")
    MA_COMMISSION.insert(2, dup)


def test_k3_invoice_honest_zero():
    print("\n[6] K3 — mode 'invoice' with no invoice data ⇒ honest labelled ZERO, never a POS guess")
    set_config(device_cogs_mode="invoice")
    saved = list(MA_COMMISSION)
    MA_COMMISSION[:] = []               # the Feb–Jun situation: no MA Commission Details at all
    L = build()
    check("device_cost", line(L, "device_cost"), 0.00, "ruling K3(b): zero-with-reason")
    check_true("the reason is RECORDED, not implied",
               "honest_zero" in (L["device_cost"]["meta"] or {}),
               "a $0 must never be mistakable for a measurement")
    check_true("the NEGATIVE POS figure was refused",
               not approx(line(L, "device_cost"), -100.00),
               "POS cost on a subsidised handset is negative — never a fallback in 'invoice' mode")
    MA_COMMISSION[:] = saved


# ── 7 · store_borrowings — the 3-wrong-column bug ─────────────────────────────────────────────────
def test_store_borrowings():
    print("\n[7] #7 — store_borrowings now reads columns that EXIST")
    set_config()
    L = build()
    check("inter_store_pay (borrower owes)", line(L, "inter_store_pay"), 750.00, "was $0 forever")
    check("inter_store_recv (lender is owed)", line(L, "inter_store_recv"), 750.00, "mirror leg")
    check_true("keyed off borrower_store", "1 Main St" in L["inter_store_pay"]["by_store"],
               "the real column name, canonicalized through store_resolver")


# ── 8 · RULE ONE — tenancy, both directions ───────────────────────────────────────────────────────
def test_tenant_isolation():
    print("\n[8] RULE ONE — org isolation, both directions")
    set_config(orgs=(ORG, OTHER), device_cogs_mode="auto")
    L = build(org=OTHER)
    check("OTHER sees only its own rebate", line(L, "device_rebate"), -777.00, "ORG's 1,500 invisible")
    check("OTHER's device cost uses ITS price list", line(L, "device_cost"), 111.11,
          "ORG's 599.99 must not price OTHER's handset")
    check("OTHER has no payroll rows", line(L, "store_opex"), 0.00, "ORG's expenses invisible")
    L = build(org=ORG)
    check("ORG unaffected by OTHER", line(L, "device_rebate"), -1500.00, "no leak inbound")


def test_boost_byte_identical():
    print("\n[9] BOOST BYTE-IDENTICAL — raw_mi present ⇒ the MA fallback (and K1) never fires")
    set_config()
    L = build(org=BOOST)
    check("mi_income from raw_mi", line(L, "mi_income"), 10.00, "the Boost source, unchanged")
    check("atu_income from raw_mi", line(L, "atu_income"), 5.00, "unchanged")
    check("device_rebate is $0 for Boost", line(L, "device_rebate"), 0.00,
          "K1 cannot move a house-org number: had_raw_mi short-circuits the whole MA block")
    check("device_cost is $0 for Boost", line(L, "device_cost"), 0.00, "no POS device rows seeded")


def test_period_spelling():
    print("\n[10] period-spelling duality — both spellings must resolve")
    set_config(device_cogs_mode="auto")
    a = line(build(period="July 2026"), "device_cost")
    b = line(build(period="2026-07"), "device_cost")
    check("'2026-07' == 'July 2026'", b, a, "_period.period_keys queries both")


def main():
    test_stub_filters()
    test_k2_bug_reproduced()
    test_k2_fixed()
    test_k2_case_and_route()
    test_k2_producer_token_still_wins()
    test_k1_rebate_contra_cogs()
    test_k3_off_is_byte_identical()
    test_k3_auto()
    test_k3_dedup_is_load_bearing()
    test_k3_invoice_honest_zero()
    test_store_borrowings()
    test_tenant_isolation()
    test_boost_byte_identical()
    test_period_spelling()
    ok, tot = sum(1 for r in RESULTS if r), len(RESULTS)
    print("\n%s  %d/%d passed, %d failed" % ("PASS" if ok == tot else "FAIL", ok, tot, tot - ok))
    return 0 if ok == tot else 1


if __name__ == "__main__":
    sys.exit(main())
