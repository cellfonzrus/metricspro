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
# ── mig 996 (owner directive 2026-09-09: "what we need to book as profit in p&L is not the device
# rebate but it should be a seaprate set of columns which represent the device margin which is equal
# to selling price + device rebate - device cost"). See the DEVICE MARGIN section at the foot of
# this module for the block, its columns and the gross-profit identity.
DEVICE_MARGIN_ROUTES = ("off", "margin_block")


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
        "device_margin_presentation": "off",
    }


_CFG_COLS_314 = ("pl_ma_store_attribution,pl_ma_month_spiff_source,"
                 "pl_ma_spiff_order_types,pl_mdf_product_tokens,pl_line_labels")
_CFG_COLS_934 = _CFG_COLS_314 + ",pl_rebate_presentation"
_CFG_COLS_996 = _CFG_COLS_934 + ",pl_device_margin_presentation"


def load_config(client, org_id):
    """Per-org MA store-attribution config (commcalc.commission_org_config, mig 314), org-scoped,
    ADAPTIVE: missing table/columns (pre-314) or row ⇒ `default_config()`. NEVER raises. Values are
    validated — an unknown spiff source or non-list/non-dict value keeps the default."""
    cfg = default_config()
    try:
        # Column-set fallback, NEWEST first: selecting a column a live DB doesn't have yet is a
        # PostgREST error for the WHOLE select, and falling all the way back to defaults would
        # silently drop the mig-314 seeds an org already runs on. So: mig-996 column set, then the
        # mig-934 set, then the mig-314 set, then defaults — each older set keeps every value it
        # does carry.
        rows = []
        for _cols in (_CFG_COLS_996, _CFG_COLS_934, _CFG_COLS_314):
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
            dmp = str(r.get("pl_device_margin_presentation") or "").strip().lower()
            if dmp in DEVICE_MARGIN_ROUTES:
                cfg["device_margin_presentation"] = dmp
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


def canonical_store_index(client, org_id):
    """I/O: {processor account id -> CANONICAL store_address} — `load_store_index` with every raw
    address collapsed onto the org's canonical store spelling through coa's `store_resolver`
    ("4640a" → "4640-A", "21880" → "218-80"), which is the normalization mig 314 itself prescribes.

    ONE canonical account→store answer for the whole platform (duplicate-check 2026-09-04): this is
    the step-3 map `payables.engine.ma_store_resolution` used to build inline and the
    residual-per-subscriber report now resolves its processor accounts through — extracted here, in
    the mig-314 module that owns the index, rather than copied a third time. NEVER raises: a missing
    index or an unreadable store vocabulary degrades to {} (callers then render "(Unassigned)",
    never a guessed store)."""
    try:
        raw = load_store_index(client, org_id) or {}
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN ma_store_pnl canonical_store_index index read failed: {e}")
        return {}
    if not raw:
        return {}
    try:
        from app.modules.account import coa as _coa
        resolve_addr = _coa.store_resolver(client, org_id)
    except Exception as e:                          # pragma: no cover - I/O guard
        print(f"WARN ma_store_pnl canonical_store_index store_resolver failed: {e}")
        return dict(raw)
    return {a: (resolve_addr(addr) or addr) for a, addr in raw.items()}


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


