"""On-demand financial-statement engine — the PLATFORM-WIDE statement service (owner directive
2026-09-02: "create an on demand financial statement whenever required platform wide not as an
added feature").

WHAT THIS IS
------------
ONE service that can produce a P&L, Balance Sheet and Cash Flow for ANY org and ANY period at ANY
moment, from the SAME deterministic chart-of-accounts inputs the stored snapshots use
(`coa.build_inputs`) — plus the balance-sheet truths of `account/balance_sheet.py` (unsold-phone
inventory basis, handset payables per the vendor's due dates, fixed journal company scoping).
Callers:
  • `statement(client, org_id, period, ...)`      — FRESH statements, no persistence (the
    on-demand service other modules / the notify report registry call);
  • `compute_and_store(client, org_id, period)`   — THE persisting compute path. Supersedes
    `engine.compute_and_store` for the /account/compute endpoint and the /account/run-due sweep
    (both re-pointed here); engine.py's own function remains untouched for compatibility.

WHAT IT FIXES OVER engine.compute_and_store (each measured on the live LuxeLink org — see
balance_sheet.py's docstring for the row-level evidence)
  1. journal entries are read with BOTH period spellings (`_period.period_keys`) — the exact-match
     read could silently drop a month of manual entries on a spelling mismatch;
  2. journal scoping honours a TYPED company designation (`balance_sheet.journal_scope_entries`):
     the owner's $250k/$100k owner-contribution and $210k loan rows, entered with the company name
     in the free-text store field, now land on the Luxlink Wireless / Nova Wave company statements
     instead of silently applying to Consolidated only;
  3. the Balance Sheet gains the `handset_payable` liability (due-dated device money owed to the
     distributor, config-driven per org) and the config-driven unsold-phone inventory basis;
  4. a derived CASH FLOW statement (indirect method over the period's BS deltas) is produced and
     stored alongside (statement_type 'cash_flow') — additive rows, nothing existing changes.

DEFAULTS ARE BYTE-IDENTICAL: with no mig-933 config row the extra BS line books nothing (auto_opt
⇒ it does not even render), the inventory basis is 'report' (today's behaviour), and the journal
fixes only ADD entries to scopes that previously missed them (consolidated is unchanged — it
always carried every entry). Proof: backend/harness_statement_engine.py (stdlib, no DB).

The NUMBERS remain deterministic (`coa.build_inputs` + pure functions); the optional Claude
narrative stays commentary via `engine._narrate` (worker-thread rules in engine.py apply — every
route reaches this module through run_in_threadpool).
"""
import calendar
from datetime import datetime, timezone

from app.modules.commcalc.calculator import safe_float
from app.modules.account import coa, balance_sheet, _period
from app.modules.account import device_cogs
from app.modules.account import analysis as _analysis   # 2026-10-06 — THE earned crosscheck verdict (index §19.49)
# NOTE: `engine` (the assembler/narrator/persistor this module reuses) is imported LAZILY inside
# the functions that need it — engine.py pulls app.core.config at import time, and keeping it off
# this module's import path is what lets the stdlib proof harness exercise the pure parts
# (cash_flow, bs_spec, period_as_of, the assembly plumbing) with no app environment.

ORG_ID = coa.ORG_ID

PL_SECTIONS = [("Revenue", "revenue"), ("Cost of Goods Sold", "cogs"),
               ("Operating Expenses", "opex"), ("Other", "other")]
BS_SECTIONS = [("Assets", "asset"), ("Liabilities", "liability"), ("Equity", "equity")]

# Cash-flow classification of the BS SPEC lines (a journal-appended line — a Loan, a note — is
# classified by SECTION + kind below: manual liability/equity lines are FINANCING, the standard
# working-capital lines are OPERATING). CASH lines are the RESULT, never an adjustment:
# `cash` (the manual bank line) and `store_cash_on_hand` (verified undeposited store cash, mig
# 938) together are "cash & cash equivalents" — the statement's begin/end balances sum them.
CF_INVESTING_ASSETS = {"fixtures"}
CF_CASH_KEYS = ("cash", "store_cash_on_hand")
CF_EXCLUDED = set(CF_CASH_KEYS) | {"retained"}


def _round(x):
    return round(safe_float(x), 2)


def bs_spec():
    """The full Balance-Sheet spec: coa.BS_SPEC + this module's additive lines. A list copy —
    never mutates coa's spec."""
    return list(coa.BS_SPEC) + list(balance_sheet.EXTRA_BS_SPEC)


def bs_label():
    return {k: lbl for k, lbl, *_ in bs_spec()}


def period_as_of(period, today=None):
    """Point-in-time date for a period's Balance Sheet: the last day of the period month, capped
    at 'today' for the open month ('YYYY-MM-DD'), or None for an unparseable period."""
    pm, py = coa.parse_period(period)
    if not pm or not py:
        return None
    last = f"{py:04d}-{pm:02d}-{calendar.monthrange(py, pm)[1]:02d}"
    t = (today or datetime.now(timezone.utc).date().isoformat())[:10]
    return min(last, t)


def _fetch_outstanding_tx(client, org_id, as_of):
    """raw_ma_daily_tx rows still inside their due-date window at `as_of` (server-side filtered:
    due_date > as_of), org-scoped, paginated. Only the guarded money column is selected."""
    cols = "account_id,order_type,tx_date,due_date," + ",".join(
        balance_sheet.HANDSET_PAYABLE_MONEY_COLUMNS)
    out, start, page = [], 0, 1000
    while start < 200000:
        chunk = (client.schema("commcalc").table("raw_ma_daily_tx").select(cols)
                 .eq("org_id", org_id).gt("due_date", as_of)
                 .range(start, start + page - 1).execute().data) or []
        out.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return out


def _fetch_asset_ledger_open(client, org_id):
    """`asset_ledger` rows for the org, paginated, ONLY the columns the open-balance derivation
    reads (the money column is named in balance_sheet.ASSET_LEDGER_MONEY_COLUMNS so it can never be
    silently swapped). The status filter is deliberately applied in the PURE function, not here —
    the vocabulary is per-org config and the meta must be able to report what it saw."""
    cols = "store,status,acquired_date,due_date," + ",".join(
        balance_sheet.ASSET_LEDGER_MONEY_COLUMNS)
    out, start, page = [], 0, 1000
    while start < 500000:
        chunk = (client.schema("commcalc").table("asset_ledger").select(cols)
                 .eq("org_id", org_id).range(start, start + page - 1).execute().data) or []
        out.extend(chunk)
        if len(chunk) < page:
            break
        start += page
    return out


class _LedgerInventoryEmpty(Exception):
    """Internal control flow: the asset landing produced no units as of the statement date, so the
    configured inventory basis stands and the reason is already recorded in meta."""


def _fetch_asset_ledger_unsold(client, org_id):
    """The columns the ASSET half needs (mig 1041). Same table, same org scope, same paginated
    reader as the payable half above — one feed, two readings, no second fetch path."""
    return coa._fetch_all(client, "asset_ledger",
                          "esn_imei,store,category,owed_to_vip,acquired_date,date_sold",
                          {"org_id": org_id})


