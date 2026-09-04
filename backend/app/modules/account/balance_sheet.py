"""Balance-sheet truths — PURE logic for the four owner-reported balance-sheet defects
(owner report 2026-09-02) + the config loader that resolves them per org.

The owner's words, verbatim: "in balance sheet, make sure the inventory shows all the unsold
phones, it should also reconcile against the inventory report being pulled in the email in the
reconciliation tab, the money owed for the phones is not being uploaded, which has already been
defined as per the due dates in the handset report in total and the asset landing in boost for
now, again nothing is hardcoded but built on a logic to be used by other prospective tenants,
i updated the owner contribution and marked the company in [notes] for both luxelink and nova
but it does not show up in the balance sheet, i also added the loan but it never showed up."

WHAT WAS WRONG (measured on the live LuxeLink org, 854f6d7b-…, 2026-09-02)
--------------------------------------------------------------------------
1. INVENTORY ≠ the unsold-device set. The BS line reads `inventory_value` (the per-store $ totals
   of the emailed b2bsoft Inventory Aging report — $173,057.07 across 20 stores) with an
   `asset_ledger` fallback; the DEVICE-level unsold set (`inventory_aging_device`, on_hand=true at
   the current snapshot) sums $166,020.16 over 938 phones — a $7,036.91 gap NOTHING reconciled.
   Worse, 556 further rows sit on_hand=true with `store=NULL` and July as_of dates ($129,454.66):
   they came from uploads that carried no store, so the sweep's per-store off-hand flip can never
   retire them — counting raw on_hand would overstate inventory by ~$129k of ghosts.
   → `device_inventory_cells` (snapshot-coherent unsold-phone set) + `inventory_recon_rows` (the
   per-store tie-out the reconciliation tab shows) + `apply_inventory_basis` (config-driven basis:
   'report' = today's behaviour byte-identical; 'devices' = the unsold-phone ledger, manual
   override still winning).

2. HANDSET PAYABLES never book. The money owed for phones IS in the tenant's own data with the
   vendor's OWN due dates — `raw_ma_daily_tx.due_date` is populated on every row (mig 620's
   verification note), and the marketplace handset purchases are the `order_type` family the org
   configures (LuxeLink: 'Postpaid Branded MarketPlace', 2,093 rows / $696,585.25 lifetime;
   $169,013.57 not yet due as of 2026-09-02). The Boost side already books its device payable from
   `asset_ledger.owed_to_vip` (the `owed_vip` BS line) — per the owner: "the asset [ledger] in
   boost for now". → `handset_payable_bookings` books the OUTSTANDING (transacted on/before as-of,
   due after as-of) rows of the configured order-type families to the NEW `handset_payable`
   liability line. Empty config (every org's default) books NOTHING — byte-identical; no carrier
   or tenant name appears in code (RULE TWO).

3+4. JOURNAL ENTRIES (owner contribution / loan) invisible outside Consolidated. The journal UI
   has no company picker, so the owner typed the company INTO the free-text store field — his live
   rows (journal_entries, org 854f6d7b, period 'August 2026', created 2026-09-02T03:05Z):
       equity  'Owner capital / contributions'  $250,000.00  store_address='Luxelink'
       equity  'Owner capital / contributions'  $100,000.00  store_address='Novawave'
       liability 'Loan'                         $210,000.00  store_address='Luxelink'
   `engine._journal_for_scope` only honours company_id / an EXACT store address, so these entries
   reach the CONSOLIDATED scope alone — and even there only after a recompute (the stored snapshot
   pre-dated them by 35 minutes). The org's companies are named 'Luxlink Wireless' (sic) and
   'Nova Wave Communications'. → `journal_company_matcher` resolves a typed designation to a
   company DETERMINISTICALLY (exact → squashed → unique squash-prefix → unique 1-edit tolerance,
   ambiguous ⇒ None, never a guess between two companies) and `journal_scope_entries` is the
   fixed scoping used by statement_engine. The lasting fix (a company PICKER on the journal page)
   is an Option-B UI follow-up; this makes the owner's already-entered rows land correctly.

EVERYTHING here is pure (rows + resolved config in, bookings/cells out) — proof:
backend/harness_balance_sheet_truths.py (stdlib only, runs the owner's real rows).
Config lives in commcalc.account_config (mig 611/933), per-org with house defaults — no tenant,
carrier or company name in code.
"""
from app.modules.commcalc.calculator import safe_float

# ── the NEW balance-sheet line this module feeds ────────────────────────────────────────────────
# Same 5-tuple shape as coa.BS_SPEC. `auto_opt` ⇒ the line materialises ONLY when it carries value
# (engine._assemble drops empty auto_opt lines), so every org with the empty default config keeps a
# byte-identical Balance Sheet. Store grain rides the mig-314 account→store index when the org has
# store attribution on; otherwise company-wide (honest beats mis-attributed).
EXTRA_BS_SPEC = [
    ("handset_payable", "Handset payables (devices due to distributor)", "liability", "auto_opt", "store"),
    # Owner directive 2026-09-02 (mig 938): "all cash collected in the store must be added to the
    # balance sheet as cash collected after it has been verified by the DM, either the cash is
    # deposited in the bank or it is used in expenses, everything needs to be updated in the
    # financials as appropriate." The store-cash lifecycle already exists row-level in the closing
    # module (declared cash → DM verification → pickups/deposits → envelope expenses/withdrawals,
    # ONE shared computation: closing.router._cash_position_core); this line surfaces the AS-OF
    # balance of that lifecycle on the Balance Sheet per store. `auto_opt` + basis 'off' default ⇒
    # byte-identical books until an org opts in. In the cash-flow statement this line is CASH
    # (statement_engine.CF_CASH_KEYS), not a working-capital delta.
    ("store_cash_on_hand", "Cash on hand — stores (undeposited)", "asset", "auto_opt", "store"),
]

