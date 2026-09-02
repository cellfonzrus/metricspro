"""MA/VidaPay → P&L STORE ATTRIBUTION + line-label config (owner spec 2026-09-02, mig 314).

The owner's words, verbatim: "it shows company wide vida commission, it should show store wise
commission for all M1 thru M12, also it should say Residual on Total side and Mi on boost side,
there is no numbers for the residual in the p&l on the luxelink side, mdf should capture the market
spiff of $1000/$500 per store if it is part of any of the commission report on the total side,
rebates and phone cost are not being captured per store, none of these are hard coded, they should
be as a part of the design".

WHAT WAS WRONG (measured, luxelink August 2026)
-----------------------------------------------
Every MA dollar in `coa.build_inputs` was booked COMPANY-WIDE (store=None): residual $28,370.84,
merchant discount $14,421.56, spiffs $7,521.85, rebates −$71,512.83. `engine._scoped` shows
company-wide money ONLY in the Consolidated scope, so the company:"Luxlink Wireless" and every
per-store P&L read $0.00 on all of them — that is the "no numbers for the residual on the luxelink
side" and the "company wide vida commission" in one root cause.

THE MAP THE OLD CODE SAID DIDN'T EXIST — DOES
---------------------------------------------
`device_cogs.py`'s docstring ruled per-store MA money out "until account_id → store_address is
mapped". It is mapped, in the dealer's own data: `raw_ma_fulfillment` carries BOTH the processor
account (`tspid`) and the store's `business_address` on every order row. Measured on luxelink
(all periods): 19 distinct tspids, ZERO ambiguous, covering 13/13 `raw_ma_daily_tx.account_id`s and
17/18 `raw_ma_commission.merchant_account_id`s (170405 has no fulfillment row → stays company-wide,
honest). A per-org override table (`commcalc.ma_account_store_map`, mig 314) wins over the derived
map so the owner can pin the stragglers; addresses run through coa's `store_resolver` so spellings
("4640a" vs "4640-A", "21880" vs "218-80") collapse onto the canonical store.

CONFIG, NEVER CODE (RULE TWO — mig 314 columns on commcalc.commission_org_config)
---------------------------------------------------------------------------------
  pl_ma_store_attribution   bool   — master switch; FALSE/absent ⇒ every org byte-identical to
                                     today (all MA money company-wide).
  pl_ma_month_spiff_source  text   — 'commission_sheet' (default, today's behaviour: book
                                     raw_ma_commission.spiff_m1..m6 at the ACTIVATION month) or
                                     'daily_tx' (cash basis: book the raw_ma_daily_tx month-spiff
                                     rows in the month PAID, M1..M12+ via the shared
                                     commission_ledger.parse_payment_month regex, and STOP booking
                                     the sheet's spiff columns so the same dollar can never book at
                                     both the activation month and the cash month).
  pl_ma_spiff_order_types   jsonb  — which daily-tx order_type families are month spiffs
                                     (default ['PostPaid Additional Spiff']); only read when
                                     source='daily_tx'.
  pl_mdf_product_tokens     jsonb  — product_name tokens whose rows book to the `mdf_income` P&L
                                     line (default [] = line never materialises; luxelink seeds
                                     ['premium store spiff'] — the $1,000-per-store market spiff
                                     rows on the Total side).
  pl_line_labels            jsonb  — per-org DISPLAY label per P&L/BS line key (the mechanism
                                     `engine._assemble` already honours via inputs[key]['label'],
                                     same as the "Gross Payroll" relabel). luxelink seeds
                                     {"mi_income": "Residual"}; Boost keeps "MI residual income".

EVERY function that decides where a dollar books is PURE (rows + resolved config in, bookings out)
— proof: backend/harness_ma_store_pnl.py. Money columns stay guarded: only `merchant_discount` and
`retail_cost` are read as money off raw_ma_daily_tx (residual_subs.assert_money_columns), and the
comm-sheet components are the audited `_MA_COMPONENTS`. A retail_cost books AT MOST ONCE per row
(precedence residual → MDF → month-spiff); merchant_discount is separate money and always books.
"""
from app.modules.commcalc.calculator import safe_float
from app.modules.commcalc.commission_ledger import parse_payment_month
from app.modules.account import residual_subs as _rs