def _earliest_taxed_sale_date(client, org_id):
    """The org's earliest sale line that CARRIED tax ('YYYY-MM-DD'), or ''. Two cheap indexed reads
    (one per side of the sales union), org-scoped, one row each — never a scan.

    This is the accrual start when the tenant has not declared one. It is a DISCOVERY, not a policy
    guess: a collected-not-remitted balance has to start somewhere, and the first day the tenant
    ever collected tax is the only defensible answer that invents nothing. A tenant that has been
    remitting since before this system holds its data sets `account_config.sales_tax_accrual_start`
    instead, and `meta['accrual_start_source']` always says which of the two was used.
    Degrades to '' (⇒ nothing books, with a reason in meta) on any read failure."""
    best = ""
    for table in ("raw_sales", "daily_sales_feed"):
        try:
            rows = (client.schema("commcalc").table(table).select("trans_date")
                    .eq("org_id", org_id).gt("tax", 0).not_.is_("trans_date", "null")
                    .order("trans_date").limit(1).execute().data) or []
        except Exception:
            continue
        d = str((rows[0] if rows else {}).get("trans_date") or "")[:10]
        if d and (not best or d < best):
            best = d
    return best


def carrier_finance_preset(client, org_id, key):
    """THE ONE carrier-preset read for finance bases, keyed by `key` ('distributor_payable' for mig
    954, 'device_cost' for mig 1041). One home: a second preset store or a second carrier normalizer
    is the duplicate defect the index rules forbid, so every finance basis that wants a carrier-level
    default comes through here.

    The basis this org's CARRIER declares, or ''. REUSES the mig-945/953
    carrier-preset machinery end to end (duplicate-check gate — no second preset store, no second
    carrier normalizer): house-org rows in `commcalc.ui_label_override` under
    scope 'finance_basis:<carrier code>', key `key`, with the carrier code coming
    from `report_labels.normalize_carrier_code` over the org's own `commcalc.carrier` rows (mig 038,
    written by the onboarding "Carrier Selection" step). Lazy auto-assign: a NEW tenant that picks
    its carrier at setup resolves correctly the first time a statement is built, with no setup hook.
    Degrades to '' (⇒ 'off') on any read failure — never raises, never guesses."""
    try:
        from app.modules.commcalc import report_labels as _rl
        rows = (client.schema("commcalc").table("carrier").select("code,name")
                .eq("org_id", org_id).limit(50).execute().data) or []
        codes = _rl.carrier_codes(rows)
        if not codes:
            return ""
        scopes = [f"finance_basis:{c}" for c in codes]
        presets = (client.schema("commcalc").table("ui_label_override")
                   .select("scope,key,label").eq("org_id", _rl.HOUSE_ORG)
                   .in_("scope", scopes).eq("key", key)
                   .limit(50).execute().data) or []
        by_scope = {str(r.get("scope") or ""): str(r.get("label") or "").strip()
                    for r in presets}
        for c in codes:                       # the org's own carrier order decides ties
            v = by_scope.get(f"finance_basis:{c}")
            if v:
                return v
    except Exception:
        return ""
    return ""


def carrier_payable_preset(client, org_id):
    """The mig-954 distributor-payable preset. A thin dereference of `carrier_finance_preset` —
    kept so the existing callers read unchanged, NOT a second mechanism."""
    return carrier_finance_preset(client, org_id, "distributor_payable")