INVENTORY_BASES = ("report", "devices")
CASH_ON_HAND_BASES = ("off", "verified", "all")

# ── DISTRIBUTOR PAYABLE BASIS (owner directive 2026-09-04, mig 954) ─────────────────────────────
# Owner, verbatim: "balance sheet in cellfonz rus is showing a wrong figure it should be open
# balance owed 358221.13 not as a hard coded figure but derived, accounts payable for total should
# come from the open balance Owed to distributor (outstanding) $281,674.04 as of 2026-09-04."
#
# The two carrier sides answer the SAME question — "what do we still owe the distributor for
# devices, as of a date" — from two different tenant feeds, so there is ONE basis knob naming which
# feed a tenant's answer comes from, never a carrier branch in code (RULE TWO):
#   'asset_ledger'    the consignment/asset ledger's OPEN rows (commcalc.asset_ledger) — the same
#                     "Open Balance Owed" the asset dashboard has always shown.
#   'marketplace_due' the marketplace handset feed inside the vendor's own due-date window — the
#                     mig-933 handset_payable derivation, unchanged.
#   'off'             books nothing (house default for an org that has declared neither).
DISTRIBUTOR_PAYABLE_BASES = ("off", "asset_ledger", "marketplace_due")

# WHICH Balance-Sheet line a basis books to is a per-tenant COST-CENTRE choice, not a truth about
# the money (owner clarification 2026-09-04, verbatim: "it does show in luxelink but in a different
# line which is acceptable as all companies have a different way of assigning their cost center").
# These are the DEFAULT targets — today's live placement on both orgs, so an org that never sets
# `distributor_payable_line` is byte-identical. A tenant may retarget to any liability line key in
# the assembled spec (pick-don't-type: `resolve_payable_line` refuses a key the spec does not have).
PAYABLE_BASIS_DEFAULT_LINE = {"asset_ledger": "owed_vip", "marketplace_due": "handset_payable"}

# The asset-ledger status vocabulary that means "still owed". Config with a house default — the
# ledger's own status column, never a tenant/carrier word in code. Measured on the live house org
# 2026-09-04: statuses present are {'Open', 'Paid In Full', NULL}; Open = 2,505 rows / $358,221.13,
# which is exactly the figure the owner named and exactly what the asset dashboard's "Open Balance
# Owed" card has always summed (backend/app/modules/asset/router.py get_asset_summary).
# Deliberately a POSITIVE vocabulary, never "not settled": one live row carries status NULL with
# $117,730.73 owed and no dates/store/category at all (id 889865 — a header/aggregate artifact of
# the ledger upload). Negating the settled set would have swept it into the books.
ASSET_LEDGER_OPEN_STATUSES_DEFAULT = ("open",)


# ── config (commcalc.account_config, mig 933/938 columns) ───────────────────────────────────────
def default_bs_config():
    """Defaults = today's behaviour for every org: inventory from the report totals, no handset
    payable booked, no store cash-on-hand booked. A tenant opts in per org — never a code branch."""
    return {"inventory_basis": "report", "handset_payable_order_types": [],
            "cash_on_hand_basis": "off",
            # mig 954. `distributor_payable_basis` None ⇒ "not declared": the resolver falls back to
            # the org's CARRIER preset (lazy auto-assign, the mig-945/953 pattern) and only then to
            # 'off'. An explicit 'off' is a tenant DECISION and wins over the preset.
            "distributor_payable_basis": None,
            "distributor_payable_line": "",
            "asset_ledger_open_statuses": list(ASSET_LEDGER_OPEN_STATUSES_DEFAULT)}


def load_bs_config(client, org_id):
    """Per-org balance-sheet config, ADAPTIVE (pre-mig-933/938 schema or no row ⇒ defaults). NEVER
    raises; each column is its own defensive read exactly like coa._account_config."""
    cfg = default_bs_config()
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("inventory_basis").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and str(rows[0].get("inventory_basis") or "").strip().lower() in INVENTORY_BASES:
            cfg["inventory_basis"] = str(rows[0]["inventory_basis"]).strip().lower()
    except Exception:
        pass
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("handset_payable_order_types").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and isinstance(rows[0].get("handset_payable_order_types"), list):
            cfg["handset_payable_order_types"] = [str(t).strip() for t in
                                                  rows[0]["handset_payable_order_types"] if str(t).strip()]
    except Exception:
        pass
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("cash_on_hand_basis").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and str(rows[0].get("cash_on_hand_basis") or "").strip().lower() in CASH_ON_HAND_BASES:
            cfg["cash_on_hand_basis"] = str(rows[0]["cash_on_hand_basis"]).strip().lower()
    except Exception:
        pass
    # mig 954 — distributor payable basis / target line / open-status vocabulary. Each column is its
    # own defensive read: a pre-954 database simply keeps the defaults (basis None ⇒ carrier preset).
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("distributor_payable_basis").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and str(rows[0].get("distributor_payable_basis") or "").strip().lower() in DISTRIBUTOR_PAYABLE_BASES:
            cfg["distributor_payable_basis"] = str(rows[0]["distributor_payable_basis"]).strip().lower()
    except Exception:
        pass
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("distributor_payable_line").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and str(rows[0].get("distributor_payable_line") or "").strip():
            cfg["distributor_payable_line"] = str(rows[0]["distributor_payable_line"]).strip()
    except Exception:
        pass
    try:
        rows = (client.schema("commcalc").table("account_config")
                .select("asset_ledger_open_statuses").eq("org_id", org_id).limit(1).execute().data) or []
        if rows and isinstance(rows[0].get("asset_ledger_open_statuses"), list):
            vocab = [str(t).strip() for t in rows[0]["asset_ledger_open_statuses"] if str(t).strip()]
            if vocab:                       # an empty list would book nothing — keep the default
                cfg["asset_ledger_open_statuses"] = vocab
    except Exception:
        pass
    return cfg