# ── "everything has a reason" — MA TX booking COVERAGE (owner directive 2026-09-08) ──────────────
# The owner's words: "all items should match and there should be nothing in unsplit, everything has
# a reason and everything is assigned to the code".
#
# `ma_tx_bookings` books a row's `retail_cost` to at most one line — residual → MDF → month spiff —
# and books NOTHING for every other order-type family. That silence is how $3,794.56 of August-2026
# 'Retroactive Postpaid Spiff' cash reached no P&L line at all without anyone noticing: the org's
# `pl_ma_spiff_order_types` names only 'PostPaid Additional Spiff', and a family nobody configured
# is indistinguishable from a family deliberately left to another feed.
#
# So the unbooked money gets NAMED, per order-type family, with its reason. Reasons are CONFIG
# (`commission_org_config.pl_ma_unbooked_reasons`, {order_type: reason}); a family with no
# configured reason carries `ma_recon.NO_RULE_REASON` — the SAME literal honest-absence marker the
# MA activation recon already uses for "sold, unpaid, nothing explains it" (mig 312). Absence of a
# rule is REPORTED, never papered over and never guessed at.
#
# This is a COVERAGE READ-OUT, not a second booking path: it re-runs the very same classification
# `ma_tx_bookings` runs (same matcher, same precedence, same config) and reports what fell through.
# It moves no dollar.
NO_RULE_REASON = "no business rule configured"     # = commcalc.ma_recon.NO_RULE_REASON (mig 312)


def load_unbooked_reasons(client, org_id):
    """Per-org {order_type: stated reason} for MA daily-tx families that book to no P&L line
    (commcalc.commission_org_config, org-scoped). ADAPTIVE — a missing column/table/row degrades to
    {} (= every unbooked family reported as `NO_RULE_REASON`). NEVER raises."""
    try:
        rows = (client.schema("commcalc").table("commission_org_config")
                .select("pl_ma_unbooked_reasons").eq("org_id", org_id).limit(1).execute().data) or []
        val = rows[0].get("pl_ma_unbooked_reasons") if rows else None
        if isinstance(val, dict):
            return {str(k).strip().lower(): str(v).strip()
                    for k, v in val.items() if str(k).strip() and str(v).strip()}
    except Exception:
        pass
    return {}


def ma_tx_coverage(rows, pnl_cfg=None, cfg=None, reasons=None):
    """PURE: raw_ma_daily_tx rows + the SAME two configs `ma_tx_bookings` resolves → coverage.

    Returns {"booked": {line_key: amount}, "unbooked": [ {order_type, rows, amount, reason}, ... ],
             "unbooked_total": float, "unexplained_total": float}
    where `unexplained_total` is the part of `unbooked_total` whose family carries no configured
    reason (i.e. the literal NO_RULE_REASON). `unbooked` is ordered by |amount| descending so the
    biggest silent family is the first thing read.

    `merchant_discount` is separate money that ALWAYS books, so it never appears as unbooked; only
    `retail_cost` — the column that books at most once — is accounted for here."""
    pnl_cfg = pnl_cfg if pnl_cfg is not None else _rs.default_ma_pnl_config()
    cfg = cfg if cfg is not None else default_config()
    reasons = {str(k).strip().lower(): str(v) for k, v in (reasons or {}).items()}
    match = _rs.ma_residual_row_matcher(pnl_cfg)
    mdf_tokens = [t.lower() for t in (cfg.get("mdf_product_tokens") or [])]
    spiff_types = ({str(t).strip().lower() for t in (cfg.get("spiff_order_types") or [])}
                   if cfg.get("month_spiff_source") == "daily_tx" else set())
    disc_line = (_rs._MA_PNL_DISCOUNT_LINE if pnl_cfg.get("merchant_discount_own_line", True)
                 else _rs._MA_PNL_LEGACY_DISCOUNT_LINE)
    booked, unbooked = {}, {}
    for r in rows or []:
        r = r or {}
        prod = str(r.get("product_name") or "")
        ot = str(r.get("order_type") or "").strip()
        md = safe_float(r.get("merchant_discount"))
        amt = -safe_float(r.get("retail_cost"))
        # The key is recorded even when the sum is 0.00: a line these rows DID classify into and
        # measured as zero must stay distinguishable from a line no row ever reached (three states,
        # not two). The same reason `booked[key]` below is written unconditionally.
        booked[disc_line] = booked.get(disc_line, 0.0) + md
        if match(r.get("product_name"), r.get("order_type")):
            key = _rs._MA_PNL_RESIDUAL_LINE
        elif mdf_tokens and any(t in prod.lower() for t in mdf_tokens):
            key = MDF_LINE
        elif spiff_types and ot.lower() in spiff_types:
            key = "carrier_comm"
        else:
            slot = unbooked.setdefault(ot, {"order_type": ot, "rows": 0, "amount": 0.0})
            slot["rows"] += 1
            slot["amount"] += amt
            continue
        booked[key] = booked.get(key, 0.0) + amt
    out = []
    for ot, slot in unbooked.items():
        slot["amount"] = round(slot["amount"], 2)
        slot["reason"] = reasons.get(ot.lower()) or NO_RULE_REASON
        out.append(slot)
    out.sort(key=lambda s: (-abs(s["amount"]), s["order_type"]))
    return {"booked": {k: round(v, 2) for k, v in booked.items()},
            "unbooked": out,
            "unbooked_total": round(sum(s["amount"] for s in out), 2),
            "unexplained_total": round(sum(s["amount"] for s in out
                                           if s["reason"] == NO_RULE_REASON), 2)}