def build_inputs_full(client, org_id, period):
    """coa.build_inputs + the balance-sheet truths. Returns (inputs, bs_cfg, meta). Every
    augmentation degrades gracefully (a failed read leaves the base inputs untouched)."""
    inputs = coa.build_inputs(client, org_id, period)
    for k, *_ in balance_sheet.EXTRA_BS_SPEC:
        inputs.setdefault(k, {"by_store": {}, "company_wide": 0.0, "detail": {}})
    cfg = balance_sheet.load_bs_config(client, org_id)
    meta = {"as_of": period_as_of(period), "inventory_basis": cfg["inventory_basis"]}

    # ── DISTRIBUTOR PAYABLE (owner directive 2026-09-04, mig 954) ───────────────────────────────
    # ONE question — "what do we still owe the distributor for devices AS OF this date" — answered
    # by ONE derivation per tenant, chosen by the resolved basis, booked to the tenant's own target
    # line. `meta['as_of']` is the single as-of: `period_as_of` = the period's last day capped at
    # today, so the CURRENT period asks the derivation for today and a CLOSED period asks the SAME
    # function for that period's end. There is no second formula anywhere (the liabilities-due tile
    # calls these same pure functions at as_of = today).
    payable_basis = balance_sheet.resolve_payable_basis(
        cfg["distributor_payable_basis"], carrier_payable_preset(client, org_id),
        bool(cfg["handset_payable_order_types"]))
    payable_line = balance_sheet.resolve_payable_line(
        payable_basis, cfg["distributor_payable_line"], [k for k, *_ in bs_spec()])
    meta["distributor_payable"] = {"basis": payable_basis, "line": payable_line,
                                   "as_of": meta["as_of"]}

    def _book(line_key, store, amt, detail):
        line = inputs.setdefault(line_key, {"by_store": {}, "company_wide": 0.0, "detail": {}})
        if store:
            line["by_store"][store] = _round(line["by_store"].get(store, 0.0) + amt)
        else:
            line["company_wide"] = _round(line["company_wide"] + amt)
        if detail:
            line["detail"][detail] = _round(line["detail"].get(detail, 0.0) + amt)

    if payable_basis == "marketplace_due" and payable_line and meta["as_of"]:
        try:
            tx = _fetch_outstanding_tx(client, org_id, meta["as_of"])
            bookings, hp_meta = balance_sheet.handset_payable_bookings(
                tx, cfg["handset_payable_order_types"], meta["as_of"])
            # store grain: the mig-314 account→store index, when the org opted in — the SAME
            # attribution the P&L uses, so BS and P&L can never place one account differently.
            acct_store = {}
            try:
                from app.modules.account import ma_store_pnl as _msp
                if _msp.load_config(client, org_id).get("store_attribution"):
                    acct_store = _msp.load_store_index(client, org_id) or {}
            except Exception:
                acct_store = {}
            resolve = coa.store_resolver(client, org_id)
            for acct, amt, detail in bookings:
                st = acct_store.get(acct)
                st = resolve(st) if st else None
                _book(payable_line, st, amt, detail)
            meta["handset_payable"] = {**hp_meta, "as_of": meta["as_of"]}
            meta["distributor_payable"]["total"] = hp_meta.get("total")
        except Exception as e:
            coa._warn("handset payable booking failed — line left empty", e)

    elif payable_basis == "asset_ledger" and payable_line and meta["as_of"]:
        # The Boost-side answer: the asset ledger's OPEN balance — the same predicate the asset
        # dashboard's "Open Balance Owed" card sums, now as-of parameterized. Booked ALONGSIDE the
        # coa `owed_vip` components, never on top of them: coa books that line only from ledger rows
        # whose STATUS reads 'on inventory' and from PENDING PayGo batches, and the open-status
        # vocabulary here excludes both — disjoint sources, pinned in harness_balance_sheet_truths.
        try:
            led = _fetch_asset_ledger_open(client, org_id)
            bookings, al_meta = balance_sheet.asset_ledger_open_bookings(
                led, cfg["asset_ledger_open_statuses"], meta["as_of"])
            today = datetime.now(timezone.utc).date().isoformat()
            al_meta["snapshot_lag"] = bool(meta["as_of"] < today)
            resolve = coa.store_resolver(client, org_id)
            for store, amt, detail in bookings:
                _book(payable_line, resolve(store) if store else None, amt, detail)
            meta["asset_ledger_open"] = {**al_meta, "as_of": meta["as_of"]}
            meta["distributor_payable"]["total"] = al_meta.get("total")
        except Exception as e:
            coa._warn("asset-ledger open balance booking failed — line left empty", e)

    # ── verified store cash on hand (config-driven; 'off' default books nothing — mig 938) ──────
    # Owner 2026-09-02: "all cash collected in the store must be added to the balance sheet as
    # cash collected after it has been verified by the DM, either the cash is deposited in the
    # bank or it is used in expenses." The MOVEMENT dicts come from the closing module's OWN
    # shared computation (`_cash_position_core` — the same function Cash Position / Store Cash on
    # Hand / pickups read, declared cash already DM-overlay-corrected, outflows = pickups +
    # deposits + approved envelope expenses/withdrawals), so the Balance Sheet can never disagree
    # with those pages; the 'verified' basis then keeps ONLY DM-verified store-days as collected
    # AND as relieved — inflows and outflows follow the SAME verification rule (symmetry fix,
    # 2026-09-02: all-day outflows against verified-only inflows booked the live August line at
    # −$36,660.91), and every store floors at zero (a cash asset never books negative; the
    # suppressed imbalance and unverified dollars are reported in meta, never silently dropped).
    # Store grain resolves through the SAME coa.store_resolver every other
    # line uses. Lazy import: account must not import the closing router at module load.
    if cfg["cash_on_hand_basis"] != "off" and meta["as_of"]:
        try:
            from app.modules.closing.router import _cash_position_core
            (_codes, decl_sd, taken_sd, _lp, _ld, _sm, _pu, _eep) = _cash_position_core(
                client, org_id, meta["as_of"], [], [], None)
            vkeys = set()
            if cfg["cash_on_hand_basis"] == "verified":
                vrows = (client.schema("commcalc").table("daily_closing_verification")
                         .select("store_code,close_date,verified").eq("org_id", org_id)
                         .eq("verified", True).limit(100000).execute().data) or []
                vkeys = {(v.get("store_code"), str(v.get("close_date"))[:10])
                         for v in vrows if v.get("verified")}
            cells, cmeta = balance_sheet.store_cash_cells(
                decl_sd, taken_sd, vkeys, cfg["cash_on_hand_basis"], meta["as_of"])
            resolve = coa.store_resolver(client, org_id)
            line = inputs["store_cash_on_hand"]
            for st, amt in cells.items():
                key = resolve(st) or st
                line["by_store"][key] = _round(line["by_store"].get(key, 0.0) + amt)
            meta["store_cash_on_hand"] = cmeta
        except Exception as e:
            coa._warn("store cash-on-hand booking failed — line left empty", e)

    # ── sales tax collected, not yet remitted (config-driven; 'off' default books nothing) ──────
    # Owner directive 2026-09-08: "now the sales tax in p&l". Sales tax reached NO statement line at
    # all — `coa._sales_union_rows` never selected the `tax` column and no spec carried a tax line —
    # so $41,239.46 of the state's money was invisible on the live house org.
    #
    # IT DOES NOT TOUCH THE P&L. Revenue here is already pre-tax (re-verified against all 24,890
    # August raw_sales rows: device_rev and service_income tie to the cent), so this books ONLY to
    # the balance sheet. Revenue, gross profit and net income cannot move — nothing below writes to
    # a P&L line key.
    #
    # ONE AGGREGATION, NOT A SECOND DERIVATION (duplicate-check gate): the dollars come from
    # `commcalc.tax_collected.aggregate` — the very function the Tax Collected page renders — read
    # through that page's OWN reader (`_sales_rows_union_txn_range`, raw_sales ∪ daily_sales_feed
    # deduped by trans_id) and keyed on the SAME `coa.store_resolver` address the P&L books under.
    # Nothing about sales tax is computed twice anywhere in the system.
    #
    # CUMULATIVE, because a balance sheet is a point in time: the balance is everything collected
    # from the accrual start through as_of, less what the tenant has remitted. The remittance side
    # is the EXISTING journal ledger — `engine._assemble` folds a manual balance_sheet/liability
    # entry onto this line by label — so it is not netted here as well (that would relieve twice).
    if cfg["sales_tax_basis"] != "off" and meta["as_of"]:
        stm = {"basis": cfg["sales_tax_basis"], "as_of": meta["as_of"]}
        try:
            from app.modules.commcalc import tax_collected as _tc
            start = cfg["sales_tax_accrual_start"] or ""
            stm["accrual_start_source"] = "config" if start else "earliest_taxed_sale"
            if not start:
                start = _earliest_taxed_sale_date(client, org_id)
            stm["accrual_start"] = start
            if not start:
                # FAIL CLOSED, WITH THE REASON. No start date and no taxed sale to discover one
                # from ⇒ book nothing rather than claim a balance from an unknown date.
                stm["reason"] = ("no accrual start: the sales-tax accrual start date is not set in account settings "
                                 "and no sale line carrying tax was found for this org")
                meta["sales_tax_payable"] = stm
            else:
                from app.modules.commcalc.router import _sales_rows_union_txn_range
                rows, rmeta = _sales_rows_union_txn_range(
                    client, org_id, period, start, meta["as_of"], cols=_tc.SALES_COLUMNS)
                agg = _tc.aggregate(rows, resolve_store=coa.store_resolver(client, org_id),
                                    start=start, end=meta["as_of"])
                bookings, smeta = balance_sheet.sales_tax_payable_bookings(
                    _tc.store_tax_rows(agg), cfg["sales_tax_basis"], meta["as_of"])
                for store, amt, detail in bookings:
                    _book("sales_tax_payable", store, amt, detail)
                stm.update(smeta)
                stm["periods_read"] = rmeta.get("periods_read")
                # The union caps a range at 24 months. A capped read UNDERSTATES the balance, so it
                # is reported rather than quietly returned as the answer.
                stm["truncated"] = bool(rmeta.get("truncated"))
                stm["store_renames"] = agg["meta"]["renamed"]
                stm["suspect_renames"] = agg["meta"]["suspect_renames"]
                stm["returns_tax_positive"] = agg["meta"]["returns_tax_positive"]
                meta["sales_tax_payable"] = stm
        except Exception as e:
            coa._warn("sales tax payable booking failed — line left empty", e)

    # ── inventory basis (config-driven; 'report' default = byte-identical) ──────────────────────
    # mig 1041: the DEVICE-COST BASIS decides this when it is the accrual basis. Same resolution,
    # same read — `statement_engine` does not have its own opinion about the device basis and does
    # not read `device_cogs_mode`. `ledger_inventory` is True only on 'asset_ledger', and it then
    # OVERRIDES `inventory_basis`, because accrual COGS relieving an inventory measured by a
    # different feed at a different cost basis is the §42.3 gap, not a configuration choice.
    try:
        dev_basis = device_cogs.load_basis(client, org_id)
    except Exception as e:
        coa._warn("device-cost basis unreadable — inventory basis left as configured", e)
        dev_basis = {"basis": "undeclared", "ledger_inventory": False, "accrual": False,
                     "suppress_cash_cogs": False, "source": "unreadable", "double_book_risk": False}
    meta["device_cost_basis"] = dev_basis
    if dev_basis.get("ledger_inventory") and meta["as_of"]:
        try:
            led = _fetch_asset_ledger_unsold(client, org_id)
            cells, amet = balance_sheet.asset_ledger_unsold_cells(led, meta["as_of"])
            resolve = coa.store_resolver(client, org_id)
            inv_rows = coa._fetch_all(client, "inventory_value",
                                      "store,swept_value,manual_value", {"org_id": org_id})
            eff = balance_sheet.apply_inventory_basis(inv_rows, cells, "asset_ledger", resolve)
            # A ledger that produced NO units is a feed problem, not a $0 inventory. Zeroing an asset
            # line because an upload is late would be exactly the "fix that hides the defect" CLAUDE.md
            # forbids, so the configured basis is kept and the fallback is REPORTED.
            if not amet.get("units"):
                amet["fell_back_to_configured_basis"] = True
                amet["reason"] = ("the asset landing produced no units as of this date — the "
                                  "configured inventory basis is kept rather than booking a $0 asset "
                                  "off a feed that has not delivered")
                meta["inventory_asset_ledger"] = amet
                raise _LedgerInventoryEmpty()
            inputs["inventory"]["by_store"] = {st: _round(v["value"]) for st, v in eff.items()}
            inputs["inventory"]["label"] = "Inventory — unsold devices (asset landing)"
            if amet.get("snapshot_basis"):
                inputs["inventory"]["note"] = (
                    "The asset landing ledger is a wipe-and-reinsert CURRENT snapshot and has been "
                    "pruned, so this as-of value is a snapshot-basis ESTIMATE of what was on hand on "
                    "%s — it measures the rows that survived pruning, not every unit invoiced."
                    % amet["as_of"])
            meta["inventory_asset_ledger"] = amet
            meta["inventory_basis"] = "asset_ledger"
            meta["inventory_basis_forced_by"] = "device_cost_basis=asset_ledger"
        except _LedgerInventoryEmpty:
            pass                          # already recorded in meta; the configured basis stands
        except Exception as e:
            coa._warn("asset-ledger inventory unavailable — configured basis kept", e)
    elif cfg["inventory_basis"] == "devices":
        try:
            dev_rows = coa._fetch_all(client, "inventory_aging_device",
                                      "store,unit_cost,on_hand,as_of_date", {"org_id": org_id})
            inv_rows = coa._fetch_all(client, "inventory_value",
                                      "store,swept_value,manual_value", {"org_id": org_id})
            cells, dmeta = balance_sheet.device_inventory_cells(dev_rows)
            resolve = coa.store_resolver(client, org_id)
            eff = balance_sheet.apply_inventory_basis(inv_rows, cells, "devices", resolve)
            if eff:
                inputs["inventory"]["by_store"] = {st: _round(v["value"]) for st, v in eff.items()}
                inputs["inventory"]["label"] = "Inventory — unsold phones (device ledger)"
            meta["inventory_devices"] = dmeta
        except Exception as e:
            coa._warn("device-basis inventory unavailable — report basis kept", e)

    return inputs, cfg, meta