# ── 1. inventory = the unsold-phone set (snapshot-coherent) ─────────────────────────────────────
def device_inventory_cells(device_rows):
    """PURE: `inventory_aging_device` rows ({store, unit_cost, on_hand, as_of_date}) → the
    unsold-phone value per store, plus the honesty meta.

    Coherence rules (each backed by the live LuxeLink evidence in the module docstring):
      • only on_hand=true rows count — off-hand devices were sold/removed;
      • a row with NO store cannot be attributed (and, live, is exactly the July ghost set the
        per-store off-hand flip can never retire) — EXCLUDED from the cells but REPORTED in meta
        (`unplaced_devices`/`unplaced_value`) so nothing vanishes silently;
      • within a store, only rows at that store's LATEST as_of_date count (`superseded_*` meta
        otherwise): the snapshot upsert stamps every on-hand device with the file date, so an
        older-dated on_hand row is a leftover a failed flip left behind, not a phone on the shelf.
    Returns (cells {store: {value, devices, as_of}}, meta)."""
    latest = {}
    for r in device_rows or []:
        r = r or {}
        if not r.get("on_hand"):
            continue
        st = (str(r.get("store") or "").strip()) or None
        if not st:
            continue
        a = str(r.get("as_of_date") or "")
        if a and a > latest.get(st, ""):
            latest[st] = a
    cells = {}
    unplaced_n, unplaced_v = 0, 0.0
    superseded_n, superseded_v = 0, 0.0
    for r in device_rows or []:
        r = r or {}
        if not r.get("on_hand"):
            continue
        st = (str(r.get("store") or "").strip()) or None
        cost = safe_float(r.get("unit_cost"))
        if not st:
            unplaced_n += 1
            unplaced_v = round(unplaced_v + cost, 2)
            continue
        a = str(r.get("as_of_date") or "")
        if latest.get(st) and a != latest[st]:
            superseded_n += 1
            superseded_v = round(superseded_v + cost, 2)
            continue
        c = cells.setdefault(st, {"value": 0.0, "devices": 0, "as_of": latest.get(st)})
        c["value"] = round(c["value"] + cost, 2)
        c["devices"] += 1
    meta = {"unplaced_devices": unplaced_n, "unplaced_value": round(unplaced_v, 2),
            "superseded_devices": superseded_n, "superseded_value": round(superseded_v, 2),
            "stores": len(cells)}
    return cells, meta


def apply_inventory_basis(inventory_value_rows, device_cells, basis, resolve=None):
    """PURE: the per-store EFFECTIVE inventory value under a basis. Precedence (per store):
        manual override  >  basis value  >  the other source (coverage never regresses).
      basis 'report'  — swept report totals first (today's behaviour), device value only where a
                        store has no report row;
      basis 'devices' — the unsold-phone ledger first (the owner's "all the unsold phones"),
                        report value only where a store has no fresh device rows.
    `resolve` is coa.store_resolver (spelling canonicalization); identity when None.
    Returns {store: {"value": x, "source": 'manual'|'devices'|'report'}}."""
    rz = resolve or (lambda s: s)
    out = {}
    swept, manual = {}, {}
    for r in inventory_value_rows or []:
        r = r or {}
        st = rz((str(r.get("store") or "").strip()) or None)
        if not st:
            continue
        if r.get("manual_value") is not None:
            manual[st] = safe_float(r.get("manual_value"))
        if r.get("swept_value") is not None:
            swept[st] = safe_float(r.get("swept_value"))
    dev = {}
    for st, c in (device_cells or {}).items():
        k = rz(st) or st
        dev[k] = round(dev.get(k, 0.0) + safe_float(c.get("value")), 2)
    primary, secondary = (dev, swept) if basis == "devices" else (swept, dev)
    p_src, s_src = ("devices", "report") if basis == "devices" else ("report", "devices")
    for st in set(list(primary) + list(secondary) + list(manual)):
        if st in manual:
            out[st] = {"value": round(manual[st], 2), "source": "manual"}
        elif st in primary:
            out[st] = {"value": round(primary[st], 2), "source": p_src}
        elif st in secondary:
            out[st] = {"value": round(secondary[st], 2), "source": s_src}
    return out