# ── DEVICE MARGIN IS THE PROFIT, NOT THE REBATE (owner directive 2026-09-09, mig 996) ────────────
# Owner, verbatim: "what we need to book as profit in p&L is not the device rebate but it should be
# a seaprate set of columns which represent the device margin which is equal to selling price +
# device rebate - device cost".
#
# ❶ THE CARRIER'S OWN `device_margin` COLUMN IS **NOT** THAT FORMULA — MEASURED, NOT ASSUMED.
#   `commcalc.raw_ma_commission.device_margin`, org 854f6d7b…, August 2026: 354 non-zero rows of
#   1,248, taking exactly TWO values — −20.00 (262 rows) and −10.00 (92 rows) — summing to −6,160.00
#   (feed convention: negative = paid TO the dealer). A flat $20/$10 per-unit allowance the master
#   agent pays on a device sale. It cannot be "selling price + rebate − cost": it does not vary with
#   the handset (an iPhone 17 Pro Max carrying a −1,199.99 rebate and an iPhone 16e carrying −575.00
#   both show −20.00), and the sheet's `consumer_value` / `consumer_margin` (the only price-shaped
#   columns) are 0.00 for the whole month. So it is KEPT WHERE IT IS — its own `ma_device_margin`
#   revenue line, dollars unchanged — and the owner's device margin is a DIFFERENT, computed thing.
#   `carrier_device_margin_profile()` below is the pure read-out that establishes this per period,
#   so no future month is assumed to behave like August.
#
# ❷ THE OWNER'S DEVICE MARGIN IS ALREADY IN THE BOOKS — SPREAD ACROSS THREE LINES IN TWO SECTIONS.
#   selling price = `device_rev` (POS device sales revenue) · device rebate = the rebate route's
#   line (`device_rebate` contra-COGS, house default, or `rebate_income`) · device cost =
#   `device_cost` (device_cogs.resolve, invoice-first). August 2026, measured:
#       80.81 + 251,946.31 − 260,206.80 = −8,179.68
#   which is EXACTLY what the device leg already contributes to gross profit today. The owner is not
#   asking for a new dollar; he is asking for that dollar to be presented as ONE margin with its
#   components as columns, instead of a $251,946.31 rebate that reads like profit next to a
#   $260,206.80 cost.
#
# ❸ SO THE BLOCK REPLACES ITS COMPONENTS — IT NEVER SITS BESIDE THEM. `margin_block` books the three
#   component amounts onto ONE line (`device_margin`) as three named COLUMNS (the drill-down detail),
#   and the lines it supersedes (`device_margin_supersedes()`) must carry nothing. Adding the block
#   while leaving the components booked would double-count the device leg — that is the single
#   dangerous mistake here, so `device_margin_gp_delta()` exists to prove the swap is GP-neutral and
#   the harness pins it at 0.00.
#
# RULE TWO: one per-org knob, `commission_org_config.pl_device_margin_presentation`
# ('off' = house default, byte-identical for every org | 'margin_block'), no carrier or tenant name.
DEVICE_MARGIN_LINE = "device_margin"
# (column key, display label, component name, sign against the component amount as it is booked to
#  the legacy line). `device_rebate` is passed in the ROUTE'S OWN sign — negative under
#  'contra_cogs' (contra-COGS), positive under 'income' — and normalised here, so the block reads
#  the same under either rebate route.
DEVICE_MARGIN_COLUMNS = (
    ("device_margin_price",  "Device selling price", "selling_price"),
    ("device_margin_rebate", "Device rebate",        "device_rebate"),
    ("device_margin_cost",   "Device cost",          "device_cost"),
)
DEVICE_MARGIN_COLUMN_KEYS = tuple(k for k, _l, _c in DEVICE_MARGIN_COLUMNS)
# The P&L lines whose dollars the block ABSORBS. Under 'margin_block' every one of these must book
# nothing (see ❸); under 'off' the block books nothing and these are untouched.
# (the two rebate lines are `REBATE_ROUTES`' own line keys — BOTH, so the block is complete under
#  either rebate route; the harness pins that equality rather than letting the two lists drift.)
DEVICE_MARGIN_SUPERSEDES = ("device_rev", "device_cost") + tuple(
    line for line, _sign in REBATE_ROUTES.values())


