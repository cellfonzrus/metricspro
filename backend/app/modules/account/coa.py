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

ORG_ID = "00000000-0000-0000-0000-000000000001"

DEVICE_DEPTS = {"Android - XP", "IPHONE - XP", "TABLET - XP"}
ACCESSORY_DEPT = "Ondigo"
VIP_FEE_CATS = {"PROCESSING FEE", "SHIPPING", "SIM KIT"}
# Accounting rule (user-set 2026-06-20): accessory COGS is a flat 20% of gross accessory
# sales, NOT the per-line recorded cost (B2B accessory lines often carry no cost → GP looked
# inflated). Commission payout still treats accessories as 100% of gross sales separately.
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
    ("vip_reimb",     "Device-financing reimbursements (VIP)",       "revenue", "auto",  "store"),
    ("vip_device_pay","VIP device payments (PayGo, paid)",           "cogs",    "auto",  "store"),
    ("accessory_cost","Accessory cost",                              "cogs",    "auto",  "store"),
    ("device_cost",   "Device cost",                                 "cogs",    "auto",  "store"),
    ("vip_fees",      "VIP fees paid (shipping / SIM kit / processing)", "cogs", "auto", "store"),
    ("rep_comm",      "Rep commissions paid",                        "opex",    "auto",  "store"),
    ("wages",         "Wages / hourly payroll",                      "opex",    "auto",  "store"),
    ("chargebacks",   "Chargebacks / clawbacks",                     "opex",    "auto",  "store"),
    ("store_opex",    "Store operating expenses (rent / utilities / supplies)", "opex", "auto", "store"),
]
BS_SPEC = [
    ("cash",            "Cash / bank",                               "asset",     "manual",  None),
    ("inventory",       "Inventory — on-hand device value",          "asset",     "auto",    "store"),
    ("inter_store_recv","Inter-store receivable (lent to stores)",   "asset",     "auto*",   "store"),
    ("fixtures",        "Fixtures / equipment",                      "asset",     "manual",  None),
    ("owed_vip",        "Owed to VIP (device financing)",            "liability", "auto",    "store"),
    ("vip_ap",          "Accounts payable — VIP invoices unpaid",    "liability", "auto",    "store"),
    ("inter_store_pay", "Inter-store payable (borrowed)",            "liability", "auto*",   "store"),
    ("chargeback_res",  "Chargeback reserve",                        "liability", "auto",    "store"),
    ("owner_capital",   "Owner capital / contributions",             "equity",    "manual",  None),
    ("retained",        "Retained earnings (accumulated net income)","equity",    "computed",None),
]
PL_LABEL = {k: lbl for k, lbl, *_ in PL_SPEC}
BS_LABEL = {k: lbl for k, lbl, *_ in BS_SPEC}


def _norm_store(s):
    return (str(s or "").strip()) or None


def parse_period(period: str):
    """'June 2026' -> (6, 2026). Also accepts 'YYYY-MM'."""
    months = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
              "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
    p = (period or "").strip().lower()
    if "-" in p and p.split("-")[0].isdigit():
        y, m = p.split("-")[:2]
        return int(m), int(y)
    parts = p.split()
    mo = months.get(parts[0], 0) if parts else 0
    yr = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    return mo, yr


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


# ── per-line aggregation: each store-keyed line → {store_address: amount}; company-wide → scalar
def build_inputs(client, org_id, period):
    """Aggregate every chart-of-accounts line for `period`. Returns a dict:
       { line_key: {"by_store": {store: amt}, "company_wide": amt, "detail": {label: amt}} }
       'detail' carries drill-down sub-lines (e.g. each expense name)."""
    pm, py = parse_period(period)
    # Upload paths store the period inconsistently — the daily-sales upload writes the month-name
    # form ("June 2026") while compute is invoked with "2026-06". Query BOTH spellings so the
    # period-string sources (raw_sales/raw_mi/comp/rep_commissions/...) aren't silently empty.
    _MONTHS = ["", "January", "February", "March", "April", "May", "June", "July",
               "August", "September", "October", "November", "December"]
    period_keys = list({period} | ({f"{_MONTHS[pm]} {py}"} if 1 <= pm <= 12 and py else set()))
    code2addr = store_code_to_address(client, org_id)
    resolve_store = store_resolver(client, org_id)

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

    # raw_mi — MI + ATU residual (company-wide)
    try:
        for r in _fetch_all(client, "raw_mi", "actual_mi_payout,actual_atu_payout",
                            {"org_id": org_id, "period": period_keys}):
            add("mi_income", None, r.get("actual_mi_payout"))
            add("atu_income", None, r.get("actual_atu_payout"))
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

    # raw_sales — accessory/device revenue + cost (store)
    try:
        for r in _fetch_all(client, "raw_sales", "department,ext_price,gp,voided,store",
                            {"org_id": org_id, "period": period_keys}):
            if str(r.get("voided") or "").strip().lower() in ("true", "yes", "1", "voided", "void"):
                continue
            dept = (r.get("department") or "").strip()
            ext, gp = safe_float(r.get("ext_price")), safe_float(r.get("gp"))
            st = _norm_store(r.get("store"))
            if dept == ACCESSORY_DEPT:
                add("accessory_rev", st, ext)
                add("accessory_cost", st, ext * ACCESSORY_COGS_PCT)
            elif dept in DEVICE_DEPTS:
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

    # store_expenses — operating expenses (opex), drill by expense_name; key by store_code→address
    try:
        for r in _fetch_all(client, "store_expenses", "store_code,expense_name,expense_type,amount,period",
                            {"org_id": org_id, "period": period_keys}):
            sa = code2addr.get(_norm_store(r.get("store_code")), _norm_store(r.get("store_code")))
            label = (r.get("expense_name") or "Expense").strip()
            add("store_opex", sa, r.get("amount"), detail_label=label)
    except Exception:
        pass

    # StoreOps payroll — wages (opex). Degrades to 0 (→ manual journal) if no shifts.
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