def inventory_recon_rows(inventory_value_rows, device_cells, basis, resolve=None):
    """PURE: the per-store tie-out between the EMAILED report totals (inventory_value.swept_value),
    the unsold-phone ledger (device_cells) and any manual override — the rows the reconciliation
    tab shows. `delta` = devices − report (0 ⇒ the two sources agree). Returns (rows, totals)."""
    rz = resolve or (lambda s: s)
    eff = apply_inventory_basis(inventory_value_rows, device_cells, basis, resolve)
    swept, manual, as_of = {}, {}, {}
    for r in inventory_value_rows or []:
        r = r or {}
        st = rz((str(r.get("store") or "").strip()) or None)
        if not st:
            continue
        if r.get("swept_value") is not None:
            swept[st] = safe_float(r.get("swept_value"))
        if r.get("manual_value") is not None:
            manual[st] = safe_float(r.get("manual_value"))
        if r.get("as_of_date"):
            as_of[st] = r.get("as_of_date")
    dev_val, dev_n, dev_as_of = {}, {}, {}
    for st, c in (device_cells or {}).items():
        k = rz(st) or st
        dev_val[k] = round(dev_val.get(k, 0.0) + safe_float(c.get("value")), 2)
        dev_n[k] = dev_n.get(k, 0) + int(c.get("devices") or 0)
        dev_as_of[k] = c.get("as_of") or dev_as_of.get(k)
    rows = []
    for st in sorted(set(list(swept) + list(dev_val) + list(manual))):
        rv, dv = swept.get(st), dev_val.get(st)
        e = eff.get(st) or {}
        rows.append({"store": st,
                     "report_value": rv, "report_as_of": as_of.get(st),
                     "device_value": dv, "device_count": dev_n.get(st, 0),
                     "device_as_of": dev_as_of.get(st),
                     "manual_value": manual.get(st),
                     "effective": e.get("value"), "effective_source": e.get("source"),
                     "delta": (round((dv or 0.0) - (rv or 0.0), 2)
                               if (rv is not None or dv is not None) else None)})
    totals = {"report_value": round(sum(v for v in swept.values()), 2),
              "device_value": round(sum(v for v in dev_val.values()), 2),
              "device_count": sum(dev_n.values()),
              "effective": round(sum(safe_float((eff.get(s) or {}).get("value")) for s in eff), 2),
              "delta": round(sum(v for v in dev_val.values()) - sum(v for v in swept.values()), 2)}
    return rows, totals


# ── 2. handset payables per the vendor's own due dates ──────────────────────────────────────────
# MONEY-COLUMN GUARD: `retail_cost` is the ONLY raw_ma_daily_tx column ever read as money here
# (`merchant_invoice` is an identifier — the same guard residual_subs.assert_money_columns enforces
# on the P&L side).
HANDSET_PAYABLE_MONEY_COLUMNS = ("retail_cost",)


def handset_payable_bookings(tx_rows, order_types, as_of):
    """PURE: raw_ma_daily_tx rows ({account_id, order_type, retail_cost, tx_date, due_date}) →
    the OUTSTANDING handset payable as of `as_of` ('YYYY-MM-DD').

    A row is outstanding when its order_type is in the configured family (case-insensitive),
    it was transacted ON/BEFORE as_of, and its due_date is AFTER as_of — the vendor's own net-terms
    window, exactly "as per the due dates in the handset report" (owner). On/after the due date the
    processor settles the line (auto-debit), so it stops being a payable. A negative retail_cost in
    the family (an RMA/credit) nets against the balance — sign is preserved, never clamped.

    Empty `order_types` ⇒ [] (every org's default — byte-identical books). Returns
    (bookings [(account_id_or_None, amount, order_type_detail)], meta {rows, total})."""
    fams = {str(t).strip().lower() for t in (order_types or []) if str(t).strip()}
    if not fams or not as_of:
        return [], {"rows": 0, "total": 0.0}
    cutoff = str(as_of)[:10]
    out, total = [], 0.0
    for r in tx_rows or []:
        r = r or {}
        ot = str(r.get("order_type") or "").strip()
        if ot.lower() not in fams:
            continue
        tx_d = str(r.get("tx_date") or "")[:10]
        due_d = str(r.get("due_date") or "")[:10]
        if not tx_d or not due_d:
            continue          # a row without both dates cannot be placed in the window — honest skip
        if tx_d > cutoff or due_d <= cutoff:
            continue
        amt = safe_float(r.get("retail_cost"))
        if not amt:
            continue
        acct = str(r.get("account_id") or "").strip() or None
        out.append((acct, amt, ot))
        total = round(total + amt, 2)
    return out, {"rows": len(out), "total": total}


# ── 2a. the OTHER side of the same question: the asset-ledger OPEN balance (mig 954) ────────────
# Money columns are named here for the same reason HANDSET_PAYABLE_MONEY_COLUMNS is: the reader
# selects exactly this column and nothing else, so a schema change can never silently swap the
# figure for a look-alike ("total_owed" on the live house org is 0.00 on every row).
ASSET_LEDGER_MONEY_COLUMNS = ("owed_to_vip",)