def device_margin_presentation(cfg):
    """PURE: the org's device-margin presentation — 'off' (house default; the block books nothing
    and every existing line is byte-identical) or 'margin_block'. An unknown/absent value is 'off',
    never a guess."""
    key = (cfg or {}).get("device_margin_presentation") if isinstance(cfg, dict) else None
    key = str(key or "").strip().lower()
    return key if key in DEVICE_MARGIN_ROUTES else "off"


def device_margin_supersedes(cfg):
    """PURE: the P&L line keys the block absorbs under this org's config — () when 'off'. Finance
    suppresses exactly these while the block is on; anything left booked would double-count."""
    return DEVICE_MARGIN_SUPERSEDES if device_margin_presentation(cfg) == "margin_block" else ()


def device_margin_columns(selling_price=0.0, device_rebate=0.0, device_cost=0.0):
    """PURE: the owner's formula, as its columns. `device_rebate` is accepted in EITHER rebate-route
    sign (negative contra-COGS or positive income) and is normalised to money-in; `device_cost` is a
    positive COGS figure and enters the margin negatively.

    Returns {"columns": [(key, label, amount), …], "net": float} where
        net = selling price + device rebate − device cost.
    August-2026 org 854f6d7b…: 80.81 + 251,946.31 − 260,206.80 = −8,179.68."""
    price = round(safe_float(selling_price), 2)
    rebate = round(abs(safe_float(device_rebate)), 2)
    cost = round(safe_float(device_cost), 2)
    amounts = {"device_margin_price": price,
               "device_margin_rebate": rebate,
               "device_margin_cost": -cost}
    cols = [(k, label, amounts[k]) for k, label, _c in DEVICE_MARGIN_COLUMNS]
    return {"columns": cols, "net": round(price + rebate - cost, 2)}


def device_margin_bookings(components_by_store, cfg=None):
    """PURE: {store_or_None: {selling_price, device_rebate, device_cost}} + resolved config →
    ordered bookings [(line_key, store_or_None, amount, column_label), …] for coa's `add()`.

    'off' ⇒ [] (byte-identical: nothing books, the three existing lines keep their dollars).
    'margin_block' ⇒ per store the THREE columns book to the ONE `device_margin` line, so the line
    total is the margin and its drill-down is the owner's formula, term by term. Stores are emitted
    in a stable order (company-wide `None` last) so the incremental 2-dp rounding in `add()` is
    deterministic."""
    if device_margin_presentation(cfg) != "margin_block":
        return []
    out = []
    keys = sorted((k for k in (components_by_store or {}) if k is not None), key=str)
    if None in (components_by_store or {}):
        keys.append(None)
    for st in keys:
        comp = (components_by_store or {}).get(st) or {}
        block = device_margin_columns(comp.get("selling_price"), comp.get("device_rebate"),
                                      comp.get("device_cost"))
        for key, label, amt in block["columns"]:
            out.append((DEVICE_MARGIN_LINE, st, amt, label))
    return out