# ── cash flow (derived, indirect method over BS deltas) ─────────────────────────────────────────
def _bs_line_index(bs_payload):
    """{(section_type, key): (label, amount, kind)} over an assembled BS payload."""
    out = {}
    for sec in (bs_payload or {}).get("sections", []):
        for ln in sec.get("lines", []):
            out[(sec.get("type"), ln.get("key"))] = (ln.get("label"), safe_float(ln.get("amount")),
                                                     ln.get("kind"))
    return out


def cash_flow(pl_cur, bs_cur, bs_prior, period, scope_key, scope_label):
    """PURE: a derived Cash Flow statement (indirect method) from this period's P&L + BS and the
    prior period's BS. Cash-basis books make this a DERIVED view, honestly labelled:

      Operating  = net income − Δ(operating assets: inventory, receivables, clearing, any manual
                   asset line except cash/fixtures) + Δ(operating liabilities: the spec payables)
      Investing  = −Δ fixtures
      Financing  = Δ owner capital + Δ manual equity lines + Δ manual/journal LIABILITY lines
                   (a Loan entered in the journal is financing, not working capital)

    `implied_cash_change` = the three subtotals summed. Because the Cash line itself is MANUAL,
    the statement reports the tie-out (`cash_delta_reported`, `tie_delta`) instead of pretending:
    when the owner keys cash each month the two agree and `tied` is true. No prior BS ⇒ the first
    computed month: deltas are the full current balances, flagged `comparative: false`."""
    cur = _bs_line_index(bs_cur)
    pri = _bs_line_index(bs_prior) if bs_prior else {}
    comparative = bs_prior is not None
    spec_keys = {k for k, *_ in coa.BS_SPEC} | {k for k, *_ in balance_sheet.EXTRA_BS_SPEC}

    def delta(sk):
        _lbl, cur_amt, _kind = cur.get(sk, (None, 0.0, None))
        _plbl, pri_amt, _pkind = pri.get(sk, (None, 0.0, None))
        return _round(cur_amt - pri_amt)

    ni = safe_float((pl_cur or {}).get("net_income"))
    op_lines, inv_lines, fin_lines = [], [], []
    for sk in sorted(set(cur) | set(pri), key=lambda x: (x[0] or "", x[1] or "")):
        sec, key = sk
        if key in CF_EXCLUDED:
            continue
        d = delta(sk)
        if not d:
            continue
        label = (cur.get(sk) or pri.get(sk))[0] or key
        kind = (cur.get(sk) or pri.get(sk))[2]
        is_spec = key in spec_keys
        if sec == "asset":
            if key in CF_INVESTING_ASSETS:
                inv_lines.append({"key": key, "label": f"Purchases of {label}", "amount": _round(-d)})
            else:
                op_lines.append({"key": key, "label": f"Change in {label}", "amount": _round(-d)})
        elif sec == "liability":
            if is_spec:
                op_lines.append({"key": key, "label": f"Change in {label}", "amount": d})
            else:                                    # journal-added liability (Loan / note) → financing
                fin_lines.append({"key": key, "label": f"Proceeds / repayment — {label}", "amount": d})
        elif sec == "equity":
            fin_lines.append({"key": key, "label": f"Change in {label}", "amount": d})
        _ = kind
    op_lines.insert(0, {"key": "net_income", "label": "Net income", "amount": _round(ni)})

    sections = [
        {"name": "Operating activities", "type": "operating", "lines": op_lines,
         "subtotal": _round(sum(l["amount"] for l in op_lines))},
        {"name": "Investing activities", "type": "investing", "lines": inv_lines,
         "subtotal": _round(sum(l["amount"] for l in inv_lines))},
        {"name": "Financing activities", "type": "financing", "lines": fin_lines,
         "subtotal": _round(sum(l["amount"] for l in fin_lines))},
    ]
    implied = _round(sum(s["subtotal"] for s in sections))
    # Cash & cash equivalents = the manual bank line + verified undeposited store cash (mig 938;
    # a missing key sums as 0, so a pre-938 payload is byte-identical).
    cash_cur = sum(cur.get(("asset", k), (None, 0.0, None))[1] for k in CF_CASH_KEYS)
    cash_pri = sum(pri.get(("asset", k), (None, 0.0, None))[1] for k in CF_CASH_KEYS)
    reported = _round(cash_cur - cash_pri)
    out = {"statement_type": "cash_flow", "period": period, "scope_key": scope_key,
           "scope_label": scope_label, "comparative": comparative, "sections": sections,
           "net_income": _round(ni), "implied_cash_change": implied,
           "cash_begin": _round(cash_pri), "cash_end": _round(cash_cur),
           "cash_delta_reported": reported, "tie_delta": _round(implied - reported),
           "tied": abs(_round(implied - reported)) < 1.0,
           "notes": ["Derived cash flow (indirect method) over the period's balance-sheet deltas. "
                     "The Cash / bank line is manual — when it is keyed monthly, the implied change "
                     "and the reported change tie out; `tie_delta` shows any gap."]}
    if not comparative:
        out["notes"].append("First computed period for this scope — no prior balance sheet, so "
                            "changes equal the full current balances.")
    return out


