# MetricsPro — System Data-Flow Index

> **Purpose.** A durable, indexed map of every report, query, data flow, table, key function, and
> known gap in the MetricsPro commission/ops platform. **Look things up here before re-investigating.**
> Every claim carries a `path:line` anchor and real `schema.table.column` / function names.
> Anchors are line numbers as of the commit this file was written against (`0bb6964`, 2026-08-15);
> line numbers drift — grep the named symbol if an anchor looks stale. Items that could not be
> verified from code are tagged `⚠ unverified`.

Primary code homes:
- Backend commission/ops: `backend/app/modules/commcalc/` (router.py is ~28.9k lines, 468 endpoints).
- Store ops / org / scheduling / payroll: `backend/app/modules/storeops/router.py` (~7.4k lines).
- Cash/deposit closing: `backend/app/modules/closing/`.
- Schema of truth: `database/migrations/` (numbered; higher number = later ALTER wins).
- Frontend: `frontend/src/app/(platform)/commcalc/**/page.tsx`.
- Default org (house tenant) in code: `00000000-0000-0000-0000-000000000001`.

---

## 1. Top index / TOC — "answers questions like…"

| # | Subsystem | Answers questions like… |
|---|-----------|--------------------------|
| 2 | **Data ingest & raw_* tables** | "How does sales/DLAR/MI/inventory data get in? Email? Upload? Sweep? Which raw table?" |
| 3 | **Sales report & the shared cell-agg** | "Where do activation/accessory counts come from? Why do Sales Report / Exec MTD / Targets agree now?" |
| 4 | **GP / P&L report** | "How is store gross-profit / P&L built? What's voided? Where's the money booked?" |
| 5 | **Daily Targets & actuals** | "How are daily targets computed vs actuals? What's an 'achieved' number? Accessory $ actual?" |
| 6 | **Rep commission (Boost)** | "How is a rep paid? premium/byod/upgrade counts, acc/setup/trade-in, tiers, KPIs. Where stored?" |
| 7 | **Carrier residual installments** | "Multi-month carrier residual pay from raw_mi. Why do named activation_types not pay?" |
| 8 | **Plan-mode sale installments** | "Sale-triggered multi-month pay (Total Wireless). Device categories, gates, ledger." |
| 9 | **Management Incentive (DM/manager)** | "How is a district/market manager paid? Components, qualifiers, bonuses, auto-resolved numbers." |
| 10 | **KPI system (DLAR / store_kpis / carrier_kpi_metric)** | "Where do ATU / protect / TMR3 / conversion come from? Store vs rep KPIs?" |
| 11 | **Inventory & aging** | "Per-device cost / days-in-stock. Is it live or a snapshot? Device history lookup." |
| 12 | **Cash / deposit reconciliation** | "Collected cash vs bank deposits. Expected deposit, variance, basis." |
| 13 | **Org hierarchy & store resolution** | "Which stores does a manager see? How is a raw store string canonicalized to a store_code?" |
| 14 | **Employees & scheduling** | "Do shifts feed pay? Rep→employee name mapping. Hours in targets." |
| 15 | **Other commission subsystems** | MA (master-agent) commission, VIP, epay, chargebacks, expenses, agency, financing, accrual/payout ledger. |
| 16 | **Cross-reference: by TABLE** | table → sections/functions that read & write it. |
| 17 | **Cross-reference: by ENDPOINT** | endpoint → handler/section. |
| 18 | **Cross-reference: by METRIC/KPI** | metric → source table → reader function. |
| 19 | **Known gaps & inert config** | stored-but-unwired, snapshot-only, surfaces that can disagree. |

---

## 2. Data ingest & `raw_*` tables

**Purpose.** All computation runs off period-scoped `raw_*` tables in schema `commcalc`. Data arrives by
three routes: (a) **manual upload** (mapping wizard), (b) **automated sweeps** (portal scrape / FTP /
email), (c) **RPC/manual entry**.

### Source tables (all `commcalc.*`, all `org_id UUID` + `period TEXT` scoped)
| Table | Migration | Key columns | Feeds |
|-------|-----------|-------------|-------|
| `raw_sales` | `002_commcalc.sql:19` | `store, salesperson, user_login, department, category, product_desc, product_id, gp, ext_price, trans_id, trans_date, contract_type, mdn, serial_1, register, tender_type, voided, trans_type, sku` | Rep commission, GP, sales report, installments |
| `daily_sales_feed` | `047_sales_feed_recon.sql:19` | superset of raw_sales + `customer, email, customer_no` | The **processed** daily sales source; falls back to raw_sales |
| `raw_payment_detail` | `002_commcalc.sql:34` | `business_address, payment_type, amount, mdn, imei, payment_date, rep_username, sequence` | GP (payment categorization), commission reimbursement |
| `raw_mi` | `002_commcalc.sql:46` | `salesforce_id, actual_mi_payout, actual_atu_payout, phone_number, subscriber_status` | Carrier residual gate (paid-proof), MI/ATU |
| `raw_dlar_rep` | `002_commcalc.sql:56` (+`012`,`031`) | `rep_name, store, atu_pct, protect_pct, byod_pct, family_plan_pct, tmr3, aal_conversion, bounty, split, ga_prepaid` | Rep KPI, comp trend |
| `raw_dlar_store` | `002_commcalc.sql:67` (+`012`,`031`) | `store_code, salesforce_id, address, total_acts, port_pct, psa_projected` **plus** later `atu, protect_pct, byod_pct, family_plan_pct, tmr3, aal_conversion, conversion_rate, gross_adds, total_upgrades, location` | Store KPIs, MI TMR3 gate |
| `raw_catalog` | `002_commcalc.sql:77` | `product_id, product_desc, cost, sku` | Device COGS, GP, installment MRC |

> **Period spelling.** Sweeps stamp month-name labels (`'July 2026'`); manual entry may differ. Every
> read matches BOTH spellings via **`_pvariants(period)`** `router.py:170` used as `.in_('period', _pvariants(period))`.

### Ingest route A — manual upload (mapping wizard)
- Endpoint `POST /upload/{file_type}` `router.py:844`; mapped ingest `POST /upload-mapped` `router.py:3637`;
  generic manual-report ingest `POST /manual-upload/ingest` `router.py:24291`.
- Column mapping config: migrations `042_column_mapping.sql`, `212_commission_manual_report_mapping.sql`;
  endpoints `/column-mapping*` `router.py:3333-3472`, `/manual-upload/mapping` `router.py:24125`.
- Upload history/trace: `/upload/history` `router.py:2168`, `/upload-trace` `router.py:16256` (mig `202`,`241`).
- Frontend: `frontend/src/app/(platform)/commcalc/upload/wizard/page.tsx`, `column-mapping/page.tsx`,
  `report-mappings/page.tsx`.

### Ingest route B — automated sweeps (`backend/app/modules/commcalc/*_sweep.py`)
| Sweep | Module | Writes | Config table / endpoints |
|-------|--------|--------|--------------------------|
| DLAR (rep+store KPI) | `dlar_sweep.py` `run_dlar_sweep:209` | **replaces** period `raw_dlar_rep`/`raw_dlar_store` (`dlar_sweep.py:237`) | `dlar_sweep_config` (mig `012`); `/dlar/sweep/*` `router.py:8447-8490` |
| B2B (sales + inventory aging) | `b2b_sweep.py` | `raw_sales`/feed + `inventory_aging_device` upsert (`b2b_sweep.py:341`) | `/b2b/sweep/*` `router.py:8575-8618`; `fetch_inventory_aging` is a **stub** (`b2b_sweep.py:77` — needs live b2bsoft creds) `⚠` |
| epay (payment detail) | `epay_sweep.py` | `raw_payment_detail` | `/epay/sweep/*` `router.py:8730-8811` (mig `020`,`025`) |
| VIP invoices | `vip_sweep.py` | vip invoice tables (mig `008`,`011`,`014`) | `/vip/sweep/*` `router.py:3034-3078` |
| FTP drop | `ftp_sweep.py` | per report-pull-map | `/ftp-sweep/*` `router.py:22175-22237` (mig `046`) |
| Email inbox | `email_sweep.py` | routes attachments to report ingest | `/email-sweep/*` `router.py:22973-23407` (mig `049`,`075`); scheduler: pg_cron → `/email-sweep/run-due` (mig `921`,`922` — backend self-registers on boot; handler advances `next_run_at` up front and sweeps via BackgroundTasks so the tick answers pg_net inside its 5 s timeout) |
| Vidapay | `vidapay_sweep.py` | payment feed | (mig `083` total processor sources) |
| Generic data-source portal login | `live_login.py` | any report | `/data-sources/*` `router.py:23760-24979`, `/data-sources/sweep/run-due` `24409` |

Connector/schedule model: mig `039_connector_model.sql`, `063`, `290_report_schedule_and_grain.sql`;
endpoints `/connectors*` `router.py:6666-6989`, `/connector-health` `23378`. Sweep store-guard
(quarantine ambiguous store strings before ingest): `ingest_store_guard.py`, mig `280`; `/ingest-guard/*`
`router.py:14375-14446`.

### Ingest route C — RPC / manual
- Sales "derive/promote" (feed → raw_sales grace promotion): `/sales/promote-feed` `router.py:22757`,
  `/sales/derive-config` `22766`, mig `266_sales_derive_grace.sql`.
- KPI/targets/expenses/plans are entered via their own PUT endpoints (see each section).

**Known gaps:** `b2b_sweep.fetch_inventory_aging` and `login` are stubs (`b2b_sweep.py:11,77`) — inventory
aging currently arrives by **upload**, not live scrape. `⚠`

---

## 3. Sales report & the shared per-(store, rep, day) aggregation

**Purpose.** ONE row-level pass feeds the Sales Report, Executive MTD, and Daily Targets so they can never
disagain (owner directive 2026-07-16, 2026-07-25).

- **The shared pass:** `_sales_cell_agg(rows, acfg, store_key=…)` — canonical skip-set + classification,
  returns per-cell bucket SETS + sums. Documented at `router.py:17842` (the `_ACTUALS_COLS` +
  long design comment). Columns selected: `_ACTUALS_COLS = trans_id,trans_date,store,salesperson,
  user_login,contract_type,department,category,product_desc,gp,ext_price,voided,trans_type` `router.py:17842`.