def device_margin_gp_delta(components_by_store, cfg=None):
    """PURE: what switching an org to 'margin_block' does to GROSS PROFIT, in dollars.

    Legacy presentation contributes `selling_price − (device_cost − device_rebate)` to GP (the
    rebate nets against cost under 'contra_cogs', or adds to revenue under 'income' — identical
    either way, mig 934). The block contributes its net. The two are the same arithmetic, so this
    is 0.00 whenever the block supersedes its components as it must; a non-zero result means a
    caller left a superseded line booked and the device leg is being counted twice."""
    legacy = block = 0.0
    for comp in (components_by_store or {}).values():
        comp = comp or {}
        price = safe_float(comp.get("selling_price"))
        rebate = abs(safe_float(comp.get("device_rebate")))
        cost = safe_float(comp.get("device_cost"))
        legacy += price - (cost - rebate)
        block += device_margin_columns(price, rebate, cost)["net"]
    return round(block - legacy, 2)


def carrier_device_margin_profile(rows, column="device_margin"):
    """PURE: what the CARRIER's own margin column actually is, for a set of raw_ma_commission rows.
    Establishes ❶ per period instead of assuming August's shape holds forever.

    Returns {"rows", "nonzero_rows", "total", "distinct_values" (sorted), "flat_per_unit" (bool),
             "matches_owner_formula" (None — undecidable from this column alone)}.
    `flat_per_unit` True means the column takes so few distinct values that it cannot be a
    price-derived margin: it is a per-unit allowance, and the owner's device margin must be
    COMPUTED (device_margin_columns) rather than read off the sheet."""
    vals, total, nonzero, n = {}, 0.0, 0, 0
    for r in rows or []:
        n += 1
        v = round(safe_float((r or {}).get(column)), 2)
        total += v
        if v:
            nonzero += 1
            vals[v] = vals.get(v, 0) + 1
    return {"rows": n, "nonzero_rows": nonzero, "total": round(total, 2),
            "distinct_values": sorted(vals), "value_counts": dict(vals),
            "flat_per_unit": 0 < len(vals) <= 3,
            "matches_owner_formula": None}


# ── "rebate received is not commission" (owner directive 2026-09-08) ─────────────────────────────
# "dont count any rebate received in the commission — it reflects in the balance sheet towards gross
# sales but not in gross profit."
#
# A device-purchase rebate is the vendor giving back part of what the dealer PAID for a handset. It
# is not the carrier paying the dealer for producing a subscriber, so it must never be summed into
# "commission received" on any surface. Measured (org 854f6d7b…, Aug-2026): `pl_rebate_presentation`
# = 'income' put $251,946.31 of rebate on the `rebate_income` REVENUE line — three times the whole
# month's real MA commission — while the master agent's own back-office P&L carries no rebate line
# at all. Switching the org back to the house default 'contra_cogs' takes those dollars out of
# revenue and nets them against Device cost where the purchase sits; gross profit and net income are
# identical either way (revenue and COGS move together), so this changes what the number IS CALLED,
# not what the dealer earned.
#
# The list below is the checked invariant: no rebate line may ever be part of the commission-received
# family. Pinned by harness_commission_backoffice_recon.py so a future edit cannot re-file a rebate
# as commission by accident.
COMMISSION_RECEIVED_LINES = (
    "carrier_comm",           # M1..M12+ month spiffs / bounties paid by the carrier
    "mi_income",              # residual (labelled "Residual" on the MA side)
    "atu_income",             # legacy airtime fold (pre-mig-309 orgs)
    "ma_merchant_discount",   # airtime margin — its own line since mig 309
    "mdf_income",             # MDF / market spiffs (the $1,000-per-store premium store spiff)
    "fee_income",             # fee margin from the commission sheet
)
REBATE_LINES = tuple(line for line, _sign in REBATE_ROUTES.values())
# Owner directive 2026-09-09 makes the device margin a BOOKED PROFIT line. That does NOT make it
# commission: it is selling price + rebate − cost on handsets the dealer bought and sold, and the
# rebate the owner explicitly excluded is one of its three columns. The block's line and all three
# of its column keys are therefore excluded from "commission received" for exactly the reason the
# rebate is — pinned by harness_commission_backoffice_recon.py §F and harness_device_margin_block.py
# so no future edit can re-file device profit as commission earned.
NON_COMMISSION_DEVICE_LINES = ((DEVICE_MARGIN_LINE, "ma_device_margin", "device_rev", "device_cost")
                               + DEVICE_MARGIN_COLUMN_KEYS + REBATE_LINES)