# ── comm-sheet component → P&L head (moved verbatim from coa.build_inputs' inline map so the pure
# booking function below is the ONE place the routing lives; coa imports it). Owner rulings
# 2026-08-10 (component re-filing) and K1 (rebate = contra-COGS, sign −1) unchanged.
MA_COMMISSION_HEADS = {
    "rebate":             ("device_rebate",    -1),
    "device_margin":      ("ma_device_margin",  1),
    "fees_margin":        ("fee_income",        1),
    "consumer_financing": ("financing_income",  1),
    "consumer_margin":    ("ma_device_margin",  1),
    "spiff_m1": ("carrier_comm", 1), "spiff_m2": ("carrier_comm", 1),
    "spiff_m3": ("carrier_comm", 1), "spiff_m4": ("carrier_comm", 1),
    "spiff_m5": ("carrier_comm", 1), "spiff_m6": ("carrier_comm", 1),
}
MA_HEAD_DETAIL = {"carrier_comm": "SPIFF / bounty",
                  "device_rebate": "Device purchase rebates (Distributor/MA)",
                  "rebate_income": "Device purchase rebates (Distributor/MA)"}
# ── mig 934 (owner report 2026-09-02: "rebate is coming in negative, it should be a positive
# number as it is coming in"). WHERE the rebate dollar presents, per org:
#   'contra_cogs' (house default, ruling K1 2026-08-10 unchanged) → `device_rebate`, booked
#       NEGATIVE inside COGS so it nets against Device cost;
#   'income' → `rebate_income` (PL_SPEC auto_opt revenue line), booked POSITIVE — money coming in
#       reads as a positive number. Net income AND gross profit are IDENTICAL either way (revenue
#       and COGS both move by the same amount); only the section subtotals move. Config, never
#       code: the routing is data-driven off the resolved org config, no tenant branch anywhere.
REBATE_ROUTES = {"contra_cogs": ("device_rebate", -1), "income": ("rebate_income", 1)}


def rebate_route(cfg):
    """PURE: (line_key, sign) the org's rebate dollars book with. Feed/ledger convention is
    unchanged — callers still compute `sign * -feed_value` (MA sheet, negative = paid to dealer)
    or `sign * ledger_amount * -1` equivalents — so 'contra_cogs' is byte-identical to pre-934
    and 'income' flips ONLY the rebate's line + sign, nothing else."""
    key = (cfg or {}).get("rebate_presentation") if isinstance(cfg, dict) else None
    return REBATE_ROUTES.get(key or "contra_cogs", REBATE_ROUTES["contra_cogs"])
# The comm-sheet spiff columns — suppressed when the org books month spiffs from the daily-tx cash
# rows instead ('daily_tx'), so one payment can never book at both the activation month (sheet
# column, back-filled by the monthly re-pull) and the cash month (MONTH-n tx row).
SPIFF_COMPONENTS = frozenset(c for c, (h, _s) in MA_COMMISSION_HEADS.items() if h == "carrier_comm")

MDF_LINE = "mdf_income"
_SPIFF_ORDER_TYPES_DEFAULT = ("PostPaid Additional Spiff",)
_SPIFF_OTHER_DETAIL = "Spiff (other)"


def default_config():
    """Mig-314 defaults — what every org gets before the migration runs / with no config row.
    All switches OFF ⇒ byte-identical to the pre-314 books for every tenant."""
    return {
        "store_attribution": False,
        "month_spiff_source": "commission_sheet",
        "spiff_order_types": list(_SPIFF_ORDER_TYPES_DEFAULT),
        "mdf_product_tokens": [],
        "line_labels": {},
        "rebate_presentation": "contra_cogs",
    }


_CFG_COLS_314 = ("pl_ma_store_attribution,pl_ma_month_spiff_source,"
                 "pl_ma_spiff_order_types,pl_mdf_product_tokens,pl_line_labels")
_CFG_COLS_934 = _CFG_COLS_314 + ",pl_rebate_presentation"


def load_config(client, org_id):
    """Per-org MA store-attribution config (commcalc.commission_org_config, mig 314), org-scoped,
    ADAPTIVE: missing table/columns (pre-314) or row ⇒ `default_config()`. NEVER raises. Values are
    validated — an unknown spiff source or non-list/non-dict value keeps the default."""
    cfg = default_config()
    try:
        # Column-set fallback, NEWEST first: selecting a column a live DB doesn't have yet is a
        # PostgREST error for the WHOLE select, and falling all the way back to defaults would
        # silently drop the mig-314 seeds an org already runs on. So: mig-934 column set, then the
        # mig-314 set, then defaults — each older set keeps every value it does carry.
        rows = []
        for _cols in (_CFG_COLS_934, _CFG_COLS_314):
            try:
                rows = (client.schema("commcalc").table("commission_org_config")
                        .select(_cols).eq("org_id", org_id).limit(1).execute().data) or []
                break
            except Exception:
                continue
        if rows:
            r = rows[0]
            if isinstance(r.get("pl_ma_store_attribution"), bool):
                cfg["store_attribution"] = r["pl_ma_store_attribution"]
            src = str(r.get("pl_ma_month_spiff_source") or "").strip().lower()
            if src in ("commission_sheet", "daily_tx"):
                cfg["month_spiff_source"] = src
            if isinstance(r.get("pl_ma_spiff_order_types"), list):
                # explicit [] honoured: 'daily_tx' with no families books no month spiffs at all
                cfg["spiff_order_types"] = [str(t).strip() for t in r["pl_ma_spiff_order_types"]
                                            if str(t).strip()]
            if isinstance(r.get("pl_mdf_product_tokens"), list):
                cfg["mdf_product_tokens"] = [str(t).strip() for t in r["pl_mdf_product_tokens"]
                                             if str(t).strip()]
            if isinstance(r.get("pl_line_labels"), dict):
                cfg["line_labels"] = {str(k).strip(): str(v).strip()
                                      for k, v in r["pl_line_labels"].items()
                                      if str(k).strip() and str(v).strip()}
            reb = str(r.get("pl_rebate_presentation") or "").strip().lower()
            if reb in REBATE_ROUTES:
                cfg["rebate_presentation"] = reb
    except Exception:
        pass
    return cfg