- **Counting rule:** DISTINCT `trans_id` (a 4-device AAL under one trans_id counts **1**, not 4).
- **Skip rules:** `gp_report.is_voided(voided)` (VOID_TOKENS), `trans_type == 'Return'`, and rep
  `'admin'`/blank are skipped. Void tokens single source: `gp_report.VOID_TOKENS = ('true','yes','1','voided','void')`
  `gp_report.py:55`; `is_voided` `gp_report.py:58`; `countable_sale_skip_reason` `gp_report.py:76`.
- **Classification single source:** `classify_contract_type(ct)` `calculator.py:40` →
  `'byod'|'upgrade'|'premium'|None`. BYOD/Upgrade by CONTAINS; premium by known set `PREMIUM_ACT` OR
  keyword `_PREMIUM_KEYS = ('activation','port-in','port in','add a line','add-a-line','new line',' aal',
  'aal ','idv','port with idv')` `calculator.py:36`. `None` = accessory/non-activation line.
- **Accessory detection:** shared `_is_accessory` driven by Classification-settings config
  (`_accessory_config` / `/accessory-config` `router.py:16313`, mig `092`,`093`,`208`,`231`,`257`).

**Endpoints:** `/sales-report` `router.py:15792`; `/sales-report/detail` `15980`;
`/sales-report/classification-unmatched` `15925`; `/sales-comparison` `16096`; `/sales-diagnostics` `16206`;
`/top-sellers/{period}` `16982`. **Frontend:** `commcalc/sales-report/page.tsx`, `exec/page.tsx`,
`exec/mtd/page.tsx`.