def commission_received_lines():
    """PURE: the P&L line keys that make up "commission received" from the carrier / master agent.
    Deliberately EXCLUDES every rebate line (`REBATE_LINES`), the device-margin block and its
    columns, the carrier's own per-unit device margin and device revenue
    (`NON_COMMISSION_DEVICE_LINES`) — a rebate is money back on a purchase, and a device margin is
    trading profit on a handset; neither is commission earned for producing a subscriber."""
    return COMMISSION_RECEIVED_LINES


# ── "a hypothesis must reproduce the stores, not just the total" (owner directive 2026-09-09) ────
# Reverse-calculating a back-office line is only finished when it lands STORE BY STORE. Two
# different money families can total the same month to the dollar and still be different money, so
# a total-only agreement is not evidence — it is a coincidence waiting to be found out. This is the
# shared comparator the back-office recon uses to say so out loud, rather than each investigation
# re-deciding what "matches" means.
AGREEMENT_REPRODUCES = "reproduces"          # total AND every store, within tolerance
AGREEMENT_TOTAL_ONLY = "total only"          # the coincidence case — explicitly NOT a match
AGREEMENT_NO = "does not reproduce"


def per_store_agreement(ours, theirs, tol=0.01):
    """PURE: {store: amount} ours vs theirs → how well a candidate reproduces a target, per store.

    Returns {"stores", "stores_exact", "total_ours", "total_theirs", "total_diff", "max_abs_diff",
             "worst_store", "verdict"}. `verdict` is AGREEMENT_REPRODUCES only when the total AND
    every store agree within `tol`; a candidate that ties on the month but misses stores is
    AGREEMENT_TOTAL_ONLY — named, so it can never be reported as a match."""
    keys = sorted(set(ours or {}) | set(theirs or {}))
    diffs = {k: round(safe_float((ours or {}).get(k)) - safe_float((theirs or {}).get(k)), 2)
             for k in keys}
    exact = sum(1 for k in keys if abs(diffs[k]) <= tol)
    to = round(sum(safe_float(v) for v in (ours or {}).values()), 2)
    tt = round(sum(safe_float(v) for v in (theirs or {}).values()), 2)
    worst = max(keys, key=lambda k: abs(diffs[k])) if keys else None
    total_ok = abs(round(to - tt, 2)) <= tol
    verdict = (AGREEMENT_REPRODUCES if total_ok and exact == len(keys) and keys
               else AGREEMENT_TOTAL_ONLY if total_ok else AGREEMENT_NO)
    return {"stores": len(keys), "stores_exact": exact, "total_ours": to, "total_theirs": tt,
            "total_diff": round(to - tt, 2),
            "max_abs_diff": round(max((abs(d) for d in diffs.values()), default=0.0), 2),
            "worst_store": worst, "verdict": verdict}


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