def asset_ledger_open_bookings(ledger_rows, open_statuses, as_of):
    """PURE: `asset_ledger` rows ({store, status, owed_to_vip, acquired_date, due_date}) → the OPEN
    balance still owed to the distributor as of `as_of` ('YYYY-MM-DD').

    THE ONE DERIVATION for this side. It is the same predicate the asset dashboard's "Open Balance
    Owed" card has always summed (`asset.router.get_asset_summary` → `total_open_balance`), made
    AS-OF-PARAMETERIC so the current period, a closed period and the liabilities tile all read the
    same function at a different date — never a second formula (owner directive 2026-09-04).

    A row is open when
      · its `status` is in the org's configured OPEN vocabulary (case-insensitive; house default
        `('open',)`), AND
      · it was acquired ON/BEFORE `as_of` — a device the distributor had not yet shipped on that
        date cannot have been owed on that date. A row with NO acquired_date is KEPT (its money is
        real; dropping it would understate the payable) and counted in meta `undated`.

    HONESTY — the as-of walk-back. `asset_ledger` is a wipe-and-reinsert CURRENT snapshot: it
    carries no settlement date, so `status` describes the ledger TODAY, not on a past date. For an
    as-of in the past this function therefore reports a snapshot-basis estimate: rows acquired by
    that date that are STILL open now. It is stated, never implied — meta carries
    `basis: 'status_snapshot'` and `snapshot_lag: True` whenever as_of is before `today`, so a
    closed-period Balance Sheet can say what its figure is. (Same posture as the mig-938 `floored`
    and the K3 `honest_zero` notes: report the limitation, never quietly pretend.)

    Returns (bookings [(store_or_None, amount, status_detail)],
             meta {rows, total, past_due, not_yet_due, undated, statuses, basis, snapshot_lag}).
    An empty vocabulary or a missing as_of books NOTHING (the 'off' default stays byte-identical).
    """
    vocab = {str(t).strip().lower() for t in (open_statuses or []) if str(t).strip()}
    base = {"rows": 0, "total": 0.0, "past_due": 0.0, "not_yet_due": 0.0, "undated": 0,
            "statuses": {}, "basis": "status_snapshot", "snapshot_lag": False}
    if not vocab or not as_of:
        return [], base
    cutoff = str(as_of)[:10]
    out, total, past_due, not_yet_due, undated = [], 0.0, 0.0, 0.0, 0
    statuses = {}
    for r in ledger_rows or []:
        r = r or {}
        st = str(r.get("status") or "").strip()
        if st.lower() not in vocab:
            continue
        acq = str(r.get("acquired_date") or "")[:10]
        if acq and acq > cutoff:
            continue                      # not yet acquired at the as-of date
        if not acq:
            undated += 1
        amt = safe_float(r.get("owed_to_vip"))
        if not amt:
            continue
        store = str(r.get("store") or "").strip() or None
        out.append((store, amt, st))
        total = round(total + amt, 2)
        statuses[st] = round(statuses.get(st, 0.0) + amt, 2)
        due = str(r.get("due_date") or "")[:10]
        if due and due <= cutoff:
            past_due = round(past_due + amt, 2)
        else:
            not_yet_due = round(not_yet_due + amt, 2)
    meta = {**base, "rows": len(out), "total": total, "past_due": past_due,
            "not_yet_due": not_yet_due, "undated": undated, "statuses": statuses}
    return out, meta


# ── 2c. basis + target-line resolution (mig 954) ────────────────────────────────────────────────
def resolve_payable_basis(org_basis, carrier_preset_basis, marketplace_families_configured=False):
    """PURE precedence, owner directive 2026-09-04 §C: ORG OVERRIDE > CARRIER PRESET > 'off'.

    `org_basis`            the tenant's own `account_config.distributor_payable_basis`. None/'' =
                           NOT DECLARED (fall through). An explicit 'off' is a tenant DECISION and
                           wins over the preset — a tenant can always turn the line off.
    `carrier_preset_basis` the basis the org's CARRIER declares (a house-org preset row keyed by
                           `report_labels.normalize_carrier_code` — the SAME lazy auto-assign the
                           mig-945/953 label presets use, so a NEW tenant that picks its carrier at
                           onboarding is correct with no setup hook and no code branch).
    An unknown value at either level is ignored rather than trusted.

    `marketplace_families_configured` is the NO-REGRESSION floor: an org that already carries
    mig-933 `handset_payable_order_types` has DECLARED a marketplace basis by configuring one, so
    it keeps booking even if nobody ever sets the mig-954 column and its carrier has no preset. It
    ranks BELOW both explicit levels, so an org that deliberately sets 'off' still turns off."""
    ob = str(org_basis or "").strip().lower()
    if ob in DISTRIBUTOR_PAYABLE_BASES:
        return ob
    cb = str(carrier_preset_basis or "").strip().lower()
    if cb in DISTRIBUTOR_PAYABLE_BASES:
        return cb
    if marketplace_families_configured:
        return "marketplace_due"
    return "off"


def resolve_payable_line(basis, org_line, valid_line_keys):
    """PURE: which Balance-Sheet line key the resolved basis books to (owner clarification §B —
    the target line is a per-tenant cost-centre choice, not a truth about the money).

    org override (when it names a line the SPEC actually has — pick-don't-type) > the basis default
    (`PAYABLE_BASIS_DEFAULT_LINE`, i.e. today's live placement on both orgs) > None for 'off'.
    An org_line naming a key the spec does not carry is IGNORED, never invented: a typo must not
    strand the money on a phantom line (the exact failure mode of the stranded journal entries)."""
    b = str(basis or "").strip().lower()
    if b not in PAYABLE_BASIS_DEFAULT_LINE:
        return None
    ol = str(org_line or "").strip()
    if ol and ol in set(valid_line_keys or ()):
        return ol
    return PAYABLE_BASIS_DEFAULT_LINE[b]


