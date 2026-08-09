"""Chart of Accounts — deterministic aggregation (the authoritative numbers).

Maps the app's existing money data into the confirmed chart of accounts
(CHART_OF_ACCOUNTS.md, cash basis, multi-company) and returns per-line totals,
broken out by store where the source carries a store key so the P&L / Balance
Sheet can be scoped consolidated / per-company / per-store.

Sources (columns verified against the live backend):
  • raw_mi              actual_mi_payout, actual_atu_payout            (company-wide)
  • raw_comp_report     business_address, compensation_type, payment_amount
  • raw_sales           department, ext_price, gp, voided, store
  • asset_ledger        owed_to_vip, reimbursement, reimbursement_date, selling_price,
                        status (== "On Inventory" ⇒ unsold), category, store, acquired_date
  • rep_commissions     total_payout, store
  • chargeback_items    amount, deduct, store
  • vip_invoices        grand_total, shipping, other_cost, status, location
  • vip_paygo_payments  amount, batch_type (pending|approved), dealer, period
  • store_expenses      amount, expense_name, expense_type, store_code  (store_code → store_address)
  • journal_entries     MANUAL P&L + Balance-Sheet lines
  • store_companies     store_address → company_id (Default Company otherwise)

Lines whose source has no usable store key (MI/ATU residual, carrier comp w/o a
matching store) are "company-wide": they appear in the CONSOLIDATED view only and
read 0 (with a note) under a company/store filter — honest beats mis-attributed.
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc import carrier_map
from app.modules.account import _period
# Canonical finance period parser lives in _period; re-exported here so existing
# `coa.parse_period` callers (recon, engine, router) keep resolving unchanged.
from app.modules.account._period import parse_period  # noqa: F401

ORG_ID = "00000000-0000-0000-0000-000000000001"

# Boost taxonomy — kept ONLY as the emergency fallback for `_sales_classifier` (below). Live
# device/accessory classification is now resolved from the SAME per-tenant config the commission side
# uses (commcalc.accessory_config mig 208 + commcalc.gp_category_map mig 069), so a non-Boost tenant
# whose POS departments/categories differ (luxelink: dept 'BrandedHandset' holds both the 'KittedBranded'
# phone and the 'HandsetBranded' accessory) classifies correctly. An EMPTY config reproduces these two
# sets exactly ⇒ Boost stays byte-identical. See `_sales_classifier`.
DEVICE_DEPTS = {"Android - XP", "IPHONE - XP", "TABLET - XP"}
ACCESSORY_DEPT = "Ondigo"
VIP_FEE_CATS = {"PROCESSING FEE", "SHIPPING", "SIM KIT"}
# Accounting rule (user-set 2026-06-20): accessory COGS is a flat 20% of gross accessory
# sales, NOT the per-line recorded cost (B2B accessory lines often carry no cost → GP looked
# inflated). Commission payout still treats accessories as 100% of gross sales separately.
# NOTE: 0.20 is now only the DEFAULT / fallback — the live rate is resolved PER-ORG from
# commcalc.account_config (mig 611) via `_account_config`, so a tenant can carry its own accessory
# margin. An empty/absent config yields 0.20 for every org ⇒ Boost byte-identical. See build_inputs.
ACCESSORY_COGS_PCT = 0.20

# ── chart-of-accounts line specs ───────────────────────────────────────────────────────────
# section ∈ revenue|cogs|opex|other (P&L) and asset|liability|equity (Balance Sheet)
# kind: "auto" (derived here), "auto*" (derived once a dependency ships; degrades to 0),
#       "manual" (entered via journal_entries), "computed" (derived from other lines)
PL_SPEC = [
    ("carrier_comm",  "Carrier commissions & incentives",            "revenue", "auto",  "company"),
    ("mi_income",     "MI residual income",                          "revenue", "auto",  "company"),
    ("atu_income",    "ATU income",                                  "revenue", "auto",  "company"),
    ("accessory_rev", "Accessory sales revenue",                     "revenue", "auto",  "store"),
    ("device_rev",    "Device sales revenue",                        "revenue", "auto",  "store"),
    ("vip_reimb",     "Device-financing reimbursements (Distributor)", "revenue", "auto",  "store"),
    ("service_income","Service fee income (bill-pay & other fees)",  "revenue", "auto",  "store"),
    ("vip_device_pay","Distributor device payments (PayGo, paid)",   "cogs",    "auto",  "store"),
    ("accessory_cost","Accessory cost",                              "cogs",    "auto",  "store"),
    ("device_cost",   "Device cost",                                 "cogs",    "auto",  "store"),
    ("vip_fees",      "Distributor fees paid (shipping / SIM kit / processing)", "cogs", "auto", "store"),
    ("rep_comm",      "Rep commissions paid",                        "opex",    "auto",  "store"),
    ("wages",         "Wages / hourly payroll",                      "opex",    "auto",  "store"),
    ("payroll_expenses", "Payroll Expenses",                         "opex",    "auto_opt", "store"),
    ("chargebacks",   "Chargebacks / clawbacks",                     "opex",    "auto",  "store"),
    ("store_opex",    "Store operating expenses (rent / utilities / supplies)", "opex", "auto", "store"),
]
BS_SPEC = [
    ("cash",            "Cash / bank",                               "asset",     "manual",  None),
    ("inventory",       "Inventory — on-hand device value",          "asset",     "auto",    "store"),
    ("inter_store_recv","Inter-store receivable (lent to stores)",   "asset",     "auto*",   "store"),
    ("fixtures",        "Fixtures / equipment",                      "asset",     "manual",  None),
    ("owed_vip",        "Owed to Distributor (device financing)",    "liability", "auto",    "store"),
    ("vip_ap",          "Accounts payable — Distributor invoices unpaid", "liability", "auto",  "store"),
    ("inter_store_pay", "Inter-store payable (borrowed)",            "liability", "auto*",   "store"),
    ("chargeback_res",  "Chargeback reserve",                        "liability", "auto",    "store"),
    ("owner_capital",   "Owner capital / contributions",             "equity",    "manual",  None),
    ("retained",        "Retained earnings (accumulated net income)","equity",    "computed",None),
]
PL_LABEL = {k: lbl for k, lbl, *_ in PL_SPEC}
BS_LABEL = {k: lbl for k, lbl, *_ in BS_SPEC}


def _norm_store(s):
    return (str(s or "").strip()) or None


def _in_period(date_str, pm, py):
    """date_str like 'YYYY-MM-DD...' (or 'MM/DD/YYYY' / 'MM/DD/YY') falls in period (pm,py)?"""
    s = str(date_str or "").strip()
    if not s:
        return False
    try:
        if "-" in s[:10] and s[:4].isdigit():
            y, m = int(s[:4]), int(s[5:7])
        elif "/" in s:
            a, b, c = (s[:10].split("/") + ["", "", ""])[:3]
            cc = "".join(ch for ch in c if ch.isdigit())
            if not a.isdigit() or not cc:
                return False
            m, y = int(a), int(cc)
            if y < 100:            # 2-digit year ('26' → 2026); previously dropped (never matched a 4-digit py)
                y += 2000
        else:
            return False
        return m == pm and y == py
    except Exception:
        return False


def _fetch_all(client, table, select, eqs=None, page=1000, cap=200000):
    """Paginated select of an entire (org-scoped) table — supabase caps a query at 1000 rows.
    A list/tuple/set filter value becomes an IN (...) clause (used for multi-spelling period)."""
    out, start = [], 0
    while start < cap:
        q = client.schema("commcalc").table(table).select(select)
        for k, v in (eqs or {}).items():
            q = q.in_(k, list(v)) if isinstance(v, (list, tuple, set)) else q.eq(k, v)
        rows = (q.range(start, start + page - 1).execute().data) or []
        out.extend(rows)
        if len(rows) < page:
            break
        start += page
    return out


def _sales_union_rows(client, org_id, period_keys):
    """The P&L's sales source: raw_sales UNION daily_sales_feed for the period, deduped by trans_id
    (raw_sales wins). This is what makes a tenant's GP correct WHETHER OR NOT the daily feed has been
    promoted into raw_sales yet (luxelink, 2026-07): the commission engine already reads the feed for
    the open month, so without this the books and the payout disagreed on what "sales" means.

    Boost-neutral proof: for a CLOSED / fully-promoted month every feed trans_id already exists in
    raw_sales, so the dedup drops every feed row → the merged set == raw_sales, byte-identical to the
    prior raw_sales-only read. A trans_id present in BOTH tables is therefore counted exactly once
    (raw_sales' copy). Only feed rows carrying a trans_id NOT in raw_sales (the not-yet-promoted days)
    are added, and a row with no trans_id can't be verified non-overlapping so it is only trusted from
    raw_sales — never pulled off the feed on top of a populated raw_sales. When raw_sales is EMPTY for
    the period (promote never ran) the whole feed is used. NEVER raises: a feed read failure degrades
    to raw_sales alone. Mirrors commcalc's `_read_sales`/`_sales_rows_union` union intent."""
    # `category` + `product_desc` are carried so the P&L device/accessory classifier can use the
    # CATEGORY discriminator (the luxelink case) — both columns exist on raw_sales AND daily_sales_feed
    # (only `sku` is feed-absent; see commcalc._q). A missing column on the feed degrades to raw-only.
    cols = "trans_id,department,category,product_desc,ext_price,gp,voided,store"
    raw = _fetch_all(client, "raw_sales", cols, {"org_id": org_id, "period": period_keys})
    try:
        feed = _fetch_all(client, "daily_sales_feed", cols, {"org_id": org_id, "period": period_keys})
    except Exception:
        feed = []
    if not raw:
        return feed
    if not feed:
        return raw
    seen = {str(r.get("trans_id") or "").strip() for r in raw if str(r.get("trans_id") or "").strip()}
    merged = list(raw)
    for r in feed:
        tid = str(r.get("trans_id") or "").strip()
        if tid and tid not in seen:          # feed-only trans (unpromoted) — add once; never double-count
            merged.append(r)
    return merged


def store_company_map(client, org_id):
    """store_address (normalized) -> company_id, plus a default-company id."""
    companies = (client.schema("commcalc").table("companies").select("id,name")
                 .eq("org_id", org_id).execute().data) or []
    default_id = next((c["id"] for c in companies if c["name"] == "Default Company"),
                      (companies[0]["id"] if companies else None))
    mp = {}
    for r in (_fetch_all(client, "store_companies", "store_address,company_id", {"org_id": org_id})):
        sa = _norm_store(r.get("store_address"))
        if sa:
            mp[sa.upper()] = r.get("company_id")
    return mp, default_id, companies


def wages_by_store(client, org_id, period):
    """StoreOps payroll for the period → {store_address: wages}. hours = actual (fallback
    scheduled) × employee pay_rate; store = shift.store_code (fallback employee home_store),
    mapped store_code → store_address. Returns {} if StoreOps has no shifts for the month."""
    pm, py = parse_period(period)
    if not pm or not py:
        return {}
    month = f"{py:04d}-{pm:02d}"
    nxt = f"{py + 1:04d}-01-01" if pm == 12 else f"{py:04d}-{pm + 1:02d}-01"
    so = client.schema("storeops")
    code2addr = store_code_to_address(client, org_id)
    emps = (so.table("employees").select("employee_id,pay_rate,home_store").eq("org_id", org_id).execute().data) or []
    rate = {e.get("employee_id"): safe_float(e.get("pay_rate")) for e in emps}
    home = {e.get("employee_id"): e.get("home_store") for e in emps}
    shifts = (so.table("shifts")
              .select("employee_id,store_code,scheduled_hours,actual_hours,shift_date,is_deleted")
              .eq("org_id", org_id).eq("is_deleted", False).gte("shift_date", f"{month}-01").lt("shift_date", nxt)
              .range(0, 9999).execute().data) or []
    out = {}
    for s in shifts:
        eid = s.get("employee_id")
        hrs = safe_float(s.get("actual_hours")) or safe_float(s.get("scheduled_hours"))
        pay = round(hrs * rate.get(eid, 0.0), 2)
        if not pay:
            continue
        code = _norm_store(s.get("store_code")) or _norm_store(home.get(eid))
        addr = code2addr.get(code, code)
        if addr:
            out[addr] = round(out.get(addr, 0.0) + pay, 2)
    return out


def store_code_to_address(client, org_id):
    """store_expenses keys by store_code; map it to the canonical store_address."""
    out = {}
    for r in (_fetch_all(client, "store_mapping", "store_code,store_address", {"org_id": org_id})):
        c, a = _norm_store(r.get("store_code")), _norm_store(r.get("store_address"))
        if c and a:
            out[c] = a
    return out


def _account_config(client, org_id):
    """Per-org finance/accounting config (commcalc.account_config, mig 611). Currently one knob:
    accessory_cogs_pct. Returns a dict of resolved values with the historical code defaults filled
    in, so a tenant with NO config row (and any tenant before mig 611 runs) is byte-identical to the
    old hard-coded behaviour. NEVER raises — a missing table/row degrades to the defaults."""
    cfg = {"accessory_cogs_pct": ACCESSORY_COGS_PCT,
           "service_fee_products": set(), "service_fee_products_list": []}
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("accessory_cogs_pct").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and rows[0].get("accessory_cogs_pct") is not None:
            pct = safe_float(rows[0]["accessory_cogs_pct"])
            if 0 <= pct <= 1:
                cfg["accessory_cogs_pct"] = pct
    except Exception:
        pass
    # SERVICE-FEE products (mig 613) — its OWN defensive query, exactly like the commcalc config
    # resolver: a missing column (pre-613) can never disturb the rate above, it just falls back to an
    # EMPTY list, and an empty list books nothing (every tenant byte-identical).
    try:
        srows = (client.schema("commcalc").table("account_config")
                 .select("service_fee_products").eq("org_id", org_id).limit(1).execute().data) or []
        if srows and isinstance(srows[0].get("service_fee_products"), list):
            picked = [str(p).strip() for p in (srows[0].get("service_fee_products") or []) if str(p).strip()]
            cfg["service_fee_products_list"] = picked
            cfg["service_fee_products"] = {p.lower() for p in picked}
    except Exception:
        pass
    return cfg


def store_resolver(client, org_id):
    """Return resolve(raw) -> canonical store_address, mirroring the app's existing store
    canonicalization so one physical store never appears under two spellings.

    Every source table carries the store in its own form (raw_sales.store, asset_ledger.store,
    vip_invoices.location, vip_paygo.dealer, raw_comp_report.business_address, a store_code, …).
    Resolution chain (same precedence the rest of the app uses):
      1. exact store_mapping.store_address (case-insensitive)        — daily_sales_actuals
      2. store_aliases.alias → store_code → store_address            — migration 023
      3. raw string IS a store_code                                  — store_expenses path
      4. leading store-number matches a known store_mapping address  — the DLAR join (calculator)
      5. unmappable (genuinely unknown store) → the cleaned raw string, kept as-is.
    Only steps that land on an address already in store_mapping merge variants, so this can never
    invent a merge between two distinct stores — it just collapses spellings of a known one."""
    def _num_key(token):
        """Leading street number, digits only — collapses hyphenated/punctuated forms so
        '116-36' and '11636' share a key. None for non-numeric leads ('B-1800') so those only
        ever match exactly, never by number."""
        if not token or not token[:1].isdigit():
            return None
        return "".join(ch for ch in token if ch.isdigit()) or None

    addr_by_addr, addr_by_code, num_addrs = {}, {}, {}
    for r in _fetch_all(client, "store_mapping", "store_code,store_address", {"org_id": org_id}):
        addr = _norm_store(r.get("store_address"))
        code = _norm_store(r.get("store_code"))
        if addr:
            addr_by_addr[addr.lower()] = addr
            nk = _num_key(addr.split(" ")[0])
            if nk:
                num_addrs.setdefault(nk, set()).add(addr)
        if addr and code:
            addr_by_code[code.upper()] = addr
    # only resolve by leading number when it is UNAMBIGUOUS (street numbers aren't unique —
    # "3 Palisade Ave" and "3 Broadway" would both be number "3"). Ambiguous numbers fall through.
    addr_by_num = {n: next(iter(a)) for n, a in num_addrs.items() if len(a) == 1}
    alias_addr = {}
    try:
        for r in _fetch_all(client, "store_aliases", "alias,store_code", {"org_id": org_id}):
            al, code = _norm_store(r.get("alias")), _norm_store(r.get("store_code"))
            if al and code and code.upper() in addr_by_code:
                alias_addr[al.lower()] = addr_by_code[code.upper()]
    except Exception:
        pass  # store_aliases (migration 023) not yet run → chain still works without it

    def resolve(raw):
        s = _norm_store(raw)
        if not s:
            return None
        low = s.lower()
        if low in addr_by_addr:
            return addr_by_addr[low]
        if low in alias_addr:
            return alias_addr[low]
        if s.upper() in addr_by_code:
            return addr_by_code[s.upper()]
        nk = _num_key(s.split(" ")[0])
        if nk and nk in addr_by_num:
            return addr_by_num[nk]
        return s

    return resolve


def _sales_classifier(client, org_id):
    """Device-vs-accessory classification for the P&L sales lines, resolved from the SAME per-tenant
    config the commission side uses — converging the classifiers instead of adding a divergent one
    (see [[accessory-flow-divergences]]). Returns (is_accessory(dept, category, product), is_device(dept)).

      • accessory ← commcalc `_accessory_config` / `_is_accessory` (per-org commcalc.accessory_config,
        mig 208, keyed on org_id; PLUS the gp_category_map 'accessory' department bridge, mig 069).
        Matches on DEPARTMENT **or** CATEGORY **or** product keyword — CATEGORY is the discriminator
        where a department is ambiguous (luxelink: dept 'BrandedHandset' holds the 'KittedBranded' phone
        AND the 'HandsetBranded' accessory; dept 'Handset' holds 'SimMarketplace' + 'Accessories').
      • device    ← commcalc `gp_report._dept_classifier` over commcalc.gp_category_map (mig 069):
        `classify(dept) == 'device'`. For a tenant whose device rows sit in an otherwise-accessory-shared
        department, map that DEPARTMENT → 'device' in gp_category_map — the accessory-FIRST precedence at
        the call site lets CATEGORY peel the accessory lines off before the department is treated as device.

    BOOST BYTE-IDENTICAL: an empty accessory_config falls back to department 'Ondigo' and an empty
    gp_category_map falls back to the built-in DEVICE_DEPTS — so with NO tenant config the two resolvers
    reproduce the old `dept == 'Ondigo'` / `dept in DEVICE_DEPTS` buckets exactly (the accessory match is
    case-insensitive, a superset that never *loses* a Boost 'Ondigo' row). NEVER raises: any failure
    degrades to the hard-coded Boost taxonomy so classification can never regress."""
    try:
        from app.modules.commcalc.router import _accessory_config, _is_accessory
        from app.modules.commcalc.gp_report import _dept_classifier
        acfg = _accessory_config(client, org_id)
        try:
            gp_map = (client.schema("commcalc").table("gp_category_map")
                      .select("department,category").eq("org_id", org_id).limit(1000).execute().data) or []
        except Exception:
            gp_map = []
        classify_dept = _dept_classifier(gp_map)
        return (lambda dept, category, product: _is_accessory(dept, category, product, acfg),
                lambda dept: classify_dept(dept) == "device")
    except Exception:
        # Hard fallback — the historical Boost taxonomy, so a resolver/import failure never regresses P&L.
        return (lambda dept, category, product: (dept or "").strip() == ACCESSORY_DEPT,
                lambda dept: (dept or "").strip() in DEVICE_DEPTS)


# ── store_expenses system-line routing (source_key → the P&L line it books into) ───────────────
# `store_expenses.source_key` (mig 206) is NULL for a hand-entered expense and a PRODUCER-CONTRACT
# token for an AUTO ("system") line pushed through POST /commcalc/expenses/{period}/system-line.
# This table is the single place that decides which P&L line each producer's token lands on.
#
# RULE TWO note — why a code table and not a config table: a source_key is a PROTOCOL constant
# agreed between two modules (like an HTTP header name), not tenant data. The tenant-authored part
# of a closing expense is its CATEGORY NAME, which travels in `expense_name` and is used only as the
# drill-down label — so a tenant adding/renaming an expense category never needs a code change here.
#
#   'payroll_gross'      → `wages`             the EXACT gross paid (mod-people). AUTHORITATIVE:
#                                              suppresses the shifts×rate estimate (see below).
#   'payroll_expenses'   → `payroll_expenses`  employer burden (tax / unemployment / WC), mod-people.
#   'additional_payroll' → `payroll_expenses`  EEP 2026-08-04: the excess of envelope CASH salary
#                                              advanced over what the employee actually earned
#                                              (mod-people). It is payroll COST, so it does not
#                                              belong in generic store_opex; it is NOT the clock-in
#                                              gross, so it must never touch `wages`.
#   'closing_expense:<category-id>'
#                        → `store_opex`        EEP 2026-08-04: the per-(period, store, category)
#                                              rollup of expense-KIND daily-closing expenses
#                                              (mod-retail-ops). Drill label = the category name.
#                                              This is the P&L auto-fill the owner asked for.
#   anything else (NULL / 'pto_accrual' / a future producer)
#                        → `store_opex`        unchanged pre-existing behaviour, drill = expense_name.
#
# Second element = the FALLBACK drill label, used only when the row carries no expense_name.
# None = this line carries no drill-down (the wages line is one exact figure, not a breakdown).
_EXPENSE_ROUTES = {
    "payroll_gross":      ("wages", None),
    "payroll_expenses":   ("payroll_expenses", "Payroll Expenses"),
    "additional_payroll": ("payroll_expenses", "Additional Payroll"),
}
_EXPENSE_PREFIX_ROUTES = (
    ("closing_expense:", ("store_opex", "Store expense")),
)
_DEFAULT_EXPENSE_ROUTE = ("store_opex", "Expense")

# ONLY these source_keys carry the EXACT gross payroll and therefore SUPPRESS the StoreOps
# shifts×rate wages fallback. 'additional_payroll' must NEVER be added here: it is an excess ON TOP
# of the clock-in gross, so treating it as authoritative would DELETE the wages line for any tenant
# that pays a cash advance but does not push a payroll_gross line. (Double-count guard, EEP.)
_WAGES_AUTHORITATIVE_KEYS = {"payroll_gross"}


def route_expense_line(source_key):
    """PURE: a `store_expenses.source_key` → (P&L line key, fallback drill label).

    Exact match first, then prefix match (the closing-expense family carries the category id in the
    key: 'closing_expense:<uuid>'), then the historical default (`store_opex`). Never raises; a
    None/blank key is the manual-expense case and takes the default, so pre-mig-206 rows and
    hand-entered expenses behave exactly as they always have."""
    sk = (source_key or "").strip()
    if sk in _EXPENSE_ROUTES:
        return _EXPENSE_ROUTES[sk]
    for prefix, route in _EXPENSE_PREFIX_ROUTES:
        if sk.startswith(prefix):
            return route
    return _DEFAULT_EXPENSE_ROUTE


# ── per-line aggregation: each store-keyed line → {store_address: amount}; company-wide → scalar
def build_inputs(client, org_id, period):
    """Aggregate every chart-of-accounts line for `period`. Returns a dict:
       { line_key: {"by_store": {store: amt}, "company_wide": amt, "detail": {label: amt}} }
       'detail' carries drill-down sub-lines (e.g. each expense name)."""
    pm, py = parse_period(period)
    # Upload paths store the period inconsistently — the daily-sales upload writes the month-name
    # form ("June 2026") while compute is invoked with "2026-06". Query BOTH spellings (via the
    # shared finance helper) so the period-string sources (raw_sales/raw_mi/comp/rep_commissions/...)
    # aren't silently empty. See _period.period_keys for the single source of truth.
    period_keys = _period.period_keys(period)
    code2addr = store_code_to_address(client, org_id)
    resolve_store = store_resolver(client, org_id)
    acct_cfg = _account_config(client, org_id)           # per-org finance knobs (mig 611); default = Boost
    accessory_cogs_pct = acct_cfg["accessory_cogs_pct"]  # 0.20 default ⇒ Boost byte-identical
    service_fee_products = acct_cfg["service_fee_products"]   # mig 613; empty ⇒ nothing booked

    L = {k: {"by_store": {}, "company_wide": 0.0, "detail": {}} for k, *_ in PL_SPEC + BS_SPEC}

    def add(key, store, amt, detail_label=None):
        amt = round(safe_float(amt), 2)
        if not amt:
            return
        # canonicalize the store key so one physical store never splits across spellings
        s = resolve_store(store) if store else None
        if s:
            L[key]["by_store"][s] = round(L[key]["by_store"].get(s, 0.0) + amt, 2)
        else:
            L[key]["company_wide"] = round(L[key]["company_wide"] + amt, 2)
        if detail_label:
            L[key]["detail"][detail_label] = round(L[key]["detail"].get(detail_label, 0.0) + amt, 2)

    # raw_mi — MI + ATU residual income (company-wide). CARRIER-AGNOSTIC, mirroring the shipped
    # residual-per-sub report (residual_subs._aggregate, dcb0807): Boost is the primary source
    # (raw_mi); a tenant with NO raw_mi for the period falls through to the VidaPay/MA tables so its
    # residual income is not silently $0 on the books. Source chosen by which data EXISTS per org —
    # never by tenant name. BOOST BYTE-IDENTICAL: a Boost org always has raw_mi for the period, so the
    # MA fallback never fires (and MA tables are empty for a Boost org regardless). Each MA source is
    # read exactly once → no double-count. See the MONEY-TOUCHING note in the finance handoff.
    had_raw_mi = False
    try:
        for r in _fetch_all(client, "raw_mi", "actual_mi_payout,actual_atu_payout",
                            {"org_id": org_id, "period": period_keys}):
            had_raw_mi = True
            add("mi_income", None, r.get("actual_mi_payout"))
            add("atu_income", None, r.get("actual_atu_payout"))
    except Exception:
        had_raw_mi = False
    if not had_raw_mi:
        # VidaPay/MA residual (Total, luxelink). SAME two figures the shipped /ma-commission/summary +
        # residual-per-sub report use (mig 083): MI-equivalent = MA Commission Details payable
        # (raw_ma_commission, sign-flipped Σ components — positive = money the dealer receives);
        # ATU-equivalent = airtime margin (raw_ma_daily_tx.merchant_discount). MA rows carry no
        # store_address (only a processor merchant/account id), so — like PayGo — this is booked
        # COMPANY-WIDE (store=None) rather than inventing a phantom per-store bucket keyed by an
        # account id. Empty MA tables (data not yet ingested) → $0, correct. NEVER raises.
        try:
            from app.modules.account.residual_subs import _MA_COMPONENTS
            for r in _fetch_all(client, "raw_ma_commission", ",".join(_MA_COMPONENTS),
                                {"org_id": org_id, "period": period_keys}):
                add("mi_income", None, -sum(safe_float(r.get(c)) for c in _MA_COMPONENTS))
        except Exception:
            pass
        try:
            for r in _fetch_all(client, "raw_ma_daily_tx", "merchant_discount",
                                {"org_id": org_id, "period": period_keys}):
                add("atu_income", None, r.get("merchant_discount"))
        except Exception:
            pass

    # raw_comp_report — carrier commissions/incentives. Broken into canonical components via
    # carrier_category_map (framework): same carrier_comm total, with a Commission/SPIFF/Reimbursement
    # drill-down. Unmapped rows fall under "Unmapped" so nothing is hidden. (zero change to totals.)
    try:
        try:
            _cc_rules = carrier_map.load_rules(client, org_id)
        except Exception:
            _cc_rules = []
        _CC_LABEL = {"COMMISSION": "Commission (promo)", "SPIFF": "SPIFF / bounty",
                     "REIMBURSEMENT": "Reimbursement", "RESIDUAL": "Residual"}
        for r in _fetch_all(client, "raw_comp_report",
                            "business_address,payment_amount,period,compensation_type",
                            {"org_id": org_id, "period": period_keys}):
            comp = None
            if _cc_rules:
                m = carrier_map.match_rule(_cc_rules, r.get("compensation_type"))
                comp = m.get("component") if m else None
            add("carrier_comm", _norm_store(r.get("business_address")), r.get("payment_amount"),
                detail_label=_CC_LABEL.get(comp, "Unmapped"))
    except Exception:
        pass

    # sales — accessory/device revenue + cost (store). UNIFIED source: raw_sales ∪ daily_sales_feed
    # (dedup by trans_id, raw_sales wins) so GP is correct whether or not the daily feed was promoted
    # into raw_sales — see _sales_union_rows for the Boost-neutral / no-double-count proof.
    is_accessory, is_device = _sales_classifier(client, org_id)
    try:
        for r in _sales_union_rows(client, org_id, period_keys):
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            dept = (r.get("department") or "").strip()
            cat, prod = r.get("category"), r.get("product_desc")
            ext, gp = safe_float(r.get("ext_price")), safe_float(r.get("gp"))
            st = _norm_store(r.get("store"))
            # Classify device vs accessory from the SAME per-tenant config the commission side uses.
            # ACCESSORY-FIRST: CATEGORY discriminates accessory lines inside an otherwise-device
            # department (luxelink). Empty config ⇒ Boost byte-identical (Ondigo→accessory, *-XP→device).
            # SERVICE FEE first (mig 613, owner 2026-08-09): a fee the store CHARGES is income, and it
            # is identifiable only by product — its department holds the pass-through bill payments too.
            # EXACT match, never containment: "ePay Service Charge" must not also catch the refill line
            # it rides on. Booked at full price with NO COGS — the store incurs no cost to collect it.
            # It is checked BEFORE accessory/device because an explicit owner pick outranks a taxonomy.
            if service_fee_products and str(prod or "").strip().lower() in service_fee_products:
                add("service_income", st, ext, detail_label=str(prod or "").strip() or None)
            elif is_accessory(dept, cat, prod):
                add("accessory_rev", st, ext)
                add("accessory_cost", st, ext * accessory_cogs_pct)
            elif is_device(dept):
                add("device_rev", st, ext)
                add("device_cost", st, ext - gp)
    except Exception:
        pass

    # asset_ledger — reimbursement income (cash, by reimbursement_date), VIP fees (COGS),
    # inventory value (BS), owed-to-VIP (BS). One scan, multiple lines.
    try:
        for r in _fetch_all(client, "asset_ledger",
                            "store,category,status,owed_to_vip,reimbursement,reimbursement_date,selling_price,acquired_date",
                            {"org_id": org_id}):
            st = _norm_store(r.get("store"))
            cat = (r.get("category") or "").strip().upper()
            status = (r.get("status") or "").strip()
            owed = safe_float(r.get("owed_to_vip"))
            reimb = safe_float(r.get("reimbursement"))
            unsold = status.lower() == "on inventory"
            # reimbursement income — recognized in the period it was received
            if reimb and _in_period(r.get("reimbursement_date"), pm, py):
                add("vip_reimb", st, reimb)
            # VIP fee categories — book ONCE, in the month the fee was assessed (the ledger charge
            # date = acquired_date / the "Date" column). Was unfiltered ("count all on the books") →
            # re-booked in every period computed, so this month's COGS carried every prior month's fees.
            if cat in VIP_FEE_CATS and _in_period(r.get("acquired_date"), pm, py):
                add("vip_fees", st, owed, detail_label=cat.title())
            # Balance Sheet (point-in-time, current): unsold on-hand value + outstanding payable
            if unsold:
                add("inventory", st, r.get("selling_price"))
                add("owed_vip", st, owed)
    except Exception:
        pass

    # vip_invoices — VIP fees paid (shipping + other_cost) + unpaid AP (BS)
    try:
        for r in _fetch_all(client, "vip_invoices", "location,shipping,other_cost,grand_total,status,period",
                            {"org_id": org_id, "period": period_keys}):
            st = _norm_store(r.get("location"))
            add("vip_fees", st, safe_float(r.get("shipping")) + safe_float(r.get("other_cost")),
                detail_label="Invoice shipping/other")
        for r in _fetch_all(client, "vip_invoices", "location,grand_total,status", {"org_id": org_id}):
            status = (r.get("status") or "").strip().lower()
            if status not in ("paid in full", "voided", "paid", "void"):
                add("vip_ap", _norm_store(r.get("location")), r.get("grand_total"))
    except Exception:
        pass

    # vip_paygo_payments — cash paid to VIP this period (approved batches), a COGS line. Company-wide.
    # NOTE: the PayGo `dealer` field is the VIP DEALER ACCOUNT (one legal entity — e.g. "Cellular
    # Services Dot net LLC (228 N Wood Ave, Syosset, NY 11791)"), NOT a retail store: 176/178 batches
    # carry that single account string. Resolving it as a store made it a PHANTOM per-store bucket that
    # wrecked per-store / per-company P&L — the device-lending bill is settled at the dealer-account
    # (company) level, and the batch grain has no per-store split. So book it COMPANY-WIDE (store=None).
    # Per-store allocation would require joining each PayGo line to the lent devices' stores via
    # asset_ledger — a future enhancement, not an alias.
    # owed_vip (BS liability) — PayGo-pending batches ARE a real, standalone amount owed to VIP for
    # device lending (weekly billing not yet settled). USER-CONFIRMED 2026-06-25: this does NOT
    # double-count the asset_ledger on-inventory owed (which nets ~$0 — those devices carry no owed)
    # nor the VIP-invoices-unpaid AP line. Booked company-wide (same dealer-account grain as the COGS
    # line above), so it shows on the consolidated BS, not as a phantom per-store bucket.
    try:
        for r in _fetch_all(client, "vip_paygo_payments", "dealer,amount,amount_overdue,batch_type,period",
                            {"org_id": org_id, "period": period_keys}):
            if (r.get("batch_type") or "").lower() == "approved":
                add("vip_device_pay", None, r.get("amount"))
        for r in _fetch_all(client, "vip_paygo_payments", "dealer,amount,batch_type", {"org_id": org_id}):
            if (r.get("batch_type") or "").lower() == "pending":
                add("owed_vip", None, r.get("amount"))
    except Exception:
        pass

    # rep_commissions — rep commissions paid (opex)
    try:
        for r in _fetch_all(client, "rep_commissions", "store,total_payout,period",
                            {"org_id": org_id, "period": period_keys}):
            add("rep_comm", _norm_store(r.get("store")), r.get("total_payout"))
    except Exception:
        pass

    # chargeback_items — clawbacks (opex) + reserve (BS, expected/undeducted)
    try:
        for r in _fetch_all(client, "chargeback_items", "store,amount,deduct,decided_at,period",
                            {"org_id": org_id, "period": period_keys}):
            st = _norm_store(r.get("store"))
            amt = safe_float(r.get("amount"))
            if r.get("deduct"):
                add("chargebacks", st, amt)
            if not r.get("decided_at"):
                add("chargeback_res", st, amt)
    except Exception:
        pass

    # store_expenses — operating expenses (opex), drill by expense_name; key by store_code→address.
    # source_key (mig 206) SPLITS the system lines OUT of generic opex so each producer's cost is
    # booked in its OWN P&L line exactly once (no double-count). The routing table + the reasoning
    # live in `_EXPENSE_ROUTES` / `route_expense_line` above; in short:
    #   'payroll_gross'      → `wages`            (exact gross; SUPPRESSES the StoreOps estimate below)
    #   'payroll_expenses'   → `payroll_expenses` (employer burden)
    #   'additional_payroll' → `payroll_expenses` (EEP: cash-advance excess over earned — NOT wages)
    #   'closing_expense:*'  → `store_opex`       (EEP: daily-closing expense categories — the auto-fill)
    #   NULL (manual) / 'pto_accrual' / anything else → `store_opex`, unchanged.
    # Degrades to pre-mig-206 behaviour (every row → store_opex, byte-identical) if source_key is absent.
    #
    # ⚠️ DOUBLE-COUNT GUARD (EEP 2026-08-04): this table is the ONLY door the envelope-expense wave
    # opens into the P&L. The envelope CASH ledgers — commcalc.envelope_withdrawal,
    # commcalc.commission_payout_ledger, storeops.salary_advance_ledger — are cash MOVEMENTS against
    # already-booked costs (clock-in wages, rep_commissions) and are deliberately NOT read anywhere
    # in this module. Reading them here would double-count every dollar. Salary/commission-KIND
    # closing-expense lines are likewise never posted as system lines by the producer.
    has_payroll_gross = False
    try:
        try:
            exp_rows = _fetch_all(client, "store_expenses",
                                  "store_code,expense_name,expense_type,amount,period,source_key",
                                  {"org_id": org_id, "period": period_keys})
        except Exception:
            exp_rows = _fetch_all(client, "store_expenses",
                                  "store_code,expense_name,expense_type,amount,period",
                                  {"org_id": org_id, "period": period_keys})
        for r in exp_rows:
            sa = code2addr.get(_norm_store(r.get("store_code")), _norm_store(r.get("store_code")))
            sk = (r.get("source_key") or "").strip()
            line_key, fallback_label = route_expense_line(sk)
            if line_key == "wages":
                add("wages", sa, r.get("amount"))            # exact Gross Payroll — relabelled below
                # ONLY an authoritative exact-gross key suppresses the shifts×rate fallback.
                has_payroll_gross = has_payroll_gross or (sk in _WAGES_AUTHORITATIVE_KEYS)
            else:
                # Drill label = the row's own expense_name (the tenant's category name for a closing
                # expense, 'Additional Payroll' for the payroll excess), falling back to the route's
                # default only when the producer sent none. store_opex's fallback is the historical
                # "Expense", so manual rows are byte-identical.
                label = (r.get("expense_name") or "").strip() or fallback_label
                add(line_key, sa, r.get("amount"), detail_label=label)
    except Exception:
        pass

    # Gross Payroll — reuses the `wages` line. AUTHORITATIVE source = the payroll_gross system line
    # (the EXACT gross paid to employees, pushed by mod-people) booked just above. Only FALL BACK to the
    # StoreOps shifts×rate ESTIMATE when NO payroll_gross line exists — booking BOTH double-counts the
    # gross. Relabel to "Gross Payroll" when the exact figure is present; keep "Wages / hourly payroll"
    # (byte-identical) for the estimate so a tenant without the payroll job is unchanged from today.
    if has_payroll_gross:
        L["wages"]["label"] = "Gross Payroll"
    else:
        try:
            for st, amt in wages_by_store(client, org_id, period).items():
                add("wages", st, amt)
        except Exception:
            pass

    # #6 inter-store borrowings (auto*, migration 018) — receivable/payable. Degrade to 0 if absent.
    try:
        bor = _fetch_all(client, "store_borrowings",
                         "from_store,to_store,amount,repaid", {"org_id": org_id})
        for r in bor:
            amt = safe_float(r.get("amount")) - safe_float(r.get("repaid"))
            if amt <= 0:
                continue
            add("inter_store_recv", _norm_store(r.get("to_store")), amt)     # lender is owed
            add("inter_store_pay", _norm_store(r.get("from_store")), amt)    # borrower owes
    except Exception:
        pass

    # Inventory value — real-time on-hand $ value from the b2bsoft Inventory Aging sweep,
    # EDITABLE. Overrides the asset_ledger-derived inventory line above on a per-store basis:
    # manual_value (a hand-entered correction) wins over swept_value. Stores with no
    # inventory_value row keep the asset_ledger fallback so coverage never regresses.
    # Degrades silently (keeps the asset_ledger value) if migration 026 hasn't been run.
    try:
        for r in _fetch_all(client, "inventory_value", "store,swept_value,manual_value",
                            {"org_id": org_id}):
            st = resolve_store(_norm_store(r.get("store")))
            if not st:
                continue
            mv, sv = r.get("manual_value"), r.get("swept_value")
            eff = mv if mv is not None else sv
            if eff is None:
                continue
            L["inventory"]["by_store"][st] = round(safe_float(eff), 2)
    except Exception:
        pass

    return L