**Gap / caution:** This shared pass is **DISPLAY ONLY** — the commission CALC path is deliberately NOT
wired through `_sales_cell_agg` (see comment at `router.py:17842`, "the commission CALC path is
deliberately NOT wired here"). Pay path re-derives counts inside `calc_rep_commissions`. This is the
single most important place two surfaces can diverge (display vs pay). See §19.

---

## 4. GP / P&L report

**Purpose.** Per-store gross-profit / P&L: device GP, accessory GP, payment categorization,
commissions, expenses.

- **Entry:** `calc_gp_report(sales, pay_detail, mi_rows, rep_comms, expenses, catalog, store_map, period, …)`
  `gp_report.py:121`. Pure (no I/O). Called from the `/gp/{period}` handler `router.py:14725`.
- **Void handling:** shares `VOID_TOKENS`/`is_voided` `gp_report.py:55,58`.
- **Department classification:** `_dept_classifier(gp_category_map)` `gp_report.py:31`, overrides
  `_gp_overrides` `gp_report.py:19`; config `commcalc.gp_category_map` (mig `069_gp_category_map.sql`),
  endpoints `/gp-category-map` `router.py:14893,14907`, `/gp-departments` `14934`.
- **Store booking:** payment-detail rows carry no store address → booked on ONE company-wide row unless
  the street-number join resolves a code (`router.py:14632,14711`).
- **Leg ladders** (multi-month carrier income roll-forward): `_leg_ladder_add/_merge` `gp_report.py:96,108`.

**Source tables:** `raw_sales`/`daily_sales_feed`, `raw_payment_detail`, `raw_mi`, `raw_catalog`,
`rep_commissions`, `store_expenses`, `store_mapping`. **GP snapshot:** mig `102_gp_snapshot.sql`.

**Endpoints:** `/gp/{period}` `router.py:14750`; `/gp-trend` `14847`; `/gp-departments` `14934`;
`/expenses-trend` `14784`; `/commission-trend` `14808`. **Frontend:** `commcalc/gp/page.tsx`,
`gp-category-map/page.tsx`.

- **MA TX → P&L booking (mig `309`, owner spec 2026-09-01 Phase B — "merchant discount as merchant
  discount, residual under residual"):** for MA/VidaPay tenants (no `raw_mi` for the period),
  `account/coa.py:build_inputs` books `raw_ma_daily_tx.merchant_discount` to the dedicated
  **`ma_merchant_discount` "Merchant discount"** revenue line (per-org opt-out:
  `commission_org_config.pl_merchant_discount_own_line=false` restores the legacy `atu_income` fold
  byte-identically), and the MA residual (−`retail_cost`) to **`mi_income` "MI residual income"**
  (label already names residual — deliberately no second Residual line) for rows matching the
  **UNION** of the `product_name` `'%residual%'` family (`residual_subs._MA_RESIDUAL_LABEL_MATCH`)
  and the configured `order_type` family (`commission_org_config.pl_ma_residual_order_types`,
  default `['Postpaid Residual Order']`) — each row books ONCE. Classification is pure
  (`residual_subs.ma_tx_pnl_bookings` / `ma_residual_row_matcher`; config
  `residual_subs.load_ma_pnl_config`, adaptive pre-309); only `merchant_discount` + `retail_cost`
  are read as money (`assert_money_columns`). Proofs: `harness_ma_tx_pnl.py`,
  `harness_ma_income_heads.py`.

---

## 5. Daily Targets & actuals

**Purpose.** Per store/rep daily targets vs achieved (activations by category + accessory $), with hours
scoping, MTD projection, and action items.

- **Engine:** `backend/app/modules/commcalc/targets_engine.py` (pure). Key funcs:
  `compute_scope(...)` `targets_engine.py:180`; `achieved_for_cat(cat,prem,byod,upg,acc)` `:43`;
  `scope_hours_by_day` `:55`; `scope_actuals_by_day` `:77`; `scope_conversion` `:104`;
  `scope_achieved_mtd` `:134`; `project_future_hours` `:152`; `derive_monthly_by_cat` `:301`;
  `reps_in_scope` `:317`; `build_action_items` `:348`; `aggregate_stores` `:445`.
- **The actuals feed (THE processed sales source):**
  `_compute_feed_actuals_py(client, org_id, period, source='daily_sales_feed', rows=None)` `router.py:18678`.
  Reads `daily_sales_feed` then falls back to `raw_sales`; classifies by CONTAINS (fixes label drift);
  canonicalizes store via `_store_code_resolver` (`router.py:18487`) so actuals key on the canonical
  `store_code` the Daily Target uses; aggregates through the shared `_sales_cell_agg`. Returns per
  `(store_code, rep, day)` dicts. **`acc_gp`** field = accessory `ext_price` + device set-up fee
  (NOT gross profit, NOT `department='Ondigo'`; owner directive 2026-07-17).
- **DEPRECATED:** the legacy SQL RPC `daily_sales_feed_actuals` (mig `048_daily_sales_feed_actuals.sql`)
  hardcodes Ondigo/gp and a rigid label list — superseded by `_compute_feed_actuals_py`. Do not use.

**Source tables:** `daily_sales_feed`/`raw_sales`; targets stored in `commcalc` targets tables (mig
`006_targets.sql`, `070_target_field_registry.sql`); hours from `storeops.shifts` via `_fetch_shifts`
`router.py:17447`.

**Endpoints:** `/targets/{period}` `router.py:19005`, PUT `19071`; `/targets/{period}/roll-forward` `19103`;
`/targets/{period}/calendar` `19205`; `/targets/{period}/summary` `19440`; `/targets/{period}/action-plan`
`21334`; `/coaching/{period}` `19800`. **Frontend:** `commcalc/targets/page.tsx`, `targets/accessories`,
`targets/action-plan`, `targets/my`, `targets/rep-map`, `targets/settings`.

---

## 6. Rep commission (Boost)

**Purpose.** Compute each rep's monthly payout: activation counts × flat spiffs, accessory %, setup-fee,
trade-in, acima, custom spiffs, plus KPI-tier multiplier, plus installment add-ons.

- **Entry:** `calc_rep_commissions(sales, pay_detail, dlar_rep, dlar_store, mi_rows, catalog, cfg,
  store_mapping, shifts, employees, stores, period, name_map, carrier_mode='boost')` `calculator.py:104`. Pure.
- **Orchestration flow:**
  1. `POST /calculate/{period}` `router.py:8968` (`calculate(...)`) →
  2. `_run_calculation(period, org_id, force, guard_token)` `router.py:9415` (fetches every raw_* set with
     `_pvariants`; note `shifts = []` — see gap) →
  3. calls `calc_rep_commissions(...)` `router.py:9539` →
  4. `_apply_new_engines(client, org_id, period, comms, carrier_mode, notices)` `router.py:9183` adds
     installment columns and writes rows.
- **Row filters (pay path):** `gp_report.is_voided`; `trans_type != 'Return'`; `salesperson != 'admin'`
  (`calculator.py:216-278`).
- **Classification:** `classify_contract_type` `calculator.py:40` (same as display).
- **Config:** `commcalc.payout_config` (mig `002_commcalc.sql:95`) — `premium_flat`(5), `byod_flat`(3),
  `upgrade_flat`(20), `trade_in_spiff`(20), `acima_spiff`(25), `acc_rate`(0.10), `setup_fee_rate`(0.10),
  KPI targets (`kpi_atu_target`…`kpi_aal_target`), tier thresholds (`tier_100_min_kpis`,`tier_75_min_kpis`,
  `tier_75_pct`,`tier_50_pct`), `custom_spiffs JSONB`. Endpoints `/config/{period}` `router.py:10467,10474`,
  `/commission-settings` `10509,10517`.

**Stored:** `commcalc.rep_commissions` (mig `002_commcalc.sql:118`). Columns:
counts `premium_acts, byod_acts, upgrade_acts`;
$ `premium_comm, byod_comm, upgrade_comm, acc_comm, setup_fee_comm, trade_in_comm, acima_comm,
custom_comm, acc_target`; tier `tier, tier_source, kpis_met, total_kpis, kpi_values JSONB`;
`subtotal, total_payout, boost_commission, boost_reimbursement`. **Added by later migrations:**
`plan_comm` (mig `061_rep_commissions_plan_comm.sql`), `residual_installment_comm` (mig `057`),
`installment_comm_sale` (mig `201:` `ALTER TABLE … ADD COLUMN installment_comm_sale`),
`carrier_statement_comm` (mig `065_carrier_commission.sql`). The `_apply_new_engines` writer sets
`residual_installment_comm`/`installment_comm_sale` at `router.py:9322-9390` only when the column exists.

**Endpoints:** `/calculate/{period}` `8968`; `/commissions/{period}` `10222`; `/calc-status/{period}` `15697`;
`/commission-drill` `16640`; `/commission-explain` `16721`; `/commission-device` `16742`;
`/commission-by-store/{period}` `22013`; `/commission-statement(s)` `12656,12750`; `/pay-simulator/*`
`1954,1967`. **Frontend:** `commcalc/commission-explain/page.tsx`, `pay-simulator/page.tsx`,
`daily-commission/page.tsx`, `commission-plans/page.tsx`.

**KEY GAP (from seed, verified):** rep pay carries **no distinct Edge or VHI/FIOS count** — both fold into
`premium_acts` in Boost. Plan-mode `home_internet` count is runtime-only (see §8). MI resolves these two
by re-scanning the same sales universe (§9).

---

## 7. Carrier residual installments (raw_mi path)

**Purpose.** Multi-month carrier residual pay: each qualifying subscriber pays a FLAT or %-of-MRC amount
per month for N months, gated on the subscriber still being active/paid (proved from `raw_mi`).

- **Engine:** `backend/app/modules/commcalc/installment_engine.py`. Entry
  `compute_installments(client, org_id, pay_period, persist=False)` `installment_engine.py:152`.
  Schedule resolution `_resolve_schedule(scheds, carrier_id, company_id, activation_type)` `:120`.
- **Config tables:** `commcalc.payout_schedule` + `payout_schedule_line` (mig `057_multi_month_payout.sql:16,35`);
  `subscriber_installments` (`:49`); per-subscriber residual RPC mig `101_residual_per_sub_rpc.sql`.
- **Writes into:** `rep_commissions.residual_installment_comm`, ADDED to `total_payout`
  (`router.py:9200` calls with `persist=True`; column write `9322-9388`).
- **Total Wireless template:** mig `078_total_wireless_template.sql` seeds named `activation_type` curves.

**INERT CONFIG (verified):** `_resolve_schedule` forces `activation_type='*'` — the engine only ever
resolves the `'*'` schedule (`installment_engine.py:194,267` pass `"*"`; simplification documented at
`installment_engine.py:13-16`). Named variants (`edge`, `fios_300/500/1g/2g`, `upgrade_edge`, `twp_protect`,
`2_month`, …) are **STORED-BUT-INERT** (mig `078` header `:34-36`). `⚠` This is the live limitation here.
(STALE-CLAIM FIX 2026-09-01: this section previously said month_index was capped at `min(3, num_months)`
— that cap was already lifted: `installment_engine.py:197` clamps at `min(12, num_months)`, honoring each
schedule's full horizon; mig `078:31` documents the old behaviour, not the code.)

---

## 8. Plan-mode sale installments (sale-triggered path)

**Purpose.** A qualifying SOLD line in month S schedules a payout for `month_index = (P−S)+1` that lands in
pay month P, gated per carrier. Used for Total Wireless plan pay. SEPARATE column from §7 so cutover never
conflates them.

- **Engine:** `backend/app/modules/commcalc/sale_installment_engine.py`. Entry
  `compute_sale_installments(client, org_id, pay_period, persist=False, _gate_source_override=None,
  _config_override=None, _sales_override=None)` `sale_installment_engine.py:1057`. Returns
  `{pay_period, by_rep, ledger, flags, totals, schedules, note}`.
- **Device category classifier:** `installment_category.py` `CATEGORY_KEYS = ('phone','tablet',
  'home_internet','sim','accessory','unknown')` `:44`; VHI → `home_internet` via product_desc word `'vhi'`
  / `'internet gateway'` / substring `'home internet'` / catalog (`installment_category.py:82-88`, mig `245`).
- **Gate evidence source (config-driven, mig `223`):** Boost carriers prove paid from `raw_mi`; master-agent
  carriers prove paid from `raw_ma_commission` per-month spiffs (docstring `sale_installment_engine.py:1057+`).
  Kill switch env `INSTALLMENT_GATE_LEGACY` forces the legacy raw_mi gate.
- **MA TX gate + MRC (mig `308`, config OPT-IN — never a default flip):** `gate_source='ma_tx'` proves
  month n from the UNION of (i) the `raw_ma_commission` spiffs (months ≤ 6, `_gate_met_ma` reused
  unchanged) and (ii) `raw_ma_daily_tx` rows whose `product_name` carries the `'MONTH n'` wording —
  parsed by `commission_ledger.parse_payment_month` (THE shared regex, never a second one) — reached
  through the TWO-HOP join `raw_sales.serial_1 ↔ raw_ma_commission.imei|sim` (digit-normalized) →
  `activation_order ↔ raw_ma_daily_tx.order_number` (`_gate_met_ma_tx`, `build_ma_link_index`,
  `build_ma_tx_index`, `ma_tx_month_evidence` — all pure). M1 evidence also counts the linked
  `order_type = cfg.ma_tx_activation_order_type` ('Activation Order') row itself. `ma_max_month` caps
  the horizon (a Total org row can set 16). MRC: `commission_org_config.installment_mrc_basis =
  'ma_tx_activation'` resolves the MRC from the linked Activation Order row's `retail_cost`
  (`ma_tx_mrc_for`; `mrc_source='ma_tx_activation'`), FALLING THROUGH to the plan-line ladder when
  unlinked. Money guard: only `retail_cost` is read as money — `merchant_invoice` is an identifier and
  is excluded from the select. Proof: `backend/harness_ma_tx_multimonth.py`.
- **Config tables:** `plan_installment_schedule` + `plan_installment_line` (mig `201:36,64`;
  `num_months` CHECK widened to 1..16 by mig `308`, engine clamp `MAX_SCHEDULE_MONTHS=16`);
  `installment_gate_source_config` (mig `223`, + `ma_tx_activation_order_type` mig `308`);
  `ma_commission_month_rate` (`month_index` CHECK widened to 1..16 by mig `308`);
  `installment_category_rule` (mig `245:50`); `installment_category_payout` flat rates (mig `256`);
  hardware guard (mig `246`); line MRC (mig `233`). Base spiff rates live in `payout_config`.
- **Ledger:** `commcalc.sale_installment_ledger` (mig `201:81`). Columns: `trans_id, mdn, serial_1,
  plan_id, schedule_id, store, epay_salesperson, sale_period, pay_period, month_index, payout_kind,
  mrc_at_pay, mrc_source, amount, paid_gate_met, gate_mode, status ('paid'|'withheld_unpaid'|
  'out_of_window'), matched_mi_period`. UNIQUE `(org_id, trans_id, mdn, month_index, pay_period)`.
  Mig `308` adds `order_number` + `account_id` (MA TX provenance; written adaptively by `_persist`
  like the mig-258 `expected_amount` tier, NULL unless the MA TX linkage resolved them).
- **Writes into:** `rep_commissions.installment_comm_sale` (`router.py:9212` persist=True; `9324-9390`).

**Endpoints:** `/plan-installments` `router.py:10726`+ (schedules, matchers, category rules, payout,
impact previews at `10726-11391`); `/expected-commission/*` `11397-11579`. **Frontend:**
`commcalc/plan-installments/page.tsx`, `expected-commission/page.tsx`.

**GAP (verified):** `sale_installment_ledger` has **NO category column** — category is computed at runtime
(`category_guard.by_category` counts are emitted but **not persisted**). So plan-mode `home_internet`/edge
counts cannot be queried historically from the ledger. `⚠`

---

## 9. Management Incentive (district/market manager pay)

**Purpose.** Pay a manager on the performance of their store-set: percent/per-unit components + qualifier-
gated consolidated bonus + inventory-aging bonus. Built this session (PRs #26/#27).

- **Engine:** `management_incentive.py`. `compute_payout(plan, *, actuals, qualifier_values,
  manager_store_count, …)` `:149`; `component_payout(component, actual, manager_store_count)` `:90`;
  `evaluate_qualifiers` `:128`; `qualifier_pass(op, value, threshold)` `:116` — **fails CLOSED**: a gate
  whose value is `None` does NOT pass (`:117`); `resolve_plan(...)` (assignment precedence
  employee>role>store>market>default) `:47`.
- **Seed (Total Wireless default):** `management_incentive_seed.py` `default_plan_spec()` `:24`,
  `seed_management_incentive_defaults(...)` `:66`. Components: `accessory_gp` percent 0.02 (2%),
  `vhi_fios_count` per_unit $2, `edge_count` per_unit $5 (`seed:36-40`). Bonuses: consolidated
  gated_by `qualifiers`, inventory `gated_by inventory_aging {"max_days":10}` (`seed:45-47`).
  Qualifiers: `zulu, tmr3, cash_deposit, twp, address_checks`. ~$2090 at full attainment.
- **Auto-resolve numbers:** `_mi_resolve_numbers(client, org_id, period, employee_id, plan)` `router.py:28800`
  — every read guarded, failures land in `unresolved` (manual). Resolves:
  - store set via RPC `storeops.org_span_for_manager` (§13);
  - `accessory_gp` = Σ `acc_gp` over stores from `_compute_feed_actuals_py` (`router.py:28825`);
  - `edge_count`/`vhi_fios_count` = re-scan of raw_sales→feed applying rep-pay filters (voids/Return/admin),
    tagged by `_mi_classify_sales_row(category, contract_type, product_desc)` (`router.py:28843-28880`) —
    shares the rep-pay basis of truth (commit `0bb6964`);
  - `tmr3` = avg of `raw_dlar_store.tmr3` over stores (`router.py:28884`);
  - `cash_deposit` = collected − deposited via `deposit_recon.closing_cash_raw_by_store_day` +
    `bank_deposits_by_store_day` (`router.py:28895`).

**Tables (mig `852_commcalc_management_incentive_plan.sql`):**
`management_incentive_plan` (`:39` — `carrier_id, level, is_default, period_type, consolidated_bonus_amount`),
`management_incentive_component` (`:64` — `kind ∈ percent|per_unit, rate, metric_source, target_per_store,
store_count, cap_at_target`), `management_incentive_bonus` (`:87` — `kind ∈ consolidated|inventory_selloff|
flat, gated_by ∈ qualifiers|inventory_aging|manual|none, config`), `management_incentive_qualifier`
(`:108` — `metric_key, source ∈ kpi|cash_deposit|inventory|manual, op, threshold, applies_to`),
`management_incentive_assignment` (`:128` — `scope, scope_value, priority`),
`management_incentive_payout` (`:143` — one row per manager/period/plan; `breakdown JSONB,
component_total, bonus_total, total, qualified, status ∈ draft|approved|paid`, override fields).

**Endpoints:** `/management-incentive/plans` GET/POST `router.py:28527,28534`, DELETE `28600`;
`/management-incentive/compute` `28613`; `/management-incentive/payouts` `28668`;
`/management-incentive/payouts/{payout_id}/decision` `28687`; `/management-incentive/resolve` `28912`.
**Frontend:** `commcalc/management-incentive/page.tsx`.

**Cross-surface caution:** MI's `edge_count`/`vhi_fios_count` are counted from the same sales universe rep
pay uses but Boost rep pay still folds them into premium acts — MI is the only place these are broken out.
See §19.

---

## 10. KPI system (DLAR / store_kpis / carrier_kpi_metric)

**Purpose.** Store- and rep-level performance KPIs used by targets, tiers, coaching, exec overview, and MI
gates.

- **Store KPIs source:** `commcalc.raw_dlar_store` (see §2 columns). Reader for the store KPI endpoint:
  `get_dlar_store_kpis(period, …)` handler at `router.py:10279` (endpoint `/dlar-store/{period}` `10278`).
  Reused by comp report: `_cr_resolve_kpi_metrics(client, org_id, period, ctx)` `router.py:25656` — selects
  `location,address,store_code,atu,protect_pct,byod_pct,family_plan_pct,tmr3,aal_conversion,conversion_rate,
  total_acts,gross_adds,total_upgrades`.
- **Rep KPIs source:** `commcalc.raw_dlar_rep` (org-averaged for comp trend `/commission-leg-trend` region,
  `router.py:15238`). Columns `atu_pct, protect_pct, byod_pct, family_plan_pct, tmr3, aal_conversion,
  bounty, split, ga_prepaid`.
- **Persisted store KPI snapshot:** `commcalc.store_kpis` (mig `002_commcalc.sql:137`) — `atu_pct,
  protect_pct, byod_pct, family_plan_pct, tmr3, psa_projected, port_pct`.
- **Configurable KPI metric registry:** `commcalc.carrier_kpi_metric` (mig `060_carrier_kpi_metrics.sql:15`)
  — `carrier_id, metric_key, label, target_default, payout_config_col, sort`. Endpoints
  `/carrier-kpi-metrics` GET/POST/DELETE `router.py:19757,19773,19794`.
- **MI ATU-by-period RPC:** mig `032_mi_atu_by_period_rpc.sql`. Comp-report columns: mig `031`.
  Conversion: mig `013_conversion.sql`.

**Endpoints:** `/dlar-store/{period}` `10278`; `/carrier-kpi-metrics` `19757`; `/exec-overview/{period}`
`20103`; `/exec-mtd/{period}` `20596`; `/productivity/{period}` `21152` + `/productivity/kpi-values/{period}`
`21218` (mig `215_productivity_registry.sql`); `/comp/residual-trend` `17339`, `/comp/rep-pay-trend` `17357`.
**Frontend:** `commcalc/dlar/page.tsx`, `productivity/page.tsx`, `comp-trend/page.tsx`, `exec/page.tsx`.

---

## 11. Inventory & aging

**Purpose.** Per-device cost and days-in-stock, used by Device History lookup and device-cost recon.

- **Table:** `commcalc.inventory_aging_device` (mig `216_commission_inventory_aging_device.sql:15`,
  +`294_inventory_aging_on_hand_flag.sql`). Columns: `imei` (canonical key, else serial), `serial, sku,
  item` (product/description), `store` (**FREE TEXT, not store_code** — `216:22`), `unit_cost` (POS on-hand
  SKU cost), `received_date` (aging basis), `days_in_stock` (report's aging days — **snapshot**, NOT live),
  `as_of_date` (file snapshot date), `on_hand BOOLEAN` (`294:22`), `off_hand_as_of DATE`.
- **UPSERT one-row-per-(org, imei)** — a SNAPSHOT, not history. Conflict target
  `inventory_aging_device_org_imei_uq` (`216:39`); ingest `b2b_sweep.py:341` upserts, and flips
  `on_hand=false` instead of deleting (`b2b_sweep.py:396-407`; mig `294:18`). Store-level roll-up is the
  separate `commcalc.inventory_value` (mig `026`).
- **Consumers:** device-cost recon `/device-cost-recon` `router.py:27338` (function region ~`27477`);
  `/device-history` `17015`; MI inventory-aging bonus gate (`config.max_days`, default 10).

**Endpoints:** `/device-history` `17015`; `/device-cost-recon` `27338`; asset module aging pages under
`commcalc/asset/aging`, `asset/aging-rebate`. **Frontend:** `commcalc/device-history/page.tsx`,
`device-cost-recon/page.tsx`.

**GAP:** `days_in_stock` is snapshot-as-of-file, not computed live; `store` is free text (not joined to
`store_code`) so per-store rollups need resolution. Aging is currently upload-fed (sweep stub, §2). `⚠`

---

## 12. Cash / deposit reconciliation

**Purpose.** Reconcile cash collected at close vs cash actually deposited in the bank, per store per day.

- **Module:** `backend/app/modules/closing/deposit_recon.py`. Helpers:
  `closing_cash_raw_by_store_day(client, org_id, date_from, date_to, store_codes)` `:147`;
  `bank_deposits_by_store_day(...)` `:179`; `cash_for_basis(t_cash, epay_cash, basis)` `:196`;
  `expected_deposit(t_cash, epay_cash, basis, …)` `:211`; `status_for(variance, tolerance=1.0)` `:246`;
  category helpers `load_categories:51`, `category_by_id:82`, `load_adjustment_types:98`,
  `load_other_adjustments:123`, `build_deposit_group:256`, `remaining_short:266`,
  `assemble_category_block:271`.
- **Tables:** `commcalc.bank_deposit` (mig `107_bank_deposit.sql:6` — `close_date, period, store_code,
  amount, category_id`; +mig `502`,`509` add `is_supplemental` and recon columns);
  `commcalc.daily_closing` (mig `029_daily_closing.sql:12` — `store_code, close_date, period, t_cash,
  store_cash, epay_on_cash`, +`daily_closing_verification :42`).
- **Ingest:** deposit slips via closing module upload/OCR (mig `502_closing_dupe_release_deposit_ocr.sql`);
  daily_closing via closing sweep (mig `033_closing_sweep.sql`).
- **Consumed by MI** `cash_deposit` qualifier (§9, `router.py:28895`).

**Endpoints:** closing module lives under `backend/app/modules/closing/` (its own router `⚠ endpoints not
enumerated here — grep `closing/router.py`). Related commcalc: `/x-tender-recon` `router.py:6249`,
closing tender recon mig `103`,`104`,`106`,`111`.

---

## 13. Org hierarchy & store resolution

**Purpose.** Determine which stores a manager may see, and canonicalize a raw store string to a
`store_code`.

- **Org tree tables (mig `050_org_hierarchy.sql`):** `storeops.org_levels(id, org_id, name, rank)` `:22`;
  `storeops.org_units(id, org_id, parent_id, level_id, name, code)` `:33`;
  `storeops.org_managers(unit_id, employee_id, org_id)` `:49`; plus `storeops.stores.org_unit_id` and
  `storeops.employees.org_unit_id`. `org_id` NOT NULL enforced by mig `400_storeops_org_notnull.sql`.
- **Manager → stores RPC:** `storeops.org_span_for_manager(p_org_id, p_employee_id)` `050_org_hierarchy.sql:112`
  — recursive subtree of the manager's units → each unit's `stores.store_code` UNION each pinned
  employee's `home_store`. Sibling RPC `org_store_codes_for_unit`. Python call
  `storeops/router.py:4832`; dedup helper `_span_codes(rows)` `storeops/router.py:4760`. Also called by
  MI resolver (`router.py:28809`).
- **Store-string canonicalization:** `_store_code_resolver(client, org_id)` `router.py:18487` (exact
  address → `store_aliases` → `store_code` → unambiguous leading number). Aliases: mig `023_store_aliases.sql`,
  `219`, `249_commission_store_resolution.sql`; per-org store_code mig `406`. Endpoints `/store-aliases`
  `router.py:14278,14305`, `/store-resolution` `14296`, `/store-unmatched` `14518`, `/stores` `14254`,
  `/markets` `14246`. **Frontend:** `commcalc/store-match/page.tsx`.

---

## 14. Employees & scheduling (does it feed pay?)

**Purpose.** Rep roster, name mapping, and shift hours.

- **Tables (mig `003_storeops.sql`):** `storeops.stores` (`:8` — `store_code, address, market,
  monthly_target`), `storeops.employees` (`:21` — `employee_id, name, home_store, role, pay_rate,
  epay_login, epay_salesperson, org_unit_id`), `storeops.shifts` (`:37` — `employee_id, employee_name,
  store_code, shift_date, start_time, end_time, scheduled_hours, actual_hours, status, is_deleted`),
  `shifts_archive` (`:60`), `schedule_templates` (`:88`), `roles` (`:97`). Shift templates mig `040`;
  timeclock mig `045`,`432`.
- **Rep→StoreOps name map:** `commcalc.name_map` (mig `002:171` — `epay_login, epay_salesperson,
  storeops_name`). Rep aliases mig `016`; endpoints `/rep-aliases` `router.py:21528`, `/rep-employee-map`
  `21242`.
- **Hours in targets:** `_fetch_shifts(client, start, end, org_id)` `router.py:17447` reads
  `storeops.shifts`; consumed by targets scope (`router.py:19238,19473,21354`) and
  `targets_engine.scope_hours_by_day`.

**GAP (verified):** the **commission calc does NOT use shifts** — in `_run_calculation`,
`shifts = fetch('storeops_shifts') if False else []` (`router.py:9490`), i.e. an empty list is passed to
`calc_rep_commissions`. Scheduling feeds **Targets** (hours scoping/projection) but not Boost pay. `⚠`

- **Pay visibility RBAC (mig `434`, 2026-09-01):** `backend/app/modules/storeops/pay_visibility.py` —
  server-side gate over every payroll/workforce money column (owner rule: pay-per-hour / gross pay /
  salary hidden below market manager; per-org config). Mode column `storeops.tenants.pay_visibility`
  (`'manager_up'` default | `'permissioned'` = `employee_pay_rates` data grant, rbac.ts `DATA_GRANTS` |
  `'all'` legacy open) + allow-list `pay_visible_roles TEXT[]` (NULL = admin/master_admin/
  market_manager/market; scope-`'all'` roles always pass). Money keys are DELETED from the payload
  pre-serialization (`strip_pay` — never zeroed, exports can't leak). Endpoints gated (route wrappers;
  the shared compute functions stay ungated for in-process callers): `GET /storeops/payroll`
  (`storeops/router.py:1390`, bare array — flag rides `X-Can-See-Pay-Rates` header),
  `/storeops/payroll-by-store` (`:1685`), `/storeops/payroll/actual-hours-detail` (`:1960`),
  `/storeops/salary-owed` (`:8023`), `GET /hr/compensation` (`hr/router.py:334`),
  `GET /hr/employee-database` (`hr/router.py:1384`); each dict response carries `can_see_pay_rates`.
  The hours-approval board (`payroll_approval.py`) keeps its own STRICTER deny-list (market managers
  hidden too) via the same module. Proof: `backend/harness_pay_visibility.py`.
- **Phase W2 — tiled Payroll & Workforce dashboards + period alignment (owner directive 2026-09-01,
  frontend-only, no new endpoints):**
  - **Two tile hubs** (landings, deliberately NOT in `REPORT_TREES`/`REPORT_DIRECTORY` as new
    entries): `/payroll` (`frontend/src/app/(platform)/payroll/page.tsx` — Payroll Setup / Employee
    Database / Payroll incl. the formerly-orphaned `/storeops/payroll-change-log` +
    `/storeops/salary-advances` / HR Total Comp) and `/storeops`
    (`storeops/page.tsx` rebuilt — Schedule / Shift Approvals / Attendance / Employees / Reports /
    Store Setup / Employee Setup tiles + StatTile KPI row with best-effort pending counts). NAV
    (`rbac.ts`): `/payroll` added first in 'Payroll & HR' (module `storeops`, `['all','market']`),
    `/storeops` relabeled 'Workforce Dashboard'; NO nav item deleted (longest-prefix gating).
  - **`/storeops/admin` split:** `/storeops/setup/stores` + `/storeops/setup/employees` (mechanical
    extraction of the two tab branches; shared helpers `storeops/setup/lib.tsx`); the combined page
    survives with a banner. Both new routes in NAV, module `storeops`, `['all','market']`.
  - **Rename:** the `/storeops/accountability` page label/h1/export title is now **"Lateness %"**
    (route + module unchanged).
  - **Period coherence — ONE shared resolver** (`frontend/src/lib/pay-period.ts`, PROMOTED from
    `storeops/lib/pay-period.ts` which now re-exports it; server authority stays
    `GET /core/tenant-settings` `preview[0]` ← `core/router.pay_period_for`): `/storeops/payroll`
    (already), `/storeops/payroll-tax` (default was rolling last-7-days → now the current pay
    period, From/To still override), `/hr/payroll-expenses` (default month = the calendar month of
    the current period's START; the `{month}` backend contract is a DOCUMENTED SEAM, not rewritten),
    `/storeops/schedule` (week grid anchored to the week containing the current period's start), and
    `/storeops/payroll/approvals` (server default stays the PREVIOUS complete period — deliberately,
    `payroll_approval.previous_pay_period` — now made explicit by a cycle chip computed client-side
    as `stepPeriod(current, settings, -1)` naming BOTH periods).
  - **Standard filters/exports** added to `/storeops/timeoff`, `/storeops/swaps`,
    `/storeops/shift-extensions`, `/storeops/timeclock-permissions`, `/storeops/employees`
    (`StandardFilterBar` + visible-rows exports; `/storeops/team` already had both via
    `TeamSnapshot`). `/storeops/payroll` now DROPS pay columns/tiles when the server stripped pay
    (mig 434 `strip_pay` deletes the keys; the bare-array route's header isn't visible to `api()`,
    so absence-of-keys is the detection).
  - **W2.1 — collapsed master tiles + sidebar cleanup (owner feedback 2026-09-01, frontend-only):**
    both hubs now render each master tile COLLAPSED via the shared
    `frontend/src/components/HubTiles.tsx` — one card per tile (icon + title + one-line desc + a
    subtle page count); interior links are hidden until the tile is clicked, then expand in place
    (independent per-tile `useState`, chevron rotates, `<button>` header with `aria-expanded`;
    nothing persisted). Single-link tiles (Employee Database, HR Total Comp, Store Setup, Employee
    Setup) are plain `<Link>`s that navigate directly. Coverage additions so the menu could be
    cleaned: NEW **"Store Ops"** tile on `/storeops` (Store Visits `/storeops/visits`, Visit
    Checklist `/storeops/visits/settings`, Google Reviews `/storeops/reviews`, Reviews Setup
    `/storeops/reviews/config`) and **HR Communications** `/hr/letters` added to the `/payroll`
    Payroll Setup tile (`/storeops/staffing` was already on the Schedule tile). Sidebar: new
    OPTIONAL `NavItem.tileOnly` flag (`rbac.ts`) on every 'Workforce' / 'Payroll & HR' item a hub
    tile covers — the two groups collapse to essentially just their Dashboard links.
    `tileOnly` is DISPLAY-ONLY and filtered at RENDER time in `(platform)/layout.tsx`: the items
    stay in `NAV`, so `canSeeItem`/`navModuleForPath`/`canAccessPath` gating, ⌘K search,
    active-group detection and the `REPORT_DIRECTORY` duplicates are untouched, and the renderer
    deliberately keeps tileOnly items visible inside the 'Reports · …' directory categories (same
    item objects). A tenant `/admin/menu` layout cannot un-hide a tileOnly item (the designer never
    persists `hidden:false`, only `hidden:true`/absent — no "show" flag exists to mirror);
    layout moves/subs of such items simply stay hidden, and a group left with zero visible items
    renders nothing. `/storeops/admin` (combined, backward-compat alias) is tileOnly WITHOUT a
    tile on purpose — its two surfaces ARE the Store Setup / Employee Setup tiles; bookmarks and
    ⌘K search still reach it.
- **Phase W3 — scheduled email/WhatsApp exports for the workforce surfaces (owner queue, backend,
  2026-09-01):** the six payroll/workforce reports are registered in the notify report registry
  (`backend/app/modules/notify/report_registry.py` — the server twin of each page's export, shared
  by on-demand `POST /notify/send` and the pg_cron scheduler `POST /notify/run-due`), so they get
  the platform's STANDARD scheduled sends (charter rule 3 — never a bespoke exporter). Entries live
  in `backend/app/modules/notify/workforce_reports.py` (`WORKFORCE_REPORTS`, spliced into
  `REPORTS`; lazy app imports — offline-provable):
  | report key | label | data path (existing handler, in-process) | pay posture |
  |---|---|---|---|
  | `storeops_payroll` | Payroll (Hours & Pay) | `storeops.router.get_payroll` | mig-434 gate = the live route's exact `can_see_pay`→`strip_pay` pair; pay COLUMNS drop with it |
  | `storeops_hours_approval` | Hours Approval | `payroll_approval.list_approvals` | HOURS-ONLY: pay stripped unconditionally, no pay column exists |
  | `storeops_payroll_tax` | Payroll with Tax (Estimate) | `storeops.router.payroll_raw` + `storeops/payroll_tax_estimate.py` | ALL-money: gate denial ⇒ ValueError, fail closed (⚠ stricter than the live `/payroll-raw`, which is UNGATED today — see §19.12) |
  | `storeops_payroll_expenses` | Payroll Expenses | `storeops.router.get_payroll_expenses` (`{YYYY-MM}`) | ALL-money: gate denial ⇒ ValueError, fail closed |
  | `storeops_attendance` | Attendance Exceptions | `storeops.router.attendance_exceptions` | hours-only |
  | `storeops_lateness` | Lateness % | `storeops.router.accountability` | hours-only |
  - **Period coherence (charter rule 2):** `workforce_reports._pay_period_range` DELEGATES to
    `core.router.pay_period_for` over `payroll_approval._pay_settings` (never a copy) — default =
    the tenant's CURRENT pay period on the schedule's business day (`wants_tz`); `period: last`
    steps one period back; explicit `start`/`end` override. Hours Approval passes blank dates
    through so `payroll_approval._resolve_period`'s own previous-COMPLETE-period default stays
    authoritative. Payroll Expenses' `{YYYY-MM}` seam: month of the current period's START (W2).
  - **`storeops/payroll_tax_estimate.py`** — the PYTHON TWIN of `frontend/src/lib/payroll-tax.ts`
    (the page's spec keeps withholding math in the browser; a scheduled send has none). Keep in
    lockstep — same cross-language-twin convention as `cell-safety.ts` ⇄ `notify/render.py`.
  - **Saved-filter validation:** `report_filters.validate_workforce_period` (registered for all six
    in `FILTER_VALIDATORS`) — a bad saved schedule surfaces as a `report_config` failure-log lead,
    not a sweep crash. All six entries are `wants_auth` (span/pay gates ride the caller's header;
    a scheduled run's `""` = the org-wide, fail-closed path — AGENT_CONTRACT §3c).
  - **Proof:** `backend/harness_workforce_report_registry.py` (stdlib-only; entry shape, registry
    splice/key-uniqueness by AST, resolver delegation, end-to-end builders with the REAL
    `strip_pay`, tax-twin vectors, validator).
- **Dashboard-builder Phase D1 — user-designed TILE LAYOUTS, backend (owner spec 2026-09-01):**
  every module's tiled dashboard layout becomes per-org CONFIG (RULE TWO), not code. SUPER ADMIN
  designs for all modules and ANY tenant; a layout saved on the HOUSE org
  (`00000000-0000-0000-0000-000000000001`) is the PLATFORM DEFAULT all tenants (and future modules)
  inherit; TENANT ADMINS override for their own tenant only; every write permission-gated.
  - **Storage (NO new migration):** one JSON row per (org, module) in `commcalc.ui_label_override`
    (mig `068`) under `scope='tiles'`, `key=<module>`, JSON `{version:1, tiles:[{title, icon?,
    desc?, items:[{href, icon?, label?, desc?}]}]}` serialized into `label` — the same multiplexing
    precedent as the sidebar designer's `scope='layout'` row. Display config, not a feed → NO
    lineage-registry/seed entry.
  - **Module:** `backend/app/modules/commcalc/tile_layout.py` — PURE `sanitize_tile_layout` (caps
    40 tiles / 60 items-tile / 400 total, trims/clamps, drops malformed items, internal-`/`-href
    allow-list, ValueError on garbage), `resolve_tile_layout` (tenant > house > None; a malformed
    row DEGRADES a layer, never raises — `training.resolve_tours` precedence),
    `tile_write_gate`/`tile_write_org` (the write-permission truth table; the BODY never decides
    the org — `training._write_org` pattern), + thin org-scoped loaders `load_tile_layout` (both
    org rows in ONE query) / `save_tile_layout` (None/empty = DELETE = revert to inheritance).
  - **Endpoints** (`commcalc/router.py`, beside the nav-config block): `GET /commcalc/tile-layout
    ?module=` → `{module, layout|null, resolved_from:'tenant'|'house'|null}` (read is
    authenticated-org-scoped like `/nav-config`; middleware pins normal users, super-admin may pass
    `org_id`); `PUT /commcalc/tile-layout` body `{module, layout|null, target:'tenant'|'house'}` —
    FAIL-CLOSED 401/503/403 ladder (`_menu_gate_caller`, `_require_import_admin` posture):
    `target='house'` or a foreign `org_id` → super-admin only; own tenant → super-admin OR the
    registered `menu_layout` settings area.
  - **SECURITY RETROFIT (same change):** `POST /commcalc/nav-labels` and `POST /commcalc/nav-layout`
    shipped with NO auth gate at all — both now require the fail-closed `menu_layout` gate
    (`_require_menu_layout_admin`) and pin a non-super caller to their OWN org; request bodies
    unchanged (`/admin/menu` + `/admin/labels` already send the standard auth headers via `api()`).
  - **Permission registry:** new `SETTING_AREAS` key `menu_layout` ("Menu & dashboard layout
    designer") in `core/router.py` — grantable per-role in the Roles UI. (The older `menu` area was
    never gated on by any endpoint; left in place for existing role rows.)
  - **Proof:** `backend/harness_tile_layout.py` (stdlib-only; sanitizer, resolve order, save
    semantics, gate truth table). Frontend consumption (designer UI + hubs reading the resolved
    layout) is Phase D2 — see §19.11.
- **Dashboard-builder Phase D2 — tiled hubs + drag-and-drop designer, frontend (owner spec
  2026-09-01):** every module group now HAS a tiled dashboard, and the tile layout D1 stores is
  live (no longer inert — §19.11).
  - **Pure resolver `frontend/src/lib/tile-hubs.ts`:** `slugGroup(name)` (group name → URL slug ==
    the backend tile-layout `module` key, e.g. 'Daily Closing'→'daily-closing'; collision-free over
    NAV, alphabet ⊂ the backend key regex) · `defaultHubGroups(group, items, subs?)` — the
    deterministic zero-config tiling (tenant `/admin/menu` sub-categories become master tiles when
    present, else natural-order chunks: ≤10 items → one '<Group> pages' tile, >10 → 2–4 balanced
    tiles titled by each chunk's first item) · `layoutToHubGroups`/`hubGroupsToLayout` (API-layout ⇄
    `HubTiles` converters; unknown/RBAC-invisible hrefs drop on render, `keepUnknown` preserves
    them in the designer) · `mergeUnplacedItems` (the NEWLY-SHIPPED-PAGE INVARIANT: any visible NAV
    item a saved layout does not name appends to a trailing 'More' tile — a design never freezes a
    module) · `subsFromNavLayout` (per-group sub extraction from the tenant nav layout). PROOF:
    `frontend/scratchpad/prove_tile_hubs.mjs` (verbatim re-impl + real-NAV parse; determinism,
    chunk balance, slug validity/collisions, round-trip, merge invariant, NAV-conversion shape).
  - **Generic hub route `(platform)/hub/[group]/page.tsx`:** `/hub/<slug>` renders ANY nav group as
    a tiled dashboard: `GET /commcalc/tile-layout?module=<slug>` (apiCached CONFIG) → designed
    layout, else `defaultHubGroups` fallback; interior links pass the SAME sidebar predicates
    (canSeeItem + tenant cap + active-carrier + nav-layout `hidden`), provenance chip, friendly
    unknown-slug notice. `/payroll` + `/storeops` deliberately keep their curated pages + KPI rows.
  - **NAV conversion (`rbac.ts`):** 16 groups gained a `/hub/<slug>` '<Group> Dashboard' entry at
    the top (module = the group's module; scopes = the BROADEST tier of the group's items, omitted
    when any item is unrestricted) with every other item `tileOnly` (W2.1 render-skip semantics —
    gating/search/Reports-directory untouched): Point of Sale · CRM · Referral · Vision ·
    Incentives · Incentive Payout Plans · Targets & Coaching · Finance · Assets · Distributors ·
    Daily Closing · Integrations & Imports · Mapping · Notify · Helpdesk · Support. SKIPPED:
    Configuration (admin pages, untouched), single-item groups (Approvals, Chat, Reports),
    Workforce + Payroll & HR (already converted, W2), and the 'Reports · …' directory mechanics.
    New rbac helpers: `Permissions.settings` + `canEditSettingArea()` (client mirror of core
    `_can_edit_setting` — affordance only, server-gated regardless).
  - **Designer `(platform)/admin/dashboards/page.tsx`** (NAV: Configuration → 'Dashboard
    Designer', module 'admin'): left panel = the group's pages, right canvas = master tiles;
    hand-rolled HTML5 DnD (admin/menu + crm/pipeline idiom, ONE drag union `page|tile|item`, NO
    library) with keyboard fallbacks (▲▼, add-to-tile select); inline tile title/emoji-icon/desc
    editing; live `HubTiles` preview; module/group picker + (platform super admins,
    `TenantMembership.super_admin`) tenant picker fed by `GET /core/tenants` with a 'Platform
    default' pseudo-entry + provenance chip. Saves `PUT /commcalc/tile-layout`
    (`target='tenant'|'house'`, `?org_id=` for foreign tenants); Revert-to-inherited sends
    `layout:null`; 403s surface as a friendly menu-layout-grant message. Page opens for super
    admins OR `canEditSettingArea('menu_layout')`; the backend gate (§14 D1) is authoritative.
  - **Backend:** NOTHING new — D2 consumes the D1 endpoints as shipped (§17 unchanged).

---

## 15. Other commission subsystems (pointers)

| Subsystem | Tables / migs | Key funcs / endpoints |
|-----------|---------------|-----------------------|
| **MA (master-agent) commission** | `raw_ma_commission`, mig `254_ma_product_class`, `251_ledger_ma_sync`, `265_ma_class_money_wiring`, `268_ma_overview_recon` | `ma_product_class.py`, `ma_class_wiring.py`, `ma_upload.py`; `/ma-commission/summary` `router.py:25009`, `/ma-overview-recon*` `25224-25450`, `/ma-handset-cogs` `26851`, `/ma-product-class*` `4868-5151` |
| **B2B ↔ MA activation recon (Pay Discrepancy, MA source)** | `discrepancy_results` (`source='ma'`), `ma_payment_rule` — mig `312_ma_payment_rules_and_discrepancy_attribution` | `ma_recon.py` (pure: `build_sold_index`/`build_paid_index`/`match_rules`/`reconcile_ma_activations`; reuses mig-308 `_gate_met_ma_tx` + the two-hop link); ran by `POST /discrepancy/run` `router.py:19056` for plan-mode orgs; rules CRUD `/ma-payment-rules*` `19200-19270`; proof `harness_ma_recon.py`. Sold-but-unpaid → status `open` + literal `'no business rule configured'`, or rule-attributed `info`/`lagged` |
| **Carrier statement commission** | mig `065_carrier_commission.sql` → `rep_commissions.carrier_statement_comm` | `/carrier-comm-file/extract` `6216`, `/commission-received-breakout` `15488` |
| **Commission plans (rule engine)** | mig `059_commission_plans.sql`, `066`,`067`,`232`,`260`,`262` | `commission_engine.py`; `/commission-plans*` `12557-14246` (coverage, pay-gate, exclusions, bulk-assign) |
| **Commission ledger (income tracking)** | mig `071_commission_ledger.sql` | `/commission-ledger/*` `3997-4602` |
| **VIP / PayGo** | mig `008`,`011`,`014` | `vip_sweep.py`; `/vip/*` `2421-3078`, `/vip/paygo/*` `8336-8365` |
| **epay** | mig `020`,`025` | `epay_sweep.py`; `/epay/*` `8730-8811`, `/tax-collected` `2106` |
| **Chargebacks** | mig `036`,`037`,`504` | `/chargeback-review*` `7063-7295`, `/chargebacks/{period}` `15675` |
| **Expenses** | mig `024`,`205`,`206`,`506` | `/expenses/{period}` `21604-21951` |
| **Flags** | `commcalc.flags` (mig `002:148`,`285-287`) | `flags.py`, `flag_store_resolver.py`; `/flags/{period}` `10299`, `/flag-rules` `7697` |
| **Agency (inter-dealer)** | mig `220-222` | `agency.py`; `/agency/*` `26021-26259` |
| **Financing** | mig `272`,`273` | `financing_tiers.py`; `/financing/*` `28058-28313` |
| **Accrual / payout ledger** | mig `267_daily_accrual_payout_ledger.sql`, `071` | `payout_accrual.py`; `/payout/*` `27772-28015` |
| **Setup-fee pay** | mig `217`,`263` | `setup_fee_pay.py`; `/setup-fee/*` `13011-13172` |
| **Custom reports** | mig `099`,`211` | `/custom-report*` `25771-25962` |
| **Expected commission** | mig `258` | `expected_commission.py`; `/expected-commission/*` `11397-11579` |
| **IMEI rebates** | mig `216` (aging) | `imei_rebate_report.py`; `/imei-rebates` `26448` |
| **ATU opportunity** | mig `295` | `atu_opportunity.py`; `/atu-opportunity` `28410` |

---

## 16. Cross-reference: by TABLE

| Table | Written by | Read by |
|-------|-----------|---------|
| `commcalc.raw_sales` | upload `/upload-mapped` `3637`, sweeps, `sales/promote-feed` `22757` | `calc_rep_commissions`, `calc_gp_report`, `_compute_feed_actuals_py` `18678`, `_sales_cell_agg`, `_mi_resolve_numbers` edge/vhi `28843`, installment engines |
| `commcalc.daily_sales_feed` | B2B/email sweeps, upload | `_compute_feed_actuals_py` (primary source), sales report, fallback in calc |
| `commcalc.raw_payment_detail` | epay sweep, upload | `calc_gp_report`, reimbursement categorization |
| `commcalc.raw_mi` | upload / MI sweep | carrier residual gate `installment_engine.compute_installments`, sale-installment gate, MI/ATU |
| `commcalc.raw_dlar_store` | `dlar_sweep.run_dlar_sweep:209` (replace), upload | `get_dlar_store_kpis` `10279`, `_cr_resolve_kpi_metrics` `25656`, MI tmr3 `28884` |
| `commcalc.raw_dlar_rep` | `dlar_sweep` (replace), upload | rep KPI, comp trend `15238` |
| `commcalc.raw_catalog` | upload `/product-mrc/import` region, catalog | GP, device COGS, installment MRC |
| `commcalc.payout_config` | `/config/{period}` `10474`, `/commission-settings` `10517` | `calc_rep_commissions` (spiffs/tiers), installment base rates |
| `commcalc.rep_commissions` | `_run_calculation`/`_apply_new_engines` `9183` | `/commissions/{period}` `10222`, GP report, commission-by-store, statements, MI (indirect) |
| `commcalc.store_kpis` | KPI ingest/snapshot | tiers, exec |
| `commcalc.carrier_kpi_metric` | `/carrier-kpi-metrics` POST `19773` | KPI/tier config resolution |
| `commcalc.flags` | calc + flag rules | `/flags/{period}` `10299`, `_cr_resolve_flags` |
| `commcalc.store_expenses` | `/expenses/{period}` PUT `21695` | GP report, `_cr_resolve_store_expenses` |
| `commcalc.sale_installment_ledger` | `compute_sale_installments(persist=True)` `9212` (mig `308` adds `order_number`/`account_id` MA TX provenance, adaptive write) | `/plan-installments/*` previews, `installment_comm_sale` |
| `commcalc.raw_ma_daily_tx` | upload `/upload/ma_daily_tx`, VidaPay sweep, `report_pull` | bill-pay recon processor side (`_billpay_processor_by_store`), residual/ATU (`residual_subs`), Commission Ledger, **installment engine mig `308`** (`sale_installment_engine._read_ma_tx` → `'ma_tx'` gate + `'ma_tx_activation'` MRC; money column `retail_cost` ONLY — `merchant_invoice` is an identifier), **P&L mig `309`** (`account/coa.build_inputs` via `residual_subs.ma_tx_pnl_bookings`: `merchant_discount` → "Merchant discount" line (or legacy `atu_income` fold per `pl_merchant_discount_own_line`), −`retail_cost` → `mi_income` for the `'%residual%'` ∪ `pl_ma_residual_order_types` union, each row once) |
| `commcalc.raw_ma_commission` | upload `/upload/ma_commission`, VidaPay sweep | MA overview/recon, installment MA gate (`_read_ma_commission` spiffs), **mig `308` two-hop link** (`build_ma_link_index`: `imei|sim → activation_order`) |
| `commcalc.payout_schedule(+_line)` | `/payout-schedule` POST `11965` | `installment_engine.compute_installments` |
| `commcalc.inventory_aging_device` | `b2b_sweep.py:341` upsert | `/device-history` `17015`, `/device-cost-recon` `27338`, MI aging bonus |
| `commcalc.bank_deposit` | closing deposit OCR/upload | `deposit_recon.bank_deposits_by_store_day:179`, MI cash gate |
| `commcalc.daily_closing` | closing sweep `033` | `deposit_recon.closing_cash_raw_by_store_day:147`, MI cash gate |
| `commcalc.name_map` | name-map UI | `calc_rep_commissions` (login→storeops name), rep-employee-map |
| `commcalc.management_incentive_*` | `/management-incentive/plans` `28534`, `/compute` `28613` | MI engine, payouts, resolve |
| `commcalc.discrepancy_results` | Boost engine `discrepancy_engine.run_discrepancy` (`source='boost'`/NULL) + MA recon `ma_recon.run_ma_discrepancy` (`source='ma'`, `comp_type='MA_ACTIVATION'`) — each delete-then-inserts ONLY its own `(org, period, source)` slice; canonical DDL + attribution columns (`rule_id/rule_key/rule_reason/evidence/source/order_number`) in mig `312` (table pre-dates migrations, console-created) | `GET /discrepancy/{period}` `router.py:19099` (selects `*`, optional `source` filter), Pay Discrepancy page |
| `commcalc.ma_payment_rule` | `/ma-payment-rules` POST/PATCH/DELETE `router.py:19214-19270` (upsert by `org_id,rule_key`; mig `312`) | `ma_recon.load_rules` → `match_rules` (first match by ascending priority; case/trim-insensitive; `effective_from/to` windows; bad regex skipped) |
| `commcalc.ui_label_override` (mig `068` — one table, scope-multiplexed DISPLAY config) | `POST /nav-labels` (scopes `nav`/`group`/`cap`), `POST /nav-layout` (scope `layout`, key `__nav__`) — both now gated on the `menu_layout` settings area; `PUT /tile-layout` (scope `tiles`, key `<module>`, tenant row or HOUSE platform-default row per `tile_layout.tile_write_gate`) | `GET /nav-config` (caller org only, no house inheritance — sidebar), `GET /tile-layout` (`tile_layout.load_tile_layout`: tenant ∪ HOUSE in one query, tenant wins) |
| `storeops.org_units/levels/managers` | org-hierarchy UI (storeops) | `org_span_for_manager` RPC → RBAC span, MI store set |
| `storeops.shifts` | scheduling UI (storeops) | `_fetch_shifts:17447` → Targets only (NOT pay); W3 scheduled workforce reports (via the storeops payroll/attendance handlers, §14 W3) |
| `storeops.employees` / `stores` | storeops roster | calc, targets, resolution |
| `storeops.timelog` / `manual_hours` / `payroll_settings` / `payroll_approval` (migs `045`,`431`) | timeclock, manual-hours UI, W-4 form, approvals board | payroll/payroll-raw/approvals handlers — now ALSO reached in-process by the W3 scheduled workforce reports (`notify/workforce_reports.py`, §14 W3); no second query path |

---

## 17. Cross-reference: by ENDPOINT (high-value)

| Endpoint | Handler line | Section |
|----------|-------------|---------|
| `POST /calculate/{period}` | `router.py:8968` | §6 rep commission |
| `GET /commissions/{period}` | `10222` | §6 |
| `GET /sales-report` | `15792` | §3 |
| `GET /gp/{period}` | `14750` | §4 |
| `GET/PUT /targets/{period}` | `19005/19071` | §5 |
| `GET /targets/{period}/summary` | `19440` | §5 |
| `GET /targets/{period}/action-plan` | `21334` | §5 |
| `GET /dlar-store/{period}` | `10278` | §10 |
| `GET /carrier-kpi-metrics` | `19757` | §10 |
| `GET /exec-overview/{period}` | `20103` | §10 |
| `GET /device-history` | `17015` | §11 |
| `GET /device-cost-recon` | `27338` | §11 |
| `GET /plan-installments` | `10726` | §8 |
| `GET /payout-schedule` | `11948` | §7 |
| `GET/POST /management-incentive/plans` | `28527/28534` | §9 |
| `POST /management-incentive/compute` | `28613` | §9 |
| `POST /management-incentive/resolve` | `28912` | §9 |
| `GET /management-incentive/payouts` | `28668` | §9 |
| `POST /upload/{file_type}` | `844` | §2 |
| `POST /upload-mapped` | `3637` | §2 |
| `GET /store-resolution` | `14296` | §13 |
| `GET /flags/{period}` | `10299` | §15 |
| `GET /storeops/payroll` (pay-gated, mig 434 — money keys stripped for callers below market manager; `X-Can-See-Pay-Rates` header) | `storeops/router.py:1390` | §14 |
| `GET /storeops/payroll-by-store` (pay-gated: `amount` stripped, `hours` kept) | `storeops/router.py:1685` | §14 |
| `GET /storeops/payroll/actual-hours-detail` (pay-gated: `pay_rate`/salary $ stripped, hours kept) | `storeops/router.py:1960` | §14 |
| `GET /storeops/salary-owed` (pay-gated: `owed_total`/`cash_paid_total`/`balance` + per-day `rate`/`owed` stripped) | `storeops/router.py:8023` | §14 |
| `GET /hr/compensation` (pay-gated: `pay_rate`/`base_salary`/`total_comp`/`annualized` stripped; commission stays — commcalc's own gate domain) | `hr/router.py:334` | §14 |
| `GET /hr/employee-database` (pay-gated forward guard: pay-classified keys stripped from field registry + rows) | `hr/router.py:1384` | §14 |
| `POST /discrepancy/run` (Boost + MA engines, best-effort each) | `19056` | §15 B2B ↔ MA recon |
| `GET /discrepancy/{period}` (`?source=boost\|ma`) | `19099` | §15 |
| `GET/POST /ma-payment-rules`, `PATCH/DELETE /ma-payment-rules/{rule_id}` | `19200-19270` | §15 B2B ↔ MA recon |
| `GET /tile-layout` (`?module=` — resolved tenant>house tile layout, dashboard-builder D1) | `commcalc/router.py` (`get_tile_layout`, beside nav-config) | §14 D1 |
| `PUT /tile-layout` (fail-closed: house/foreign → super-admin; own org → `menu_layout` grant) | `commcalc/router.py` (`put_tile_layout`) | §14 D1 |
| `POST /nav-labels`, `POST /nav-layout` (RETROFIT 2026-09-01: were UNGATED — now fail-closed `menu_layout` gate, non-super pinned to own org) | `commcalc/router.py` (`set_nav_label`/`set_nav_layout`) | §14 D1 |
| `POST /notify/send`, `POST /notify/run-due` → report keys `storeops_payroll` / `storeops_hours_approval` / `storeops_payroll_tax` / `storeops_payroll_expenses` / `storeops_attendance` / `storeops_lateness` (W3 scheduled workforce reports) | `notify/router.py` `_dispatch` → `report_registry.build_payload` → `notify/workforce_reports.py` builders | §14 W3 |
| `GET /storeops/payroll-raw` (payroll-tax page inputs; ⚠ UNGATED for pay — §19.12) | `storeops/router.py:6288` | §14 W3 |
| `GET /storeops/payroll-expenses/{period}`, `GET /storeops/payroll/approvals`, `GET /storeops/timeclock/attendance-exceptions`, `GET /storeops/accountability` | `storeops/router.py:7703` / `payroll_approval.py:469` / `storeops/router.py:4294` / `:4318` | §14 W3 |

(Full 468-endpoint list: `grep -nE '@router\.(get|post|put|patch|delete)\(' backend/app/modules/commcalc/router.py`.)

---

## 18. Cross-reference: by METRIC / KPI

| Metric | Source table.column | Reader function |
|--------|--------------------|-----------------|
| Activation counts (premium/byod/upgrade) | `raw_sales.contract_type` | `classify_contract_type` `calculator.py:40`; display via `_sales_cell_agg` `router.py:17842` |
| Accessory $ ("acc_gp") | `raw_sales.ext_price` (+ device set-up fee; NOT gp, NOT Ondigo) | `_compute_feed_actuals_py` `router.py:18678` |
| Edge count | `raw_sales` product tokens | `_mi_classify_sales_row` via `_mi_resolve_numbers` `router.py:28843` (MI only; folded into premium in rep pay) |
| VHI/FIOS / home-internet count | `raw_sales` product tokens / `installment_category.py:82` | `_mi_resolve_numbers` `28843`; `installment_category` (plan-mode, runtime-only) |
| ATU % | `raw_dlar_store.atu` / `raw_dlar_rep.atu_pct` / `store_kpis.atu_pct` | `_cr_resolve_kpi_metrics` `25656`; MI ATU RPC mig `032` |
| Protect % | `raw_dlar_store.protect_pct` | `_cr_resolve_kpi_metrics` `25656` |
| BYOD % | `raw_dlar_store.byod_pct` | `_cr_resolve_kpi_metrics` `25656` |
| Family-plan % | `raw_dlar_store.family_plan_pct` | `_cr_resolve_kpi_metrics` `25656` |
| TMR3 (3-month retention) | `raw_dlar_store.tmr3` | `_cr_resolve_kpi_metrics` `25656`; MI gate `_mi_resolve_numbers` `28884` |
| Conversion / AAL conversion | `raw_dlar_store.conversion_rate` / `aal_conversion` | `_cr_resolve_kpi_metrics` `25656`; mig `013` |
| Port % | `raw_dlar_store.port_pct` / `store_kpis.port_pct` | store KPI reader `10279` |
| PSA projected | `raw_dlar_store.psa_projected` | store KPI reader |
| MI payout / ATU payout | `raw_mi.actual_mi_payout` / `actual_atu_payout` | installment gate; MI ATU RPC |
| MA-TX MRC (M1 activation) | `raw_ma_daily_tx.retail_cost` on the `order_type='Activation Order'` row (config: `ma_tx_activation_order_type`) | `sale_installment_engine.ma_tx_mrc_for` via the two-hop serial→`activation_order`→`order_number` join (mig `308`; `mrc_source='ma_tx_activation'`) |
| MA-TX month-n paid evidence | `raw_ma_daily_tx.retail_cost` net of the `'MONTH n'`-worded rows (`product_name` via `commission_ledger.parse_payment_month`) | `sale_installment_engine.ma_tx_month_evidence` / `_gate_met_ma_tx` — UNION with `raw_ma_commission.spiff_m{n}` (n ≤ 6); direction `ma_payout_sign`, floor `ma_min_amount`, horizon `ma_max_month` ≤ 16 (mig `308`) |
| MA merchant discount (P&L "Merchant discount") | `raw_ma_daily_tx.merchant_discount` (+, dealer income) | `account/coa.build_inputs` via `residual_subs.ma_tx_pnl_bookings` (mig `309`); per-org toggle `commission_org_config.pl_merchant_discount_own_line` — `false` = legacy `atu_income` fold, byte-identical dollars |
| MA residual (P&L "MI residual income") | `raw_ma_daily_tx.retail_cost` sign-flipped (negative = paid to dealer) on rows in the `'%residual%'` product family ∪ `pl_ma_residual_order_types` order types (default `Postpaid Residual Order`) | `residual_subs.ma_residual_row_matcher` → `coa.build_inputs` (mig `309`; union dedup — each row books once) |
| B2B sold vs MA paid (activation discrepancy) | sold: `SALES_DISPLAY_SOURCES` rows with non-blank `contract_type` (no swap/void), keyed on digit-normalized `serial_1`; paid: `raw_ma_commission.spiff_m1`+`rebate`/`device_margin` ∪ `raw_ma_daily_tx` month-1 / activation-order evidence (two-hop join, +1-month lookahead) | `ma_recon.reconcile_ma_activations` via `sale_installment_engine._gate_met_ma_tx` (mig `312`); unpaid rows → `discrepancy_results` `source='ma'` with rule attribution or `'no business rule configured'` |
| Cash-deposit variance | `daily_closing.t_cash` − `bank_deposit.amount` | `deposit_recon` `:147/:179`; MI gate `28895` |
| Days-in-stock (aging) | `inventory_aging_device.days_in_stock` (snapshot) | device-cost recon `27338`; MI aging bonus |
| Lateness % (`late_rate` — late shifts ÷ scheduled shifts) | `storeops.timelog` punches vs `storeops.shifts` windows | `attendance_exceptions.compute_attendance_exceptions` → `accountability.aggregate`; surfaced by `/storeops/accountability` ('Lateness %' page, W2 rename) and the `storeops_lateness` scheduled report (§14 W3) |
| Withholding estimate (gross/FICA/federal/state/net) | `storeops.timelog`+`manual_hours` hours × `employees.pay_rate` × `payroll_settings` W-4 | browser: `frontend/src/lib/payroll-tax.ts computePay`; server twin: `storeops/payroll_tax_estimate.compute_pay` (§14 W3 — keep in lockstep) |

---

## 19. Known gaps & inert config

1. **Named `payout_schedule.activation_type` variants are STORED-BUT-INERT.** `installment_engine.
   _resolve_schedule` forces `activation_type='*'` (`installment_engine.py:120,194,267`; mig `078:34-36`).
   Edge/fios/upgrade_edge/twp_protect curves never compute today.
2. ~~Carrier residual installments cap at month_index ≤ min(3, num_months)~~ **FIXED/STALE** (2026-09-01
   verification): `installment_engine.py:197` clamps at `min(12, num_months)` — schedules pay their full
   declared horizon. Only mig `078:31`'s header text still describes the old min(3) behaviour.
3. **Rep pay (Boost) has no distinct Edge or VHI/FIOS count** — both fold into `premium_acts`
   (`calculator.py`, §6). Only MI breaks them out, by re-scanning the same sales universe
   (`_mi_resolve_numbers` `28843`). If Boost pay ever needs these split, the calc must change.
4. **Plan-mode `sale_installment_ledger` has NO category column** (mig `201:81`) — `home_internet`/edge
   category counts are runtime-only (`category_guard.by_category`, not persisted), so they can't be queried
   historically.
5. **Scheduling does NOT feed commission pay.** `_run_calculation` passes `shifts = []`
   (`router.py:9490`). Shifts feed Targets only. A store's hours never affect Boost payout.
6. **Display vs pay divergence risk.** The shared `_sales_cell_agg` (§3) is DISPLAY ONLY; the commission
   CALC deliberately re-derives counts inside `calc_rep_commissions`. They share `classify_contract_type`,
   `VOID_TOKENS`, and the Return/admin skips, but are separate code paths — the classic place two surfaces
   drift (owner directives 2026-07-16/25 exist precisely because they did).
7. **Inventory aging is a SNAPSHOT, not history** — `inventory_aging_device` is upserted one-row-per
   `(org, imei)` (mig `216:39`); `days_in_stock` is as-of-file; `store` is FREE TEXT not `store_code`
   (mig `216:22`). Live b2bsoft scrape is a **stub** (`b2b_sweep.py:77`), so aging arrives by upload.
8. **DEPRECATED SQL RPC `daily_sales_feed_actuals`** (mig `048`) hardcodes Ondigo/gp and a rigid label
   list — superseded by `_compute_feed_actuals_py`. Do not build on it.
9. **`raw_dlar_store` / `raw_dlar_rep` columns accreted across migrations** (`002`→`012`→`031`). Base mig
   `002` lists only a subset; readers select later-added columns (`atu, conversion_rate, gross_adds,
   total_upgrades, location`). Confirm a column exists in the applied schema before relying on it.
10. **MI gates fail CLOSED** (`management_incentive.py:117`): any qualifier whose value can't be measured
    (`None`) does NOT pass. An unresolved cash/tmr3 read silently blocks the consolidated bonus unless
    entered manually. Check `_mi_resolve_numbers` `unresolved` list.
11. ~~Tile-layout rows are STORED-BUT-INERT until Phase D2~~ **RESOLVED by D2 (2026-09-01, same
    day):** the generic hub route `(platform)/hub/[group]/page.tsx` reads the resolved layout for
    every converted group and the designer `(platform)/admin/dashboards` writes it (§14 D2). STILL
    TRUE, by design: the curated `/payroll` and `/storeops` hubs render their hardcoded tile arrays
    (owner-specced taxonomy + KPI rows) — a layout saved for 'Workforce' / 'Payroll & HR' affects
    only their generic `/hub/workforce` / `/hub/payroll-hr` pages, which the sidebar does not link.
    Also still open — the STALE external claim: `backend/app/data/support_docs_seed.json`
    (menu/labels help docs) still describes `POST /nav-labels` / `POST /nav-layout` as open
    admin-page saves — since the D1 retrofit both require the `menu_layout` settings grant (a save
    without it now fails 403, and signed-out saves 401 even in open-app mode).
12. **`GET /storeops/payroll-raw` is NOT pay-gated (mig 434 gap, found during W3 2026-09-01).** The
    payroll-tax page's input feed serves `pay_rate` + W-4 settings to any caller who passes span
    scoping — it is absent from the six gated money routes. The W3 SCHEDULED report over it
    (`storeops_payroll_tax`) applies the charter-4 `can_see_pay` gate itself (fail closed), so the
    scheduled path cannot leak — but the LIVE endpoint still can, to a below-market-manager caller
    on a `manager_up` tenant. Fix belongs on the route (same `get_payroll_route` posture); reported
    rather than patched inline because it changes a live page's behavior.

---

### Areas flagged for a follow-up verification pass (`⚠`)
- **Closing module endpoints** (§12) were not enumerated — only `deposit_recon.py` helpers verified.
  Grep `backend/app/modules/closing/router.py` for the deposit/closing endpoint surface.
- **b2b_sweep live scrape** confirmed as stub from code comments; actual production ingest path
  (upload vs a different live connector) not traced end-to-end.
- **MI resolver qualifiers `twp`, `address_checks`, `zulu`, `inventory_aging`** — the first ~6 sources in
  `_mi_resolve_numbers` were read (`28800-28912`); the tail of the function (these four) was not fully
  read line-by-line. `_mi_classify_sales_row` token rules not quoted verbatim.
- **`carrier_kpi_metric` seeded rows / payout_config_col wiring** — table shape verified; the exact
  metric_key→payout_config column mapping was not dumped.
- Exact **rep-commission tier math** (how `kpis_met`→`tier` maps through `tier_100/75_min_kpis`) lives
  inside `calc_rep_commissions` body (`calculator.py:104-520`) and was not line-quoted here.