def _prior_period_label(period):
    pm, py = coa.parse_period(period)
    if not pm or not py:
        return None
    pm, py = (12, py - 1) if pm == 1 else (pm - 1, py)
    return f"{_period._MONTHS[pm]} {py}"


def _stored_bs(client, org_id, scope_key, period):
    """The stored balance-sheet snapshot for (scope, period), matching both period spellings.
    None when never computed — the caller treats that as 'no prior baseline'."""
    if not period:
        return None
    try:
        rows = (client.schema("commcalc").table("account_statements").select("payload")
                .eq("org_id", org_id).in_("period", list(_period.period_keys(period)))
                .eq("statement_type", "balance_sheet").eq("scope_key", scope_key)
                .limit(1).execute().data) or []
        return rows[0].get("payload") if rows else None
    except Exception:
        return None


def _journal_rows(client, org_id, period):
    """Manual journal entries for the period — BOTH spellings (the finance-wide month-name /
    numeric duality; the old exact-match read silently dropped a month on a mismatch).

    DEREFERENCES `coa.journal_rows`, the one home, so this and `engine.compute_and_store` can never
    answer the question differently again (index §19.47)."""
    return coa.journal_rows(client, org_id, period)


def _scopes(inputs, companies, company_of, journal=None, matcher=None, profit_scopes=None):
    """Scope list. `all_stores` is every store any LINE places money at — plus (mig 954) every
    store a MANUAL entry is keyed to: a store whose only figure is a hand-entered cash balance had
    no scope at all before, so "cash per store" could be entered and never render its own column.

    `profit_scopes` (mig 1022, index §37.1): the org's PROFIT CENTERS as one more scope family —
    `centers.profit_center_scopes` tuples (`profit_center:<code>`, label, the center's stores, False),
    exactly the shape of a company scope. None / [] (every org without profit centers) ⇒ the list is
    byte-identical."""
    all_stores = set(coa.stores_in_inputs(inputs))   # ONE home for "which stores do the lines name"
    for e in (journal or []):
        grain, store, _co = balance_sheet.entry_grain(e, matcher, company_of)
        if grain == "store" and store:
            all_stores.add(store)
    scopes = [("consolidated", "Consolidated (all companies)", None, True)]
    for c in companies:
        cstores = {s for s in all_stores if company_of(s) == c["id"]}
        scopes.append((f"company:{c['id']}", c["name"], cstores, False))
    for s in sorted(all_stores):
        scopes.append((f"store:{s}", s, {s}, False))
    for sk, label, stores, cw in (profit_scopes or []):
        scopes.append((sk, label, set(stores or ()), bool(cw)))
    return scopes, all_stores


def _profit_scopes(client, org_id):
    """The org's profit-center scope family (centers.load_profit_scopes through the P&L's OWN store
    resolver, so a center holds exactly the store keys the statement books under). [] on any failure."""
    try:
        from app.modules.account import centers as _centers
        return _centers.load_profit_scopes(client, org_id, coa.store_resolver(client, org_id))
    except Exception as e:
        coa._warn("profit-center scopes unavailable — company/store scopes only", e)
        return []


def side_meta(inputs):
    """The side entries coa attaches beside the lines (index §37): what the sales scan passed over
    (`_unbooked_sales`) and the royalty report's booked / excluded / unmapped lines
    (`_royalty_coverage`). Reported in statement META — never on the P&L payload, which stays
    byte-identical."""
    out = {}
    u = inputs.get("_unbooked_sales") or {}
    if u.get("unbooked") or u.get("suppressed") or u.get("mapped"):
        out["unbooked_sales"] = {k: u.get(k) for k in ("unbooked", "unbooked_total",
                                                       "unbooked_gp_total", "suppressed", "mapped")}
    r = inputs.get("_royalty_coverage")
    if r:
        out["royalty"] = {k: r.get(k) for k in ("reports", "book_pl", "booked", "excluded", "unmapped")}
    return out


def _assemble_scope(client, org_id, period, inputs, journal, matcher,
                    scope_key, scope_label, stores_in_scope, include_cw, company_of=None):
    """One scope's (pl, bs, cf) — deterministic assembly via engine._assemble with the extended
    BS spec and the FIXED journal scoping."""
    from app.modules.account import engine
    # mig 954 — the manual-entry GRAIN rule (per store / per company / one tenant total). Routing is
    # unchanged (journal_grain_entries wraps journal_scope_entries); what it adds is netting a
    # coarser grain by the finer entries nested inside it, so a rollup can never count a dollar
    # twice. One grain in use ⇒ byte-identical to the plain routing.
    jscope, jmeta = balance_sheet.journal_grain_entries(
        journal, scope_key, stores_in_scope, matcher, company_of)
    # ONE home for the chart (§STOREOPS-09): `coa.pl_spec_for` adds the lines THIS org declared —
    # a wages line per org level — on top of the shared `PL_SPEC`. Never `coa.PL_SPEC` directly.
    pl = engine._assemble(inputs, jscope, coa.pl_spec_for(inputs), coa.pl_label_for(inputs), PL_SECTIONS,
                          scope_key, stores_in_scope, include_cw)
    prior_ni = engine._prior_accum_ni(client, org_id, scope_key, period)
    bs = engine._assemble(inputs, jscope, bs_spec(), bs_label(), BS_SECTIONS,
                          scope_key, stores_in_scope, include_cw,
                          prior_accum_ni=prior_ni, current_ni=pl["net_income"])
    for stmt in (pl, bs):
        stmt["period"], stmt["scope_key"], stmt["scope_label"] = period, scope_key, scope_label
    prior_bs = _stored_bs(client, org_id, scope_key, _prior_period_label(period))
    cf = cash_flow(pl, bs, prior_bs, period, scope_key, scope_label)
    bs["journal_grains"] = jmeta
    return pl, bs, cf


