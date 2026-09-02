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


def build_inputs_full(client, org_id, period):
    """coa.build_inputs + the balance-sheet truths. Returns (inputs, bs_cfg, meta). Every
    augmentation degrades gracefully (a failed read leaves the base inputs untouched)."""
    inputs = coa.build_inputs(client, org_id, period)
    for k, *_ in balance_sheet.EXTRA_BS_SPEC:
        inputs.setdefault(k, {"by_store": {}, "company_wide": 0.0, "detail": {}})
    cfg = balance_sheet.load_bs_config(client, org_id)
    meta = {"as_of": period_as_of(period), "inventory_basis": cfg["inventory_basis"]}

    # ── handset payables (config-driven; empty default books nothing) ───────────────────────────
    if cfg["handset_payable_order_types"] and meta["as_of"]:
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
            line = inputs["handset_payable"]
            for acct, amt, detail in bookings:
                st = acct_store.get(acct)
                st = resolve(st) if st else None
                if st:
                    line["by_store"][st] = _round(line["by_store"].get(st, 0.0) + amt)
                else:
                    line["company_wide"] = _round(line["company_wide"] + amt)
                if detail:
                    line["detail"][detail] = _round(line["detail"].get(detail, 0.0) + amt)
            meta["handset_payable"] = {**hp_meta, "as_of": meta["as_of"]}
        except Exception as e:
            coa._warn("handset payable booking failed — line left empty", e)

    # ── verified store cash on hand (config-driven; 'off' default books nothing — mig 938) ──────
    # Owner 2026-09-02: "all cash collected in the store must be added to the balance sheet as
    # cash collected after it has been verified by the DM, either the cash is deposited in the
    # bank or it is used in expenses." The MOVEMENT dicts come from the closing module's OWN
    # shared computation (`_cash_position_core` — the same function Cash Position / Store Cash on
    # Hand / pickups read, declared cash already DM-overlay-corrected, outflows = pickups +
    # deposits + approved envelope expenses/withdrawals), so the Balance Sheet can never disagree
    # with those pages; the 'verified' basis then keeps ONLY DM-verified store-days as collected
    # (balance_sheet.store_cash_cells — pure; unverified dollars are reported in meta, never
    # silently dropped). Store grain resolves through the SAME coa.store_resolver every other
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

    # ── inventory basis (config-driven; 'report' default = byte-identical) ──────────────────────
    if cfg["inventory_basis"] == "devices":
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
    numeric duality; the old exact-match read silently dropped a month on a mismatch)."""
    try:
        return (client.schema("commcalc").table("journal_entries").select("*")
                .eq("org_id", org_id).in_("period", list(_period.period_keys(period)))
                .execute().data) or []
    except Exception:
        return []


def _scopes(inputs, companies, company_of):
    all_stores = set()
    for ln in inputs.values():
        if isinstance(ln, dict):
            all_stores.update((ln.get("by_store") or {}).keys())
    scopes = [("consolidated", "Consolidated (all companies)", None, True)]
    for c in companies:
        cstores = {s for s in all_stores if company_of(s) == c["id"]}
        scopes.append((f"company:{c['id']}", c["name"], cstores, False))
    for s in sorted(all_stores):
        scopes.append((f"store:{s}", s, {s}, False))
    return scopes, all_stores


def _assemble_scope(client, org_id, period, inputs, journal, matcher,
                    scope_key, scope_label, stores_in_scope, include_cw):
    """One scope's (pl, bs, cf) — deterministic assembly via engine._assemble with the extended
    BS spec and the FIXED journal scoping."""
    from app.modules.account import engine
    jscope = balance_sheet.journal_scope_entries(journal, scope_key, stores_in_scope, matcher)
    pl = engine._assemble(inputs, jscope, coa.PL_SPEC, coa.PL_LABEL, PL_SECTIONS,
                          scope_key, stores_in_scope, include_cw)
    prior_ni = engine._prior_accum_ni(client, org_id, scope_key, period)
    bs = engine._assemble(inputs, jscope, bs_spec(), bs_label(), BS_SECTIONS,
                          scope_key, stores_in_scope, include_cw,
                          prior_accum_ni=prior_ni, current_ni=pl["net_income"])
    for stmt in (pl, bs):
        stmt["period"], stmt["scope_key"], stmt["scope_label"] = period, scope_key, scope_label
    prior_bs = _stored_bs(client, org_id, scope_key, _prior_period_label(period))
    cf = cash_flow(pl, bs, prior_bs, period, scope_key, scope_label)
    return pl, bs, cf


# ── the on-demand service (fresh numbers, no persistence) ───────────────────────────────────────
def statement(client, org_id, period, scope="consolidated", kinds=("pl", "balance_sheet", "cash_flow")):
    """FRESH statements for ANY org / period / scope, computed now from the live bookings. The
    platform-wide on-demand service: other modules, the notify report registry (scheduled /
    on-demand sends) and the API endpoint call this. Org-scoped end to end; an unknown company
    scope returns computed:false rather than another org's data (fail closed)."""
    inputs, cfg, meta = build_inputs_full(client, org_id, period)
    journal = _journal_rows(client, org_id, period)
    company_of, _default, companies = coa.company_assignment(client, org_id)
    matcher = balance_sheet.journal_company_matcher(companies)
    scopes, _stores = _scopes(inputs, companies, company_of)
    match = next((s for s in scopes if s[0] == scope), None)
    if match is None:
        return {"period": period, "scope": scope, "computed": False,
                "note": "unknown scope for this org"}
    scope_key, scope_label, stores_in_scope, include_cw = match
    pl, bs, cf = _assemble_scope(client, org_id, period, inputs, journal, matcher,
                                 scope_key, scope_label, stores_in_scope, include_cw)
    out = {"period": period, "scope": scope_key, "scope_label": scope_label, "computed": True,
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
    journal = _journal_rows(client, org_id, period)
    company_of, _default, companies = coa.company_assignment(client, org_id)
    matcher = balance_sheet.journal_company_matcher(companies)
    scopes, all_stores = _scopes(inputs, companies, company_of)

    # Purge ALL prior snapshots for the period first (orphan-scope rule, same as engine.py).
    client.schema("commcalc").table("account_statements").delete() \
        .eq("org_id", org_id).eq("period", period).execute()

    written = 0
    for scope_key, scope_label, stores_in_scope, include_cw in scopes:
        pl, bs, cf = _assemble_scope(client, org_id, period, inputs, journal, matcher,
                                     scope_key, scope_label, stores_in_scope, include_cw)
        pl["notes"] = engine._notes(scope_key, include_cw)
        bs["notes"] = engine._notes(scope_key, include_cw) + (
            [] if bs["balanced"] else ["Balance sheet is not yet balanced — enter cash / opening "
                                       "balances via the Journal so Assets = Liabilities + Equity."])
        if scope_key == "consolidated":
            narrative, model = engine._narrate(pl, bs, scope_label, period)
        else:
            narrative, model = "", "deterministic"
        for stmt, st_type in ((pl, "pl"), (bs, "balance_sheet"), (cf, "cash_flow")):
            engine._persist(client, org_id, period, st_type, scope_key, scope_label, stmt,
                            narrative if st_type == "pl" else "", model,
                            stmt.get("balanced", stmt.get("tied", True)))
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