# ── account → store index ────────────────────────────────────────────────────────────────────────
def account_store_index(fulfillment_rows, override_rows=None):
    """PURE: {processor account id -> store address}. Sources, in precedence order:
      1. `override_rows` — per-org config rows (commcalc.ma_account_store_map, mig 314):
         {account_id, store_address}. An owner-pinned mapping always wins.
      2. `fulfillment_rows` — raw_ma_fulfillment {tspid, business_address}: the dealer's own order
         sheet names both the account and the store on every row.
    A tspid seen with TWO different addresses in the fulfillment data is AMBIGUOUS and is dropped
    from the derived map (booked company-wide — honest beats mis-attributed; the phantom-store
    lesson from PayGo) unless an override pins it. Addresses are returned RAW — the caller resolves
    them through coa's `store_resolver` so spellings collapse onto the canonical store."""
    derived, ambiguous = {}, set()
    for r in fulfillment_rows or []:
        r = r or {}
        acct = str(r.get("tspid") or "").strip()
        addr = str(r.get("business_address") or "").strip()
        if not acct or not addr:
            continue
        prev = derived.get(acct)
        if prev is None:
            derived[acct] = addr
        elif prev.lower() != addr.lower():
            ambiguous.add(acct)
    for a in ambiguous:
        derived.pop(a, None)
    for r in override_rows or []:
        r = r or {}
        acct = str(r.get("account_id") or "").strip()
        addr = str(r.get("store_address") or "").strip()
        if acct and addr:
            derived[acct] = addr
    return derived


def load_store_index(client, org_id):
    """I/O: build the account→store index for an org (override table ∪ fulfillment-derived).
    NEVER raises — any failure degrades to {} (= everything company-wide, the pre-314 grain)."""
    ful, ovr = [], []
    try:
        start, page = 0, 1000
        while start < 200000:
            chunk = (client.schema("commcalc").table("raw_ma_fulfillment")
                     .select("tspid,business_address").eq("org_id", org_id)
                     .range(start, start + page - 1).execute().data) or []
            ful.extend(chunk)
            if len(chunk) < page:
                break
            start += page
    except Exception:
        ful = []
    try:
        ovr = (client.schema("commcalc").table("ma_account_store_map")
               .select("account_id,store_address").eq("org_id", org_id)
               .limit(2000).execute().data) or []
    except Exception:
        ovr = []  # mig 314 table not present yet — derived map alone still works
    try:
        return account_store_index(ful, ovr)
    except Exception:
        return {}


# ── pure booking functions ───────────────────────────────────────────────────────────────────────
def ma_commission_bookings(rows, cfg=None):
    """PURE: raw_ma_commission rows + resolved mig-314 config → ordered bookings
    [(line_key, account_id_or_None, amount, detail_label), ...] for coa's `add()`.

    Byte-identity: with `default_config()` this emits exactly what coa's old inline loop emitted —
    same component order (`_MA_COMPONENTS`), same sign conventions (feed negative = paid TO the
    dealer, so heads get `sign * -value`; wallet_funding NOT flipped → `distributor_clearing`),
    same per-row-per-component granularity (incremental 2-dp rounding in add() unchanged) — with
    account None (company-wide). Differences are config-gated:
      • store_attribution → account = row's merchant_account_id (caller maps it to a store);
      • month_spiff_source='daily_tx' → the spiff_m1..m6 columns are NOT booked here (the daily-tx
        cash rows book them instead, see ma_tx_bookings) — everything else still books."""
    cfg = cfg if cfg is not None else default_config()
    attribute = bool(cfg.get("store_attribution"))
    skip_spiffs = (cfg.get("month_spiff_source") == "daily_tx")
    reb_route = rebate_route(cfg)   # mig 934: ('device_rebate', -1) default / ('rebate_income', 1)
    out = []
    for r in rows or []:
        r = r or {}
        acct = (str(r.get("merchant_account_id") or "").strip() or None) if attribute else None
        for c in _rs._MA_COMPONENTS:
            if c == "wallet_funding":
                # Balance sheet clearing (owner ruling 2026-08-10) — settlement is entity-level
                # cash, so it stays company-wide even under store attribution.
                out.append(("distributor_clearing", None, safe_float(r.get(c)), None))
                continue
            head_sign = reb_route if c == "rebate" else MA_COMMISSION_HEADS.get(c)
            if not head_sign:
                continue
            if skip_spiffs and c in SPIFF_COMPONENTS:
                continue
            head, sign = head_sign
            out.append((head, acct, sign * -safe_float(r.get(c)), MA_HEAD_DETAIL.get(head)))
    return out


