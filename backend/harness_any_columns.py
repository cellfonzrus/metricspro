#!/usr/bin/env python3
"""ANY-SUBSET COLUMN READS — the proof (index §4b.1; owner defect 2026-09-22).

THE DEFECT, measured on the live tenant (org f4f1c16e…, 2026-09-22): the owner applied mig 1013 and
chose the ledger on the Commission Ledger page — `commission_org_config.pl_commission_source =
'ledger'` — and `ma_store_pnl.load_config` returned `feeds`. The row lacks the mig-996 column
`pl_device_margin_presentation`, so the mig-1013 column block AND the mig-996 block both failed and the
reader's ladder fell to the mig-934 block, which predates the switch. The P&L booked $0 on every
commission line and the page said nothing.

THE CLASS: a per-org config reader that selects column SETS as blocks lets one missing OLDER column hide
every NEWER one. THE FIX: one reading rule (core/column_tolerant.py) — a config row is read whole and the
reader takes what is there; a wide table probes each optional column on its own; what is missing is
REPORTED. Every ladder under backend/app is gone (harness_any_columns_lock.py fails the build on one).

WHAT THIS PROVES (no DB, no network — the fixture and the fake client are harness_pl_commission_source's,
reused, so the money figures are the ones that file already pins):
  §A the helper: a complete row, a row lacking a column, no row (probed), an absent table, the scope.
  §B THE LIVE SHAPE — has pl_commission_source='ledger', LACKS pl_device_margin_presentation:
     load_config reads 'ledger' and REPORTS the missing column + its migration; build_inputs books the
     ledger; the read endpoint says ready with nothing missing for the switch; the OLD ladder, replayed
     over the same rows, reads 'feeds' (the regression, reproduced). The INVERSE (has 996, lacks 1013):
     'feeds', config_columns_missing=['pl_commission_source'], the P&L words name mig 1013, the
     endpoint is not ready. Both absent; no row at all.
  §C BYTE-IDENTICAL under complete columns, for every reader that changed: the P&L config, the wage
     cells, the ledger rows, the what-if ledger read, the expenses read, the bill-pay and residual
     configs, the pay-path config, the classification config, the memberships.
  §D the router readers: one read of accessory_config instead of nine; the projections that keep
     credentials out of a payload; the provenance readers' `ready`; the pull diagnostic; the registry
     auto map; the tender tokens; the activation-details rules dereferencing the one cached read.

  cd backend && python3 harness_any_columns.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import harness_pl_commission_source as H      # noqa: E402 — the fixture + the fake client, REUSED
from app.core import column_tolerant as ct     # noqa: E402
from app.modules.account import ma_store_pnl as msp, ledger_pnl as LP, coa, billpay_pl, residual_subs  # noqa: E402
from app.modules.commcalc import commission_engine as CE, whatif as WI, expenses_effective as EX     # noqa: E402

CHECKS = []
ORG, OTHER, P = H.ORG, H.OTHER, H.P
Client, tables, total, same = H.Client, H.tables, H.total, H.same
CFG_T = "commission_org_config"
DMP, PCS = "pl_device_margin_presentation", "pl_commission_source"
MIG_996, MIG_1013 = "996_pl_device_margin_presentation.sql", "1013_pl_commission_source.sql"


def check(name, ok, detail=""):
    CHECKS.append((bool(ok), name))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"   {detail}"))
    return ok


def cfg_table(client):
    return lambda: client.schema("commcalc").table(CFG_T)


def org_scope(org):
    return lambda q: q.eq("org_id", org)


def old_ladder_source(client, org_id):
    """THE REMOVED READER, replayed: the newest-first column-set ladder of ma_store_pnl.load_config as it
    stood at #273 (1013 → 996 → 934 → 314 → defaults). Returns the commission_source it would have read.
    Kept here ONLY as the regression oracle — the live defect, reproduced on demand."""
    c314 = "pl_ma_store_attribution,pl_ma_month_spiff_source,pl_ma_spiff_order_types,pl_mdf_product_tokens,pl_line_labels"
    c934 = c314 + ",pl_rebate_presentation"
    c996 = c934 + "," + DMP
    c1013 = c996 + "," + PCS
    rows, picked = [], None
    for cols in (c1013, c996, c934, c314):
        try:
            rows = (client.schema("commcalc").table(CFG_T).select(cols).eq("org_id", org_id).limit(1).execute().data) or []
            picked = cols
            break
        except Exception:
            continue
    # PostgREST returns ONLY the selected columns — the fake returns the row, so project it here
    rows = [{k: r.get(k) for k in picked.split(",")} for r in rows] if picked else []
    v = str((rows[0].get(PCS) if rows else "") or "").strip().lower()
    return v if v in msp.COMMISSION_SOURCES else "feeds"


def strip_new(cfg):
    return {k: v for k, v in cfg.items() if k not in ("config_columns_missing", "config_migrations_missing")}


def run():
    print("ANY-SUBSET COLUMN READS — one reading rule, every reader, what is missing is reported")
    print("=" * 78)

    # ── §A the helper ──────────────────────────────────────────────────────────────────────────
    print("\n§A the helper — read_row / present_columns / select_list")
    expected = [c for c, _m in msp.PL_CONFIG_COLUMNS]
    db = Client(tables())
    rd = ct.read_row(cfg_table(db), org_scope(ORG), expected)
    check("A1 a complete row: the org's row whole, nothing missing, readable",
          rd.readable and rd.missing == [] and rd.row["org_id"] == ORG and set(expected) <= rd.present, rd)
    db = Client(tables(), missing={CFG_T: {DMP}})
    rd = ct.read_row(cfg_table(db), org_scope(ORG), expected)
    check("A2 a row lacking ONE column: every other column present, that one reported missing",
          rd.readable and rd.missing == [DMP] and DMP not in rd.row and PCS in rd.row, rd)
    db = Client(tables(), missing={CFG_T: {PCS}})
    rd = ct.read_row(cfg_table(db), org_scope("org-nobody"), expected)
    check("A3 NO row for the org: readable, row None, the missing column still found by probing each expected column on its own",
          rd.readable and rd.row is None and rd.missing == [PCS] and DMP in rd.present, rd)
    db = Client(tables(), absent={CFG_T})
    rd = ct.read_row(cfg_table(db), org_scope(ORG), expected)
    check("A4 an ABSENT table: not readable, row None, every expected column missing — never raises",
          not rd.readable and rd.row is None and rd.missing == expected, rd)
    check("A5 present_columns probes each column on its own: 'origin' present / absent",
          ct.present_columns(lambda: Client(tables()).schema("commcalc").table("commission_ledger"), org_scope(ORG), ("origin",)) == {"origin"}
          and ct.present_columns(lambda: Client(tables(), missing={"commission_ledger": {"origin"}}).schema("commcalc").table("commission_ledger"), org_scope(ORG), ("origin",)) == frozenset())
    widest = "category,payout_total,store,source_report,origin,is_payout,raw_amount,payment_month,product_name,period"
    check("A6 select_list spells the SAME column set the former widest block did when every column exists, and drops only what is absent",
          set(ct.select_list(LP._LEDGER_REQUIRED, LP._LEDGER_OPTIONAL, {"origin"}).split(",")) == set(widest.split(","))
          and set(ct.select_list(LP._LEDGER_REQUIRED, LP._LEDGER_OPTIONAL, set()).split(",")) == set(widest.split(",")) - {"origin"})
    rd = ct.read_row(cfg_table(Client(tables())), org_scope(OTHER), expected)
    check("A7 the scope is the org's: another org's row never comes back for this org", rd.row["org_id"] == OTHER and rd.row[PCS] == "ledger")

    # ── §B THE LIVE SHAPE ──────────────────────────────────────────────────────────────────────
    print("\n§B the live shape (2026-09-22): the row HAS pl_commission_source='ledger' and LACKS pl_device_margin_presentation")
    live = Client(tables({PCS: "ledger"}), missing={CFG_T: {DMP}})
    cfg = msp.load_config(live, ORG)
    check("B1 load_config reads 'ledger' — the owner's choice reaches the P&L",
          cfg["commission_source"] == "ledger", cfg)
    check("B2 …and REPORTS the missing column and its migration instead of hiding it",
          cfg["config_columns_missing"] == [DMP] and cfg["config_migrations_missing"] == [MIG_996], cfg)
    check("B3 …and keeps every other switch the row carries (spiff source, labels, rebate route); the absent one reads its house default",
          cfg["month_spiff_source"] == "daily_tx" and cfg["line_labels"] == {"mi_income": "Residual"}
          and cfg["rebate_presentation"] == "contra_cogs" and cfg["device_margin_presentation"] == "off")
    check("B4 THE REGRESSION, reproduced: the removed ladder over the SAME rows read 'feeds'",
          old_ladder_source(live, ORG) == "feeds")
    L = coa.build_inputs(live, ORG, P)
    check("B5 build_inputs books the LEDGER on this shape (carrier_comm 2,130.00 — the figure harness_pl_commission_source §B pins), not the feeds' 730.00",
          same(total(L, "carrier_comm"), 2130.0), total(L, "carrier_comm"))
    m = L["carrier_comm"].get("commission_source") or {}
    check("B6 …and the line's meta says the ledger booked it, with the switch READY (the missing column is not the switch)",
          m.get("source") == "ledger" and m.get("switch_ready") is True and "not applied" not in m.get("words", ""), m)
    meta = LP.load_source_meta(live, ORG)
    check("B7 load_source_meta (the panel's read-back) dereferences the SAME reader: 'ledger', ready, the 996 gap reported",
          meta["configured"] == "ledger" and meta["ready"] is True and meta["config_columns_missing"] == [DMP]
          and meta["migration"] == MIG_1013, meta)
    try:
        from app.modules.commcalc import router as R
        from fastapi import HTTPException
        R.sb = lambda: live
        R.require_org = lambda o: None
        got = R.get_pl_commission_source(org_id=ORG)
        check("B8 GET /pl-commission-source: value 'ledger', ready, no not-ready note, and the 996 migration named as missing",
              got["value"] == "ledger" and got["ready"] is True and got["not_ready_note"] is None
              and got["config_migrations_missing"] == [MIG_996], {k: got[k] for k in ("value", "ready", "not_ready_note", "config_migrations_missing")})
    except ImportError as e:
        check("B8 router importable (fastapi present)", False, str(e))
        R, HTTPException = None, None

    print("\n   the inverse: the row HAS pl_device_margin_presentation and LACKS pl_commission_source")
    inv = Client(tables({DMP: "margin_block"}), missing={CFG_T: {PCS}})
    cfg = msp.load_config(inv, ORG)
    check("B9 load_config reads 'feeds' (the house default) AND reads the mig-996 value that is there ('margin_block')",
          cfg["commission_source"] == "feeds" and cfg["device_margin_presentation"] == "margin_block", cfg)
    check("B10 …reporting config_columns_missing=['pl_commission_source'] and the 1013 migration",
          cfg["config_columns_missing"] == [PCS] and cfg["config_migrations_missing"] == [MIG_1013], cfg)
    L = coa.build_inputs(inv, ORG, P)
    m = L["carrier_comm"].get("commission_source") or {}
    check("B11 build_inputs books the FEEDS (730.00) and the line's words SAY the switch column is not applied, naming mig 1013; switch_ready False",
          same(total(L, "carrier_comm"), 730.0) and m.get("switch_ready") is False and MIG_1013 in m.get("words", "")
          and "not applied" in m.get("words", ""), m)
    meta = LP.load_source_meta(inv, ORG)
    check("B12 load_source_meta: not ready, 'feeds', the 1013 gap reported", meta["ready"] is False and meta["configured"] == "feeds" and meta["config_columns_missing"] == [PCS], meta)
    if R is not None:
        R.sb = lambda: inv
        got = R.get_pl_commission_source(org_id=ORG)
        check("B13 GET /pl-commission-source: ready=False, the note names mig 1013, config_columns_missing=['pl_commission_source'] — the read side says what the save side refuses",
              got["ready"] is False and MIG_1013 in (got["not_ready_note"] or "") and got["config_columns_missing"] == [PCS], got.get("not_ready_note"))
        try:
            R._require_commission_admin = lambda a, o: None
            R.put_commission_settings(R.PutCommissionSettingsIn(pl_commission_source="ledger"), authorization="", org_id=ORG)
            check("B14 …and the save is still refused naming mig 1013 (unchanged)", False)
        except HTTPException as e:
            check("B14 …and the save is still refused naming mig 1013 (unchanged)", e.status_code == 400 and MIG_1013 in str(e.detail))
    both = Client(tables(), missing={CFG_T: {DMP, PCS}})
    cfg = msp.load_config(both, ORG)
    check("B15 both absent: 'feeds', both columns and both migrations reported, in column order",
          cfg["commission_source"] == "feeds" and cfg["config_columns_missing"] == [DMP, PCS]
          and cfg["config_migrations_missing"] == [MIG_996, MIG_1013], cfg)
    cfg = msp.load_config(Client(tables(), missing={CFG_T: {PCS}}), "org-nobody")
    check("B16 no config row at all: the defaults, and the missing column still reported (probed) — nothing silent",
          strip_new(cfg) == strip_new(msp.default_config()) and cfg["config_columns_missing"] == [PCS], cfg)
    cfg = msp.load_config(Client(tables(), absent={CFG_T}), ORG)
    check("B17 the table itself absent (pre-314): the defaults, every column reported missing, never raises",
          strip_new(cfg) == strip_new(msp.default_config()) and cfg["config_columns_missing"] == expected)

    # ── §C BYTE-IDENTICAL under complete columns ───────────────────────────────────────────────
    print("\n§C byte-identical when every column exists — every reader that changed")
    cfg = msp.load_config(Client(tables()), ORG)
    check("C1 load_config on the complete row: exactly the values the row carries, nothing missing",
          strip_new(cfg) == {"store_attribution": True, "month_spiff_source": "daily_tx",
                             "spiff_order_types": ["PostPaid Additional Spiff"], "mdf_product_tokens": ["premium store spiff"],
                             "line_labels": {"mi_income": "Residual"}, "rebate_presentation": "contra_cogs",
                             "device_margin_presentation": "off", "commission_source": "feeds"}
          and cfg["config_columns_missing"] == [] and cfg["config_migrations_missing"] == [], cfg)
    check("C2 the P&L books exactly the figures harness_pl_commission_source pins: feeds 730.00 / ledger 2,130.00 on carrier_comm",
          same(total(coa.build_inputs(Client(tables()), ORG, P), "carrier_comm"), 730.0)
          and same(total(coa.build_inputs(Client(tables({PCS: "ledger"})), ORG, P), "carrier_comm"), 2130.0))
    check("C3 default_config carries the two report keys empty (a config nobody read reports nothing missing)",
          msp.default_config()["config_columns_missing"] == [] and msp.default_config()["config_migrations_missing"] == [])

    # wages: the same rows into the same derive_wage_cells, with and without the mig-416/417 columns
    emps_full = [{"org_id": ORG, "employee_id": "E1", "pay_rate": 20.0, "home_store": "S1", "pay_basis": "hourly", "pay_amount": None, "is_active": True},
                 {"org_id": ORG, "employee_id": "E2", "pay_rate": 0.0, "home_store": "S2", "pay_basis": "monthly", "pay_amount": 3000.0, "is_active": True}]
    shifts = [{"org_id": ORG, "employee_id": "E1", "store_code": "S1", "scheduled_hours": 8, "actual_hours": 8, "shift_date": "2026-07-03", "is_deleted": False},
              {"org_id": ORG, "employee_id": "E2", "store_code": "S2", "scheduled_hours": 8, "actual_hours": 8, "shift_date": "2026-07-03", "is_deleted": False}]
    hourly_cols = ("employee_id", "pay_rate", "home_store", "org_id")
    emps_pre416 = [{k: v for k, v in e.items() if k in hourly_cols} for e in emps_full]
    db_full = Client({**tables(), "employees": emps_full, "shifts": shifts})
    db_pre = Client({**tables(), "employees": emps_pre416, "shifts": shifts})
    code2addr = coa.store_code_to_address(db_full, ORG)
    w_full, w_pre = coa.wages_by_store(db_full, ORG, P), coa.wages_by_store(db_pre, ORG, P)
    check("C4 wages_by_store == derive_wage_cells over the same roster rows (salary columns present)",
          w_full == coa.derive_wage_cells(emps_full, shifts, code2addr) and w_full.get(H.STORE_A) == 160.0, w_full)
    check("C5 …and on a pre-416 roster (no salary columns) == derive_wage_cells over the hourly-only rows — hourly-only, exactly as before",
          w_pre == coa.derive_wage_cells(emps_pre416, shifts, code2addr) and w_pre.get(H.STORE_A) == 160.0, w_pre)
    check("C6 …and the two differ only by what the salary columns add (the salaried store), never on the hourly one",
          w_full.get(H.STORE_A) == w_pre.get(H.STORE_A) and (w_full.get(H.STORE_B) or 0) != (w_pre.get(H.STORE_B) or 0), (w_full, w_pre))

    # the ledger rows: with / without origin
    keys = [P, "2026-07"]
    r_full = LP.load_ledger_rows(Client(tables()), ORG, keys)
    r_pre = LP.load_ledger_rows(Client(tables(), missing={"commission_ledger": {"origin"}}), ORG, keys)
    check("C7 load_ledger_rows: the same rows with and without the mig-251 `origin` column (only that key differs), none from another org or period",
          len(r_full) == len(r_pre) == len(H.STATEMENT) + 1 and all("origin" in r for r in r_full) and all("origin" not in r for r in r_pre)
          and [{k: v for k, v in r.items() if k != "origin"} for r in r_full] == r_pre and all(r["org_id"] == ORG for r in r_full), (len(r_full), len(r_pre)))
    check("C8 …an absent ledger table is an empty ledger, never an exception", LP.load_ledger_rows(Client(tables(), absent={"commission_ledger"}), ORG, keys) == [])

    # what-if: the capability flags are each their own probe
    n = len([r for r in H.LEDGER if r["org_id"] == ORG])
    wi = lambda **kw: WI._ledger_income_rows(Client(tables(), **kw), ORG)      # noqa: E731
    check("C9 whatif._ledger_income_rows: complete → (rows, ready, origin_ready, name_ready) = (n, True, True, True)",
          (len(wi()[0]),) + wi()[1:] == (n, True, True, True), wi()[1:])
    check("C10 …origin absent → origin_ready False and name_ready STILL True; product_name absent → the reverse; table absent → ([], False, False, False)",
          wi(missing={"commission_ledger": {"origin"}})[1:] == (True, False, True)
          and wi(missing={"commission_ledger": {"product_name"}})[1:] == (True, True, False)
          and wi(absent={"commission_ledger"}) == ([], False, False, False))

    # expenses: source_key probed on its own
    exp_rows = [{"org_id": ORG, "period": P, "store_code": "S1", "amount": 100.0, "source_key": None},
                {"org_id": ORG, "period": P, "store_code": "S1", "amount": 50.0, "source_key": "payroll:auto"}]
    e_full, _ = EX.effective_expense_rows(Client({**tables(), "store_expenses": exp_rows}), ORG, P, keys, "store_code,amount")
    e_pre, _ = EX.effective_expense_rows(Client({**tables(), "store_expenses": exp_rows}, missing={"store_expenses": {"source_key"}}), ORG, P, keys, "store_code,amount")
    check("C11 effective_expense_rows: the period's rows carry `source_key` when the column exists (the auto row is told apart) and come without it pre-206 (every row manual), the same rows either way",
          len(e_full) == 2 and all("source_key" in r for r in e_full) and any(r["source_key"] for r in e_full)
          and len(e_pre) == 2 and all("source_key" not in r for r in e_pre), (e_full, e_pre))

    # bill-pay + residual configs
    bp = billpay_pl.load_config(Client(tables({"pl_billpay_presentation": "carveout", "pl_billpay_settlement": "net_from_commission"})), ORG)
    bp_pre = billpay_pl.load_config(Client(tables({"pl_billpay_presentation": "carveout"}), missing={CFG_T: {"pl_billpay_settlement"}}), ORG)
    check("C12 billpay_pl.load_config: both read when present; the presentation still read when the settlement column is absent, and that absence reported",
          bp["presentation"] == "carveout" and bp["settlement"] == "net_from_commission" and bp["config_columns_missing"] == []
          and bp_pre["presentation"] == "carveout" and bp_pre["settlement"] == "remit_separate" and bp_pre["config_columns_missing"] == ["pl_billpay_settlement"], (bp, bp_pre))
    rs = residual_subs.load_ma_pnl_config(Client(tables({"pl_merchant_discount_own_line": False})), ORG)
    rs_pre = residual_subs.load_ma_pnl_config(Client(tables({"pl_merchant_discount_own_line": False}), missing={CFG_T: {"pl_ma_residual_order_types"}}), ORG)
    check("C13 residual_subs.load_ma_pnl_config: the toggle read with or without the order-types column; the absence reported",
          rs["merchant_discount_own_line"] is False and rs["residual_order_types"] == ["Postpaid Residual Order"]
          and rs_pre["merchant_discount_own_line"] is False and rs_pre["config_columns_missing"] == ["pl_ma_residual_order_types"], (rs, rs_pre))

    # the pay-path config: the exact rung the old ladder dropped
    ppc = lambda **kw: CE._plan_pay_config(Client(tables({"plan_ct_resolution": "mapped", "store_resolution": "alias", "activation_source": "activation_details"}), **kw), ORG)  # noqa: E731
    check("C14 commission_engine._plan_pay_config: all three read when present, nothing missing",
          ppc() == {"plan_ct_resolution": "mapped", "store_resolution": "alias", "activation_source": "activation_details", "config_columns_missing": []}, ppc())
    check("C15 …the mig-249 column absent: plan_ct_resolution AND activation_source (mig 296) STILL read — the old ladder fell to 'plan_ct_resolution' alone and lost the activation source",
          ppc(missing={CFG_T: {"store_resolution"}}) == {"plan_ct_resolution": "mapped", "store_resolution": "exact", "activation_source": "activation_details", "config_columns_missing": ["store_resolution"]},
          ppc(missing={CFG_T: {"store_resolution"}}))
    check("C16 …table absent → the defaults, every column reported missing",
          ppc(absent={CFG_T})["plan_ct_resolution"] == "raw" and ppc(absent={CFG_T})["config_columns_missing"] == ["plan_ct_resolution", "store_resolution", "activation_source"])

    # the classification config
    acc = [{"org_id": ORG, "contract_type_map": {"New": "premium"}, "activation_rules": [{"field": "department", "bucket": "byod"}],
            "activation_details_rules": {"fields": ["contract_type"]}}]
    lr, rules = CE._read_ct_classification_config(Client({**tables(), "accessory_config": acc}), ORG)
    lr2, rules2 = CE._read_ct_classification_config(Client({**tables(), "accessory_config": acc}, missing={"accessory_config": {"activation_rules"}}), ORG)
    check("C17 _read_ct_classification_config: the map and the rules read; with activation_rules absent the map and the activation_details_rules are STILL read",
          rules == [{"field": "department", "bucket": "byod"}] and rules2 == [] and lr == lr2 and isinstance(lr, dict), (rules, rules2))

    # memberships (identity): rows whole
    from app.core import tenant_middleware as TM
    users = [{"org_id": ORG, "auth_id": "u1", "super_admin": False, "is_default_org": True, "role": "admin", "twofa_enabled": True, "created_at": "2026-01-01"},
             {"org_id": OTHER, "auth_id": "u1", "super_admin": False, "is_default_org": False, "role": "user", "created_at": "2026-02-01"},
             {"org_id": ORG, "auth_id": "u2", "super_admin": True, "created_at": "2026-01-01"}]
    rows = TM._fetch_memberships(Client({**tables(), "app_users": users}), "u1")
    check("C18 tenant_middleware._fetch_memberships: every row for the login, whole (role, twofa_enabled present when the columns are), never another login's",
          [r["org_id"] for r in rows] == [ORG, OTHER] and rows[0]["twofa_enabled"] is True and rows[0]["role"] == "admin")
    rows = TM._fetch_memberships(Client({**tables(), "app_users": users}, missing={"app_users": {"twofa_enabled", "role"}}), "u1")
    check("C19 …pre-706/711 (no role / twofa_enabled): the rows still come, without those keys — 2FA-off, as before",
          len(rows) == 2 and "twofa_enabled" not in rows[0] and rows[0]["is_default_org"] is True)
    dead = Client({**tables()}, absent={"app_users"})
    try:
        got = TM._fetch_memberships(dead, "u1")
        check("C20 …a dead backend: [] only under the break-glass env (IDENTITY_BACKEND_503=0), never a schema-shaped fallback",
              got == [] and not TM._identity_503(), got)
    except TM.IdentityBackendUnavailable:
        check("C20 …a dead backend RAISES IdentityBackendUnavailable (the 2026-08-03 posture, kept)", TM._identity_503())

    # ── §D the router readers ──────────────────────────────────────────────────────────────────
    print("\n§D the router readers")
    if R is not None:
        # one read of accessory_config instead of nine — count the selects the fake sees
        counts = {}
        _orig_select = H._Q.select

        def _counting_select(self, cols="*", **kw):
            counts[self.table] = counts.get(self.table, 0) + 1
            return _orig_select(self, cols, **kw)
        acc_full = [{"org_id": ORG, "departments": ["Accessories"], "categories": [], "product_keywords": ["case"], "acima_tenders": ["ACIMA"],
                     "box_departments": ["Phones"], "setup_fee_keywords": ["Setup"], "contract_type_map": {"New": "premium"},
                     "activation_details_rules": {"edge_contract_tokens": ["edge"]}, "activation_rules": [], "billpay_products": ["Bill Pay"],
                     "box_count_buckets": ["byod"], "apply_to_gp": True, "catalog_classify_enabled": False,
                     "catalog_accessory_categories": [], "definition_drives_pay": True, "gp_acc_basis": "gp",
                     "billpay_card_tenders": ["Visa"], "billpay_cash_tenders": ["Cash"]}]
        H._Q.select = _counting_select
        try:
            ac = R._accessory_config_uncached(Client({**tables(), "accessory_config": acc_full}), ORG)
        finally:
            H._Q.select = _orig_select
        check("D1 _accessory_config_uncached: ONE read of accessory_config (was nine single-column reads of the same row)",
              counts.get("accessory_config") == 1, counts)
        check("D2 …and every section takes its column from that one row",
              ac["departments_list"] == ["Accessories"] and ac["products_list"] == ["case"] and ac["acima_tenders_list"] == ["ACIMA"]
              and ac["box_departments_list"] == ["Phones"] and ac["setup_fee_keywords_list"] == ["Setup"] and ac["contract_type_map"] == {"new": "premium"}
              and ac["billpay_products_list"] == ["Bill Pay"] and ac["box_count_buckets_list"] == ["byod"] and ac["apply_to_gp"] is True
              and ac["definition_drives_pay"] is True and ac["gp_acc_basis"] == "gp" and ac["activation_details_rules_raw"] == {"edge_contract_tokens": ["edge"]},
              {k: ac[k] for k in ("departments_list", "box_departments_list", "gp_acc_basis", "apply_to_gp")})
        ac2 = R._accessory_config_uncached(Client({**tables(), "accessory_config": acc_full}, missing={"accessory_config": {"box_departments", "gp_acc_basis", "billpay_products"}}), ORG)
        check("D3 …a row lacking three of the columns: THOSE sections read their defaults, every other section keeps its value (no column hides another)",
              ac2["departments_list"] == ["Accessories"] and ac2["box_departments_list"] == list(R._BOX_DEPTS) and ac2["gp_acc_basis"] == "sales"
              and ac2["billpay_products_list"] == [] and ac2["apply_to_gp"] is True and ac2["setup_fee_keywords_list"] == ["Setup"])
        ac3 = R._accessory_config_uncached(Client({**tables(), "accessory_config": [], "flag_rules": [{"org_id": ORG, "id": 1, "accessory_departments": ["Legacy"], "accessory_categories": [], "accessory_product_keywords": [], "acima_tenders": []}]}), ORG)
        check("D4 …no row for the org: the pre-208 flag_rules fallback, exactly as before", ac3["departments_list"] == ["Legacy"])
        tt = R._billpay_tender_tokens(Client({**tables(), "accessory_config": acc_full}, missing={"accessory_config": {"billpay_cash_tenders"}}), ORG)
        from app.modules.commcalc import metric_recon as MR
        check("D5 _billpay_tender_tokens: the card tenders read while the cash column is absent (the house default for cash)",
              tt["card"] == ("Visa",) and tt["cash"] == MR.DEFAULT_CASH_TENDERS, tt)
        adr = R._activation_details_rules(Client({**tables(), "accessory_config": acc_full}), ORG)
        check("D6 _activation_details_rules dereferences the ONE cached config read (the configured token is in force)",
              "edge" in [t.lower() for t in adr["edge_contract_tokens"]], adr)

        sweep = [{"org_id": ORG, "enabled": True, "last_run_at": "2026-09-01", "last_status": "ok", "last_detail": None, "next_run_at": None,
                  "frequency": "daily", "day_of_week": None, "day_of_month": None, "hour": 6, "timezone": "America/New_York",
                  "last_attempt_at": "2026-09-02", "password": "SECRET", "username": "u"}]
        st = R._connector_status(Client({**tables(), "epay_sweep_config": sweep}), ORG, "epay_sweep_config")
        st_pre = R._connector_status(Client({**tables(), "epay_sweep_config": sweep}, missing={"epay_sweep_config": {"last_attempt_at"}}), ORG, "epay_sweep_config")
        check("D7 _connector_status: status + schedule + last_attempt_at from ONE read, PROJECTED — the credentials never leave",
              st["enabled"] is True and st["frequency"] == "daily" and st["last_attempt_at"] == "2026-09-02" and "password" not in st and "username" not in st, st)
        check("D8 …pre-241 (no last_attempt_at): every other field still comes; an absent config table → {}",
              st_pre["frequency"] == "daily" and "last_attempt_at" not in st_pre and R._connector_status(Client(tables(), absent={"epay_sweep_config"}), ORG, "epay_sweep_config") == {})

        SRC = H.LEDGER[0]["source_report"]        # the fixture's statement template
        ex, ex_ready = R._ledger_existing_by_origin(Client(tables()), ORG, SRC, P)
        ex_pre, ex_pre_ready = R._ledger_existing_by_origin(Client(tables(), missing={"commission_ledger": {"origin", "synced_at"}}), ORG, SRC, P)
        n_stmt = len([r for r in H.LEDGER if r["org_id"] == ORG and r["period"] in (P, "2026-07") and r.get("source_report") == SRC])
        check("D9 _ledger_existing_by_origin: with `origin` ready=True and the lines split by provenance; without it ready=False and everything reads as 'file' — the same line count either way",
              ex_ready is True and ex_pre_ready is False and list(ex_pre) == [R.ledger_ma_sync.ORIGIN_FILE]
              and sum(o["lines"] for o in ex.values()) == sum(o["lines"] for o in ex_pre.values()) == n_stmt, (ex_ready, ex_pre_ready, n_stmt))
        R.sb = lambda: Client(tables(), missing={"commission_ledger": {"origin", "synced_at"}})
        prov = R.commission_ledger_provenance(source_report=SRC, org_id=ORG)
        R.sb = lambda: Client(tables())
        prov_full = R.commission_ledger_provenance(source_report=SRC, org_id=ORG)
        check("D10 commission_ledger_provenance: ready=False without `origin`, ready=True with it — the read never fails on the other column",
              prov["ready"] is False and prov_full["ready"] is True, (prov.get("ready"), prov_full.get("ready")))

        ds = [{"org_id": ORG, "id": "src-1", "label": "portal", "processor": "x", "last_pull_diag": {"seen": 3}, "last_pull_at": "2026-09-01",
               "last_status": "ok", "last_run_at": "2026-09-01", "last_attempt_at": "2026-09-01", "auth_status": "ok", "credentials": "SECRET"}]
        R.sb = lambda: Client({**tables(), "data_source": ds})
        dg = R.data_source_pull_diagnostic("src-1", org_id=ORG)
        R.sb = lambda: Client({**tables(), "data_source": ds}, missing={"data_source": {"last_pull_diag", "last_pull_at"}})
        dg_pre = R.data_source_pull_diagnostic("src-1", org_id=ORG)
        check("D11 data_source_pull_diagnostic: the diagnostic when mig 242 is applied; pre-242 ready=False with the note and the projected row — the credentials never enter the payload",
              dg.get("ready") is not False and dg_pre["ready"] is False and "242" in dg_pre["note"] and dg_pre["row"]["label"] == "portal"
              and "credentials" not in dg_pre["row"] and "credentials" not in str(dg), (dg.get("ready"), dg_pre.get("ready")))
        try:
            R.data_source_pull_diagnostic("src-nope", org_id=ORG)
            check("D12 …an unknown source is still 404", False)
        except HTTPException as e:
            check("D12 …an unknown source is still 404", e.status_code == 404)

        defs = [{"org_id": ORG, "report_key": "a", "auto": True, "carrier_id": None}, {"org_id": ORG, "report_key": "b", "auto": False, "carrier_id": "c-9"}]
        am = R._registry_auto_map(Client({**tables(), "report_definitions": defs, "carrier": [{"org_id": ORG, "id": "c-1", "name": "one"}]}), ORG)
        am_pre = R._registry_auto_map(Client({**tables(), "report_definitions": defs, "carrier": [{"org_id": ORG, "id": "c-1", "name": "one"}]}, missing={"report_definitions": {"carrier_id"}}), ORG)
        check("D13 _registry_auto_map: with carrier_id a report of a carrier the tenant does not run is hidden; pre-291 (no carrier_id) every row reads as carrier-agnostic — as before",
              am == {"a": True} and am_pre == {"a": True, "b": False}, (am, am_pre))

    print("\n" + "=" * 78)
    failed = [c for c in CHECKS if not c[0]]
    print(f"{len(CHECKS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