# ── 2b. verified store cash on hand (owner directive 2026-09-02, mig 938) ───────────────────────
def store_cash_cells(decl_by_store_day, taken_by_store_day, verified_keys, basis, as_of):
    """PURE: the per-store UNDEPOSITED cash balance as of `as_of` ('YYYY-MM-DD'), from the closing
    module's own movement dicts (the shape `_cash_position_core` returns —
    {store_code: {day: amount}}; `decl` already carries the DM-corrected figure for verified
    days, `taken` = everything that left the envelope: pickups/deposits + approved expenses +
    withdrawals).

    basis 'verified' — the owner's rule ("cash… added to the balance sheet AS CASH COLLECTED
    AFTER it has been verified by the DM"): only store-days present in `verified_keys`
    ({(store_code, day)} with verified=true) COUNT AS COLLECTED; unverified declared cash is
    EXCLUDED from the books and REPORTED in meta (`unverified_declared`) so nothing vanishes
    silently. SYMMETRY (defect fix, 2026-09-02 — the live LuxeLink August BS booked this ASSET
    line at −$36,660.91): outflows follow the SAME verification rule as inflows. Every movement
    dict is keyed (store, close_date) — an outflow's day IS the envelope it relieved (a pickup /
    deposit / approved expense / withdrawal books against its envelope's close_date) — so an
    outflow on an UNVERIFIED store-day moves cash that never entered this line and must not
    relieve it (live: 157 unverified store-days' pickups, $36,660.91, drained a bucket only 6
    verified store-days had filled). Unverified outflows are REPORTED in meta
    (`unverified_taken` / `unverified_taken_days`), never silently dropped.
    basis 'all' — every declared day counts AND every outflow counts (the operational
    cash-position number, exactly what GET /closing/store-cash-on-hand shows) — internally
    consistent, semantics unchanged.
    FAIL-SAFE FLOOR (every basis, every grain): a cash ASSET line never books negative. Any
    per-store negative residual (e.g. a DM correcting a verified day's cash BELOW what was
    already picked up, or pathological 'all'-basis data where pickups exceed declarations) is
    floored to zero and the suppressed amount reported per store in meta (`floored` /
    `floored_total`) — a real signal, surfaced honestly, never a negative asset and never
    silently dropped. Rollups sum floored per-store values, so no grain can go negative.

    Returns (cells {store_code: balance}, meta)."""
    if basis not in ("verified", "all") or not as_of:
        return {}, {"basis": basis, "stores": 0}
    cutoff = str(as_of)[:10]
    vkeys = verified_keys or set()
    cells = {}
    unverified_declared, unverified_days, verified_days = 0.0, 0, 0
    for st, days in (decl_by_store_day or {}).items():
        for d, amt in (days or {}).items():
            dd = str(d)[:10]
            if not dd or dd > cutoff:
                continue
            a = safe_float(amt)
            if basis == "verified" and (st, dd) not in vkeys:
                unverified_declared = round(unverified_declared + a, 2)
                unverified_days += 1
                continue
            verified_days += 1
            cells[st] = round(cells.get(st, 0.0) + a, 2)
    taken_total = 0.0
    unverified_taken, unverified_taken_days = 0.0, 0
    for st, days in (taken_by_store_day or {}).items():
        for d, amt in (days or {}).items():
            dd = str(d)[:10]
            if not dd or dd > cutoff:
                continue
            a = safe_float(amt)
            if basis == "verified" and (st, dd) not in vkeys:
                unverified_taken = round(unverified_taken + a, 2)
                unverified_taken_days += 1
                continue
            taken_total = round(taken_total + a, 2)
            cells[st] = round(cells.get(st, 0.0) - a, 2)
    floored = {}
    for st in list(cells):
        if cells[st] < 0:
            floored[st] = round(-cells[st], 2)
            cells[st] = 0.0
    cells = {st: v for st, v in cells.items() if v}
    meta = {"basis": basis, "as_of": cutoff, "stores": len(cells),
            "counted_days": verified_days, "taken_total": taken_total,
            "unverified_days": unverified_days,
            "unverified_declared": round(unverified_declared, 2),
            "unverified_taken_days": unverified_taken_days,
            "unverified_taken": round(unverified_taken, 2),
            "floored": floored, "floored_total": round(sum(floored.values()), 2),
            "total": round(sum(cells.values()), 2)}
    return cells, meta


# ── 3+4. journal entries: company designation + fixed scoping ───────────────────────────────────
def _squash(v):
    """UPPER alphanumeric-only spelling key (same folding as coa._squash_key; kept local so this
    module's purity does not depend on coa's import graph)."""
    return "".join(ch for ch in str(v or "").upper() if ch.isalnum())


def _edit1(a, b):
    """True when strings a and b are within ONE edit (insert/delete/substitute). Bounded, pure."""
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if a == b:
        return True
    if la == lb:                                     # one substitution
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la                  # a is shorter; one insertion into a
    i = j = diff = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            diff += 1
            if diff > 1:
                return False
            j += 1
    return True