# ── A SPIFF PAID LATE BELONGS TO THE ACTIVATION THAT EARNED IT (owner directive 2026-09-09) ──────
# Owner, verbatim: "not sure what retrto active post paid spiff is im assuming spiff paid later but
# it must be assigned to a phone number or imei or order actiavted at a certain store".
#
# He is right about the principle, and the data answers only half of it. MEASURED, org 854f6d7b…,
# August 2026, the 230 `'Retroactive Postpaid Spiff'` rows ($3,794.56, which now book to
# `carrier_comm` because the owner added the family to `pl_ma_spiff_order_types`):
#   • STORE — YES. All 230 carry `account_id`, and all 230 resolve to a store through the mig-314
#     account→store index. Nothing is company-wide; nothing is allocated by a formula.
#   • REP — YES. All 230 carry `user_name` (42 distinct clerk logins).
#   • WHEN PAID — YES. All 230 carry `tx_date`, all inside August.
#   • THE ACTIVATION — NO. Every product_name is a MONTH-1 "… New Activation Commission", i.e. an
#     activation-month spiff arriving late, exactly as the owner assumed. But the row cannot be
#     pointed AT that activation: `raw_ma_daily_tx` has no imei and no mdn, and its `order_number`
#     matches NOTHING — not `raw_ma_commission.activation_order` (0 of 230 against all 2,522
#     activation orders we hold, June–September), not `merchant_invoice`, `platform_tx_id`,
#     `pos_invoice` or `external_ref` (0 each), and not any other daily-tx row: each of the 230
#     order numbers appears exactly once in 56,515 rows spanning February–September.
#     ⚠ This is NOT special to the retroactive family — it is how the whole PAYOUT side of this feed
#     is keyed. `order_number` matches an activation order ONLY on `'Activation Order'` rows (1,735
#     of 4,995); across `Postpaid Residual Order` (19,790), `PostPaid Additional Spiff` (11,060),
#     `Sales Order` (10,758), `Postpaid Promo Order` (3,707) and every other family the match rate
#     is 0.0%. So the mig-308 hop-2 premise ("one order = activation row + MONTH-n rows +
#     adjustments", `sale_installment_engine.build_ma_tx_index`) does not hold for this tenant's
#     export: each payout row carries its own transaction id, so `build_ma_tx_index[order]['months']`
#     can never be populated for an order whose activation row the same index holds. REPORTED as a
#     live-data fact, not worked around in code.
#
# WHAT THE FEED WOULD HAVE TO CARRY: the activation's own identifier on the payout row — the same id
# space as `raw_ma_commission.activation_order` / the `'Activation Order'` rows' `order_number` — or
# an `imei`/`mdn` column. With either, the month a late spiff belongs to becomes a lookup and the
# `activation_period` below stops being None. WITHOUT it, matching a $15.00 retroactive row to one
# of a store's month-old $15.00 activations by amount+plan is a GUESS, and a guess that looks
# precise is worse than a stated absence — so this function never makes one.
ATTRIBUTION_NO_ACTIVATION_REASON = (
    "activation not identifiable — the payout row carries no imei/mdn and its order_number "
    "matches no activation order in any feed")


def _norm_order(v):
    """Trim + strip an Excel-float trailing '.0'. The SAME normalization the mig-308 join owner
    (`sale_installment_engine._norm_order`) applies to `activation_order` ↔ `order_number`; it is
    restated here as two lines rather than importing that heavy module, so this stays a pure,
    DB-free read-out. NOT digit-only — order numbers can be alphanumeric."""
    s = str(v or "").strip()
    return s[:-2] if s.endswith(".0") else s