# ── the on-demand service (fresh numbers, no persistence) ───────────────────────────────────────
def statement(client, org_id, period, scope="consolidated", kinds=("pl", "balance_sheet", "cash_flow")):
    """FRESH statements for ANY org / period / scope, computed now from the live bookings. The
    platform-wide on-demand service: other modules, the notify report registry (scheduled /
    on-demand sends) and the API endpoint call this. Org-scoped end to end; an unknown company
    scope returns computed:false rather than another org's data (fail closed)."""
    inputs, cfg, meta = build_inputs_full(client, org_id, period)
    meta.update(side_meta(inputs))
    journal = _journal_rows(client, org_id, period)
    company_of, _default, companies = coa.company_assignment(client, org_id)
    matcher = balance_sheet.journal_company_matcher(companies)
    scopes, _stores = _scopes(inputs, companies, company_of, journal, matcher,
                              _profit_scopes(client, org_id) if str(scope).startswith("profit_center:") else None)
    match = next((s for s in scopes if s[0] == scope), None)
    if match is None:
        return {"period": period, "scope": scope, "computed": False,
                "scope_display": coa.scope_display_label(scope, None, companies),
                "note": "unknown scope for this org"}
    scope_key, scope_label, stores_in_scope, include_cw = match
    pl, bs, cf = _assemble_scope(client, org_id, period, inputs, journal, matcher,
                                 scope_key, scope_label, stores_in_scope, include_cw, company_of)
    out = {"period": period, "scope": scope_key, "scope_label": scope_label, "computed": True,
           # THE display name through the one home (§13b.1) — `companies` here IS the canonical
           # enumeration (coa.company_assignment ⇒ org_companies), so an on-demand statement titles
           # itself with the entity's current name and never with `company:<uuid>`.
           "scope_display": coa.scope_display_label(scope_key, scope_label, companies),
           "on_demand": True, "computed_at": datetime.now(timezone.utc).isoformat(),
           "meta": meta}
    if "pl" in kinds:
        out["pl"] = pl
    if "balance_sheet" in kinds:
        out["balance_sheet"] = bs
    if "cash_flow" in kinds:
        out["cash_flow"] = cf
    return out


# ── the persisting compute path (supersedes engine.compute_and_store) ───────────────────────────
def compute_and_store(client, org_id, period):
    """Build inputs once; assemble + persist consolidated, per-company and per-store snapshots for
    P&L, Balance Sheet AND Cash Flow. Same tables, same shapes, same purge-then-insert semantics
    as engine.compute_and_store — plus the balance-sheet truths and fixed journal scoping. Claude
    narrates the consolidated statements only (cost-bounded), via engine._narrate."""
    from app.core.config import settings
    from app.modules.account import engine
    inputs, cfg, meta = build_inputs_full(client, org_id, period)
    meta.update(side_meta(inputs))
    journal = _journal_rows(client, org_id, period)
    company_of, _default, companies = coa.company_assignment(client, org_id)
    matcher = balance_sheet.journal_company_matcher(companies)
    scopes, all_stores = _scopes(inputs, companies, company_of, journal, matcher,
                                 _profit_scopes(client, org_id))

    # Purge ALL prior snapshots for the period first (orphan-scope rule, same as engine.py).
    client.schema("commcalc").table("account_statements").delete() \
        .eq("org_id", org_id).in_("period", list(_period.period_keys(period))).execute()

    written = 0
    for scope_key, scope_label, stores_in_scope, include_cw in scopes:
        pl, bs, cf = _assemble_scope(client, org_id, period, inputs, journal, matcher,
                                     scope_key, scope_label, stores_in_scope, include_cw, company_of)
        pl["notes"] = engine._notes(scope_key, include_cw)
        bs["notes"] = engine._notes(scope_key, include_cw) + (
            [] if bs["balanced"] else ["Balance sheet is not yet balanced — enter cash / opening "
                                       "balances via the Journal so Assets = Liabilities + Equity."])
        if scope_key == "consolidated":
            narrative, model = engine._narrate(pl, bs, scope_label, period)
        else:
            narrative, model = "", "deterministic"
        for stmt, st_type in ((pl, "pl"), (bs, "balance_sheet"), (cf, "cash_flow")):
            # The flag is EARNED, never defaulted (index §19.49). This read
            # `stmt.get("balanced", stmt.get("tied", True))`: `balanced` is set only on a balance
            # sheet and `tied` only on a cash flow, so a P&L fell through to the literal True and
            # every stored P&L claimed a crosscheck that had never run. `statement_crosscheck` now
            # computes the P&L's own identities and returns None — not True — for anything it
            # cannot judge, so "never checked" can no longer read as "checked and fine".
            engine._persist(client, org_id, period, st_type, scope_key, scope_label, stmt,
                            narrative if st_type == "pl" else "", model,
                            _analysis.statement_crosscheck(st_type, stmt)["ok"])
            written += 1

    return {"period": period, "snapshots": written, "scopes": len(scopes),
            "companies": len(companies), "stores": len(all_stores),
            "statements": ["pl", "balance_sheet", "cash_flow"],
            "engine": "claude+deterministic" if settings.ANTHROPIC_API_KEY else "deterministic",
            "meta": meta}


# ── inventory reconciliation (the tie-out the reconciliation tab reads) ─────────────────────────
def inventory_reconciliation(client, org_id):
    """Per-store tie-out: emailed-report totals (inventory_value) vs the unsold-phone ledger
    (inventory_aging_device, snapshot-coherent) vs manual overrides vs the effective BS value
    under the org's configured basis. Read-only; org-scoped."""
    cfg = balance_sheet.load_bs_config(client, org_id)
    dev_rows = coa._fetch_all(client, "inventory_aging_device",
                              "store,unit_cost,on_hand,as_of_date", {"org_id": org_id})
    inv_rows = coa._fetch_all(client, "inventory_value",
                              "store,swept_value,manual_value,as_of_date", {"org_id": org_id})
    cells, dmeta = balance_sheet.device_inventory_cells(dev_rows)
    resolve = coa.store_resolver(client, org_id)
    rows, totals = balance_sheet.inventory_recon_rows(inv_rows, cells, cfg["inventory_basis"], resolve)
    return {"org_id": org_id, "basis": cfg["inventory_basis"], "rows": rows, "totals": totals,
            "device_meta": dmeta,
            "note": "report_value = the emailed Inventory Aging per-store totals; device_value = "
                    "the unsold phones on the device ledger at each store's current snapshot; "
                    "delta = device − report. Unplaced/superseded devices are excluded from the "
                    "cells and counted in device_meta — nothing vanishes silently."}