def journal_company_matcher(companies):
    """PURE: fn(text) -> company_id | None. Resolves a typed company designation (the free-text
    the journal UI let the owner enter) against the org's OWN companies table — config data, never
    a name in code. DETERMINISTIC AND FAIL-CLOSED: every fuzzy tier requires a UNIQUE candidate;
    two companies both within tolerance ⇒ None (the entry stays consolidated-only, honest).

    Chain (first UNIQUE hit wins):
      1. exact name, case-insensitive                     'Nova Wave Communications'
      2. squashed spelling equality                       'nova wave communications'
      3. unique squash-PREFIX either direction (≥4 chars) 'Novawave' → NOVAWAVECOMMUNICATIONS
      4. unique 1-edit tolerance against the same-length
         squash prefix (≥5 chars)                         'Luxelink' → LUXLINKWIRELESS
    Owner's live rows prove 3 and 4 (harness_balance_sheet_truths.py runs them verbatim)."""
    exact, squash = {}, {}
    named = []
    for c in companies or []:
        c = c or {}
        cid, name = c.get("id"), str(c.get("name") or "").strip()
        if not cid or not name:
            continue
        exact[name.lower()] = cid
        sq = _squash(name)
        if sq:
            squash.setdefault(sq, cid)
            named.append((sq, cid))

    def match(text):
        t = str(text or "").strip()
        if not t:
            return None
        cid = exact.get(t.lower())
        if cid:
            return cid
        sq = _squash(t)
        if not sq:
            return None
        cid = squash.get(sq)
        if cid:
            return cid
        if len(sq) >= 4:
            hits = {c for s, c in named if s.startswith(sq) or sq.startswith(s)}
            if len(hits) == 1:
                return next(iter(hits))
            if hits:
                return None                          # ambiguous — never guess between companies
        if len(sq) >= 5:
            hits = set()
            for s, c in named:
                for ln in (len(sq) - 1, len(sq), len(sq) + 1):
                    if 0 < ln <= len(s) and _edit1(sq, s[:ln]):
                        hits.add(c)
                        break
            if len(hits) == 1:
                return next(iter(hits))
        return None

    return match


def entry_company(entry, matcher):
    """PURE: the company an entry designates — the saved company_id when present, else the typed
    text in store_address or memo resolved through the matcher (None ⇒ unattributed)."""
    e = entry or {}
    if e.get("company_id"):
        return e.get("company_id")
    return (matcher(e.get("store_address")) or matcher(e.get("memo"))) if matcher else None


def journal_scope_entries(journal, scope_key, stores_in_scope, matcher=None):
    """PURE: the FIXED engine._journal_for_scope. Consolidated keeps every entry (unchanged).
    A company scope now also receives entries whose TYPED designation (store_address/memo text)
    resolves to that company — the owner's 'marked the company in [notes]' rows. A store scope
    stays an exact store-address match (a company-designated entry is not a store's entry)."""
    if scope_key == "consolidated":
        return list(journal or [])
    if scope_key.startswith("company:"):
        cid = scope_key.split(":", 1)[1]
        ss = stores_in_scope or set()
        return [j for j in (journal or [])
                if j.get("company_id") == cid
                or (j.get("store_address") in ss)
                or (not j.get("company_id") and matcher is not None
                    and entry_company(j, matcher) == cid)]
    if scope_key.startswith("store:"):
        addr = scope_key.split(":", 1)[1]
        return [j for j in (journal or []) if j.get("store_address") == addr]
    return list(journal or [])


# ── 3b. MANUAL-ENTRY GRAINS: cash at bank per store / per company / per tenant (mig 954) ─────────
# Owner directive 2026-09-04, verbatim: "for balance sheet to enter the cash at bank it should be
# an option to enter the cash per store or cash per company or overall total to each tenant as
# everything else is based per store, if there is cash per store then we can get a close to reality
# figure."
#
# The three grains are just the three ways a journal row can be addressed, and the scoping above
# already routes each one correctly. What did NOT exist is the rule that keeps a ROLLUP honest when
# more than one grain is used on the SAME line in the same period: consolidated used to sum every
# entry, so a tenant total keyed alongside per-store rows was counted TWICE.
#
# THE NO-DOUBLE-COUNT RULE (net-of-finer / residual), stated once and applied everywhere:
#
#   A coarser entry is a statement of the TOTAL for its subtree; finer entries inside that subtree
#   refine WHERE that total sits. So a coarser entry books NET of the finer entries nested in it:
#
#       store  s            books in full at s
#       company c residual  = (company entry for c) − Σ store entries at c's stores
#       tenant   residual   = (tenant entries)      − Σ over companies of  stated(c)
#                                                   − Σ store entries with no company
#           where stated(c) = the company entry for c if one exists, else Σ its store entries.
#
#   • ONE grain used (the normal case, and every live row on both orgs today) ⇒ the residual IS the
#     whole entry and every scope is BYTE-IDENTICAL to before this rule existed.
#   • Mixed grains ⇒ the consolidated total is the owner's own coarsest stated total, exactly once,
#     with the finer rows placing the part of it they account for. No dollar is duplicated and no
#     dollar is lost.
#   • A residual that comes out NEGATIVE means the finer rows exceed the coarser stated total — a
#     conflict in the owner's own numbers, never something to book as negative cash. It floors at
#     zero and the suppressed amount is REPORTED in `meta['conflicts']` (the mig-938 floor
#     precedent: suppress the impossible figure, surface it, never drop it silently).
#
# PURE: rows + the org's store/company facts in, the entries each scope may count out.
_TENANT = "tenant"
_COMPANY = "company"
_STORE = "store"