def ma_payout_attribution(rows, activation_orders=None, store_index=None, activation_period=None):
    """PURE: raw_ma_daily_tx payout rows → what each order-type family CAN honestly be attributed
    to, and what it cannot.

    A READ-OUT beside `ma_tx_bookings` / `ma_tx_coverage` — not a second join and not a second
    booking path. It reports which rows the EXISTING mig-308 activation_order ↔ order_number linkage
    reaches, and names the absence for the rest. It moves no dollar.

      rows              — raw_ma_daily_tx dicts (order_type, order_number, account_id, user_name,
                          tx_date, retail_cost).
      activation_orders — known activation order ids (`raw_ma_commission.activation_order` ∪ the
                          `'Activation Order'` rows' `order_number`). None/empty ⇒ nothing links.
      store_index       — {account_id: store} (`canonical_store_index`). None ⇒ no store is claimed
                          for any row; a store is NEVER inferred from anything else.
      activation_period — {activation_order: period} so a linked late spiff can be reported against
                          the month it was EARNED in. Absent ⇒ no period is claimed.

    Returns {"families": [{order_type, rows, amount, stores, reps, dated, linked_rows,
                           linked_amount, unlinked_rows, unlinked_amount, store_resolved_rows,
                           store_unresolved_rows, activation_periods, reason}, …],
             "rows", "amount", "linked_amount", "unlinked_amount", "store_resolved_amount"}
    ordered biggest-|amount| first. `amount` is money TO the dealer (−retail_cost) — the same sign
    convention `ma_tx_bookings` books with. `reason` is set only for a family with unlinked rows."""
    known = {_norm_order(o) for o in (activation_orders or [])} - {""}
    idx = {str(k).strip(): v for k, v in (store_index or {}).items() if str(k).strip()}
    per = {}
    for r in rows or []:
        r = r or {}
        ot = str(r.get("order_type") or "").strip()
        fam = per.setdefault(ot, {"order_type": ot, "rows": 0, "amount": 0.0,
                                  "stores": set(), "reps": set(), "dated": 0,
                                  "linked_rows": 0, "linked_amount": 0.0,
                                  "unlinked_rows": 0, "unlinked_amount": 0.0,
                                  "store_resolved_rows": 0, "store_unresolved_rows": 0,
                                  "activation_periods": set()})
        amt = -safe_float(r.get("retail_cost"))
        fam["rows"] += 1
        fam["amount"] += amt
        store = idx.get(str(r.get("account_id") or "").strip())
        if store:
            fam["stores"].add(store)
            fam["store_resolved_rows"] += 1
        else:
            fam["store_unresolved_rows"] += 1
        rep = str(r.get("user_name") or "").strip()
        if rep:
            fam["reps"].add(rep)
        if str(r.get("tx_date") or "").strip():
            fam["dated"] += 1
        order = _norm_order(r.get("order_number"))
        if order and order in known:
            fam["linked_rows"] += 1
            fam["linked_amount"] += amt
            p = (activation_period or {}).get(order)
            if p:
                fam["activation_periods"].add(str(p))
        else:
            fam["unlinked_rows"] += 1
            fam["unlinked_amount"] += amt
    out = []
    for fam in per.values():
        fam["stores"] = sorted(fam["stores"])
        fam["reps"] = sorted(fam["reps"])
        fam["activation_periods"] = sorted(fam["activation_periods"])
        for k in ("amount", "linked_amount", "unlinked_amount"):
            fam[k] = round(fam[k], 2)
        fam["reason"] = ATTRIBUTION_NO_ACTIVATION_REASON if fam["unlinked_rows"] else None
        out.append(fam)
    out.sort(key=lambda fm: (-abs(fm["amount"]), fm["order_type"]))
    return {"families": out,
            "rows": sum(fm["rows"] for fm in out),
            "amount": round(sum(fm["amount"] for fm in out), 2),
            "linked_amount": round(sum(fm["linked_amount"] for fm in out), 2),
            "unlinked_amount": round(sum(fm["unlinked_amount"] for fm in out), 2),
            "store_resolved_amount": round(
                sum(fm["amount"] for fm in out if not fm["store_unresolved_rows"]), 2)}