def ma_tx_bookings(rows, pnl_cfg=None, cfg=None):
    """PURE: raw_ma_daily_tx rows + mig-309 config (`residual_subs.load_ma_pnl_config`) + mig-314
    config → ordered bookings [(line_key, account_id_or_None, amount, detail_label), ...].

    SUPERSET of `residual_subs.ma_tx_pnl_bookings` — with `default_config()` the emitted
    (line, amount) sequence is byte-identical to it (proof: harness_ma_store_pnl.py), so the mig-309
    behaviour is preserved exactly for every org that hasn't opted in. Per row:
      • +merchant_discount → 'Merchant discount' (or the legacy atu_income fold) — ALWAYS; airtime
        margin is its own money, independent of what retail_cost is.
      • −retail_cost books AT MOST ONCE, precedence:
          1. RESIDUAL (the mig-309 union matcher, unchanged) → `mi_income`;
          2. MDF — product_name contains any cfg['mdf_product_tokens'] token (case-insensitive)
             → `mdf_income`, detail = the trimmed product_name (so "$1,000 Premium Store Spiff ×12
             stores" reads as itself in the drill-down);
          3. MONTH SPIFF (only when month_spiff_source='daily_tx') — order_type ∈
             cfg['spiff_order_types'] (case-insensitive) → `carrier_comm`, detail 'M<n>' via THE
             shared commission_ledger.parse_payment_month regex ('TBV MONTH 4', 'M1 Proration',
             'SPF Month 1' all parse; no month token → 'Spiff (other)'). M1..M12+ come from the
             data, never from a hardcoded count."""
    pnl_cfg = pnl_cfg if pnl_cfg is not None else _rs.default_ma_pnl_config()
    cfg = cfg if cfg is not None else default_config()
    match = _rs.ma_residual_row_matcher(pnl_cfg)
    disc_line = (_rs._MA_PNL_DISCOUNT_LINE if pnl_cfg.get("merchant_discount_own_line", True)
                 else _rs._MA_PNL_LEGACY_DISCOUNT_LINE)
    attribute = bool(cfg.get("store_attribution"))
    mdf_tokens = [t.lower() for t in (cfg.get("mdf_product_tokens") or [])]
    spiff_types = ({str(t).strip().lower() for t in (cfg.get("spiff_order_types") or [])}
                   if cfg.get("month_spiff_source") == "daily_tx" else set())
    out = []
    for r in rows or []:
        r = r or {}
        acct = (str(r.get("account_id") or "").strip() or None) if attribute else None
        out.append((disc_line, acct, safe_float(r.get("merchant_discount")), None))
        prod = str(r.get("product_name") or "")
        if match(r.get("product_name"), r.get("order_type")):
            out.append((_rs._MA_PNL_RESIDUAL_LINE, acct, -safe_float(r.get("retail_cost")), None))
        elif mdf_tokens and any(t in prod.lower() for t in mdf_tokens):
            out.append((MDF_LINE, acct, -safe_float(r.get("retail_cost")), prod.strip() or None))
        elif spiff_types and str(r.get("order_type") or "").strip().lower() in spiff_types:
            n = parse_payment_month(prod)
            detail = ("M%d" % n) if n else _SPIFF_OTHER_DETAIL
            out.append(("carrier_comm", acct, -safe_float(r.get("retail_cost")), detail))
    return out


def apply_line_labels(lines, labels):
    """PURE: set the per-line display label override (`lines[key]['label']`) for each configured
    key that exists — the SAME passthrough `engine._assemble` already honours for 'Gross Payroll'.
    Unknown keys are ignored (a typo cannot invent a P&L line); empty labels are ignored. Mutates
    and returns `lines`. This is what puts 'Residual' on the Total side while Boost keeps
    'MI residual income' — per-org config, no carrier branch anywhere."""
    for key, label in (labels or {}).items():
        k, v = str(key).strip(), str(label).strip()
        if k and v and k in (lines or {}):
            lines[k]["label"] = v
    return lines