# ── THE PROFIT PRODUCER: "quote the P&L, never derive profit twice" ─────────────────────────────
# Owner report 2026-11-17, verbatim: "The profit is not correct it should be same as in the finance
# dashboard".
#
# THE CLASS OF DEFECT, named once. A report outside finance needed "profit per store" and computed
# its own: the Gross Profit report's `net_profit` (`total_rev − rep_pay − exp_total −
# net_phone_cost`, over store-attributed lines only) is a DIFFERENT QUESTION from net income, and
# the two answers diverged by $366,646.17 on the house org in October 2026 (GP +340,102.98 vs P&L
# net income −26,543.19) and by $30,545.90 on LuxeLink (+31,122.43 vs +576.53). A district manager
# was told the company made $340k in a month the books say it lost $26.5k.
#
# The design fix is not a second formula that agrees better — it is that there is ONE producer of
# "what did the books earn", here, in the finance module, and every other report QUOTES it. So this
# function states no accounting rule of its own: it runs the SAME `build_inputs_full` inputs through
# the SAME `_scopes` scope list and the SAME `engine._assemble` over `coa.pl_spec_for` /
# `coa.pl_label_for` that `_assemble_scope` uses, and hands back what those payloads already carry.
# Its per-scope `net_income` is `statement(..., kinds=("pl",))["pl"]["net_income"]` for that scope,
# to the cent, BY CONSTRUCTION — not by arithmetic that happens to match.
#
# CHEAP ENOUGH FOR ONE CALL PER REPORT OPEN: P&L only. No balance sheet, no cash flow, no
# `_stored_bs` prior-BS read, no `engine._prior_accum_ni` per scope — the whole cost is the single
# `build_inputs_full` (measured live 2026-11-17, October 2026: ~57s house / ~14.5s LuxeLink), and
# assembling every scope's P&L on top of it is ~3ms for 38 scopes.
#
# NOTHING EXISTING CHANGES: additive module surface only. `statement`, `compute_and_store`,
# `/accounts/pl` and the dashboard payloads are untouched.

#: How a P&L SECTION enters net income — the identity `engine._assemble` itself applies
#: (`net_income = revenue − cogs − opex − other`). Stated once so the reconciliation below can
#: explain a company-wide dollar's effect on profit without re-deriving the P&L.
PL_SECTION_SIGN = {"revenue": 1.0, "cogs": -1.0, "opex": -1.0, "other": -1.0}


def pl_figures(pl_payload):
    """PURE: the figures an ASSEMBLED P&L payload ALREADY CARRIES, read — never re-derived.

    Section subtotals + `gross_profit` / `net_operating_income` / `net_income` exactly as
    `engine._assemble` computed them. A figure the payload does not carry comes back None (the
    house rule: a figure nobody measured is None with a reason, never 0)."""
    p = pl_payload or {}
    subs = {}
    for sec in p.get("sections") or []:
        subs[sec.get("type")] = _round(sec.get("subtotal"))
    out = {}
    for sec_type in ("revenue", "cogs", "opex", "other"):
        out[sec_type] = subs.get(sec_type)
    for k in ("gross_profit", "net_operating_income", "net_income"):
        out[k] = _round(p[k]) if k in p else None
    return out


def company_wide_pl_cells(inputs, spec):
    """PURE: the P&L lines holding money at NO store.

    `engine._scoped` gives these dollars ONLY to a scope with `include_company_wide` — i.e.
    consolidated. No `store:` scope can ever carry them, so they are the first honest reason a
    caller summing the store rows does not reach the consolidated figure. Mirrors `_assemble`'s own
    skips (a `manual`/`computed` line is zeroed there, so it contributes nothing here either)."""
    out = []
    for key, label, section, kind, _grain in (spec or []):
        if section not in PL_SECTION_SIGN or kind in ("manual", "computed"):
            continue
        ln = (inputs or {}).get(key)
        if not isinstance(ln, dict):
            continue
        amt = _round(ln.get("company_wide"))
        if not amt:
            continue
        out.append({"key": key, "label": (ln.get("label") or label), "section": section,
                    "amount": amt,
                    "net_income_effect": _round(PL_SECTION_SIGN[section] * amt)})
    return out


def unstored_journal_pl_cells(journal, matcher=None, company_of=None):
    """PURE: the manual P&L journal entries that are NOT keyed to a store.

    `balance_sheet.entry_grain` is the one home for an entry's grain; a company- or tenant-grain
    entry is counted by consolidated (and by its company scope) and by NO store scope — the second
    honest reason store rows may not sum to consolidated."""
    out = []
    for e in (journal or []):
        if (str((e or {}).get("statement") or "").strip()) != "pl":
            continue
        section = str((e or {}).get("account_type") or "").strip()
        grain, store, company = balance_sheet.entry_grain(e, matcher, company_of)
        if grain == "store" and store:
            continue
        amt = _round((e or {}).get("amount"))
        sign = PL_SECTION_SIGN.get(section)
        out.append({"grain": grain, "company_id": company, "section": section or None,
                    "line": str((e or {}).get("account_line") or "Manual entry").strip(),
                    "amount": amt,
                    "net_income_effect": _round(sign * amt) if sign is not None else None,
                    "reason": None if sign is not None
                              else "entry's account_type is not a P&L section — effect not measured"})
    return out