def entry_grain(entry, matcher=None, company_of=None):
    """PURE: (grain, store_or_None, company_or_None) for one manual journal entry.

    A named STORE is the finest grain and wins even when a company is also picked (the store's own
    company is then carried alongside). A store field that is really a COMPANY DESIGNATION — the
    free text the pre-picker journal UI accepted, e.g. the owner's live 'Luxelink'/'Novawave' rows —
    resolves through the SAME `journal_company_matcher` the scoping uses, so those rows are company
    grain here exactly as they are there. Neither ⇒ tenant grain (a single total for the org)."""
    e = entry or {}
    cid = e.get("company_id") or None
    addr = str(e.get("store_address") or "").strip()
    if addr:
        designated = matcher(addr) if matcher else None
        if designated:
            return (_COMPANY, None, cid or designated)
        return (_STORE, addr, cid or (company_of(addr) if company_of else None))
    if cid:
        return (_COMPANY, None, cid)
    designated = matcher(e.get("memo")) if matcher else None
    if designated:
        return (_COMPANY, None, designated)
    return (_TENANT, None, None)


def _line_key(entry):
    e = entry or {}
    return (str(e.get("statement") or "").strip(),
            str(e.get("account_type") or "").strip(),
            str(e.get("account_line") or "").strip().lower())


def journal_grain_entries(journal, scope_key, stores_in_scope, matcher=None, company_of=None):
    """PURE: the entries ONE scope may count, with the no-double-count rule above applied.

    Supersedes nothing — it WRAPS `journal_scope_entries` (which stays the routing truth: which
    scope an entry belongs to at all) and then nets the coarser grains. Returns
    (entries, meta {grains, residuals, conflicts}); `entries` is a list of the same dicts
    `engine._assemble` folds, with a coarser entry replaced by a single synthetic RESIDUAL row
    carrying the same statement/type/line and the netted amount."""
    rows = list(journal or [])
    meta = {"grains": {_STORE: 0, _COMPANY: 0, _TENANT: 0}, "residuals": [], "conflicts": []}
    if not rows:
        return [], meta

    # classify once; the classification is scope-independent
    graded = []
    for e in rows:
        g, st, co = entry_grain(e, matcher, company_of)
        graded.append((e, g, st, co))
        meta["grains"][g] += 1

    # per-line sums at each grain
    store_sum, comp_sum, tenant_sum = {}, {}, {}
    store_company = {}
    for e, g, st, co in graded:
        lk = _line_key(e)
        amt = safe_float(e.get("amount"))
        if g == _STORE:
            store_sum.setdefault(lk, {})[st] = round(store_sum.setdefault(lk, {}).get(st, 0.0) + amt, 2)
            store_company[st] = co
        elif g == _COMPANY:
            comp_sum.setdefault(lk, {})[co] = round(comp_sum.setdefault(lk, {}).get(co, 0.0) + amt, 2)
        else:
            tenant_sum[lk] = round(tenant_sum.get(lk, 0.0) + amt, 2)

    def _residual(lk, stated, subtract, what):
        net = round(stated - subtract, 2)
        if net < 0:
            meta["conflicts"].append({"line": lk[2], "grain": what, "stated": stated,
                                      "finer": subtract, "suppressed": round(-net, 2)})
            return 0.0
        return net

    # the entries this scope routes to (unchanged truth), then net the coarse ones
    routed = journal_scope_entries(rows, scope_key, stores_in_scope, matcher)
    routed_ids = {id(e) for e in routed}
    out = []
    seen_company_lines, seen_tenant_lines = set(), set()
    for e, g, st, co in graded:
        if id(e) not in routed_ids:
            continue
        lk = _line_key(e)
        if g == _STORE:
            out.append(e)                                  # finest grain — always in full
            continue
        if g == _COMPANY:
            if (lk, co) in seen_company_lines:
                continue                                   # one residual per (line, company)
            seen_company_lines.add((lk, co))
            finer = sum(v for s, v in (store_sum.get(lk) or {}).items()
                        if store_company.get(s) == co)
            net = _residual(lk, (comp_sum.get(lk) or {}).get(co, 0.0), round(finer, 2), _COMPANY)
            if net:
                out.append({**e, "amount": net})
                meta["residuals"].append({"line": lk[2], "grain": _COMPANY, "company_id": co,
                                          "amount": net})
            continue
        # tenant grain — consolidated only (journal_scope_entries already enforces that)
        if lk in seen_tenant_lines:
            continue
        seen_tenant_lines.add(lk)
        companies_on_line = set((comp_sum.get(lk) or {}).keys()) | {
            store_company.get(s) for s in (store_sum.get(lk) or {}) if store_company.get(s)}
        subtract = 0.0
        for c in companies_on_line:
            stated_c = (comp_sum.get(lk) or {}).get(c, 0.0)
            if not stated_c:
                stated_c = sum(v for s, v in (store_sum.get(lk) or {}).items()
                               if store_company.get(s) == c)
            subtract += stated_c
        subtract += sum(v for s, v in (store_sum.get(lk) or {}).items()
                        if not store_company.get(s))
        net = _residual(lk, tenant_sum.get(lk, 0.0), round(subtract, 2), _TENANT)
        if net:
            out.append({**e, "amount": net})
            meta["residuals"].append({"line": lk[2], "grain": _TENANT, "amount": net})
    return out, meta