def fold_profit_by_store(period, org_id, consolidated_pl, store_pls, company_pls,
                         company_wide_cells, unstored_journal_cells,
                         identity=None, identity_reason=None, scope_counts=None):
    """PURE: the profit payload — the rows, the consolidated figures, the coverage statement and
    the reconciliation that tells a caller WHY the store rows may not sum to consolidated.

    `store_pls` / `company_pls` are [(scope_key, scope_label, assembled_pl)]. `identity` is
    `store_identity.store_identity_index` (canonical store address → code / market / codes), or
    None with `identity_reason` when it could not be read — the store CODE is then None with a
    reason on every row rather than a guess.

    The reconciliation is stated, never assumed: residual = consolidated − Σ store rows; `explained`
    = the company-wide cells' + unstored journal entries' effect on net income; anything left is
    reported as `unexplained` with `reconciles: false`, so a caller can always tell from the payload
    alone why the arithmetic does not close."""
    ident = identity if isinstance(identity, dict) else {}

    def _ident_fields(store):
        if identity is None:
            return {"store_code": None, "store_codes": [], "market": None, "salesforce_id": None,
                    "identity_ambiguous": None, "is_active": None,
                    "store_code_reason": identity_reason or "store identity was not read"}
        ent = ident.get(store)
        if not ent:
            return {"store_code": None, "store_codes": [], "market": None, "salesforce_id": None,
                    "identity_ambiguous": False, "is_active": None,
                    "store_code_reason": "no store_mapping row claims this canonical store key"}
        return {"store_code": (str(ent.get("store_code") or "").strip() or None),
                "store_codes": list(ent.get("codes") or []),
                "market": (str(ent.get("market") or "").strip() or None),
                "salesforce_id": (str(ent.get("salesforce_id") or "").strip() or None),
                "identity_ambiguous": bool(ent.get("ambiguous")),
                "is_active": ent.get("is_active"),
                "store_code_reason": ("two or more declared codes claim this store key"
                                      if ent.get("ambiguous") else None)}

    rows = []
    for scope_key, scope_label, pl in (store_pls or []):
        store = scope_key.split(":", 1)[1] if ":" in scope_key else scope_key
        row = {"scope": scope_key, "store": store, "store_label": scope_label}
        row.update(pl_figures(pl))
        row.update(_ident_fields(store))
        rows.append(row)
    rows.sort(key=lambda r: str(r.get("store") or ""))

    companies = []
    for scope_key, scope_label, pl in (company_pls or []):
        ent = {"scope": scope_key, "company_id": scope_key.split(":", 1)[1] if ":" in scope_key else None,
               "company": scope_label}
        ent.update(pl_figures(pl))
        companies.append(ent)

    cons = pl_figures(consolidated_pl)
    stores_ni = _round(sum(safe_float(r.get("net_income")) for r in rows))
    residual = _round(safe_float(cons.get("net_income")) - stores_ni)
    cw_effect = _round(sum(safe_float(c.get("net_income_effect")) for c in (company_wide_cells or [])))
    je_effect = _round(sum(safe_float(c.get("net_income_effect")) for c in (unstored_journal_cells or [])))
    explained = _round(cw_effect + je_effect)
    unexplained = _round(residual - explained)
    reconciles = abs(unexplained) < 0.01

    why = []
    if company_wide_cells:
        why.append(f"{len(company_wide_cells)} P&L line(s) hold money the books place at NO store "
                   f"(net income effect {cw_effect:+,.2f}); only the consolidated scope counts them")
    if unstored_journal_cells:
        why.append(f"{len(unstored_journal_cells)} manual journal entr(ies) are keyed above store "
                   f"grain (net income effect {je_effect:+,.2f})")
    if not why:
        why.append("every dollar on the P&L is placed at a store, so the store rows sum to "
                   "consolidated")
    if not reconciles:
        why.append(f"{unexplained:+,.2f} of the difference is NOT explained by either — treat the "
                   f"store rows as incomplete and report this figure")

    unplaced = [r["store"] for r in rows if r.get("store_code") is None]
    return {
        "producer": "account.statement_engine.profit_by_store",
        "org_id": org_id,
        "period": period,
        "basis": ("Net income as the BOOKS state it: the finance module's own P&L for this period "
                  "(account.statement_engine over coa.build_inputs + coa.pl_spec_for), i.e. "
                  "revenue − cost of goods sold − operating expenses − other, per scope. Identical "
                  "by construction to statement(client, org, period, scope, kinds=('pl',))"
                  "['pl']['net_income'] for the same scope. It is NOT the Gross Profit report's "
                  "`net_profit` (total_rev − rep_pay − exp_total − net_phone_cost over "
                  "store-attributed lines only), which answers a different question."),
        "consolidated": dict(cons, scope="consolidated"),
        "stores": rows,
        "companies": companies,
        "coverage": {
            "store_scopes": len(rows),
            "company_scopes": len(companies),
            "scopes_total": (scope_counts or {}).get("scopes_total"),
            "stores_with_no_store_code": unplaced,
            "stores_with_ambiguous_identity": [r["store"] for r in rows if r.get("identity_ambiguous")],
            "store_identity_read": identity is not None,
            "store_identity_reason": identity_reason,
            "note": ("One row per store scope the P&L books money at — the same scope list the "
                     "stored snapshots use (statement_engine._scopes). A store with no money in "
                     "this period has no scope and therefore no row; it is not a zero."),
        },
        "reconciliation": {
            "consolidated_net_income": cons.get("net_income"),
            "stores_net_income_total": stores_ni,
            "residual": residual,
            "explained": explained,
            "company_wide_net_income_effect": cw_effect,
            "unstored_journal_net_income_effect": je_effect,
            "unexplained": unexplained,
            "reconciles": reconciles,
            "company_wide_lines": list(company_wide_cells or []),
            "unstored_journal_entries": list(unstored_journal_cells or []),
            "why": why,
        },
    }


def profit_by_store(client, org_id, period):
    """THE finance-side answer to "what did the books earn this period, company-wide and per store"
    — the figure every other report must QUOTE instead of deriving profit a second way (owner
    report 2026-11-17).

    ONE `build_inputs_full`, P&L ONLY (no balance sheet, no cash flow, no prior-BS read), then the
    existing `_scopes` scope list assembled through `engine._assemble` over `coa.pl_spec_for` /
    `coa.pl_label_for` — the same machinery `_assemble_scope` uses, so every `net_income` here IS
    `statement(..., kinds=("pl",))["pl"]["net_income"]` for that scope.

    Org-scoped end to end (every read carries `org_id`; the scope list is built from THIS org's
    inputs, companies and journal, so no other tenant's row can appear). Each store row carries the
    store key as the BOOKS place it — the canonical store address `coa.store_resolver` produces —
    plus that store's declared CODE and market from the one store-identity home
    (`store_identity.store_identity_index`), so a caller keying stores by code can join without
    inventing a second resolution. Read-only; moves no money, writes nothing."""
    from app.modules.account import engine
    inputs, _cfg, meta = build_inputs_full(client, org_id, period)
    meta.update(side_meta(inputs))
    journal = _journal_rows(client, org_id, period)
    company_of, _default, companies = coa.company_assignment(client, org_id)
    matcher = balance_sheet.journal_company_matcher(companies)
    # Profit centers are a scope FAMILY this producer does not report (stores + companies +
    # consolidated are the grains a caller joins to), so the extra config read is skipped; the
    # store/company/consolidated entries of the list are identical either way.
    scopes, _all_stores = _scopes(inputs, companies, company_of, journal, matcher, None)
    spec, label_map = coa.pl_spec_for(inputs), coa.pl_label_for(inputs)

    def _pl(scope_key, stores_in_scope, include_cw):
        jscope, _jmeta = balance_sheet.journal_grain_entries(
            journal, scope_key, stores_in_scope, matcher, company_of)
        return engine._assemble(inputs, jscope, spec, label_map, PL_SECTIONS,
                                scope_key, stores_in_scope, include_cw)

    consolidated_pl, store_pls, company_pls = None, [], []
    for scope_key, scope_label, stores_in_scope, include_cw in scopes:
        if scope_key == "consolidated":
            consolidated_pl = _pl(scope_key, stores_in_scope, include_cw)
        elif scope_key.startswith("store:"):
            store_pls.append((scope_key, scope_label, _pl(scope_key, stores_in_scope, include_cw)))
        elif scope_key.startswith("company:"):
            company_pls.append((scope_key, scope_label, _pl(scope_key, stores_in_scope, include_cw)))

    # THE store identity (§64) — the one home, never a second resolution here.
    identity, identity_reason = None, None
    try:
        from app.modules.account import store_identity as _sid
        mapping_rows = coa._fetch_all(
            client, "store_mapping",
            "store_address,store_code,market,salesforce_id,is_active", {"org_id": org_id})
        identity = _sid.store_identity_index(mapping_rows, coa.store_resolver(client, org_id))
    except Exception as e:
        identity, identity_reason = None, f"store identity unavailable: {str(e)[:160]}"
        coa._warn("profit_by_store: store identity unavailable — store codes not measured", e)

    out = fold_profit_by_store(
        period, org_id, consolidated_pl, store_pls, company_pls,
        company_wide_pl_cells(inputs, spec),
        unstored_journal_pl_cells(journal, matcher, company_of),
        identity=identity, identity_reason=identity_reason,
        scope_counts={"scopes_total": len(scopes)})
    out["computed_at"] = datetime.now(timezone.utc).isoformat()
    out["meta"] = meta
    return out
