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
| 12 | **External credit machine + Card Settlement Recon** | "Where does the external / white-machine card figure live, what is it called for this tenant, and how does it tally with what the processor actually settled?" |
| 7a | **Residual per Subscriber report** | "Where does the residual/subscriber trend come from per carrier? Why is a Total/MA store named, not a processor account id?" |
| 8 | **Plan-mode sale installments** | "Sale-triggered multi-month pay (Total Wireless). Device categories, gates, ledger." |
| 9 | **Management Incentive (DM/manager)** | "How is a district/market manager paid? Components, qualifiers, bonuses, auto-resolved numbers." |
| 10 | **KPI system (DLAR / store_kpis / carrier_kpi_metric)** | "Where do ATU / protect / TMR3 / conversion come from? Store vs rep KPIs?" |
| 11 | **Inventory & aging** | "Per-device cost / days-in-stock. Is it live or a snapshot? Device history lookup." |
| 12 | **Cash / deposit reconciliation** | "Collected cash vs bank deposits. Expected deposit, variance, basis." |
| 12a | **Merchant-processor portals** | "Where do the card processors' own daily figures come from, and how are they tallied against what employees declared?" |
| 13 | **Org hierarchy & store resolution** | "Which stores does a manager see? How is a raw store string canonicalized to a store_code?" |
| 14 | **Employees & scheduling** | "Do shifts feed pay? Rep→employee name mapping. Hours in targets." |
| 15 | **Other commission subsystems** | MA (master-agent) commission, VIP, epay, chargebacks, expenses, agency, financing, accrual/payout ledger. |
| 16 | **Cross-reference: by TABLE** | table → sections/functions that read & write it. |
| 17 | **Cross-reference: by ENDPOINT** | endpoint → handler/section. |
| 18 | **Cross-reference: by METRIC/KPI** | metric → source table → reader function. |
| 19 | **Known gaps & inert config** | stored-but-unwired, snapshot-only, surfaces that can disagree. |
| 20 | **Super-admin control box** | "Is the platform working? What is red right now, what is NOT being watched at all, did the daily check actually run, and how do I hand this failure to Claude Code safely?" |
| 21 | **Billing — usage & pricing** | "What did this tenant use, what did it cost us, what do we bill them, which modules are still unpriced, and what does their itemized statement say?" |
| 22 | **Platform OPERATOR console** | "Who operates the platform rather than a company in it, how does an operator enter a tenant without it being a secret, what is on the record afterwards, and how do we stop platform power from riding on somebody's employee row?" |

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
| `merchant_settlement_day` | `955_merchant_portal_settlement.sql` | `org_id, source_id, portal_key, settlement_role, business_date, merchant_id, terminal_id, store_code, card_brand, gross_amount, refund_amount, net_amount, fee_amount, txn_count, batch_ref, raw` | **Merchant-processor card settlement** — the PROCESSOR side of the daily-closing card tally (§12a). Grain = org × source × merchant × business_date × card_brand |
| `merchant_settlement_batch` | `955_merchant_portal_settlement.sql` | `org_id, source_id, portal_key, settlement_role, deposit_date, batch_date, merchant_id, store_code, batch_ref, deposit_amount, fee_amount, raw` | Processor **funding** events (money to the bank) — cash/deposit recon (§12). A DIFFERENT grain from settlement; never sum the two |
| `raw_mi` | `002_commcalc.sql:46` | `salesforce_id, actual_mi_payout, actual_atu_payout, phone_number, subscriber_status` | Carrier residual gate (paid-proof), MI/ATU |
| `raw_dlar_rep` | `002_commcalc.sql:56` (+`012`,`031`) | `rep_name, store, atu_pct, protect_pct, byod_pct, family_plan_pct, tmr3, aal_conversion, bounty, split, ga_prepaid` | Rep KPI, comp trend |
| `raw_dlar_store` | `002_commcalc.sql:67` (+`012`,`031`) | `store_code, salesforce_id, address, total_acts, port_pct, psa_projected` **plus** later `atu, protect_pct, byod_pct, family_plan_pct, tmr3, aal_conversion, conversion_rate, gross_adds, total_upgrades, location` | Store KPIs, MI TMR3 gate |
| `raw_catalog` | `002_commcalc.sql:77` | `product_id, product_desc, cost, sku` | Device COGS, GP, installment MRC |

> **Period spelling.** Sweeps stamp month-name labels (`'July 2026'`); manual entry may differ. Every
> read matches BOTH spellings via **`_pvariants(period)`** `router.py:170` used as `.in_('period', _pvariants(period))`.

### Ingest route A — manual upload (mapping wizard)
- Endpoint `POST /upload/{file_type}` `router.py:844`; mapped ingest `POST /upload-mapped` `router.py:3637`;
  generic manual-report ingest `POST /manual-upload/ingest` `router.py:24291`.
- **Multi-sheet stitching (2026-09-01 feed-freeze RCA):** `/upload/{file_type}` parses Excel via
  `_read_excel_all_sheets` (`router.py`, beside `_flatten_grouped_sales`) — b2bsoft splits a large
  month-to-date export across continuation worksheets, and the old first-sheet-only read silently
  truncated it (LuxeLink daily feed frozen at 08-30 while emails carried 08-31 data on sheet 2).
  Pure rules in `commcalc/multisheet.py` (`continuation_sheet_names` = later sheets with the EXACT
  same header, `is_header_echo` = repeated header rows dropped; summary/reordered tabs excluded),
  proven by `backend/harness_multisheet_ingest.py`. The email/FTP sweeps reuse this endpoint, so
  they inherit the fix. MA-overview and ePay uploads keep their own single-sheet readers.
- **Slice-scoped replace (incident 2026-09-02):** every replace path deletes ONLY the slice the file
  proves it owns — rules are PURE in `commcalc/ingest_slice.py` (`INGEST_PARTITION`, `replace_scope`
  /`apply_scope` for the period-replace paths, `day_replace_filters`/`apply_filters` for the
  DATE_KEYED per-day path in `/upload/{file_type}`). The DATE_KEYED feeds (`daily_sales`,
  `ma_commission`, `ma_daily_tx`, `ma_fulfillment`) used to delete-then-insert per **(org, day)**;
  when one org feeds the same table from TWO master-agent portals (LuxeLink=VidaPay Chicago +
  Novawave/Total-Access NY/NJ, org `854f6d7b-…`) that wiped the other portal's rows for every day in
  the file — 2026-09-02: the Novawave August MA Commission upload erased the VidaPay bridge rows,
  `raw_ma_commission` Aug 824 → 364, 750+ devices un-paid in the recon. Now the delete narrows to
  **(org, day-set, account-set)** — `merchant_account_id` (raw_ma_commission), `account_id`
  (raw_ma_daily_tx), `tspid` (raw_ma_fulfillment) — when every incoming row carries the account; a
  file with any blank account value falls back to the whole-day replace (a real delete, never a
  silent no-delete that would double-count on re-upload). `daily_sales_feed` is single-source, not
  in the partition map → unchanged. Idempotent: an identical re-upload replaces its own slice only;
  the response reports `replace_slice`. Recovery from the incident = re-upload the full-August
  VidaPay file (its slice re-lands; Novawave's rows coexist). Proofs
  `backend/harness_ma_slice_replace.py` (armed pre-fix negative control) +
  `backend/harness_ingest_partition_replace.py`; delete still runs insert-first via
  `safe_replace.py`. These are table-structure facts, not per-org policy → no config table.
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
| Email inbox | `email_sweep.py` | routes attachments to report ingest | `/email-sweep/*` `router.py:22973-23407` (mig `049`,`075`); scheduler: pg_cron → `/email-sweep/run-due` (mig `921`,`922` — backend self-registers on boot; handler advances `next_run_at` up front and sweeps on a dedicated thread so the tick answers pg_net inside its 5 s timeout; per-mailbox in-progress lock `sweeping_since` (mig `932`) stops overlapping sweeps; non-terminal files stop re-fetching after `SWEEP_MAX_NONTERMINAL_ATTEMPTS` — surfaced in `last_status`, never silent) |
| Vidapay | `vidapay_sweep.py` | payment feed | (mig `083` total processor sources) |
| Generic data-source portal login | `live_login.py` | any report | `/data-sources/*` `router.py:23760-24979`, `/data-sources/sweep/run-due` `24409` (cron path advances each due source's `next_run_at` up front and pulls on a dedicated thread — the email-sweep incident pattern; the secret-less org-scoped call still pulls inline; interactive login/2FA/live-login endpoints on the API service proxy transparently to the sweeps worker when `BROWSER_SERVICE_URL` is set — `service_role.BrowserWorkProxy` + handler in `main.py`) |

Connector/schedule model: mig `039_connector_model.sql`, `063`, `290_report_schedule_and_grain.sql`;
endpoints `/connectors*` `router.py:6666-6989`, `/connector-health` `23378`. Sweep store-guard
(quarantine ambiguous store strings before ingest): `ingest_store_guard.py`, mig `280`; `/ingest-guard/*`
`router.py:14375-14446`. CI-pinned since 2026-09-03 (§19.15): `harness_org_scope_guard.py`'s
ingest-screen section fails the build on any raw_sales/daily_sales_feed write not fronted by
`_isg.screen`, and `harness_cross_tenant_isolation.py` replays the real 2026-07-14 cross-tenant
batch through the guard and the union/promotion paths.

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
- **Activation-Details basis override:** when the tenant's basis of truth is the b2b Activation Details
  report, `_apply_activation_basis` REPLACES the cell activation counts with the AD buckets
  (`_ad_cells_full` ← `_cr_resolve_activation_details` ← `activation_bucketing.py`, mig `313` per-org
  token rules — see §15 "Activation-Details basis").

- **Carrier-aware report COLUMN LABELS + banner terminology (owner 2026-09-02, mig `945`):** the
  activation-report column headers (Exec MTD / Activations — e.g. the device-financing column: Total
  side "Edge", Boost side "ACIMA") and the Exec-MTD unrecognized-contract-type warning (whose text
  names the b2bsoft MTD reconciliation) are DISPLAY TERMINOLOGY resolved from config, never carrier
  branches (RULE TWO). Storage reuses `commcalc.ui_label_override` (mig `068` scope-multiplexing, the
  `scope='tiles'` precedent): HOUSE-org rows `scope='report_col:<carrier>'` /
  `'report_banner:<carrier>'` are the CARRIER PRESETS (mig `945` seeds boost: `edge`→`ACIMA` +
  `unrecognized_ct_recon`→`off`; total: `edge`→`Edge` + `on` — byte-identical for Total/LuxeLink);
  tenant rows under the un-suffixed scopes are that org's OVERRIDES. Resolution (PURE:
  `report_labels.py` — `parse_label_rows`/`resolve_columns`/`resolve_banners`/`banner_on`/
  `build_payload`; proof `harness_report_labels.py`): **tenant override > carrier preset > built-in
  default**, keyed off the org's `commcalc.carrier` rows (mig `038`, the onboarding "Carrier
  Selection" step) → auto-assign for a NEW tenant is LAZY (pick a carrier, labels follow; no setup
  hook; no carrier row / no preset = built-ins, byte-identical). Served by `GET /report-labels`
  (resolved per carrier + raw layers), edited by `PUT /report-labels` (registry-validated keys,
  ''=delete=revert-to-inheritance; gated on the `classification` settings area). Frontend:
  `lib/report-labels.ts` (`useReportLabels`/`pickLabelMap` — payload-carries-the-label, the mig-932
  gp `acc_label` pattern; active-carrier lens picks the map) consumed by `exec/mtd/page.tsx`
  (headers + exports + the `unrecognized_ct_recon` banner gate) and `activations/page.tsx`;
  settings surface `components/ReportLabelSettings.tsx` ("🏷 Column labels" on Exec MTD). Display
  config, not a feed → NO lineage-registry entry.

- **Exec-MTD METRIC DEFINITIONS: carrier presets + the silent-zero detector (owner 2026-09-04, mig `962`):**
  owner report — *"executive mtd in cellfonz r us does not have bill payment qty, but luxelink has it"*.
  ROOT CAUSE: the `bill_payment` bucket matches lines by EXACT `department`/`category` membership against
  tokens that `router._EXEC_METRIC_DEFAULTS` derived from ONE tenant's export (`rtr` / `rtr product` /
  `other carr. payments`). The other tenant spells the SAME concept `bill payments` / `boost rtr` /
  `xfinity refill`, so it matched NOTHING and the column read ~0 in silence — **2 lines / $74.77 vs
  6,869 lines / $359,873.05** for August 2026. Same defect CLASS as LI/1115 (a vocabulary pinned to one
  tenant's spelling, with no signal when it matches nobody).
  MECHANISM (mirrors mig `945`/`953`, reusing THEIR carrier identity primitives —
  `report_labels.normalize_carrier_code` / `default_carrier`, never a second carrier resolver):
  `commcalc.exec_metric_config` gains a nullable **`carrier`** column — NULL = that org's own definition
  (every pre-962 row), NOT NULL at the HOUSE org = that carrier's PRESET. Resolution is PURE
  (`commcalc/exec_metric_defs.py`: `CODE_DEFAULTS`/`line_match`/`split_rows`/`resolve`/`strip_sources`):
  **tenant row > house carrier preset > built-in default**, LAZY auto-assign (a new tenant that picks a
  carrier inherits the preset the moment the resolver runs; no setup hook; no carrier / no preset =
  built-ins, byte-identical). `router._EXEC_METRIC_DEFAULTS` and `router._exec_line_match` are now
  ALIASES of this module — one vocabulary, one predicate, no sibling copy.
  THE PRECAUTION: `exec_metric_defs.bucket_coverage` → `GET /exec-mtd/*` response key **`metric_coverage`**
  → the red banner on `exec/mtd/page.tsx` (sibling of the `classification_gaps` banner). Any LINE bucket
  matching ZERO rows over a period that HAS rows is reported with the department/category values that DID
  occur, so a definition describing nobody's data becomes visible instead of printing a quiet 0. An empty
  period reports no gap (an empty month is not a broken definition). `activation` is excluded (its rules
  are contract-type tokens, and a zero there is a legitimate answer).
  WHY NOT the substring token `boost rtr` (which `_BILLPAY_DEFAULT_TOKENS` uses for the Daily-Targets
  conversion): it OVER-MATCHES 1,339 August PROTECTION lines reading *"… included in your boost rtr
  payment"*. The seeded rule is an EXACT department match with `exclude_category ['other charge']` (that
  category inside the department is the processor SERVICE-CHARGE fee — 4,303 lines / $17,088.00 — a fee,
  not a payment). ⚠ The `_BILLPAY_DEFAULT_TOKENS` over-match is UNFIXED and pre-existing — see §19.
  MONEY: none. No P&L line, payout, accrual or commission figure reads this bucket; it feeds Exec MTD's
  display columns, `/metric-recon`'s SECONDARY basis and Leg B of the mig-`944` 3-way bill-pay recon. The
  mig-`939` P&L carve-out books from the PROCESSOR feed (`_billpay_processor_by_store_day`), untouched.
  Other-tenant output verified byte-identical on live August rows (1,731 lines / $73,914.79 before and
  after). Proof: `backend/harness_exec_metric_defs.py` (81 checks). Display/definition config → NO
  lineage entry.
  **FOLLOW-UP (mig `963`)** — the detector's FIRST live run flagged two more house columns, both
  answered by the owner 2026-09-04 (*"tablet is not a phone but counts towards total activation,
  activation fee with boost is called device set up fee which is accounted for"*):
  (a) `phones` was a real defect — built-in tokens are category `cellphone`/`kittedbranded`; the house
  handset lines carry BRAND categories under handset DEPARTMENTS, so the column read 0. Seeded
  `department ['iphone - xp','android - xp']` → **0 → 1,192 lines / $74,007.34** (Aug 2026).
  `tablet - xp` is deliberately EXCLUDED (a tablet is not a phone) and `byod` too (357 of its 369
  lines are accessories). Tablets still count toward Total Activation and that is UNCHANGED — `_row()`
  folds tablet into `d['activation']` before `ta` is summed; only the DISPLAYED Activation column
  subtracts it (`_pure_new`).
  (b) `activation_fee` is NOT a defect — the carrier equivalent is the DEVICE SET-UP FEE, already
  counted in its own mig-`263` column (dept `dev. charges or fees` / cat `device setup charge`, 1,955
  lines / $55,378.92 Aug). Mapping it here would DOUBLE-COUNT, so the rule is left intact and the
  bucket is marked `applicable=false` (new `exec_metric_config.applicable` column) — the detector
  reports it under `not_applicable` instead of flagging a correct 0 every month. The flag silences the
  BANNER only: `strip_sources` never carries it, so the bucket is still classified and counted and a
  line that does appear is never hidden. Neither column feeds `/metric-recon` or the mig-`944` recon —
  no money. Live-verified: house gaps `['phones','activation_fee']` → `[]`; other tenant byte-identical
  on all three buckets.

- **Carrier VOCABULARY TERMS + the two-sided vocabulary rule (owner 2026-09-04, mig `953`):** the
  owner's rule — a tenant must ONLY ever see its own carrier's vocabulary (no
  "VidaPay/T-CETRA/Total Wireless/MA …" wording on the Boost side; no "Boost/VIP/ACIMA/PayGo/ePay/
  Asset Ledger" wording on the Total side). Mechanism EXTENDS mig `945` (same
  `commcalc.ui_label_override` store, same resolution) with a third scope family
  `report_term`/`report_term:<carrier>`: registered TERM keys (`report_labels.LABELABLE_TERMS`:
  `processor`/`distributor`/`financing`/`marketplace_feed`/`pos_system`) whose built-in defaults
  are NEUTRAL nouns; mig `953` seeds boost (`ePay`/`VIP Wireless`/`ACIMA`/`b2bsoft` — byte-identical
  wording for Boost tenants) and total (`VidaPay`/`VidaPay / T-CETRA`/`Edge`/the marketplace-feed
  name). Same GET/PUT `/report-labels` payload (`terms`, `editable_terms`); frontend
  `lib/report-labels.ts` `pickTermMap` + `useReportLabels().term(key, neutralFallback)` — consumers:
  `ClosingSubmitForm`/`DailyClosingVerify`/`closing/page`/`closing/_lib/SubmissionsTable` (bill-pay
  processor + financing labels), settings editor `ReportLabelSettings.tsx` ("Carrier vocabulary"
  section). Whole-feature gating rides the EXISTING `NAV_CARRIERS` registry (`lib/rbac.ts`,
  admin-overridable `caps['carrier:<href>']`) — sweep 2026-09-04 added the asset/VIP/ePay surfaces
  (`/commcalc/asset`, `asset/invoice-due`, `asset/inventory-recon`, `asset/oninv-3way-recon`,
  `/commcalc/epay/sweep`, `/closing/epay-recon` → boost) and the MA/VidaPay surfaces
  (`ma-overview-recon`, `ma-product-class`, `commission-category-map`, `report-mappings` → total);
  the mapping hub + upload tiles/portals filter through the same gate/lens. CI GUARD:
  `backend/harness_carrier_vocab_guard.py` — static scan of frontend display copy (string literals
  + JSX text, comments stripped) fails on any hardcoded cross-side carrier term not covered by a
  NAV_CARRIERS gate or its pinned REVIEWED_EXCEPTIONS table (term-is-data configs, lens-gated and
  data-conditional copy, the CarrierPicker onboarding screen). Proof of resolution + the two-sided
  truth table (boost payload renders zero Total vocabulary and vice versa; neutral fallback for a
  presetless carrier): `harness_report_labels.py`. Display config, not a feed → NO lineage entry.
  **BACKEND consumers (server-rendered copy)** resolve one term through the canonical pair
  `report_labels.term_from_payload(payload, key)` (PURE) / `report_labels.carrier_term(client,
  org_id, key)` (I/O wrapper) — precedence default carrier > the org's other carriers > `_`
  (override-only) > the NEUTRAL noun, degrading to the neutral noun on any label-service failure and
  NEVER to another carrier's word. Extracted 2026-09-04 so a report naming a processor/distributor
  in its own payload copy has ONE resolution rule: `commcalc/processor_ledger._processor_term` (§15)
  and `account/residual_subs._carrier_terms` (§7a provenance + empty-state copy) both bind it.

**Endpoints:** `/sales-report` `router.py:15792`; `/sales-report/detail` `15980`;
`/sales-report/classification-unmatched` `15925`; `/sales-comparison` `16096`; `/sales-diagnostics` `16206`;
`/top-sellers/{period}` `16982`; `/report-labels` GET/PUT (carrier-aware column labels, beside
`/accessory-config`). **Frontend:** `commcalc/sales-report/page.tsx`, `exec/page.tsx`,
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
- **MA → P&L STORE ATTRIBUTION + line labels (mig `314`, owner spec 2026-09-02 — "store wise
  commission for all M1 thru M12 … Residual on Total side … mdf should capture the market spiff …
  rebates and phone cost per store, none hardcoded"):** `account/ma_store_pnl.py` (all booking
  functions PURE — proof `harness_ma_store_pnl.py`) supersedes coa's inline MA loops. Config on
  `commission_org_config` (all defaults = pre-314 byte-identical): `pl_ma_store_attribution`
  attributes every MA booking (residual→`mi_income`, merchant discount, sheet components incl.
  rebate contra-COGS, MDF, month spiffs, MA device COGS via `device_cogs.resolve(ma_acct_index=…)`)
  to the row's processor account's STORE — index = `raw_ma_fulfillment` `tspid→business_address`
  (`account_store_index`, ambiguous tspids dropped) overridden by `commcalc.ma_account_store_map`;
  unmapped accounts stay company-wide (honest). `pl_ma_month_spiff_source='daily_tx'` books
  M1..M12+ commission CASH from `raw_ma_daily_tx` rows (`pl_ma_spiff_order_types`, default
  `['PostPaid Additional Spiff']`) with `M<n>` detail via THE shared
  `commission_ledger.parse_payment_month`, and suppresses the sheet's `spiff_m1..m6` booking (no
  dollar at both activation month and cash month); default `'commission_sheet'` keeps today's
  activation-month booking. `pl_mdf_product_tokens` books matching rows' −`retail_cost` to the new
  **`mdf_income` "MDF (market spiffs)"** `auto_opt` line (luxelink: `['premium store spiff']`, the
  $1,000-per-store rows). A `retail_cost` books at most once: residual → MDF → month-spiff.
  `pl_line_labels` renames P&L/BS lines per org via the `inputs[key]['label']` passthrough
  (luxelink: `{"mi_income": "Residual"}`; Boost untouched). LuxeLink opt-in seeded by mig `314`
  (Aug-2026 dollars itemised in the migration header).

- **P&L rebate presentation (mig `934`, owner report 2026-09-02 — "rebate is coming in negative,
  it should be a positive number as it is coming in"):**
  `commission_org_config.pl_rebate_presentation` (`'contra_cogs'` house default = ruling K1
  byte-identical: rebates NEGATIVE on `device_rebate` inside COGS; `'income'` = the SAME dollars
  POSITIVE on the new `auto_opt` revenue line **`rebate_income` "Rebates (device purchase)"**,
  with the empty contra line suppressed via the `inputs[key]['suppress_zero']` `engine._assemble`
  passthrough). ONE resolved route (`ma_store_pnl.rebate_route`, read by
  `ma_store_pnl.ma_commission_bookings` AND coa's `activation_rebate_ledger` booking) so both
  rebate sources always present alike; store grain (mig `314` account→store index) unchanged;
  gross profit and net income identical under both presentations (revenue and COGS move
  together). `ma_store_pnl.load_config` falls back mig-934 → mig-314 → defaults column sets so a
  pre-934 DB keeps its mig-314 seeds. LuxeLink seeded `'income'` by mig `934`. Proof:
  `harness_pl_rebate_presentation.py`.

- **Bill-pay pass-through carve-out + coverage recon (owner directive 2026-09-02, mig `939`):**
  "billpay is deducted from [revenue] as it is not income and is offset by either the cash
  deposited… or by the commission received; different carriers do it in a different way." (a)
  P&L: NEW matched revenue pair **`billpay_collected`** (+) / **`billpay_offset`** (−, label per
  the org's settlement convention) — both `auto_opt`, store grain — built from the daily-closing
  declared ePay split (`epay_on_cash`+`epay_on_credit`, DM-VERIFIED corrections winning at
  store-day grain); the pair nets to ZERO by construction so gross profit / net income never
  move. Config on `commission_org_config` (RULE TWO, no carrier names):
  `pl_billpay_presentation` (`'off'` house default = byte-identical | `'carveout'`) and
  `pl_billpay_settlement` (`'remit_separate'` = commission paid separately, payments remitted
  separately | `'net_from_commission'` = payments netted from commission owed). Pure module
  `account/billpay_pl.py` wired in `coa.build_inputs`; LuxeLink seed (`'carveout'` +
  `'net_from_commission'`, Aug $38,324.39 measured) COMMENTED behind the owner gate in mig
  `939`. (b) COVERAGE recon: `GET /billpay-coverage/{period}` — per store per DAY, Σ bill-pay ≤
  Σ(cash + card declared at closing, DM-corrected); bill-pay side = the carrier PROCESSOR feed
  (the same `metric_source_of_truth`/data_source resolution `/metric-recon` uses; NEW day-grain
  sibling `_billpay_processor_by_store_day`) falling back to the declared closing split;
  exceptions ONLY when bill-pay EXCEEDS collected ("equal to or less" passes). Pure math
  `metric_recon.reconcile_billpay_coverage`. Proof: `harness_billpay_pl.py`. 2026-09-02 #2
  (mig `944`, §12 3-way recon): the daily-TX processor leg is now FILTERED to bill-payment rows
  (`metric_recon.ma_billpay_predicate`), accounts resolve through store_merchant_id → the
  mig-314 index, and the processor auto-detect no longer requires an enabled data_source row —
  the coverage recon inherits all three fixes through the shared helpers.

- **P&L store/market filter + company scope (fix 2026-09-02, owner: "market filter shows no data /
  company selection shows improper information"):** the aggregated-statement filter
  (`account/statement_filter.py`, read by `GET /account/pl|balance-sheet/{period}?stores=&markets=`)
  now resolves **markets through the canonical UNION market index** (`core/scope.market_index`:
  storeops.stores ∪ store_mapping ∪ store_aliases — the same authority as `/core/markets`, so the
  picker can never offer a market the resolver cannot bind), **case-insensitively**, and matches a
  member store's snapshot by ANY known spelling (exact → squashed → unambiguous leading street
  number; fail-closed on ambiguity/unknown market — `market_key_expansion`/`build_store_matcher`).
  The company **scope selector** composes with the filter via `statement_filter.scope_predicate` →
  `coa.company_assignment` — the SAME store→company attribution `engine.compute_and_store` books
  company snapshots with (`coa.build_company_matcher`: exact → squash → unambiguous street number →
  DEFAULT company), so a sales-spelling drift (live house: `1115 Liberty Ave Brooklyn, NY 11208` vs
  assignment `1115 Liberty Ave`, $3,786.27 Aug revenue leaked to "Default Company") re-attributes
  correctly (recompute refreshes stored company snapshots). Proof:
  `harness_pl_filter_semantics.py`.
- **Wages estimate is salary-basis aware (fix 2026-09-02, owner: "employee salaries … not getting
  autoloaded from the payroll"):** `coa.wages_by_store` → pure `coa.derive_wage_cells`: hourly
  employees stay hours×`pay_rate` (byte-identical); SALARIED employees
  (`storeops.employees.pay_basis` weekly/monthly/annual + `pay_amount`, migs 416/417 — the same
  columns the payroll report pays from) book ONE monthly equivalent
  (`coa.monthly_salary_equivalent`: monthly=amt, annual=amt/12, weekly=amt×52/12) allocated across
  worked stores ∝ hours (active zero-hours → home_store, else company-wide; inactive zero-hours →
  skipped). A salaried row's `pay_rate` holds PER-PERIOD pay, so the old hours×pay_rate booked
  phantom wages (live LuxeLink Aug 2026: Wages $234,523.57 → $110,190.54 on recompute). Rep
  commissions verified autoloading (Σ `rep_commissions.total_payout` = the `rep_comm` line to the
  cent). Proof: `harness_wages_salary_basis.py`.
- **Sticky store-expenses carry-forward (fix 2026-09-02, owner: "GP expenses column is not auto
  pulling from the expenses sheet — systematic fix not a band aid"):** NEW shared module
  `commcalc/expenses_effective.py` — the ONE carry-forward rule (a period with NO `store_expenses`
  rows reads the latest strictly-prior period's MANUAL rows; system lines — `payroll_gross`,
  `pto_accrual` etc. — never carry; a period with its own rows is byte-identical to the raw read) —
  consumed by BOTH the GP report (`router._compute_gp`, payload field `expenses_carried_from`) and
  the P&L (`account/coa.build_inputs` `store_opex` + the K2 payroll-name suppression), matching
  what the sticky Expenses sheet (`GET /expenses/{period}`) has always DISPLAYED. Proof:
  `harness_expenses_carry_forward.py`.
- **GP accessory column basis — "Acc Sales" (owner 2026-09-02, mig `932_gp_acc_basis.sql`):**
  `accessory_config.gp_acc_basis` (`'sales'` = Σ `ext_price` of accessory lines — HOUSE DEFAULT,
  applied on NULL/absent; `'gp'` = legacy Σ `gp`, per-org opt-back via `PUT /accessory-config`) →
  `calc_gp_report(acc_basis=…)`; the payload carries `acc_basis` + `acc_label`
  ('Acc Sales'/'Acc GP') and `commcalc/gp/page.tsx` labels the column from it (no hardcoded
  strings). The basis flows consistently into `total_rev`/`net_profit`. Proof:
  `harness_gp_acc_basis.py`.
- **Balance-sheet truths + the ON-DEMAND statement engine (owner report 2026-09-02, mig `933`):**
  two NEW account modules. `account/balance_sheet.py` (PURE — proof
  `harness_balance_sheet_truths.py`, run on the owner's live rows): (a) **unsold-phone inventory**
  — `device_inventory_cells` builds the snapshot-coherent unsold set from
  `inventory_aging_device` (on_hand at each store's latest as_of; store-NULL ghosts + superseded
  rows EXCLUDED and reported — live LuxeLink: 556 July ghosts worth $129,454.66),
  `apply_inventory_basis` resolves the BS line per `account_config.inventory_basis`
  (`'report'` default = the emailed Inventory-Aging totals in `inventory_value`, byte-identical;
  `'devices'` = the phone ledger; manual override always wins), `inventory_recon_rows` is the
  per-store report↔devices tie-out served by `GET /account/inventory-recon` (LuxeLink delta
  measured: report $173,057.07 vs devices $166,020.16); (b) **handset payables** — NEW
  `handset_payable` BS liability (`auto_opt`): `handset_payable_bookings` books
  `raw_ma_daily_tx` rows of the org's `account_config.handset_payable_order_types` families still
  inside the vendor's OWN due-date window (`tx_date ≤ as-of < due_date`; money column
  `retail_cost` ONLY; LuxeLink measured $169,013.57 outstanding); empty default books nothing;
  Boost's device payable stays `owed_vip` (asset_ledger) — sources disjoint, no double count;
  (c) **journal company designation** — `journal_company_matcher` (exact → squash → unique
  prefix → unique 1-edit, ambiguous ⇒ None) + `journal_scope_entries` fix the scoping that
  stranded the owner's $250k/$100k contributions + $210k loan (typed 'Luxelink'/'Novawave' into
  the free-text store field) on Consolidated only. `account/statement_engine.py` (proof
  `harness_statement_engine.py`): `statement(client, org, period, scope, kinds)` = FRESH
  P&L/BS/**Cash Flow** for any org/period/scope (endpoint `GET /account/statement/{period}`;
  notify report key `financial_statement` in `notify/finance_reports.py` — scheduled/on-demand
  email+WhatsApp via the standard registry); `compute_and_store` SUPERSEDES
  `engine.compute_and_store` on `POST /account/compute/{period}` and the `/account/run-due` sweep
  (`autocompute.py` imports it aliased) — same snapshots + a stored `cash_flow` statement_type
  (indirect method over BS deltas: spec payables=operating, fixtures=investing, journal
  loans/owner capital=financing; manual-cash tie-out reported in `tie_delta`), journal read via
  BOTH period spellings, `PUT /account/journal` now echoes `rejected` (reasoned) + `resolved`
  (company attribution) instead of dropping rows silently. LuxeLink org seeds ship COMMENTED
  behind the owner gate in mig `933` (mig-622 precedent). Roadmap of the remaining buildout:
  `docs/FINANCE_PLATFORM_ROADMAP.md`.

- **DM-verified store cash → Balance Sheet (owner directive 2026-09-02, mig `938`):** "all cash
  collected in the store must be added to the balance sheet as cash collected after it has been
  verified by the DM, either the cash is deposited in the bank or it is used in expenses." NEW BS
  asset line **`store_cash_on_hand`** "Cash on hand — stores (undeposited)" (`auto_opt`, store
  grain, `balance_sheet.EXTRA_BS_SPEC`), gated by `account_config.cash_on_hand_basis` (`'off'`
  house default = byte-identical; `'verified'` = the owner's rule — only DM-VERIFIED store-days'
  declared cash counts as collected, unverified dollars reported in statement meta, never
  silently dropped; `'all'` = the operational number). Movement dicts come from the closing
  module's OWN `_cash_position_core` (lazy import in `statement_engine.build_inputs_full` —
  declared cash already DM-overlay-corrected; outflows = cash pickups/deposits + approved
  envelope expenses/withdrawals — so the BS can never disagree with Cash Position / Store Cash
  on Hand), then PURE `balance_sheet.store_cash_cells` filters/nets as-of period end; store
  grain via `coa.store_resolver`. In the derived Cash Flow the line is CASH
  (`statement_engine.CF_CASH_KEYS = ('cash','store_cash_on_hand')` — summed into
  cash_begin/cash_end, excluded from operating deltas), so a bank deposit (store→bank) leaves
  reported cash unchanged and a cash-paid expense relieves the line while landing on the P&L via
  the existing closing-expense sweep. LuxeLink seed (`'verified'`) COMMENTED behind the owner
  gate in mig `938`. Proof: `harness_verified_cash_bs.py`.
  **SEMANTICS FIX (2026-09-02, post-merge live defect):** the original `'verified'` basis counted
  inflows on verified store-days only but subtracted outflows from ALL days — dollars left a
  bucket they never entered, and the live LuxeLink August consolidated line booked **−$36,660.91**
  (6 verified store-days in vs 157 unverified store-days of pickups out). Fixed in
  `balance_sheet.store_cash_cells`: (1) **SYMMETRY** — under `'verified'`, outflows follow the
  same verification rule as inflows (movement dicts are keyed (store, close_date), so an
  outflow's day IS the envelope it relieved; an unverified envelope's cash never entered the
  line and its pickup/expense cannot relieve it — excluded and reported in meta
  `unverified_taken`/`unverified_taken_days`); `'all'` basis counting is unchanged (every day in,
  every day out — internally consistent). (2) **FAIL-SAFE ZERO FLOOR, every basis** — a cash
  asset line never books negative at any grain: per-store negatives floor to zero with the
  suppressed imbalance reported per store in meta (`floored`/`floored_total`), never silently
  dropped; rollups sum floored stores so no grain goes negative. Fixed August 2026 LuxeLink
  numbers: every store $0.00 (each verified day's cash was picked up same-day for the same
  amount), nothing floored. Proof extended in `harness_verified_cash_bs.py` (§B symmetry+floor,
  §B2 the exact live shape, §C floored-line cash-flow tie-out).

- **DISTRIBUTOR PAYABLE — one derivation per carrier side, parameterized by AS-OF; the tenant
  mapping; and the cash-at-bank GRAINS (owner directives 2026-09-04, mig `954`):** verbatim —
  (A) "balance sheet in cellfonz rus is showing a wrong figure it should be open balance owed
  358221.13 not as a hard coded figure but derived, accounts payable for total should come from the
  open balance Owed to distributor (outstanding) $281,674.04 as of 2026-09-04";
  (B) "it does show in luxelink but in a different line which is acceptable as all companies have a
  different way of assigning their cost center";
  (C) "these should be mapped when setting up the new tenant for proper reporting";
  (D) "for balance sheet to enter the cash at bank it should be an option to enter the cash per
  store or cash per company or overall total to each tenant … if there is cash per store then we can
  get a close to reality figure."
  - **The defect (live, 2026-09-04).** House-org (`00000000-…-0001`) consolidated BS `owed_vip` read
    **$0.00**; correct is **$358,221.13**. ROOT CAUSE: `coa.build_inputs` books that line from
    `asset_ledger` rows whose **`status`** reads `'on inventory'` — but that column only ever carries
    `'Open'` / `'Paid In Full'` / NULL across all 34,015 live rows (ZERO matches); *"On Inventory"* is
    a **CATEGORY** value (`'On Inventory. NET60'`). The predicate can never fire, and the line's only
    other contributor (PENDING PayGo batches) is also $0.00 today. LuxeLink's side was already
    correct — `handset_payable` = **$281,674.04**, the owner's Total-side figure to the cent — and
    stays on its own line (directive B).
  - **ONE derivation per side, never a second path** (duplicate-check gate). Boost/consignment side:
    NEW PURE `balance_sheet.asset_ledger_open_bookings(rows, open_statuses, as_of)` = Σ
    `asset_ledger.owed_to_vip` over rows whose `status` is in the org's OPEN vocabulary and whose
    `acquired_date ≤ as-of` — the SAME predicate the asset dashboard's "Open Balance Owed" card has
    always summed (`asset/router.get_asset_summary` → `total_open_balance`), now as-of parametric.
    Money column named in `ASSET_LEDGER_MONEY_COLUMNS` (`owed_to_vip` ALONE). Total/marketplace side:
    the mig-`933` `handset_payable_bookings`, unchanged. `GET /account/liabilities-due` now calls
    THESE functions at `as_of = today` instead of reading the Boost figure off a stored snapshot —
    the snapshot survives only as a `tie_delta` staleness cross-check.
  - **AS-OF RULE (one function, the date is the only variable).** Both sides are asked for
    `statement_engine.period_as_of(period)` = the period's last day CAPPED AT TODAY — so the OPEN
    period asks for today and a CLOSED period asks for that period end; the tile asks the same
    function for today. Truth table pinned in `harness_liabilities_due.py` §J. HONESTY: the asset
    ledger is a wipe-and-reinsert CURRENT snapshot with no settlement date, so a past as-of is a
    snapshot-basis estimate — stated in statement meta (`basis:'status_snapshot'`, `snapshot_lag`),
    never implied. The open-status vocabulary is POSITIVE on purpose: one live house row (id 889865)
    carries status NULL with $117,730.73 and no store/dates/category, which a "not settled" negation
    would have swept into the books.
  - **NO DOUBLE COUNT with the existing `owed_vip` contributors.** `coa` books that line only from
    `status='on inventory'` rows and PENDING PayGo batches; `'on inventory'` is not in the open
    vocabulary, so the predicates are provably disjoint (pinned, `harness_balance_sheet_truths.py` §F).
  - **TENANT-SETUP MAPPING (directive C).** THREE per-org columns on `commcalc.account_config`:
    `distributor_payable_basis` (`'off'|'asset_ledger'|'marketplace_due'`),
    `distributor_payable_line` (the BS liability line key it books to), `asset_ledger_open_statuses`.
    Resolution `balance_sheet.resolve_payable_basis`: **org column > CARRIER PRESET > a declared
    mig-933 family (no-regression floor) > 'off'**. The carrier preset REUSES the mig-`945`/`953`
    preset machinery end to end — house-org rows in `commcalc.ui_label_override`, scope
    `finance_basis:<carrier code>`, key `distributor_payable`, carrier code via
    `report_labels.normalize_carrier_code`/`carrier_codes` — so a NEW tenant that picks its carrier
    in the onboarding "Carrier Selection" step (`commcalc.carrier`, mig `038`) resolves correctly the
    first time a statement is built, with NO setup hook and NO carrier branch in code (RULE TWO).
    Reader `statement_engine.carrier_payable_preset`; surfaced (resolved basis + source + line
    options) on `GET /account/config` → the Accounting-settings panel, settable on `PUT /account/config`.
  - **TARGET LINE is a per-tenant COST-CENTRE choice (directive B).** `resolve_payable_line`:
    org column (only when it names a line the assembled spec HAS — pick-don't-type, a typo can never
    strand money on a phantom line) > the basis default (`asset_ledger`→`owed_vip`,
    `marketplace_due`→`handset_payable` = today's live placement on both orgs, so LuxeLink's line
    does NOT move) > None for `'off'`. The line's LABEL was already tenant-mappable through the
    existing `commission_org_config.pl_line_labels` → `ma_store_pnl.apply_line_labels` machinery
    (mig `314`, applied to BS keys as well as P&L) — reused, not duplicated.
  - **CASH AT BANK — three grains, one honest rollup rule (directive D).** NO schema change: the
    three grains are the three ways a `journal_entries` row is already addressed (store picked /
    company picked / neither), and `journal_scope_entries` already routes each correctly. What was
    missing is the ROLLUP rule — consolidated used to SUM every entry, so a tenant total keyed
    alongside per-store rows counted twice. NEW PURE `balance_sheet.entry_grain` +
    `journal_grain_entries` (a WRAPPER over `journal_scope_entries`, not a sibling path) apply the
    **net-of-finer / residual rule**: a coarser entry is a statement of the TOTAL for its subtree and
    books NET of the finer entries nested inside it (`company residual = company entry − its stores`;
    `tenant residual = tenant entries − Σ stated(company) − unattached stores`). ONE grain in use —
    every live row on both orgs today — is BYTE-IDENTICAL. A negative residual (finer rows above the
    coarser stated total) is a conflict in the owner's own numbers: it floors at zero and is REPORTED
    in `bs['journal_grains']['conflicts']` (the mig-938 floor precedent), rendered on the Balance
    Sheet page. `_scopes` now also opens a store scope for a store whose ONLY figure is a manual
    entry, so "cash per store" renders its own column. Cash Flow stays coherent: the cash lines are
    `CF_CASH_KEYS`, so a grain change moves placement, never the consolidated cash total.
  - Proofs: `harness_balance_sheet_truths.py` §F (live-figure reproduction, as-of truth table,
    disjointness), §G (basis + target-line precedence), §H (grain truth table + no-double-count);
    `harness_liabilities_due.py` §J (BS ⟺ tile tie-out per side, as-of parameterization contract).
  - Migration `954` seeds the carrier presets (boost→`asset_ledger`, total→`marketplace_due`) and
    maps BOTH live orgs explicitly — pinning what the presets would resolve lazily, so a later preset
    edit can never move a live tenant's books. ⚠ MONEY-TOUCHING for the house org ($0.00 →
    $358,221.13). **Recompute after applying:** September 2026 (both orgs) and August 2026 (house).

- **Statement auto-recompute self-schedules (roadmap Phase 1, mig `940`, 2026-09-02):** the
  `POST /account/run-due` staleness sweep (`account/autocompute.recompute_due` — recomputes
  current+prior period statements ONLY where a tenant's own ingest/journal edit is newer than its
  snapshot) finally has its scheduler tick: pg_cron job `account-recompute-run-due` (every 2h),
  installed by `commcalc.ensure_account_recompute_cron(url, secret)` (SECURITY DEFINER,
  service_role-only EXECUTE — the mig-922 email-sweep pattern verbatim, no secret in the
  migration) and re-registered on EVERY backend boot (`main.py` `_account_recompute_cron_startup`
  → `account/router._ensure_account_recompute_cron`), so a rotated secret or lost job self-heals
  on the next deploy. Closes §19 gap 13 (the owner's twice-in-one-day "entered but never showed
  up" staleness). WHEN, never WHAT: numbers stay `statement_engine.compute_and_store`
  byte-identical.

- **Financial-analysis series (roadmap Phase 3 backend, 2026-09-02):** `GET /account/analysis`
  (`?months=N`, gated by the `account_trends` data grant — the charts-hub gate) serves the
  chart-ready payload for the Financial Analysis page: consolidated monthly P&L/BS trend
  (revenue/COGS/opex/GP/NI, cash & equivalents = `cash`+`store_cash_on_hand`, assets/liabilities/
  equity/inventory) with margin ratios (`None` on a zero base — a chart gap, never a fake 0),
  per-month OPEX composition (stacked-bar ready) and per-company / per-store comparison series.
  ONE MATH PATH: pure `account/analysis.py` reads STORED `account_statements` payloads only
  (spelling-duality dedupe, freshest computed_at wins) — never a second computation — so charts
  can never disagree with the statements. Proof: `harness_financial_analysis.py` (also pins
  `analysis.CASH_KEYS == statement_engine.CF_CASH_KEYS`).

- **Projection engine (roadmap Phase 4, mig `941`, 2026-09-02):** `GET /account/projection`
  (`?months=&horizon=`, `account_trends` grant) — PURE, config-driven, **deterministic-only** (no
  LLM in the math) forward projection of the consolidated P&L: `account/projection_engine.py`
  extends the SAME `analysis.assemble` monthly history (stored snapshots — one math path) via
  least-squares **linear** trend or **seasonal_naive** (same-month-last-year × recent YoY level;
  ≥15 months or noted linear fallback; `auto` picks). Per-org `account_config.projection_config`
  (mig `941`, house defaults in `resolve_projection_config`): `method`, `trailing_months`,
  `horizon_months`, `growth_rate_override` (revenue compounds from last actual — config wins over
  fit), `expense_inflation` (COGS+OPEX compound). GP/NI are DERIVED per projected month (never
  independently trended); magnitude lines floor at $0 with the clamp reported; every row is
  flagged `projected: true` (display-only — nothing books from it); cash runway = cash &
  equivalents ÷ avg projected burn. Proof: `harness_projection_engine.py`.

- **Company valuation (roadmap Phase 5, mig `941`, 2026-09-02):** `GET /account/valuation` —
  gated by its OWN default-closed `company_valuation` data grant (`report_gates.py`; deliberately
  NOT bundled under `account_trends`). PURE `account/valuation.py`: an assumption-driven ESTIMATE
  range from the org's OWN stored statements — TTM basis (`ttm_metrics`: <12 computed months
  ANNUALIZE ×12/n and say so; EBITDA ≈ NI + P&L `other`; SDE = EBITDA + configured owner
  addbacks), revenue/SDE/EBITDA multiple methods (zero/negative basis marked not-meaningful,
  never silently priced), asset-based floor (latest BS assets − liabilities), and a DCF fed by
  the Phase-4 deterministic projection (NI as the cash-basis FCF proxy; monthly discounting +
  discounted terminal = terminal multiple × final projected year; 3×3 rate × multiple sensitivity
  grid). Summary = min/median/max across meaningful earnings methods with the asset floor lifting
  the low end (flagged). EVERY multiple/rate/horizon is per-org `account_config.valuation_config`
  (mig `941`) with house defaults, each method citing its source ('house default'/'org config');
  payload always carries the full assumptions block + the "not an appraisal" disclaimer the UI
  must show. Proof: `harness_valuation.py` (closed-form DCF check to the cent).

- **Finance UI wave (roadmap Phases 2–5 UI, 2026-09-02 — Option-B, awaiting owner preview):**
  (a) NEW `/accounts/analysis` (Financial Analysis hub — reads `/account/analysis` +
  `/account/projection` + `/account/valuation` ONLY; computes nothing): headline tiles, P&L trend
  with dashed projection overlay, stacked OPEX composition (shared `TrendChart` gained an
  additive `stack` prop), margin/cash trends, per-company + top-store comparison bars, projection
  table + runway + assumptions, valuation range/methods/sensitivity + the disclaimer (valuation
  section lock-chips on its own `company_valuation` 403). (b) NEW `/accounts/cash-flow` page
  (stored `cash_flow` snapshot; scope select, staleness banner, honest tie-out banner, export).
  (c) `/accounts/inventory` gained the read-only reconciliation grid (`/account/inventory-recon`:
  report ↔ devices ↔ manual ↔ effective + ghost chips). (d) `/accounts/journal` — RULE THREE
  pickers: company picker (`/account/companies`) + store picker (canonical roster; legacy typed
  values still render), and the PR-#179 server echo surfaced (REJECTED rows with reasons in red,
  `resolved` company attributions confirmed — nothing silently dropped). Registered in nav +
  route-module map + `DATA_GRANTS` (`company_valuation`) in `rbac.ts` and the Reports directory
  (`reports.ts`).

- **Canonical entity/scope enumeration — fail-closed (owner directive 2026-09-04, mig `952`):**
  "cash flow analysis in cellfonz r us has other companies like nova wave, and luxelink in the drop
  down menu along with the T stores, need to fix this as a system not a band aid." The dropdown on
  the Cash Flow / P&L / BS / dashboard pages is `GET /account/overview/{period}` `scopes` (stored
  `account_statements` scope rows). The foreign names came from POISONED DATA, not an unscoped
  query: two LuxeLink entities ("Novawave Communications LLC" `9b22c0d8…`, "Luxelink Wireless LLC"
  `b5993b9d…`) were created under the HOUSE org on 2026-06-27 (zero store assignments, zero journal
  refs, all-zero snapshots), and `statement_engine._scopes` faithfully built a `company:` scope per
  row. Systemic fix (doctrine §13b): `coa.org_companies(client, org_id, cols)` is THE one read of
  `commcalc.companies` (pure fail-closed core `coa.own_entities` — blank org raises, foreign/
  orphan rows drop; house org gets ONLY its own entities, config-only inheritance never applies to
  entities), and pure `coa.filter_org_scopes` drops any `company:<id>` scope not in the org's own
  inventory from `overview` and `analysis.assemble(own_company_ids=…)` — a stale or foreign
  snapshot can exist in storage and still never render. Converged callers: `router.list_companies`
  / `list_stores` / `put_journal` echo / `overview`, `coa.store_company_map` (⇒ `company_assignment`
  ⇒ engine + statement_engine + statement_filter), `finance_attention`. CI: entity-enumeration
  section of `harness_org_scope_guard.py` fails the build on any OTHER `.table('companies')` select
  backend-wide (writes + billing's org-scoped count probe classified, exactly ONE canonical read
  pinned); isolation truth table `harness_finance_entity_enumeration.py`. Data cleanup mig `952`
  (by-id deletes of the 2 phantom companies + their 16 all-zero scope snapshots + the 2 stale
  §19.15 Diversey scope rows whose source raw_sales were removed 2026-09-03 — recompute July 2026
  after applying to purge the $29.99 Diversey remnant from the stored consolidated P&L).

- **Current Monetary Liabilities (owner directive 2026-09-03, with the mig-948 dashboards):**
  `GET /account/liabilities-due` (`account/router._liabilities_due_impl`) — "monies owed to the
  distributor, this weeks payments due, Payroll Due this week, payroll tax due, Rents due this
  week, any other recurring expenses due" — COMPOSED ENTIRELY of existing derivations
  (duplicate-check gate): (a) distributor = `statement_engine._fetch_outstanding_tx` +
  the mig-933 `balance_sheet.handset_payable_bookings` config/predicate (outstanding as of
  today) plus its due-THIS-WEEK sibling `liabilities_due.payables_due_in_window` (predicate
  equivalence PINNED in the harness), store grain via the mig-314 account→store index +
  `coa.store_resolver`; Boost/invoice side = `owed_vip`/`vip_ap` read off the STORED consolidated
  BS snapshot (computed_at surfaced, one math path); (b) payroll + payroll tax = storeops
  `payroll_raw` + the `payroll_tax_estimate.compute_pay` twin for the pay period(s) whose PAYDAY
  (core `pay_period_for`, the one shared resolver, walked by `liabilities_due.paydays_in_window`)
  falls inside the week, plus the accruing current period — mig-434 `can_see_pay` gate FAIL-CLOSED
  (denied ⇒ `allowed:false`, zero dollars in the payload); (c) rents + insurance = the mig-946
  `storeops.store_lease` columns via the documented helpers (`rent_for_month` /
  `resolve_rent_due` / `rent_due_window`; premium recurrence per `insurance_premium_frequency`),
  gated whole by `store_lease.can_see_lease` (ACH columns never selected); unknown rent = null
  amount + counted, never a fake $0. Store rows span-filtered (storeops scope_keyset), markets
  stamped via the canonical `core.scope.market_by_code` (§13a). Window/aggregation math PURE in
  `account/liabilities_due.py` (proof `harness_liabilities_due.py` — week windows, rent/insurance
  recurrence, payroll tax split, gate truth table). Frontend `/accounts/liabilities-due`
  (StandardFilterBar markets/stores; server-gated sections render restriction notes). Registered
  in `reports.ts` (Accounts) + REPORT_DIRECTORY ('finance'); tiled on the Management Overview
  dashboard (§14 mig 948).
  **2026-09-04 (mig `954`):** the distributor section no longer has a Boost-side path of its own —
  it resolves the org's payable BASIS and calls the SAME pure derivation the Balance Sheet books
  (`asset_ledger_open_bookings` or `handset_payable_bookings`) at `as_of = today`, and reports
  `basis` / `bs_line` / `snapshot.tie_delta` (tile-at-today − the target line on the last computed
  snapshot) so a non-zero delta names a STALE SNAPSHOT, never a disagreement about the math.

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
`006_targets.sql`, `070_target_field_registry.sql`); hours from `storeops.shifts` via `_fetch_shifts`; the schedule↔sales rep join keys on `targets_engine.name_key` (uppercase, punctuation-free, tokens sorted) so 'Last, First' POS spellings match 'First Last' schedule spellings — explicit `name_map`/`rep_aliases` rows still canonicalize first
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
- **Sales basis read (`_fetch_sales_unified`, inside `_run_calculation`):** under the default
  `sales_source='legacy'` the open month reads `daily_sales_feed`, a closed month reads
  `raw_sales`, each falling back to the other only when the primary is EMPTY — a PARTIAL
  closed-month `raw_sales` is trusted whole. That is how the 2026-09-03 "August activations
  wrong, Exec MTD right" defect happened (§19.16: sales auto-derive off since 2026-08-09 froze
  `raw_sales` at Aug 1–9). Since 2026-09-03 this Boost-path read ALSO honors
  `commission_org_config.sales_source='union'` (mig 306, previously plan-engines-only via
  `commission_engine._read_sales`): it then reads the transaction-grain `_sales_rows_union_txn`
  — immune to a partial month. Default 'legacy' is byte-identical; flipping the config row is the
  deliberate money event. Proof: `harness_cross_tenant_isolation.py` §C.
- **Cross-tenant hygiene (2026-09-03, §19.15):** the July 2026 house snapshot briefly carried a
  Luxelink phantom rep paid from 6 mis-filed `raw_sales` rows — removed by id and recomputed;
  the class is CI-pinned (`harness_org_scope_guard.py` ingest-screen section +
  `harness_cross_tenant_isolation.py`).
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

### 7a. REPORT — Residual per Subscriber (per store, month over month, vs commission)

**Where.** `backend/app/modules/account/residual_subs.py` → `compute(client, org_id, months)`;
endpoint `GET /account/residual-per-sub` (`account/router.py:~1042`, DEFAULT-CLOSED behind the
`residual_per_sub` OR `account_trends` grant); page
`frontend/src/app/(platform)/accounts/residual-per-sub/page.tsx` (the Trends hub reads the same
endpoint). `commcalc`'s What-If simulator calls `compute` directly under its own carrier_residual gate.

**Residual source is resolved per TENANT by which data EXISTS — never by a carrier branch**
(`_aggregate`): Boost = `raw_mi` MI+ATU via RPC `commcalc.residual_per_sub_by_store` (mig `101`) with
a bounded Python fallback; a tenant with NO `raw_mi` falls through to the MA/VidaPay source
(`_aggregate_ma`).

**MA/VidaPay side (Total, luxelink) — the residual rows are the P&L's residual rows.** ONE sweep of
`raw_ma_daily_tx` over the window books both figures off the same rows: residual = −`retail_cost` on
rows matching **`residual_subs.ma_residual_row_matcher`** (the mig `309`/`314` union — `'%residual%'`
product family ∪ `commission_org_config.pl_ma_residual_order_types`, resolved by `load_ma_pnl_config`;
RULE TWO, no literal), airtime margin = `merchant_discount` on every row. Subscribers = one per
`raw_ma_commission` row. The window is `_latest_ma_period` = the LATER of the two feeds' newest month.
Equality against `ma_tx_pnl_bookings` is pinned in `harness_residual_per_sub.py`, so this report and
the P&L's `mi_income` cannot drift.

**Store attribution (MA rows carry a PROCESSOR ACCOUNT, not a store).** `account_id` /
`merchant_account_id` → **`ma_store_pnl.canonical_store_index`** = the mig-`314` account→store index
(`raw_ma_fulfillment` tspid×business_address ∪ the `ma_account_store_map` override) collapsed onto the
canonical store spelling by `coa.store_resolver`. Store CODE + MARKET then come from the org's own
vocabulary (`store_mapping` ∪ §13a `core.scope.store_market_resolver`). Both feeds resolve through the
SAME index, so a store's dollars and its subscriber count land on ONE row. An account the index cannot
place renders `"(Unassigned)"` — never dropped, never guessed — and is NAMED in the payload
(`store_attribution.unresolved_accounts` + `store_note`) so the owner can pin it. Pure truth table:
`resolve_ma_account_store` (harness E). `canonical_store_index` is the SAME map
`payables.engine.ma_store_resolution` (step 3, commit `4d5fcb0`) binds — extracted, not copied.

**Market options** compose through §13c `core.scope.org_market_options` (pinned CANONICAL in
`harness_market_enumeration_guard.py`). `"(Unassigned)"` is a placement, never offered as a market.

**Provenance in the payload** (read-only, moves no figure): `source`, `source_label`, `ma_coverage`
(per-period `commission_rows`/`daily_tx_rows`/`residual_rows`/`entities`), `data_note` (a month with
daily-tx rows but no Commission Details rows is airtime-only, not a decline), `entity_note` (partial
master-agent entity coverage), `store_attribution`/`store_note`. Processor/distributor NAMES in that
copy come from the mig-`953` `report_term` vocabulary (`report_labels.carrier_term` /
`term_from_payload`) — tenant override > house carrier preset > neutral noun, never a vendor literal.

**FIXED 2026-09-04 (owner: "residual per subscriber is not giving any information on the luxelink
side, it is also not showing the store name just the store codes").** Two root causes, both live-measured
on luxelink (`854f6d7b…`): (1) the coverage counter in `_aggregate_ma` incremented a key it had not
seeded → `KeyError` INSIDE the residual sweep's blanket `except Exception: pass`, aborting the entire
Total-side residual aggregation after ONE row — 18,070 residual rows / $73,846.71 reported as $0, the
report showing airtime margin alone as "residual" (Aug 2026 read $19,488.16 = $19,481.36 merchant
discount + the single $6.80 row that got in before the raise; after the fix Aug = $54,972.03 =
`mi_income` $35,490.67 + merchant discount $19,481.36, matching `ma_tx_pnl_bookings` to the penny).
The residual filter was also a server-side `.ilike('%residual%')` — HALF the mig-309 union, dropping
order-type-only rows the books DO book. (2) Store rows rendered the processor account id ("170084") or
the master-agent ENTITY name ("Luxelink Wireless LLC"), and the two feeds bucketed under DIFFERENT
labels, so stores showed dollars with no subscribers beside stores with subscribers and no dollars;
20/20 accounts resolve through the mig-314 index (markets NY + Chicago). The synthetic
`"(VidaPay/MA)"` market stamp — a carrier word masquerading as a market — is gone.
**Proof:** `backend/harness_residual_per_sub.py`.

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
- **Failing-KPI report (owner directive 2026-09-03, with the mig-948 dashboards):**
  `GET /kpi-failing/{period}` — a VIEW over the EXISTING KPI machinery, never a sibling
  derivation: defs + targets = `_kpi_defs(org_id)` + the per-period `payout_config` target
  columns (the exact `/coaching` / action-plan resolution); STORE actuals = `get_dlar_store_kpis`
  called IN-PROCESS (same raw_dlar_store read, storeops span filter inherited); REP actuals =
  `rep_commissions.kpi_values` (what the pay engine tiered on), span-filtered on store;
  store→market via §13a `_store_market_resolver`. Classification is PURE
  (`commcalc/kpi_failing.py`: below-target fails, a missing value is `no_data` and NEVER counted
  as failing, `STORE_KPI_COLUMNS` = the metric-key→raw_dlar_store column map; proof
  `harness_kpi_failing.py`). Frontend `commcalc/kpi-failing/page.tsx` — StandardFilterBar
  (month · markets · stores · reps), summary tiles + per-metric rollup, store rows expanding to
  KPI detail + the failing reps at that store, WYSIWYG export. Registered in `reports.ts`
  (Commissions) + REPORT_DIRECTORY ('targets') + REPORT_TREES ('commissions' area); tiled on the
  Management Overview dashboard (§14 mig 948).

**Endpoints:** `/dlar-store/{period}` `10278`; `/kpi-failing/{period}` (beside it); `/carrier-kpi-metrics` `19757`; `/exec-overview/{period}`
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
  `/device-history` `17015`; MI inventory-aging bonus gate (`config.max_days`, default 10);
  `payables/engine.ma_store_resolution` — device-grain store source for the Device Forecasting /
  Vendor Payables Total-side store attribution (2026-09-04, §15).

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

- **DM-verification audit trail + export parity (owner directive 2026-09-02, mig `935`):** the
  store-entered ORIGINALS were never overwritten (rep figures live on `commcalc.daily_closing`;
  DM corrections in the separate `dm_*` columns of `daily_closing_verification`, applied as a
  read-time overlay — `closing/verified_overlay.py`), but (a) the two date-range exports never
  showed the DM's modified values or the envelope photo, and (b) the verification row is an
  UPSERT, so a second DM save overwrote the previous `dm_*` correction with no history. Now:
  `POST /closing/verify` appends one revision row per changed save to
  **`commcalc.daily_closing_verification_audit`** (mig `935`; append-only — new values, prior
  values, `changed_fields`, `edited_after_verify` = a money figure changed on an
  ALREADY-verified day — the owner's exact scenario; pure builder
  `closing/verification_audit.py`, proof `harness_dm_verification_audit.py`);
  `GET /closing/submissions` returns the six `dm_*` modified values + `dm_note` + `dm_corrected`
  per row (store-day grain) AND `envelope_view_url`; `GET /closing/summary` store cards carry
  `totals_original` (the pre-overlay store-entered aggregate, present only when a correction
  applied) next to the authoritative overlaid `totals`; NEW `GET /closing/envelope-view?row_id=`
  signs the private-bucket envelope photo on demand and 302-redirects (org-scoped lookup — the
  clickable link exports carry; list endpoints still never do per-row Storage round trips).
  Frontend: the DM Verify export (`DailyClosingVerify.tsx` — Original vs DM columns + per-rep
  envelope link) and the dashboard export (`closing/_lib/SubmissionsTable.tsx` — DM columns +
  clickable envelope link) show original and modified side by side.

- **Envelope report + envelope-short chargebacks (owner directive 2026-09-02, mig `936`) — a
  REPORT:** one line per envelope (= one `daily_closing` rep-day row): declared cash, the
  management COUNT (`commcalc.envelope_count`, mig `936` — counted amount, variance,
  short/over/match, comment, counted_by), the envelope photo link, and the linked chargeback.
  `GET /closing/envelope-report` (RULE FIVE standard filters: date range + markets/stores/reps,
  bucket-aware markets, manager-span keyset; `status` filter
  short|over|match|uncounted|discrepancy|commented|chargeback);
  `POST /closing/envelope-count` saves a count and — short + `assign_chargeback` — inserts a
  PENDING PARENT row into the EXISTING `commcalc.ops_chargeback` (mig `504`) with reason
  **`envelope_short`**, `applied_to='commission'`, amount = the ACTUAL shortage (idempotent on
  the mig-504 parent key; unticking deletes the link only while still pending);
  `POST /closing/envelope-chargeback/decide` = the same `ops_chargebacks.decide_chargeback`
  machinery, reason-filtered, management-gated. The reason auto-surfaces in the Ops Chargeback
  Amounts policy editor ("reasons in the wild"); POSTED rows settle through the commission
  module's existing `_settle_ops_chargebacks` cascade (commission-agent domain — closing only
  ever creates parents). Pure logic `closing/envelope_report.py`, proof
  `harness_envelope_report.py`. Scheduled/on-demand sends: notify report key
  **`closing_envelope_report`** (`notify/closing_reports.py`, W3 pattern — in-process reuse of
  the live endpoint). Frontend `/closing/envelope-report` (`closing/envelope-report/page.tsx`;
  NAV Daily Closing group + REPORT_DIRECTORY 'ops').

- **Closing entry-quality coaching (owner directive 2026-09-02, mig `937`):** "a training walkthru
  for an employee if their data is not entered correctly for a second day in a row". Detection is
  PURE (`closing/entry_quality.py`, proof `harness_closing_entry_quality.py`): signals
  `dm_corrected` (the store-day the employee submitted on was DM-verified WITH a correction) and
  `sent_to_review` (the row hit `auto_accepted`/`mgmt_flag`); an employee with
  `threshold_days` (house default 2) CONSECUTIVE incorrect days gets the walkthrough. Config
  per org (`commcalc.closing_entry_quality_config`, mig `937`: enabled / threshold_days /
  signals / notify_channel none|email|whatsapp|both / message_template / tour_slug — default the
  EXISTING Training-Center tour `closing-submit`); idempotency log
  `commcalc.closing_entry_coaching` (one row per employee × streak_end). Endpoints:
  `GET /closing/entry-quality` (management report), `GET /closing/entry-quality/me` (rep banner —
  NO dollar amounts, money-secrecy preserved), `POST /closing/entry-quality/run-due`
  (NOTIFY_RUN_SECRET cron sweep; email via notify channels when the org opts in) +
  `/entry-quality/run` (manual, one org). Frontend: guidance banner + "Walk me through"
  (`startTour(tour_slug)`) on `ClosingSubmitForm.tsx`.

- **Bill Payment Pickup & Deposit (owner directive 2026-09-02, mig `942`):** "one more pick up for
  the bill payment pickup and deposit menu, just under the cash pick up module, the same process
  same wiring as the cash pick up." SIBLING table **`commcalc.billpay_pickup`** (mig `942` — the
  mig-034 cash_pickup shape + the mig-089 deposit columns; sibling, NOT a kind column: the UNIQUE
  upsert key is load-bearing and a missed kind filter would leak billpay rows into the general
  cash movement — fail-closed by construction) + **`commcalc.billpay_pickup_config`** (recipient;
  notify falls back to the cash-pickup recipient). ONE parameterized machinery: the cash
  endpoints delegate to shared impls (`_confirm_pickup_impl` / `_undo_pickup_impl` /
  `_record_deposit_impl` / `_get/_put_pickup_config_impl`, `closing/router.py`) which the
  billpay endpoints point at the sibling table. Endpoints: `GET /closing/billpay-pickups`
  (the /pickups mirror — envelope amount = declared `epay_on_cash`; same filters/keyset/market
  fallback; `by_store` = bill-pay position declared−picked=pending via `_billpay_position_core`,
  DM `dm_epay_cash` corrections replacing verified days), `POST /closing/billpay-pickup`,
  `POST /closing/billpay-pickup/undo` (409 after disposition), `POST /closing/billpay-pickup/
  deposit` (declared default = the envelope's ePay-on-cash), `GET/PUT /closing/
  billpay-pickup-config`. **MONEY MODEL (evidence-verified, LuxeLink live 2026-09-02):** declared
  cash (`t_cash`) INCLUDES ePay-on-cash (`epay_on_cash` is a subset breakdown — 177/231 live
  rows subset, the 54 exceptions are the mig-939 coverage defect class;
  `deposit_recon.cash_for_basis`: store_cash = t_cash − epay_on_cash BY DEFINITION; owner
  verbatim "Total cash in store including Bill Payments"), and the general cash_pickup envelope
  sweeps the FULL declared cash — so by default a billpay pickup NEVER relieves
  `_cash_position_core` (no double-count; it is the physical counterpart of the mig-939
  remittance/coverage side and leaves the mig-938 BS line + Cash Flow untouched). Per-org knob
  **`cash_pickup_config.billpay_relieves_cash`** (mig `942`, default false = byte-identical)
  folds billpay pickups into the general outflows exactly once for split-envelope orgs
  (pure `closing/billpay_pickup.fold_billpay_outflows`; rides the mig-938 verified-day symmetry
  + zero floor). Pure logic `closing/billpay_pickup.py`; proof `harness_billpay_pickup.py`.
  Frontend `/closing/billpay-pickup` (`closing/billpay-pickup/page.tsx` — the pickup page
  mirror; NAV Daily Closing group directly under Cash Pickup + REPORT_DIRECTORY 'ops').

- **Management one-screen cash recon + POS bill-pay cross-check (owner directive 2026-09-02):**
  "for the management it should show what has been received as per the system in both cash pick
  up, epay pick up and the cash declared and the epay declared fields and the credit fields of
  what has been recorded by the POS reports … the employee is gated out of it, dm is gated out
  of it only market manager and above see it." `GET /closing/cash-recon-management` — per
  (store, day): declared cash/credit/ePay splits (DM overlay winning), recorded cash + billpay
  pickups, POS X-report cash/card (`_xreport_tenders_by_store`), POS bill payments via the SAME
  processor resolution the mig-939 coverage recon uses (`commcalc.router.
  _billpay_processor_by_store_day` + `_metric_source`, lazily imported — never a second path),
  and a declared-vs-POS mismatch flag (pure `billpay_pickup.billpay_pos_mismatch`; feed absent ⇒
  `no_pos_data`, never a fake mismatch). **GATE:** `billpay_pickup.can_see_cash_recon` —
  market manager and above, the mig-434 pay-visibility posture (fail-closed; scope-'all' passes;
  allow-list = `storeops.tenants.cash_recon_visible_roles` (mig `942`), NULL ⇒
  `pay_visibility.DEFAULT_VISIBLE_ROLES`); rows additionally span-scoped through the manager
  keyset. Submit-form wording (same directive): the cash tender reads "Total cash in store
  including Bill Payments" (stock-label-only override; a tenant's custom label wins) and the
  ePay section reads "Bill Payments, already included above" (`ClosingSubmitForm.tsx`). Proof
  `harness_billpay_pickup.py` (§E). Frontend `/closing/cash-recon-management`
  (`closing/cash-recon-management/page.tsx`; NAV Daily Closing group + REPORT_DIRECTORY 'ops').

- **POS-beside-declared on the pickup pages + deposit accountability (owner directive
  2026-09-02, mig `943`):** two asks. (1) "the cash pick up and bill pick up only show what the
  stores have entered but not what is in the system, from the pos report, those numbers should
  be right next to these numbers": `GET /closing/pickups` and `GET /closing/billpay-pickups`
  rows now carry `pos_cash`/`pos_billpay` + `pos_delta` + `pos_status`
  (ok|mismatch|no_pos_data) + `pos_declared_day`, resolved through the SHARED helpers
  **`closing/router._pos_tenders_for_days`** (X-report, `_xreport_tenders_by_store`) and
  **`_pos_billpay_for_days`** (the mig-939 metric_source_of_truth processor resolution) —
  factored OUT of `cash_recon_management`, which now calls the same two helpers: one path,
  never a second derivation. Comparison at store-day grain ($1 tolerance): cash declared = Σ
  `t_cash or store_cash` (the cash-recon-management formula); billpay declared = Σ
  `epay_on_cash` (the page's own envelopes; store codes pre-translated through the canonical
  key). Honest gaps: X-report missing ⇒ `no_pos_data`; billpay feed present-but-silent ⇒ honest
  zero, feed absent ⇒ `no_pos_data` — pure `deposit_accountability.pos_next_to`.
  (2) "cash deposit capture should be shown as a separate line item under cash deposit recon …
  every cash deposit should be accompanied by the bank deposit slip … handed over to the
  management then a check box … then the management should be able to confirm … making the
  color green for the days the cash has been accounted for … similar workflow as did for the
  approval": mig `943` adds `mgmt_confirmed`/`mgmt_confirmed_by`/`mgmt_confirmed_at` to BOTH
  `commcalc.cash_pickup` and `commcalc.billpay_pickup` (the §14 approvals actor+timestamp
  shape). `GET /closing/deposit-recon` day blocks gain `pickup_deposit` — the pickup-flow
  deposit CAPTURE (slips + OCR amounts, `deposit_accountability.pickup_deposit_line`) as its
  OWN line item, never summed into expected/deposited (bank_deposit stays the recon's source;
  report population unchanged). NEW `GET /closing/deposit-accountability` (keyset-scoped
  board): per (store, day) envelope states — deposited / MISSING_SLIP / handed_unconfirmed /
  handed_confirmed / undisposed — and THE GREEN RULE: green ⇔ ≥1 picked-up envelope AND every
  picked-up envelope accounted (deposited WITH slip, or handed AND mgmt-confirmed). SLIP
  POSTURE = flag-never-block (mig-089 precedent + live evidence: all 9 pre-943 deposits have no
  slip — a hard block would strand them); a slip-less day loudly `missing_slip`, never green.
  NEW `POST /closing/deposit-mgmt-confirm` (confirm/revoke, per store-day or per envelope) —
  GATED `billpay_pickup.can_see_cash_recon` (market manager and above, mig-434 posture,
  fail-closed; DMs keep recording pickups/dispositions as today). Pure state machine
  `closing/deposit_accountability.py` (`envelope_state`/`day_accountability`); proof
  `harness_deposit_accountability.py` (green-rule truth table + POS-beside semantics + gate).
  Frontend: POS column on `closing/pickup/page.tsx` + `closing/billpay-pickup/page.tsx`
  (cash-recon-management mismatch styling); capture line item + the green-day
  `AccountabilityBoard` (handed + mgmt-confirm checkboxes) on `closing/deposit-recon/page.tsx`.

- **Actual cash picked from the envelope (owner directive 2026-09-04, mig `949`):** "for cash
  pick up, one more column is needed actual cash picked from envelope." The DM confirming a
  pickup records the ACTUAL cash physically taken, beside the declared snapshot:
  `commcalc.cash_pickup.actual_picked_amount` (+ the `billpay_pickup` sibling — mig-942 shared
  machinery, so `POST /closing/billpay-pickup` gets it for free), written by
  `_confirm_pickup_impl` ONLY when the item carries `actual_amount` (NULL = not recorded, never
  a fake 0; blank clears). DELIBERATELY NOT `envelope_count.counted_amount` (duplicate-check
  verdict): that is MANAGEMENT's later count (mig 936 — different actor/moment, keyed on the
  re-sync-replaced `closing_row_id`, and the envelope_short chargeback keys off it); the DM's
  pickup-time count lives on the pickup row like the deposit step's
  `deposit_amount`/`declared_amount` pair. Variance + short/over/match REUSES
  `envelope_report.count_fields` via pure **`closing/pickup_actual.py`**
  (`variance_fields`/`row_variance`/`outflow_amount`/`actual_relieves_cash`) — the envelope
  report's own truth table, one derivation. MONEY POSTURE: the declared `amount` keeps
  relieving `_cash_position_core` (→ mig-938 BS store-cash line, Cash Position, Store Cash on
  Hand, pickups `by_store`) — byte-identical default; per-org knob
  **`cash_pickup_config.pickup_actual_relieves_cash`** (mig 949, default false, owner-gated
  commented seed — flipping it moves the BS cash number) makes the recorded ACTUAL relieve the
  movement where present (declared where none recorded; billpay fold rides the same knob via
  `pickup_totals_by_store_day(actual_wins=)`). The knob-on select adds the column (knob true ⇒
  mig applied), so a pre-949 schema can never turn into "zero pickups". Variance is DISPLAY +
  FLAG everywhere: `GET /closing/pickups` + `/billpay-pickups` envelopes carry
  `actual_picked_amount`/`pickup_variance`/`pickup_variance_status`; the deposit-accountability
  day view (`day_accountability`) carries per-envelope actuals + day `pickup_short_rows`/
  `pickup_over_rows`/`pickup_variance_total` + summary `short_pickup_days` — NEVER gating green.
  Proof `harness_cash_pickup.py` (§6 truth table: knob-off byte-identity, knob-on outflow swap,
  blank-clears, billpay mirror) + `harness_deposit_accountability.py` (§G). Frontend: "Actual
  picked" input column on `closing/pickup/page.tsx` + `closing/billpay-pickup/page.tsx`
  (optional; live short/over hint), short-pickup chips on the `deposit-recon` board.

- **Bill-pay-on-credit column + 3-WAY bill-payment recon (owner directive 2026-09-02 #2, mig
  `944`):** "in the billpayment pick, add another column for bill payment on credit card, and the
  pos bill payments are showing 0 … two ways it will be done and a part of 3 way recon for bill
  payments … again nothing hardcoded and everything indexed for future." FOUR pieces. (1) COLUMN:
  `GET /closing/billpay-pickups` envelopes carry `credit` (declared `epay_on_credit`) +
  `total_credit`; a credit-only closing is a DISPLAY row (no checkbox — card money settles with
  the processor, nothing to pick up; `ready` counts cash envelopes only), and the POS
  comparison base becomes declared cash+credit (the processor feed reports every tender
  together — cash-only comparison would fake-flag every card bill payment).
  (2) POS-ZERO DEFECT FIX (evidence-first, org 854f… live): three stacked causes — (a) the
  daily-TX leg of `_billpay_processor_by_store(_day)` summed EVERY `raw_ma_daily_tx` row
  (18,120/22,163 Aug rows were handsets/residuals/spiffs) → now filtered through
  `metric_recon.ma_billpay_predicate` (config `metric_source_of_truth.processor_order_types` /
  `processor_product_tokens`, mig 944, house defaults `Sales Order` + `rtr|wallet funding`,
  UNION the org's mig-214 `billpay_products` exact list); (b) account→store rode ONLY
  `storeops.store_merchant_id` (empty) → now `_vidapay_account_resolver` falls back to the
  mig-314 account→store index (`ma_store_pnl.load_store_index` — the P&L/BS resolution, reused);
  (c) `_billpay_processor_name` auto-detect required an ENABLED `data_source` row — a
  paused/expired portal login hid 52k already-ingested rows → now falls back to a
  configured-but-disabled row (`enabled` gates the sweep, not the org's processor identity).
  (3) THE 3-WAY RECON per (store, day) on `GET /closing/cash-recon-management`: Leg A declared
  (`epay_on_cash`+`epay_on_credit`, DM overlay) vs Leg B the email-ingested SALES TRANSACTIONS
  (`_billpay_sales_by_store_day` → the SHARED `_sales_cell_agg` exec `bill_payment`
  classification, day grain, via closing's `_sales_billpay_for_days` — sibling of
  `_pos_billpay_for_days`, one path) WITH the tender split (`_sales_cell_agg(tender_cfg=…)`
  additive accumulators `bill_amt_card/cash/mixed/other/tendered`;
  `metric_recon.classify_tender`, config `accessory_config.billpay_card_tenders`/
  `billpay_cash_tenders` mig 944, defaults credit|debit / cash; multi-tender ⇒ 'mixed', never
  guessed; live evidence: 10,720/10,777 Aug rows carry tender_type) vs Leg C the processor
  side ((2) above — 'owner's portal' ePay feed or daily-TX per the mig-923 resolution). Pure
  math `metric_recon.reconcile_billpay_three_way_days` (honest gaps: feed absent ⇒ leg None +
  `no_sales_data`/`no_processor_data`, never a fake zero/mismatch; present-but-silent ⇒ honest
  0; `declared_only`); rows gain `sales_billpay(_card/_cash/_mixed)`, `three_way_status`,
  pair deltas; payload gains `three_way` + `sales_source`. (4) W3 scheduled report
  **`closing_billpay_recon`** (`notify/closing_reports.py` — cash_recon_management in-process,
  gate inherited). Proof `harness_billpay_threeway.py` (tender truth table, row-filter defect
  class, 3-way truth table, `_sales_cell_agg` byte-identity with `tender_cfg=None`);
  regressions harness_billpay_pickup / billpay_pl / deposit_accountability / cash_pickup.
  Frontend: credit column + stat on `closing/billpay-pickup/page.tsx`; sales-tx/3-way columns
  on `closing/cash-recon-management/page.tsx`.

- **Multi-market-grant market filter on the pickup pages (OWNER BUG REPORT 2026-09-02: "the cash
  pick up and the bill pay pick up for the district managers is not showing the daily envelopes …
  whereas admin i can see all"):** a scope-'market' login's `app_users.market` is a COMMA-JOINED
  multi-market grant ("Chicago, NY" = both markets; `core.scope.login_grant_breakdown` comma-splits
  it for the SPAN), and both pickup pages auto-apply that raw grant as the singular `market=` param
  — which `GET /closing/pickups` / `GET /closing/billpay-pickups` compared as ONE exact string, so
  every resolved-market envelope was dropped (live 2026-09-02: 12/12 dropped by the market filter
  for DM E189, 0 by her keyset — the span was never the problem; admin scope-'all' gets no auto
  market). PRE-EXISTING on the cash side, mirrored into billpay at birth; the DM dashboard/DM-Verify
  never broke because they send the CSV `markets=` param, which `_resolve_market_filter` already
  comma-splits. FIX at the shared source (duplicate-check: REUSED the working pages' mechanism):
  both pickup endpoints + `GET /closing/recon` (same auto-apply, same exact-match class) now
  resolve `market=` through `_resolve_market_filter`, whose singular arm admits the comma-split
  components ALONGSIDE the whole string (a canonical market name containing a comma still matches
  whole; pure widening, never narrows). Keyset/span gating, the pickup pages' blank-market
  leniency, and empty-span fail-closed are all UNCHANGED. Proof
  `harness_pickup_market_span.py` (resolver truth table + span truth table: multi-market DM /
  single-market DM / admin / empty-span, both pickup endpoints); regressions harness_cash_pickup /
  billpay_pickup / deposit_accountability / billpay_threeway / closing_reports_span_scope.

---

### 12a. Merchant-processor portals — the external credit-card / POS-merchant scrape

**Owner directive 2026-09-04, verbatim:** *"a lot of tenants will be using 3rd party credit card
processor which is not integrated to the pos, which is recorded as external credit card … need to pull
in data from the merchants from both pos merchant provider and the external credit card provider …
need to scrape the reports on a daily basis and tally with our platform as entered by the employees."*

**Purpose.** The standalone card terminal's money never reaches the POS, so the only record of it is
what an employee typed at closing. This feed is the other side of that tally.

- **Adapters (PURE):** `commcalc/merchant_portals.py` — the portal registry + normalizers.
  `PORTALS` keys: `payanywhere` (paymentshub.com — PayAnywhere/NAB, the EXTERNAL card terminal both
  current tenants run), `transfirst` (translink.transfirst.com — TSYS, POS merchant),
  `businesstrack` (cl.businesstrack.com — Fiserv ClientLine, POS merchant). Key functions:
  `card_brand`, `money`, `iso_date`, `map_headers`, `normalize_settlement`, `normalize_batches`,
  `dedupe_settlement`, `totals_by_store_day`, `settlement_role`, `public_catalog`.
- **Runtime:** `commcalc/merchant_portal_sweep.py` — `run_merchant_portal_sweep` (scheduled, cold
  session restore), `pull_reports_on_page` (the ONE pull implementation, also used live),
  `make_pull_fn` (the `pull_fn` `live_login.start_session` already accepts), `ingest_report`,
  `resolve_stores`, `store_settlement`, `store_batches`, `date_range`, `read_table`.
- **Login + 2FA:** REUSES `commcalc/live_login.py` verbatim — one live browser from login through code
  entry, CDP screencast to the operator, the human types the code into the very page that requested
  it. No second login engine. The durable `data_source.session_state` then drives every daily pull.
- **Session health:** `commcalc/portal_session_health.py` (PURE) — `evaluate`, `should_notify`,
  `summarize`, `worse_of`. States, worst-last: `healthy < expiring_soon < error < expired <
  needs_login < never_linked`. Surfaced as a chip on the data-source row and by
  `GET /commcalc/merchant-portals/health`.
- **Authenticator (TOTP):** `commcalc/portal_totp.py` (PURE, RFC 6238) — used ONLY where the owner has
  enrolled the portal account in an authenticator app and supplied the secret. Never for SMS/email OTP
  and never for a captcha. Secret lives in `data_source.totp_secret`, inside `router._SOURCE_SECRETS`.
- **Config (RULE TWO):** per-source on `commcalc.data_source` — `processor` (which portal),
  `settlement_role`, `portal_reports`, `portal_calibration`, `portal_window_days`,
  `session_warn_hours`, plus the shared `enabled/frequency/hour/next_run_at/proxy_url`.
- **Store attribution:** `storeops.store_merchant_id` (mig `902`) via `storeops/merchant_ids.resolve_map`
  — the SAME map the ePay/VidaPay feeds use. No new mapping table. An unmapped merchant id is
  REPORTED, never counted as $0 for a store.
- **Scheduling:** the EXISTING `POST /commcalc/data-sources/sweep/run-due`, dispatching on
  `router._SOURCE_SCRAPERS[processor]`. Mig `956` makes that cron self-registering on boot
  (`main.py:_data_sources_cron_startup` → `router._ensure_data_sources_cron` →
  `commcalc.ensure_data_sources_cron`) — mig `241` had only left commented-out SQL for a human to run.
- **Consumed by:** the daily-closing card tally, `closing/external_credit_recon.py` (sibling work, migs
  `960`/`961`), which resolves this feed through `commcalc.report_pull_map` (`report_key`
  `merchant_settlement` / `merchant_funding`, seeded by mig 955) and filters on `settlement_role`
  (`external_cc` | `pos_merchant` — slugs shared verbatim between the two modules).
- **Admin attention:** `commcalc/import_audit.p_portal_sessions` (provider `commcalc_portal_sessions`)
  raises a portal login whose session needs a human into `GET /core/attention` (the admin login popup),
  so a dead session is not merely a chip on a page nobody is looking at. Fires only on
  `never_linked`/`expired`/`needs_login`; `expiring_soon` stays a chip.
- **Harnesses:** `harness_merchant_portals.py` (73), `harness_portal_session_health.py` (41),
  `harness_portal_totp.py` (35 — RFC 6238 vectors + secret hygiene).

- **External credit machine + CARD SETTLEMENT RECON (owner directive 2026-09-04, migs `960`/`961`):**
  "a lot of tenants will be using 3rd party credit card processor which is not integrated to the
  pos, which is recorded as external credit card … need to scrape the reports on a daily basis and
  tally with our platform as entered by the employees … need to add another field on daily closing
  as external credit machine — (label should be changed to be renamed as White machine for these
  tenants but remain as external credit card for other tenants)". THREE pieces, and the FIRST
  finding is that most of it already existed.
  (1) **THE FIELD ALREADY EXISTED — no new column.** `commcalc.daily_closing.t_ext_cc` ("External
  Credit Card (separate terminal)") has been a physical column since mig `103`, written by
  `ClosingSubmitForm` / `POST /closing/row` / `/closing/attempt`, read by
  `closing/router._row_display_tenders`, summed into `/closing/summary` `totals.t_ext_cc` and
  `/closing/submissions`, and **already inside the CARD base of the mig-939 coverage recon**
  (`commcalc.router._closing_collected_by_store_day`: card = `t_credit|store_cc` + `t_ext_cc` +
  `epay_cc`) **and the mig-944 3-way recon** (`cash_recon_management`, same expression). Live
  2026-09-04: **$62,107.78 over 315 house-org rows + $1,577.24 over 9 LuxeLink rows**. Excluding it
  would MOVE a booked comparison base, so it is deliberately left exactly as it is — no knob, no
  seed, nothing to approve.
  (2) **THE LABEL is the mig-945/953 preset machinery, reused** — NOT a second mechanism: one NEW
  key `closing_t_ext_cc` in `report_labels.LABELABLE_COLUMNS` (built-in default "External Credit
  Card") + mig `960` house carrier presets on the EXISTING `commcalc.ui_label_override`
  (`report_col:boost` / `report_col:total` → "White machine"). Resolution and lazy carrier
  auto-assign are unchanged: tenant override > house carrier preset > built-in, keyed off the org's
  `commcalc.carrier` rows; an org with no carrier/preset renders the built-in wording,
  byte-identical. Rendered through the existing `useReportLabels().colLabel` on
  `ClosingSubmitForm.tsx`, `closing/_lib/SubmissionsTable.tsx` and `DailyClosingVerify.tsx`
  (grid + export headers from ONE resolution, mig-932 pattern). "White machine" is carrier-neutral
  wording — `harness_carrier_vocab_guard.py` stays green.
  (3) **DM SPLIT (mig `961`) — the defect the tally would otherwise have fabricated.**
  `verified_overlay.apply_overlay` maps the DM's ONE corrected card figure `dm_store_cc` onto both
  column families and ZEROED the folded siblings (`epay_cc`, `t_ext_cc`) — correct arithmetic for a
  COMBINED total, but it destroys the external split on every corrected store-day. Live evidence
  2026-09-04: **124 of the 320 store-days carrying external-credit money ($26,880.45) are
  DM-card-corrected**, so a naive tally would have called that entire amount SHORT. Mig 961 adds
  `dm_ext_cc` to `commcalc.daily_closing_verification` **and its append-only mig-935 audit twin**
  (`dm_ext_cc` + `prior_dm_ext_cc`), joined to `verification_audit.DM_FIELDS` so `changed_fields` /
  `build_audit_row` / `edited_after_verify` / `submission_dm_fields` and the Original-vs-DM exports
  cover it with NO new logic. **MONEY INVARIANT, proven:** `dm_ext_cc` NULL ⇒ pre-961 behavior
  byte-for-byte; `dm_ext_cc` set ⇒ `t_ext_cc = dm_ext_cc` and `t_credit = dm_store_cc − dm_ext_cc`,
  so `t_credit + t_ext_cc == dm_store_cc` in BOTH branches — the card TOTAL every consumer books
  never moves, only the split becomes known. `build_overlay_map` retries the legacy six-column
  select, and `POST /closing/verify` sends/upserts `dm_ext_cc` only when stated (with a strip-and-
  retry on both the audit insert and the upsert), so a pre-961 database keeps working.
  (4) **THE TALLY.** `GET /closing/external-credit-recon` — per (store, day, processor ROLE):
  DECLARED at closing vs SETTLED by the processor, variance, verdict. **The verdict IS
  `envelope_report.count_fields`** (the mig-936 truth table, the same one `pickup_actual.py` reuses
  — expected = declared, counted = settled, so a negative variance is SHORT); there is no second
  classifier. Pure logic `closing/external_credit_recon.py`
  (`tender_processor_map`/`role_columns`/`declared_cells`/`apply_dm_split`/
  `normalize_settlement_rows`/`settlement_cells`/`recon_row`/`assemble_rows`/`status_filter`/
  `totals`), which also OWNS the one `TENDER_COLUMN` map (`closing/router._TCOL` is re-pointed at
  it, not copied). ROLES are neutral slugs shared verbatim with the scrape side —
  `external_cc` (standalone terminal, not POS-integrated) / `pos_merchant` (the POS card tender's
  provider); the BRAND behind a role is data (`data_source.processor/settlement_role`,
  `store_merchant_id.processor`, `report_pull_map`), never a code branch (RULE TWO).
  **NOTHING HARDCODED — every resolution is an EXISTING one, reused:** declared-tender → role =
  `commcalc.closing_tender_def.processor_key` (mig 960) over the house map
  `DEFAULT_TENDER_PROCESSOR`; the feed's TABLE + COLUMN SPELLING = `commcalc.report_pull_map`
  (mig `207`, report_key `merchant_settlement`, org row over house — seeded by mig `955` pointing at
  `commcalc.merchant_settlement_day`); merchant id → store = `storeops.store_merchant_id` (mig
  `902`) via `storeops/merchant_ids.resolve_map` (applied again at READ time, so a store mapped
  after the pull is picked up without a re-scrape); tolerance = `metric_source_of_truth` (mig `923`)
  metric `card_settlement`, house default 0.00 (commented seed in mig 960).
  **MARKET is FULLY CANONICAL here, not an overlay (§13a/§13c).** Unlike its closing siblings — which
  read `storeops.stores.market` and fill blanks (classification OVERLAY) — this report resolves
  market ONLY through **`core.scope.market_by_code`** (the cached union storeops.stores ∪
  store_mapping ∪ store_aliases + the code-group fold) and takes ADDRESS ONLY off the roster, so it
  registers NO site in `harness_market_resolution_guard`. Two reasons this report is the first that
  needed it: (a) a **settlement-only store-day** — the processor settled money for a store that filed
  no closing row — has no roster row to join a market from at all; (b) a store whose market is
  spelled only in `store_mapping` (the MIRROR of B-1115/LI, whose market is only on `storeops.stores`)
  would bucket `(no market)` and vanish the instant a market filter is picked, silently hiding real
  settled money. The market DROPDOWN is likewise composed by **`core.scope.org_market_options`**
  (canonical vocabulary ∪ this report's own stamps; `(no market)` appended by the page) — pinned
  `external_credit_recon: CANONICAL` in `harness_market_enumeration_guard`, and the page reads
  `market_options` from the payload rather than from the loaded roster. Truth table for both
  divergence shapes + the settlement-only store: `harness_external_credit_recon.py` §J.
  Store/market filter application stays on the shared `_market_bucket` +
  `_resolve_market_filter`/`_resolve_store_filter` helpers. **HONEST GAPS, never a fake zero:**
  `no_processor_data` (feed unregistered, or the day is outside what the scrape covers),
  `no_declared_data`, `dm_merged` (3 above) — each carries `variance = None` and contributes to a
  COUNT only, never to a dollar total; a day the feed DOES cover but is silent about for a store is
  an honest 0.00 settled (the mig-944 present-but-silent vs absent distinction). An unmapped
  merchant id is surfaced in `unmapped`, never counted as $0 for a store. **GATE:**
  `billpay_pickup.can_see_cash_recon` (market manager and above, mig-434 posture, fail-closed 403 —
  the SAME gate and the same per-org `storeops.tenants.cash_recon_visible_roles` allow-list as the
  management cash recon, reused rather than a second gate), plus the manager keyset at admission.
  W3 scheduled/emailed report key **`closing_external_credit_recon`** (`notify/closing_reports.py`,
  the live endpoint in-process — gate inherited). Frontend `/closing/external-credit-recon`
  (`closing/external-credit-recon/page.tsx`; NAV Daily Closing group beside Cash Recon (Management)
  + REPORT_DIRECTORY `'ops'`; carrier-neutral page copy — the terminal's tenant name arrives in the
  payload's `role_titles`). Proof `harness_external_credit_recon.py` (§A config, §B declared leg,
  §C the mig-961 total-preservation invariant + audit trail, §D the adapter, §E the truth table AND
  its byte-identity with `count_fields`, §F honest gaps, §G totals/filters, §H RULE TWO + migration
  hygiene, §I the cross-agent contract with mig 955); regressions harness_verified_overlay /
  dm_verification_audit / envelope_report / report_labels / billpay_threeway / billpay_pickup /
  billpay_pl / cash_pickup / deposit_accountability / carrier_vocab_guard / org_scope_guard.

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

### 13a. CANONICAL STORE→MARKET RESOLUTION — the once-for-all contract (owner directive 2026-09-03)

**Owner (verbatim class):** "1115 liberty ave which has been assigned LI as the market does not show
up under any filter as in the rep incentive report but shows in the daily target report, again this
is an issue with index and using the same data everywhere, why can this not be fixed once for all."

**The doctrine.** The org's market truth is the UNION index `core.scope.market_index()`
(storeops.stores ∪ commcalc.store_mapping ∪ commcalc.store_aliases — §13 above). ANY code that
answers "which market is this store in?" for a filter, group-by, bucket, option list, or routing
fallback resolves through ONE of the shared helpers in `app/core/scope.py`:
  - `store_market_resolver(client, org_id)` → `(resolve(raw store string) → canonical market, markets)`
    (pure twin `build_store_market_lookup(idx)`): accepts store_code / any address spelling from
    either vocabulary / POS synonym (case+punctuation-insensitive via `_squash`) / unambiguous
    leading street number; FAIL-CLOSED '' on ambiguity — never an arbitrary winner.
  - `market_by_code(client, org_id)` → `{UPPER store_code → canonical market}` (pure twin
    `build_market_by_code(idx)`; folded first-non-empty across vocabularies, `code_groups`
    inheritance for two-codes-one-store tenants) — for rows that already carry a store_code.
Reading ONE vocabulary for market resolution is a DEFECT of the same class as: 2026-08 market
grants binding nothing, the 2026-09-02 P&L filter bug (§ P&L above), the 2026-09-02 DM pickup bug
(commit 79d9ef6), and this one.

**The 2026-09-03 root cause (row evidence, Cellfonz R Us org `…0001`):** `storeops.stores` has
`B-1115 / "1115 Liberty Ave" / market LI`; `commcalc.store_mapping` has NO row for it;
`rep_commissions` rows carry `store="1115 Liberty Ave"`. The Rep Incentive report
(`GET /commissions/{period}`, frontend `commcalc/reports/page.tsx`) stamped `market` via
`_store_market_resolver` — then a store_mapping-ONLY lookup → `''` → invisible under every market
filter; Daily Targets sources its roster from `storeops.stores` → visible. The same scan found a
second silent victim: `"2778 Ephraim Ave"` (8 rows, $1,709.77 payout) market-invisible the same
way. Both now resolve (LI / PA) — verified live against all 31 distinct rep_commissions store
spellings for the org (every one resolves a market).

**ENFORCED BY CI:** `backend/harness_market_resolution_guard.py` scans the backend for any query
reading a market column off `stores`/`store_mapping` and FAILS the build unless the site is
canonical or pinned there with a reviewed classification (CANONICAL / OVERLAY — blank-fill from the
canonical map, set values never overwritten / EDITOR / AUDITOR / GRANT / ROSTER / PAY-ENGINE /
ATTRIBUTION / STORED — asset's persisted conflict-audited backfill). The pin table in that harness
IS the per-module inventory; changing it is a reviewed act in the same PR. Resolver truth table
(incl. the 1115-Liberty shape, the mirror-image mapping-only shape, aliases, ambiguity fail-closed,
code_groups inheritance): `backend/harness_store_market_resolution.py`.

**Converged 2026-09-03 (all previously divergent):** commcalc `_store_market_resolver` (now
delegates to core.scope — fixes `/tax-collected`, `/commissions/{period}` **Rep Incentive**,
`/commission-statement`, `/sales-comparison`, `/comp/rep-pay-trend`, `/financing/{period}`,
`/atu-opportunity` in one move), Sales-Report + Exec-MTD inline resolver copies, `_market_for_fn`
(activation counts), `_prod_store_maps` (productivity), `_cr_market_resolver` (custom report),
`_accrual_market_map`, `/coaching/{period}`, `/targets/{period}/summary` (roster market overlay —
the mirror-image storeops-only gap), commission-plan roster, accessory-flags options, `_compute_gp`
+ expenses/commission/gp trends (`_trend_market_by_code`) + `_leg_store_index`; closing module
(`_overlay_canonical_market` on rollup/summary/recon/DM-verify/envelope/pickups/positions/
accountability — replaces the pickup pages' private stores+store_mapping inline union), closing
DM routing (`ops_chargebacks._dm_for_stores_batch`, storeops `_dm_for_store`/`_managers_above_dm`);
payables (`_market_by_store` + filter-options); pos tax-code resolve/markets/store-grid; storevisit
`/stores`; storeops hours-budgets; core `/filter-options` overlay; core onboarding tax coverage;
asset `_store_mapping_market_index` (storeops rows join the conflict-audited candidate pool),
asset registry stores + PO recommendations; account residual-subs market stamps.

**Deliberately NOT converged (documented divergence):** `commission_engine._read_store_market` —
commission PLAN ATTACHMENT (market-scope plans) reads store_mapping ONLY. It is a MONEY path:
widening it changes payouts, so it stays byte-identical until the owner approves (route via
commission-agent; the plan-assignment audit `router.py` mirror deliberately matches the engine).
Pinned PAY-ENGINE in the guard.

### 13b. CANONICAL ENTITY (COMPANY) ENUMERATION — tenant entities never cross orgs (owner directive 2026-09-04)

**Owner (verbatim):** "cash flow analysis in cellfonz r us has other companies like nova wave, and
luxelink in the drop down menu along with the T stores, need to fix this as a system not a band aid."

**The doctrine.** A tenant's COMPANY/ENTITY list (`commcalc.companies`) is per-org DATA, never
config: house-default inheritance applies to CONFIG rows only and NEVER unions or falls back
another org's entities into a tenant's enumeration — the house org gets ONLY its own companies
through the exact same predicate as every other org. ANY code that answers "which companies does
this org have?" — a dropdown, a scope inventory, a picker, an attribution map, an attention audit —
resolves through the ONE canonical helper:
  - `account/coa.org_companies(client, org_id, cols)` — org-scoped, ordered, FAIL-CLOSED: blank
    org raises; every returned row is double-filtered by the pure core `coa.own_entities(rows,
    org_id)` (rows with a missing/foreign org_id DROP even if a poisoned client/view hands them
    back).
  - `coa.filter_org_scopes(scopes, own_company_ids)` (pure) — the DROPDOWN cross-check: a
    `company:<id>` scope renders ONLY when `<id>` is in the org's own current inventory, so a
    stale snapshot (deleted company) or a poisoned snapshot (foreign entity) can sit in
    `account_statements` and still never reach a picker. Wired into `GET /account/overview/{period}`
    (the single scope-picker source for the Account dashboard / P&L / Balance Sheet / Cash Flow
    pages) and `analysis.assemble(own_company_ids=…)` (per-company comparison series).

**The 2026-09-04 root cause (row evidence, mig `952`):** not an unscoped query — every read was
`.eq('org_id', …)`-scoped — but POISONED ROWS: LuxeLink's entities "Novawave Communications LLC"
(`9b22c0d8…`) and "Luxelink Wireless LLC" (`b5993b9d…`) were created UNDER the house org as a
batch on 2026-06-27 (the LuxeLink tenant `854f6d7b…` got its own proper rows 06-28/07-01), so
`statement_engine._scopes` dutifully computed all-zero `company:` snapshots for them and the
cellfonz dropdowns listed them for July–September 2026. Zero store assignments, zero journal
references, all-zero payloads → removed by id in mig `952` together with their 16 snapshot rows
and the 2 stale §19.15 Diversey store-scope rows (source rows removed 2026-09-03; July 2026 needs
one recompute after applying to purge the $29.99 remnant from the stored consolidated P&L).

**ENFORCED BY CI:** the entity-enumeration section of `backend/harness_org_scope_guard.py` scans
the WHOLE backend for `.table('companies')` and fails the build on any select outside
`coa.org_companies` (writes must be org-scoped/payload-scoped; count-only probes — billing's
quantity driver — must be org-scoped; exactly ONE canonical read is pinned, and the helper must
keep both its `.eq('org_id', …)` and its `own_entities` double filter). Isolation truth table
(org A never in org B's list either direction, house gets only its own, blank org raises, orphan
rows drop, foreign/stale/malformed `company:` scopes never render):
`backend/harness_finance_entity_enumeration.py`.

**Converged 2026-09-04:** `account/router.list_companies` (`GET /account/companies` — the journal
+ companies-page picker), `list_stores` (assignment page), `put_journal` company echo,
`overview` (scope dropdowns, + `filter_org_scopes`), `financial_analysis` (passes
`own_company_ids`), `coa.store_company_map` (⇒ `company_assignment` ⇒ `engine.compute_and_store`,
`statement_engine.statement`/`compute_and_store`, `statement_filter.filtered_statement` — the
compute and filter paths inherit the canonical enumeration in one move), and
`finance_attention` (finance_config audit). Billing's `per_entity` count stays a count (returns no
rows) and is pinned org-scoped in the guard.

### 13c. CANONICAL MARKET VOCABULARY / ENUMERATION — every market dropdown serves the union (owner directive 2026-09-04)

**Owner (verbatim):** "B-1115 is under super nova and LI market under Cellfonz R us, that has been
missing from a lot of reports when market is chosen, this needs to be fixed as a design not a band
aid as this could happen to a new store also, first find the root cause then fix and put precaution
in place."

**Root cause (row evidence, 2026-09-04 live).** House store `B-1115` "1115 Liberty Ave" (created
2026-06-17, company Supernova Wireless LLC) carries market `LI` **ONLY on `storeops.stores`** — NO
`commcalc.store_mapping` row, NO `store_aliases` row (its 1,000 `raw_sales` rows carry exactly the
storeops spelling "1115 Liberty Ave"). §13a (2026-09-03) converged store→market RESOLUTION, so
row stamping/filtering is canonical — but market ENUMERATION was still per-surface: each dropdown
fed from its own source. Measured divergent feeds (each drops a single-vocabulary market or its
store): `payables_filter_options` (store_mapping-only roster → B-1115 absent from the payables/
forecast store picker; LI survived only because 7 OTHER stores spell it in store_mapping),
`asset _registry_stores` (store_mapping-only → B-1115 absent from the borrowed-money/full-roster
pickers), `asset /filter-options` markets (asset_ledger stamps only → a market with no financed
device yet is unofferable), `pos tax_code_markets` (active `storeops.stores` only → the MIRROR
shape, a store_mapping-only market, could never get a tax rate), `commcalc _org_markets` (its own
duplicate union fold — correct today, guaranteed to drift), and every data-present option list
(trends, residual-subs, imei-rebates/handset-COGS/device-cost-recon `_opts`, `optionsFromRows`
client-side). Second finding: the ORG TREE disagrees with the market column — B-1115's org_unit
"1115 Liberty Ave" is parented under **NYC District**, not LI District (whose children are B-103 /
B-11634 / B-1750 / B-1800 / B-2612 / B-418) — so an org-unit-subtree manager span for LI does NOT
cover B-1115, while a `market=LI` login grant DOES (grants resolve through the union index).

**The doctrine.** The org's market VOCABULARY is `core.scope.canonical_markets(client, org_id)`
(= `market_index()['markets']`: storeops.stores ∪ store_mapping ∪ aliases, most-common-spelling
canonicalization — the SAME index §13a resolution and the grant machinery bind, so an offered
market always filters and a filterable market is always offered). EVERY market dropdown/enumeration
composes through `core.scope.merge_market_options(canonical, present)` (pure) /
`org_market_options(client, org_id, present)` (I/O twin): **canonical vocabulary ∪ the surface's
own row stamps**, case-drift collapsing to the canonical spelling, sentinels ("(no market)")
appended after. A NEW market typed on a NEW store is in the vocabulary the moment the row exists
(both editors' writes call `invalidate_market_index`, incl. commcalc `PUT /stores/{id}` since
2026-09-04) and therefore in every converged dropdown with zero extra setup.

**Span/grant coherence.** Options endpoints are org-scoped, not span-scoped (documented per-surface
exceptions narrow AFTER composition, e.g. accessory-flags) — scope-'all'/admin callers see every
vocabulary market with no setup. A scope-'market' manager sees a new market once it is granted on
their `app_users.market` (comma list; resolves via `login_grant_breakdown` → union index → binds
the market's member stores INCLUDING single-vocabulary stores like B-1115). An org-unit-subtree
span follows the TREE, not the market column — placing the store's unit under the right district
is the tenant's setup step for tree-spanned managers; a market-column/tree divergence (B-1115
today) is a data-quality state the scope-preview diagnostic exposes, not a code path.

**Converged 2026-09-04:** `core/filter-options` (StandardFilterBar feed — explicit superset),
`core/markets` + `storeops/markets` + `commcalc/markets` (`_org_markets` now DELEGATES to
`canonical_markets` — the duplicate fold is gone), `payables_filter_options` (union stores
additive + canonical markets), `asset /filter-options` (`markets` ∪ canonical; `_registry_stores`
∪ union-index stores), `pos tax_code_markets` (canonical markets added with union store counts),
`targets/{period}/summary`, expenses/commission/gp trends (`_trend_markets`), imei-rebates /
ma-handset-cogs / device-cost-recon endpoints (canonical ∪ data, sentinel last), account
residual-subs. Already-canonical (verified, pinned): sales-report, exec MTD, tax-collected,
productivity, ATU-opportunity, custom-report, accessory-flags, closing (client-side
`optionsFromRows` over canonically-stamped rows = DATA-PRESENT classification).

**ENFORCED BY CI:** `backend/harness_market_enumeration_guard.py` — scans the backend for any
function shipping a `markets`/`market_options` payload key; unpinned sites fail the build, and a
CANONICAL pin is verified to actually reference a canonical composition helper. Truth table
pinning the exact B-1115/LI shape (a market present ONLY on `storeops.stores` — and its
store_mapping-only mirror, and a brand-new store's brand-new market — must appear in the
vocabulary, appear in every composed option list, resolve its stores under the filter, and bind
as a market-grant keyset member; ambiguity fails closed):
`backend/harness_market_vocabulary_truth.py`. Sibling guards: §13a
`harness_market_resolution_guard.py` (resolution reads), `harness_commcalc_market_dropdown.py`
(editor options + write normalization, updated for the delegation).

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
  A SEVENTH route joined 2026-09-01 (§19.12 closure): `GET /storeops/payroll-raw`
  (`payroll_raw_route`) — same gate, but FAIL-CLOSED 403 instead of strip, because that feed is
  ALL-money by purpose (rate + W-4 for the browser tax calc; no hours-only consumer) — matching its
  scheduled twin `storeops_payroll_tax`. The hours-approval board (`payroll_approval.py`) keeps its
  own STRICTER deny-list (market managers hidden too) via the same module.
  Proof: `backend/harness_pay_visibility.py` (§I covers the payroll-raw route).
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
  | `storeops_payroll_tax` | Payroll with Tax (Estimate) | `storeops.router.payroll_raw` + `storeops/payroll_tax_estimate.py` | ALL-money: gate denial ⇒ ValueError, fail closed (same posture as the live `/payroll-raw`, gated fail-closed 403 since 2026-09-01 — §19.12 closed) |
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
- **Incentives dashboard restructure (owner directive 2026-09-03, mig `947`):** the `/hub/incentives`
  tile layout now SHIPS a HOUSE platform-default row (D1 storage as designed — `ui_label_override`
  scope `tiles`, key `incentives`, seeded `ON CONFLICT DO NOTHING` so a Designer save is never
  clobbered; tenants override in the Designer as always): tiles "Carrier Commission - Received" /
  "Carrier Commission - Reconciliation" (how the TENANT is paid by the carrier), "Employee
  Incentive" (how EMPLOYEES are paid), "Commission Discrepancy" (not-received + appeals hub, §15),
  "Sales & Performance", "Setup & Tools". NO hardcoded frontend layout — pure D1 config. Same mig
  adds HOUSE nav-label PRESETS: NEW scopes `nav_default`/`group_default` at the HOUSE org are
  platform-default display labels every tenant inherits (`get_nav_config` reads them FIRST, then
  overlays the tenant's own `nav`/`group` nicknames — tenant > house preset > built-in, the mig-945
  preset pattern); seeded: `/commcalc/commission-legs` → 'Commission received over M1-M12' (the
  owner's rename; rbac.ts built-in + the page/GP/MA/ledger headers carry the same default wording).
- **Store lease / landlord / insurance capture (mig `946`, owner directive 2026-09-03):** store
  setup now records landlord + site contact, rent payment links / ACH, current rent + annual
  escalation (percentage OR an explicit monthly-rent schedule), the rent-due window, insurance +
  premium due, and append-only lease/COI document versions.
  - **Tables (mig `946_store_lease_insurance.sql`):** `storeops.store_lease` — ONE row per
    (org_id, store_code): `landlord_name/email/phone`, `site_contact_name/phone`,
    `rent_payment_links TEXT[]`, `ach_bank_name/ach_routing_number/ach_account_number/ach_notes`
    (SENSITIVE), `current_rent`, `rent_effective_from`, `escalation_pct`,
    `rent_schedule JSONB [{effective_from, monthly_rent}]`, `rent_due JSONB {kind:'week'|'day',
    value}` (NULL = org default), `lease_start/lease_end`, `insurance_company/policy_number/
    premium/premium_due/premium_frequency('annual' dflt |semiannual|quarterly|monthly)/notes`.
    `storeops.store_document` — APPEND-ONLY versions per (org, store, `doc_kind` ∈
    lease|insurance_coi); current = newest `uploaded_at`; files in the PRIVATE bucket
    **`store-docs`** (envelope-photo precedent: raw `storage_path` in the row, on-demand signed
    URL). Org config on `storeops.tenants`: `rent_due_default JSONB` (column DEFAULT
    `{"kind":"week","value":1}` — the owner's "first week of the month", defined not hardcoded)
    + `lease_visible_roles TEXT[]` (NULL = mig-434 `DEFAULT_VISIBLE_ROLES`).
  - **THE READ CONTRACT for the sibling finance "rents due this week / recurring expenses due"
    build:** rent for a month = `store_lease.rent_for_month` (schedule entry with latest
    `effective_from` ≤ first-of-month wins; else `current_rent` × (1+`escalation_pct`/100)^whole
    anniversary-years since `rent_effective_from`; else `current_rent`; nothing → None, never 0).
    Due window = `resolve_rent_due` (store `rent_due` → tenant `rent_due_default` → house
    first-week) + `rent_due_window(y, m, due)` (week N = days 7N-6..min(7N, month end), clamped;
    day d clamped to month end). Insurance recurrence = `insurance_premium` on
    `insurance_premium_due` repeating per `insurance_premium_frequency`. Compute from these
    columns/helpers — do NOT re-derive.
  - **Gate (SENSITIVE — ACH + lease docs are money-adjacent):** every route gated whole by
    `store_lease.can_see_lease` (mig-434 posture, FAIL-CLOSED 403: allow-list
    `tenants.lease_visible_roles`, NULL = market manager and above; scope-`'all'` and the
    `store_lease_docs` data grant pass; open-app parity carve-out only). `GET /storeops/stores`
    untouched (separate tables — no employee-level endpoint can echo ACH/paths). RLS deviates
    from storeops open_all ON PURPOSE: no policy, service-role only.
  - **Endpoints (`storeops/router.py`, beside the store CRUD):** `GET/PUT /storeops/store-lease
    ?store_code=` (record + document versions + resolved due + current-month rent / validated
    upsert), `PUT /storeops/store-lease/tenant-defaults` (org `rent_due_default`),
    `POST /storeops/store-lease/doc` (base64 pdf/png/jpg/webp ≤15MB → bucket + APPEND version
    row), `GET /storeops/store-lease/doc-url` (org-scoped by doc id → 1h signed URL; the page's
    download path) and `GET /storeops/store-lease/doc-view` (302 twin, envelope-view pattern).
  - **Frontend:** `/storeops/setup/stores` per-row "🏢 Lease" expandable panel
    (`storeops/setup/stores/LeasePanel.tsx`) — renders the gate's 403 as a friendly restriction
    note. AWAITING OWNER PREVIEW (merge policy Option B).
  - **Proof:** `backend/harness_store_lease.py` (stdlib-only: rent math incl. anniversary
    boundaries, due-window clamps, gate truth table end-to-end, ACH strip, upload decode caps).
    Not an external feed → no lineage registry entry.
- **Insurance POLICIES (one policy, many stores) + AI document reading + expiry notices (migs
  `964`-`967`, owner directive 2026-09-05):** "there should be a link to upload the insurance policy
  and assign that policy to multiple stores as one insurance policy can cover multiple stores, the
  uploaded policy should then be interpreted by the system using ai and the fields filled ... Please
  to upload the certificate of insurance of respective stores. Similarly for the lease ... All with
  the help of ai tools ... a notification when a coi is expir[ing] or th[e] lease is getting over at
  least 60 days in advance or as per lease requirement."
  - **WHAT WAS EXTENDED, NOT REBUILT (build-gate check):** `storeops.store_document` (the SAME
    append-only table, private `store-docs` bucket and signed-URL-by-id download — a policy document
    is one more row with `policy_id` set and `store_code` NULL); `store_lease.can_see_lease` (the
    SAME fail-closed gate on every new route); `store_lease.rent_for_month` /
    `normalize_rent_schedule` / `normalize_rent_due` (an ACCEPTED rent schedule is written in exactly
    the shape the §14 mig-946 read contract already reads — no second derivation);
    `storeops.alert_log` (mig `433` — the SAME dedupe the lateness alerts use, scopes
    `doc_expiry_lease`/`doc_expiry_insurance`); `storeops.tenants` (the SAME per-org config table);
    `PUT /storeops/store-lease/tenant-defaults` (extended, not forked, for the two new config keys);
    `core.import_health.register_provider` (the attention feed, not a second notifier); and the mig
    `922`/`940`/`950`/`956` self-scheduling pg_cron pattern.
  - **Tables:** `storeops.insurance_policy` (mig `964` — one row per CONTRACT: `policy_number`,
    `insurer`, `coverage_type` (free text; vocabulary is CONFIG), `coverage_start/coverage_end`,
    `premium`/`premium_frequency`/`premium_due`, `inclusions_summary`, `extra_items JSONB`,
    `notice_days`, `is_active`). `storeops.insurance_policy_store` (the multi-store assignment,
    UNIQUE org+policy+store). `storeops.document_extraction` (mig `965` — the AI DRAFT:
    `fields`/`clauses`/`extra_items`/`contacts`/`applied` JSONB, `status`
    draft|accepted|partially_accepted|rejected|failed|not_extracted). `storeops.document_contact`
    (mig `966` — MULTIPLE expiry contacts per subject; `subject_kind` 'lease' with
    `subject_ref` = store_code covers BOTH that store's lease and its certificate, 'insurance_policy'
    with `subject_ref` = policy id). New `store_lease` columns (mig `966`): `lease_notice_days`,
    `notice_address`, `lease_exit_clause`, `lease_termination_liabilities`,
    `lease_critical_clauses JSONB`, `coi_expires`. New `store_document` column (mig `964`):
    `policy_id`, plus doc_kind `insurance_policy` and a nullable `store_code`.
  - **THE MONEY RULE (this is the point of mig 965):** `account/liabilities_due.py` books rent and
    insurance premiums FROM `store_lease`, and `account/engine.py` states the house posture — the AI
    "never originates a dollar amount that ships". So an extracted premium/rent/escalation NEVER
    writes to `store_lease`. It lands in `document_extraction` with per-field confidence and the
    VERBATIM source snippet + page, and **`doc_intel.apply_plan` is the only door to a live column**:
    it refuses an unknown key, a key absent from the extraction, a field with no target, an
    ACH/identity column ALWAYS (no override flag exists), and every `doc_intel.MONEY_GUARDED` field
    (`current_rent`, `rent_effective_from`, `escalation_pct`, `rent_schedule`, `rent_due`,
    `insurance_premium`, `insurance_premium_due`, `premium`, `premium_due`) unless a human ticks the
    money confirmation. `insurance_policy.premium` is INFORMATIONAL — no money reader reads that
    table at all.
  - **CONFIG, NEVER CODE (RULE TWO):** coverage types are `storeops.tenants.insurance_coverage_types`
    ([{key,label}], house default seeded as the mig-964 column DEFAULT — the owner's BOP and workers
    comp are two rows of it, alongside GL/property/umbrella/cyber/EPLI/auto). No DB CHECK, no Python
    enum, no tenant/insurer branch anywhere. The notice floor is
    `storeops.tenants.doc_expiry_notice_days` `{"lease":60,"insurance":60}`.
  - **THE NOTICE WINDOW:** `doc_intel.resolve_notice_days` = **MAX(the document's own requirement,
    the org floor)**, house fallback `HOUSE_NOTICE_DAYS = 60`. MAX, not override: a lease demanding
    90 or 180 days beats the 60-day floor, and one demanding 30 never drops below it. Reminder ladder
    `milestones_for` = the window, then every house nudge strictly below it (60/30/14/7/1), then 0 =
    expired — ASCENDING, so the milestone that fires is the TIGHTEST one crossed (descending would
    re-pick the widest one daily and, once logged, silence every later nudge). A contact's own
    `notice_days` can only make their notice EARLIER, never later than the floor.
  - **AI call (`doc_intel_ai.extract_document`):** SEV-1 2026-07-30 discipline in both layers — the
    route is `async def` and hops via `run_in_threadpool`, and the sync Anthropic client carries
    explicit `DOC_INTEL_TIMEOUT_S` (120s) x (1 + `DOC_INTEL_MAX_RETRIES`). Model is env config
    (`DOC_INTEL_MODEL`, default `claude-opus-5`), structured outputs (json_schema) so the model
    cannot answer in prose, adaptive thinking. NOTHING from `store_lease` is ever put in the prompt —
    above all the ACH columns; returning snippets are additionally masked for bank-ish digit runs
    (`doc_intel.scrub_snippet`). No key ⇒ status `not_extracted` (a clean empty draft), never an
    exception. NOT yet routed through the sibling's shared AI-call guard (`core/control_box`,
    mig `972`): that guard's authorization is super-admin + no-prompt-passthrough, whereas this call
    is lease-gated and passes a tenant document — converging needs one purpose there whose auth check
    is `can_see_lease`.
  - **Endpoints (`storeops/router.py`, beside the mig-946 lease block; ALL gated `can_see_lease`,
    fail-closed 403, every query org-scoped):** `GET/POST/PUT/DELETE /storeops/insurance-policies`,
    `PUT /storeops/insurance-policies/stores` (the multi-store assignment; every code validated
    against this org's `storeops.stores`), `POST /storeops/insurance-policies/doc` (master policy
    upload — same bucket/caps/append-only contract), `POST /storeops/document-extract`
    (run_in_threadpool), `GET /storeops/document-extraction`,
    `POST /storeops/document-extraction/accept` (THE money gate),
    `GET/PUT /storeops/document-contacts`, `GET /storeops/doc-expiry`,
    `POST /storeops/doc-expiry/run-now` (dry-run default), `POST /storeops/doc-expiry/run-due`
    (NOTIFY_RUN_SECRET-gated pg_cron entrypoint, mig `967`, daily 13:00 UTC). `GET
    /storeops/store-lease` additionally returns `policies` (covering this store), `contacts` and
    `notice_days_resolved`.
  - **Cron registration:** mig `967` installs the idempotent
    `storeops.ensure_doc_expiry_alert_cron(url, secret)` RPC (service_role only, no literal secret in
    the file). The four existing crons register from a `main.py` boot hook; this build does NOT touch
    `main.py` (a sibling agent was editing it concurrently), so `_maybe_register_doc_expiry_cron()`
    arms the job on the first policy/expiry request of the process instead. **Open follow-up: add the
    12-line `_doc_expiry_cron_startup` hook to `main.py` calling
    `storeops.router._ensure_doc_expiry_alert_cron`, matching the mig-950 block.**
  - **Attention (safety net, not the channel):** `storeops/attention.py` providers
    `storeops_doc_expiry` (documents inside their notice window) and `storeops_doc_expiry_no_contact`
    (inside the window with NO mailable contact — the failure mode that makes the whole feature
    silent). Both REUSE `router._expiry_subjects` + `doc_intel` window math via a lazy import; the
    attention feed must never become a second derivation of "what is expiring".
  - **Frontend:** NEW page `/storeops/setup/insurance` (`storeops/setup/insurance/page.tsx`) —
    policies, store assignment, upload + "Read the policy with AI", contacts, the expiry table and
    preview/send buttons; shared review component `storeops/setup/insurance/ExtractionReview.tsx`
    (nothing pre-ticked; every value shows its quoted source; money fields behind their own
    confirmation) reused by `storeops/setup/stores/LeasePanel.tsx`, which also gained the mig-966
    lease fields, the covering policies, and the store's notification contacts. `rbac.ts` nav row
    (module `storeops`, scopes all/market, tileOnly). AWAITING OWNER PREVIEW (merge policy Option B).
  - **Proof:** `backend/harness_doc_intel.py` (stdlib-only, 127 checks: coercion, config-driven
    coverage types, extraction mapping + provenance, THE MONEY GATE's five refusals, the MAX notice
    window, the alert ladder/dedupe/recipient windows, catalogue↔prompt coherence, interop with the
    shipped `store_lease` money path, and a multi-tenant static scan of the four new tables — that
    last one because `harness_org_scope_guard.py` only reads commcalc's router). Not an external feed
    → no lineage registry entry.
- **Management Overview + Flags & Compliance dashboards (owner directive 2026-09-03, mig `948`):**
  two NEW top-level dashboard CATEGORIES; the tiles are D1 DATA (mig 068 `ui_label_override`
  scope='tiles', HOUSE platform-default rows seeded `ON CONFLICT DO NOTHING` — the mig-947
  pattern; tenants override in the Dashboard Designer), the NAV groups ship in `rbac.ts` (the D2
  convention) with every existing page as a tileOnly DUPLICATE keeping its original
  module + scopes (zero RBAC change — the Reports-directory duplicate precedent).
  - **Management Overview** (`/hub/management-overview`, the generic D2 hub route; house layout
    key `management-overview`): one tile per report — Sales Reports (`/commcalc/sales-report`),
    Executive MTD, Sales Comparison, **"Rep Incentive"** = `/commcalc/exec` with the relabel as
    tile-layout item-label DATA (layout label > NAV label; nothing hardcoded, tenant-editable),
    **Failing KPIs** (NEW report, §10), **Cash at Hand** = a SECOND PLACEMENT of the existing
    `/closing/store-cash-on-hand` report (same endpoint/component, no forked derivation), and
    **Current Monetary Liabilities** (NEW report, §4).
  - **Flags & Compliance** (`/compliance` — a curated page, the /storeops hub precedent): every
    flag/exception/compliance queue under one roof. StatTile COUNTS from
    `GET /commcalc/compliance-summary` — ONE thin count pass over the existing queues' own
    queries/handlers (flags open · discrepancy open · ingest-guard quarantine pending ·
    ops_chargeback pending · attendance exceptions (current pay period, in-process handler) ·
    hours-approval pending (payroll_approval totals) · approvals inbox (`approvals.engine.summary`) ·
    deposit-accountability non-green store-days · bill-pay coverage exceptions · statement
    staleness (`autocompute.staleness`)); a failed probe reports count=null + `unavailable`, NEVER
    a fake 0 (pure assembly `commcalc/compliance_summary.py`, proof
    `harness_compliance_summary.py`). Below the counts the page renders the D1 tile layout for
    module `flags-compliance` (house seed: Commission Flags / Pay Discrepancy / Data Quality &
    Ingest / Workforce Compliance / Cash & Closing Compliance) with the exact /hub/[group] RBAC
    predicates. `/commcalc/ingest-guard` gained its FIRST NAV home here (page pre-existed,
    menu-less; scopes `['all']`).
  - **Frontend registration:** `rbac.ts` (two groups + REPORT_TREES `/commcalc/kpi-failing` +
    REPORT_DIRECTORY rows), `reports.ts` (Failing KPIs, Current Monetary Liabilities);
    `prove_tile_hubs.mjs` (17 hub groups) + `prove_import_health_nav.mjs` (B6 duplicate) updated.
    ALL THREE NEW SCREENS (kpi-failing · liabilities-due · compliance) AWAIT OWNER PREVIEW
    (merge policy Option B). Display config, not a feed → NO lineage entry.
- **Google Reviews (storeops, migs `411`/`412`/`413`/`420`/`430`; lineage rows 126–128 in mig
  `925`):** logic in `backend/app/modules/storeops/google_reviews.py` (framework-free; harness
  `harness_people_google_reviews.py`), endpoint glue in `storeops/router.py` (`/google-reviews/*`
  — config, sweep-config, run-now, run-due, stores, store-config, resolve-place, my, dm-dashboard,
  store/{code}, employee/{id}, employee-summary). Data path: per-store address (+
  `google_review_config.search_brand`) → Places API (New) Text Search → place pin cached in
  `google_review_store` (manual pin wins, `wrong_street_number` guard refuses drifted matches) →
  Place Details → `google_review_snapshot` (rating/count per sweep) + `google_review_item`
  (deduped by `review_hash`, conservative employee name-matching) → below-target stores
  materialize `action_plan` rows + edge-triggered notifications. HONEST LIMITATION: Places
  returns Google's curated ~5 reviews per store, not all (Business Profile API is the Phase-2
  path). **2026-09-04 incident closure ("still not able to pull google reviews"):** root cause =
  Postgres 42501 `permission denied for sequence google_review_store_id_seq` — migs 411/412/413
  granted the TABLES to service_role but never the BIGSERIAL SEQUENCES, so every sweep died on the
  first DB write after a successful Google call (both orgs 20/20 errors 2026-08-17/20; all three
  data tables at zero rows despite working keys). Fixed by mig `951` (sequence grants). Two
  companion fixes: mig `950` — `/google-reviews/sweep/run-due` self-schedules via pg_cron job
  `google-reviews-sweep-run-due` (every 15 min; `storeops.ensure_google_reviews_sweep_cron`,
  re-registered on every boot by `main.py` → `router._ensure_google_reviews_sweep_cron`, mig
  922/940 pattern — before this NO job ever invoked run-due); and
  `_do_google_reviews_sweep` now persists a SAMPLE of distinct per-store error texts in
  `google_review_sweep_config.last_detail` (and drops the "OK —" prefix when 0 stores succeeded)
  so the next failure is diagnosable from the row itself.

| Subsystem | Tables / migs | Key funcs / endpoints |
|-----------|---------------|-----------------------|
| **MA (master-agent) commission** | `raw_ma_commission`, mig `254_ma_product_class`, `251_ledger_ma_sync`, `265_ma_class_money_wiring`, `268_ma_overview_recon` | `ma_product_class.py`, `ma_class_wiring.py`, `ma_upload.py`; `/ma-commission/summary` `router.py:25009`, `/ma-overview-recon*` `25224-25450`, `/ma-handset-cogs` `26851`, `/ma-product-class*` `4868-5151`. **Ingest replace is account-slice scoped** (2026-09-02 two-portal wipe incident, §2): `ingest_slice.py` `day_replace_filters` narrows `/upload/ma_commission|ma_daily_tx|ma_fulfillment` deletes to (org, day, account) — proof `harness_ma_slice_replace.py` |
| **B2B ↔ MA activation recon (Pay Discrepancy, MA source)** | `discrepancy_results` (`source='ma'`), `ma_payment_rule` — mig `312_ma_payment_rules_and_discrepancy_attribution` | `ma_recon.py` (pure: `build_sold_index`/`build_paid_index`/`match_rules`/`reconcile_ma_activations`; reuses mig-308 `_gate_met_ma_tx` + the two-hop link); ran by `POST /discrepancy/run` `router.py:19056` for plan-mode orgs; rules CRUD `/ma-payment-rules*` `19200-19270`; proof `harness_ma_recon.py`. Sold-but-unpaid → status `open` + literal `'no business rule configured'`, or rule-attributed `info`/`lagged` |
| **Commission Discrepancy hub + APPEALS (owner directive 2026-09-03)** | appeal columns ON `discrepancy_results` (`appeal_status/appeal_note/appealed_by/appealed_at`) — mig `947_commission_discrepancy_hub` (NO new table: rows stay the two engines' output, the hub only ANNOTATES; the mig-098 denied-appeal claw-back pipeline `/recovery/*` is a DIFFERENT lifecycle, linked not re-derived). Mig 947 also seeds the HOUSE Incentives tile layout (§14 D1) + the `nav_default` label preset | pure state machine `discrepancy_appeals.py` (`validate_transition`/`apply_appeal`/`period_range_variants`/`summarize_appeals`; states `appeal_filed→appeal_won\|appeal_denied\|written_off`, NULL = none, clear = full reset); `GET /discrepancy-appeals` (period-RANGE query, spelling-agnostic; filters source/status/appeal_status/store/activation-date; degrades `appeals_ready=false` pre-947) + `PATCH /discrepancy-appeals/{row_id}` (org-scoped read-validate-update, who/when via `_caller_uid`) beside the discrepancy block; page `commcalc/commission-discrepancy` (StandardFilterBar + appeal buttons + `/recovery/claims` chase list); proof `harness_discrepancy_appeals.py` |
| **Carrier statement commission** | mig `065_carrier_commission.sql` → `rep_commissions.carrier_statement_comm` | `/carrier-comm-file/extract` `6216`, `/commission-received-breakout` `15488` |
| **Commission plans (rule engine)** | mig `059_commission_plans.sql`, `066`,`067`,`232`,`260`,`262` | `commission_engine.py`; `/commission-plans*` `12557-14246` (coverage, pay-gate, exclusions, bulk-assign) |
| **Commission ledger (income tracking)** | mig `071_commission_ledger.sql` | `/commission-ledger/*` `3997-4602` |
| **VIP / PayGo** | mig `008`,`011`,`014` | `vip_sweep.py`; `/vip/*` `2421-3078`, `/vip/paygo/*` `8336-8365` |
| **epay** | mig `020`,`025` | `epay_sweep.py`; `/epay/*` `8730-8811`, `/tax-collected` `2106` |
| **Processor Daily Debits & Credits (owner directive 2026-09-04)** | NO new table / NO migration — reads the EXISTING processor feeds `raw_payment_detail` (§2, epay sweep, migs `020`/`025`) and `raw_ma_daily_tx` (§2, VidaPay sweep/upload/`report_pull`, mig `083`). Naming config = the mig-`953` `report_term` vocabulary (`processor` key); primary-feed config = `metric_source_of_truth`/`data_source` (migs `923`/`939`) | **`commcalc/processor_ledger.py`** — PURE core `classify_amount`/`fold_cells`/`filter_cells`/`day_type_rollup`, IO only in `assemble`. Rows = DAY × TRANSACTION TYPE with DEBITS / CREDITS / NET (= credits − debits) columns; cell grain (processor, date, tx_type, store) so every rollup ties out. **DEBIT/CREDIT RULE is per FEED SHAPE, never a carrier branch** (`FEED_SHAPES`, RULE TWO): `raw_payment_detail.amount` > 0 = CREDIT to the dealer / < 0 = DEBIT; `raw_ma_daily_tx.retail_cost` > 0 = DEBIT (a charge) / < 0 = CREDIT — both verified against live rows 2026-09-04 (house 2026-07-27: D 1,001.60 / C 80,214.66 / N 79,213.06; luxelink 2026-09-02: D 30,297.96 / C 987.50 / N −29,310.46), pinned in the harness. RESOLUTIONS REUSED, never re-derived: processor identity `router._metric_source`+`_billpay_processor_name`, processor NAME `report_labels.load_report_labels` term `processor` (§18 — no vendor literal in module or page copy), VidaPay account→store `router._vidapay_account_resolver`, raw→canonical store `account.coa.store_resolver`, address→code `flag_store_resolver`, store→market + market dropdown `core.scope.market_by_code`/`org_market_options` (§13a/§13c). Endpoint `commcalc/processor_ledger_api.py` `GET /commcalc/processor-ledger` (span-gated via `scope_keyset`/`in_keyset`; unmapped-store cells hidden from scoped callers). Page `commcalc/processor-ledger/page.tsx` (NAV Assets & Inventory + REPORT_DIRECTORY `'assets'` + REPORT_TREES `'asset'`; carrier-NEUTRAL → deliberately NOT in `NAV_CARRIERS`). Scheduled/emailed via notify W3 key `processor_ledger` (`report_registry.py`; filters date_from/date_to/store/type/market). Proof `harness_processor_ledger.py`; guards `harness_market_enumeration_guard.py` (pins `assemble` CANONICAL), `harness_carrier_vocab_guard.py`, `harness_org_scope_guard.py` |
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
| **Device Forecasting & Vendor Payables (module 095)** | `device_payable_ledger` + `payable_source_map` (mig `095_device_payables` — a NEW carrier is a config row: source_table/imei_field/store_field/owed_field/sold-match/reimbursement), `device_model_alias` (mig `096` — raw model → canonical + carrier, the forecast alignment) | `payables/engine.py` (`build_ledger` delete+insert per carrier; **`ma_store_resolution`/`resolve_ma_store` — Total/MA device→store attribution, 2026-09-04**: POS sale line → `inventory_aging_device` §11 device grain → mig-314 account index (`ma_store_pnl.load_store_index` + `coa.store_resolver` spelling collapse) → None, fills blanks only); `payables/router.py` `/api/v1/payables/*` — `/forecast` (phones-only velocity/on-hand/recommend, per carrier; Boost leg `raw_sales` device lines, Total leg `raw_ma_commission` + the attribution above), `/payables` (per-IMEI ledger read; read-time blank-store fill via the same attribution), `/filter-options` (canonical §13c roster+markets — the frontend bar's PLATFORM-WIDE gate source), `/owed-by-date` (ledger, else `raw_ma_daily_tx` vendor-feed fallback), `/due`, `/priority`, `/source-maps*`, `/phone-map*`, `/settings`, `POST /rebuild`. Frontend `commcalc/payables/page.tsx` (bar gated on roster ∪ rows — owner 2026-09-04 "need to be platform wide"). Proofs `harness_device_forecast_store_filter.py`, `harness_payables_market_filter.py` |
| **ATU opportunity** | mig `295` | `atu_opportunity.py`; `/atu-opportunity` `28410` |
| **Activation-Details basis (b2b activation TYPE buckets)** | `raw_custom_import` (signature-detected sheet: `Serial#`+`Contract Type`); config `accessory_config.activation_details_rules` — mig `313_activation_details_bucket_rules` (per-org token rules; RULE TWO) | `activation_bucketing.py` (PURE: `activation_details_bucket`/`resolve_rules`/`BUCKET_RANK`/`TOTAL_ACTIVATION_EXCLUDED`) ← delegated to by `router._activation_details_bucket`; rules loaded per-org by `_activation_details_rules` (defensive, mig-214 posture); resolver `_cr_resolve_activation_details` (serial-dedup by rank); consumers `_ad_cells_full` → `_apply_activation_basis` (Exec MTD + Sales Report), `_ad_activation_buckets` (metric recon), `GET /activation-counts/{period}`; proof `harness_activation_bucketing.py`. HOUSE DEFAULTS (2026-09-01 approved fix): Edge = whole-word `edge` in CONTRACT TYPE only (`edge_name_tokens` opts device-name matching back in per org — the Motorola-Edge over-match trade-off); `BYOD Upgrade` = its own hidden bucket (excluded from Total Activation exactly like Upgrade, NOT shown in the Upgrade column; `upgrade_hidden_contract_tokens: []` restores one family) |

---

## 16. Cross-reference: by TABLE

| Table | Written by | Read by |
|-------|-----------|---------|
| `commcalc.raw_sales` | upload `/upload-mapped` `3637`, sweeps, `sales/promote-feed` `22757` | `calc_rep_commissions`, `calc_gp_report`, `_compute_feed_actuals_py` `18678`, `_sales_cell_agg`, `_mi_resolve_numbers` edge/vhi `28843`, installment engines |
| `commcalc.daily_sales_feed` | B2B/email sweeps, upload | `_compute_feed_actuals_py` (primary source), sales report, fallback in calc |
| `commcalc.merchant_settlement_day` | `merchant_portal_sweep.store_settlement` (daily portal scrape) | `closing/external_credit_recon` (declared-vs-settled card tally, §12a), resolved via `report_pull_map.merchant_settlement` |
| `commcalc.merchant_settlement_batch` | `merchant_portal_sweep.store_batches` | cash/deposit recon (§12); NEVER summed into the closing card tally (different grain) |
| `commcalc.raw_payment_detail` | epay sweep, upload | `calc_gp_report`, reimbursement categorization, **Processor Daily Debits & Credits** (`processor_ledger.assemble` — `amount` sign = credit/debit to the dealer, §15) |
| `commcalc.raw_mi` | upload / MI sweep | carrier residual gate `installment_engine.compute_installments`, sale-installment gate, MI/ATU |
| `commcalc.raw_dlar_store` | `dlar_sweep.run_dlar_sweep:209` (replace), upload | `get_dlar_store_kpis` `10279`, `_cr_resolve_kpi_metrics` `25656`, MI tmr3 `28884` |
| `commcalc.raw_dlar_rep` | `dlar_sweep` (replace), upload | rep KPI, comp trend `15238` |
| `commcalc.raw_catalog` | upload `/product-mrc/import` region, catalog | GP, device COGS, installment MRC |
| `commcalc.payout_config` | `/config/{period}` `10474`, `/commission-settings` `10517` | `calc_rep_commissions` (spiffs/tiers), installment base rates |
| `commcalc.rep_commissions` | `_run_calculation`/`_apply_new_engines` `9183` | `/commissions/{period}` `10222`, GP report, commission-by-store, statements, MI (indirect) |
| `commcalc.store_kpis` | KPI ingest/snapshot | tiers, exec |
| `commcalc.carrier_kpi_metric` | `/carrier-kpi-metrics` POST `19773` | KPI/tier config resolution |
| `commcalc.flags` | calc + flag rules | `/flags/{period}` `10299`, `_cr_resolve_flags` |
| `commcalc.store_expenses` | `/expenses/{period}` PUT `21695` | GP report + P&L, BOTH via the sticky carry-forward reader `expenses_effective.effective_expense_rows` (2026-09-02, §4); `_cr_resolve_store_expenses` |
| `commcalc.sale_installment_ledger` | `compute_sale_installments(persist=True)` `9212` (mig `308` adds `order_number`/`account_id` MA TX provenance, adaptive write) | `/plan-installments/*` previews, `installment_comm_sale` |
| `commcalc.raw_ma_daily_tx` | upload `/upload/ma_daily_tx` (slice-scoped replace: org × day × `account_id`, `ingest_slice.py` §2), VidaPay sweep, `report_pull` | bill-pay recon processor side (`_billpay_processor_by_store(_day)` — since mig `944` FILTERED to bill-payment rows via `metric_recon.ma_billpay_predicate`, accounts via store_merchant_id → mig-314 index; §12 3-way Leg C), **residual-per-subscriber report §7a** (`residual_subs._aggregate_ma` — ONE sweep: residual = −`retail_cost` on the mig-309 `ma_residual_row_matcher` union, airtime margin = `merchant_discount`; stores via the mig-314 account index), Commission Ledger, **installment engine mig `308`** (`sale_installment_engine._read_ma_tx` → `'ma_tx'` gate + `'ma_tx_activation'` MRC; money column `retail_cost` ONLY — `merchant_invoice` is an identifier), **P&L mig `309`** (`account/coa.build_inputs` via `residual_subs.ma_tx_pnl_bookings`: `merchant_discount` → "Merchant discount" line (or legacy `atu_income` fold per `pl_merchant_discount_own_line`), −`retail_cost` → `mi_income` for the `'%residual%'` ∪ `pl_ma_residual_order_types` union, each row once), **P&L mig `314`** (`ma_store_pnl.ma_tx_bookings`: per-store via `account_id`→store index; MDF token rows → `mdf_income`; `'daily_tx'` month-spiff rows → `carrier_comm` `M<n>` detail), **BS mig `933`** (`balance_sheet.handset_payable_bookings` via `statement_engine._fetch_outstanding_tx`: configured `handset_payable_order_types` rows with `tx_date ≤ as-of < due_date` → the `handset_payable` liability; money column `retail_cost` ONLY), **liabilities-due 2026-09-03** (`GET /account/liabilities-due`: same fetch + same family predicate — outstanding today + `liabilities_due.payables_due_in_window` for the due-this-week rows, equivalence pinned in `harness_liabilities_due.py`; §4), **Processor Daily Debits & Credits** (`processor_ledger.assemble` — `retail_cost` sign = debit/credit to the dealer, §15) |
| `commcalc.raw_ma_commission` | upload `/upload/ma_commission` (slice-scoped replace: org × day × `merchant_account_id`, `ingest_slice.py` §2 — 2026-09-02 two-portal wipe incident), VidaPay sweep | MA overview/recon, installment MA gate (`_read_ma_commission` spiffs), **mig `308` two-hop link** (`build_ma_link_index`: `imei|sim → activation_order`), **P&L mig `314`** (`ma_store_pnl.ma_commission_bookings`: component heads per-store via `merchant_account_id`→store index; sheet spiffs suppressed under `pl_ma_month_spiff_source='daily_tx'`; MA device COGS store slice `device_cogs._ma_sold_cost`), **residual-per-subscriber SUBSCRIBER count §7a** (`residual_subs._aggregate_ma` — one row = one activated line, keyed by `merchant_account_id` through the SAME mig-314 index the residual rows use) |
| `commcalc.raw_ma_fulfillment` | upload `/upload/ma_fulfillment` (slice-scoped replace, `ingest_slice.py` §2) | `device_cogs.ma_unit_price_map` (handset price list), MA overview/recon, **mig `314` account→store map source** (`tspid`+`business_address` → `ma_store_pnl.account_store_index`; canonical-spelling wrapper `ma_store_pnl.canonical_store_index` = index ∪ `coa.store_resolver`, the ONE account→store answer, consumed by `payables/engine.ma_store_resolution` — forecast/payables Total-side store attribution, 2026-09-04 §15 — and by `residual_subs.compute` for the §7a report's store names) |
| `commcalc.ma_account_store_map` (mig `314`) | owner-pinned rows (SQL seed / future admin UI) | `ma_store_pnl.load_store_index` — wins over the fulfillment-derived map; covers accounts fulfillment never names (luxelink `170405`) |
| `commcalc.payout_schedule(+_line)` | `/payout-schedule` POST `11965` | `installment_engine.compute_installments` |
| `commcalc.inventory_aging_device` | `b2b_sweep.py:341` upsert | `/device-history` `17015`, `/device-cost-recon` `27338`, MI aging bonus, **BS inventory under `inventory_basis='devices'` + `GET /account/inventory-recon`** (`balance_sheet.device_inventory_cells` via `statement_engine`, mig `933`); statement staleness probe (`autocompute._POINT_IN_TIME_SOURCES`); **device-grain store source for `payables/engine.ma_store_resolution`** (forecast/payables Total-side attribution, 2026-09-04 §15 — its `store` is the store_mapping vocabulary, measured 20/20) |
| `commcalc.journal_entries` | `PUT /account/journal/{period}` (`account/router.py` — delete+insert per period; echoes `rejected`/`resolved`) | `statement_engine._journal_rows` (BOTH period spellings) → `balance_sheet.journal_scope_entries` (fixed company scoping, mig `933`) → **`balance_sheet.journal_grain_entries`** (mig `954` GRAIN rule: store / company / tenant-total entries, a coarser row booked NET of the finer rows inside it — no double count; conflicts surfaced in `bs['journal_grains']`); legacy `engine.compute_and_store` exact-period read; staleness probe |
| `commcalc.account_statements` | `statement_engine.compute_and_store` (purge-then-insert per period; statement_types `pl`/`balance_sheet`/**`cash_flow`**) — legacy writer `engine.compute_and_store` retained | `GET /account/pl|balance-sheet|cash-flow/{period}`, `/account/overview` (company scopes cross-checked against `coa.org_companies` via `coa.filter_org_scopes` — §13b), `statement_filter.filtered_statement`, `engine._prior_accum_ni`, `statement_engine._stored_bs` (prior-BS for cash flow), notify `account_pl`/`account_balance_sheet` |
| `commcalc.companies` | `POST/PATCH /account/companies` (org_id in payload/filter; mig `952` removed the two 2026-06-27 wrong-org LuxeLink rows) | ONLY `coa.org_companies` (§13b canonical fail-closed enumeration; CI-pinned by `harness_org_scope_guard.py`) → `list_companies`/`list_stores`/journal echo/`overview`/`analysis`/`finance_attention`/`store_company_map`⇒`company_assignment`; billing `per_entity` org-scoped count probe |
| `commcalc.account_config` (per-org finance config, migs `611`/`613`/`621`/`933`/`938`/`941`/`954`) | `PUT /account/config` (incl. the mig-954 tenant mapping `distributor_payable_basis`/`distributor_payable_line`/`asset_ledger_open_statuses`); mig-933 columns (`inventory_basis`, `handset_payable_order_types`) seeded per org behind the owner gate; mig-941 columns (`projection_config`, `valuation_config` JSONB — display-only assumptions, org seeds gated) | `coa._account_config` (rates/K2/K3), `balance_sheet.load_bs_config` (mig-933/938 knobs, adaptive), `projection_engine.load_projection_config`, `valuation.load_valuation_config` (mig-941, adaptive); **mig-954 distributor-payable mapping** via `balance_sheet.load_bs_config` → `resolve_payable_basis`/`resolve_payable_line` (org column > carrier preset > declared mig-933 family > off) |
| `commcalc.asset_ledger` (consignment / asset-lending ledger; wipe-and-reinsert CURRENT snapshot) | mod-asset upload `process_asset_ledger_bytes`, `vip_sweep.run_asset_ledger_sweep` | asset dashboard `GET /asset/summary` ("Open Balance Owed" = Σ `owed_to_vip` where `status='Open'`), `account/device_cogs` (consignment COGS), `coa.build_inputs` (`vip_reimb`/`vip_fees`, and the legacy `owed_vip`/`inventory` `status='on inventory'` predicate that matches NOTHING on the live feed), **BS distributor payable under `distributor_payable_basis='asset_ledger'`** (`balance_sheet.asset_ledger_open_bookings` via `statement_engine._fetch_asset_ledger_open`, mig `954`; money column `owed_to_vip` ONLY; as-of = `period_as_of`) and the SAME derivation behind `GET /account/liabilities-due`; statement staleness probe (`autocompute._POINT_IN_TIME_SOURCES`) |
| `core.system_check` (mig `970`; per-tenant OVERRIDES over the code-derived check registry — retune / disable / DECLARE a check) | `PUT`-less by design today: rows are written by SQL/console; the board never writes them | `control_box_api.effective_registry` (code defaults < HOUSE rows < org rows) → `GET /core/control-box` |
| `core.system_check_run` (mig `970`; daily-run history — the PROOF the check ran + the baseline escalation compares against) | `control_box_api._persist_run` (from `POST /core/control-box/run` and `/run-due`) | `GET /core/control-box/history`; `_previous_results` → `control_box.escalations` (notify-once) |
| `core.system_check_state` (mig `970`; per-org `enabled`/`cadence_hours`/`last_run_at`/`next_run_at`) | `control_box_api._persist_run` upsert | `control_box.due_orgs` (which tenants are due) + `control_box.selfcheck_row` (the board's row about ITSELF) |
| `core.ai_budget_config` / `core.ai_call_audit` (mig `972`; SHARED per-`(org,purpose)` AI ceiling + per-call meter/audit — tokens only, $ joins `core.token_rates`) | `control_box_api._audit` (every attempt, allowed AND refused) | `control_box.rollup_usage` → `control_box.ai_guard_decision`; refusal scan = the "someone is probing us" signal |
| `core.ai_margin_config` (mig `973`; per-tenant AI margin, effective-dated + APPEND-ONLY so history IS the audit) | `PUT /billing/ai-margin` (super-admin, records `changed_by`) | `ai_usage.margin_for` → `price_period` → the statement's AI line |
| `core.ai_usage_period` (mig `973`; FROZEN AI period snapshots — rate + margin + figures at close) | `POST /billing/ai-usage/close` | `ai_usage.price_period(frozen=)` — read, NEVER recomputed |
| `core.module_usage_daily` (mig `974`; per (org, module, day) counters — `billable_calls` vs `system_calls` vs `anonymous_calls`) | `core.bump_module_usage` RPC from `billing/usage_flush` (batched every 30s; the request path only increments a dict) | `module_usage.rollup_by_module` → `statement.build_statement` → `GET /billing/statement` |
| `core.module_route_map` (mig `974`; route prefix → billable module overrides, RULE TWO) | operator SQL | `module_usage.classify` (unmapped is SHOWN, never guessed) |
| `core.module_price` (mig `975`; price per plan x module, effective-dated; UNPRICED = the ABSENCE of a row) | `PUT /billing/module-pricing` (super-admin, `changed_by`) | `statement.price_for` → `module_line` / `pricing_grid` |
| `core.billing_statement` (mig `975`; FROZEN itemized statements incl. the `complete` flag) | `POST /billing/statement/close` | `statement.build_statement(frozen=)` — read, NEVER recomputed |
| `storeops.pricing_package` / `storeops.tenants.package_key` (mig `908`, REUSED) | existing `/billing/*` pricing endpoints | the PLAN TIERS (free/starter/premium are ROWS, not an enum) + the monthly-fee line on every statement |
| `commcalc.bank_deposit` | closing deposit OCR/upload | `deposit_recon.bank_deposits_by_store_day:179`, MI cash gate |
| `commcalc.daily_closing` | closing sweep `033` | `deposit_recon.closing_cash_raw_by_store_day:147`, MI cash gate; **BS `store_cash_on_hand` line via `_cash_position_core` → `balance_sheet.store_cash_cells`** (mig `938`, basis-gated); **P&L bill-pay carve-out** (`account/billpay_pl.billpay_cells` on `epay_on_cash`/`epay_on_credit`, mig `939`, presentation-gated); **bill-pay coverage recon** (`_closing_collected_by_store_day` → `/billpay-coverage/{period}`); **CARD SETTLEMENT RECON** (`external_credit_recon.declared_cells` on the tender columns the org's `closing_tender_def.processor_key` routes — house map `t_ext_cc`→external_cc, `t_credit`→pos_merchant — → `GET /closing/external-credit-recon`, mig `960`/`961`, §12) |
| `commcalc.billpay_pickup` (mig `942`, sibling of `cash_pickup`) | `POST /closing/billpay-pickup` (+`/undo`, `/deposit` — the parameterized cash-pickup machinery pointed at this table) | `GET /closing/billpay-pickups` (`_billpay_position_core`: declared `epay_on_cash` − picked = pending remittance), `GET /closing/cash-recon-management`; folds into `_cash_position_core` general outflows ONLY under `cash_pickup_config.billpay_relieves_cash` (default false — no double-count; §12) |
| `commcalc.billpay_pickup_config` (mig `942`) | `PUT /closing/billpay-pickup-config` | `_notify_pickup` (billpay kind; falls back to `cash_pickup_config` recipient when unset) |
| `commcalc.cash_pickup` + `commcalc.billpay_pickup` `mgmt_confirmed(+by/at)` (mig `943`) | `POST /closing/deposit-mgmt-confirm` (management-gated confirm/revoke) | `GET /closing/deposit-accountability` (green-day rule), `GET /closing/deposit-recon` `pickup_deposit` line item (§12 deposit accountability) |
| `commcalc.cash_pickup` + `commcalc.billpay_pickup` `actual_picked_amount` (mig `949`) + `cash_pickup_config.pickup_actual_relieves_cash` knob | `POST /closing/pickup` / `/billpay-pickup` (item `actual_amount`, shared `_confirm_pickup_impl`; NULL = not recorded) | `GET /closing/pickups` + `/billpay-pickups` variance fields, `GET /closing/deposit-accountability` short-pickup chips (pure `closing/pickup_actual.py`, reusing `envelope_report.count_fields`); outflow swap in `_cash_position_core` ONLY under the knob (default false = declared, byte-identical; §12 actual cash picked) |
| `commcalc.daily_closing_verification` | `POST /closing/verify` (upsert; `dm_*` = the DM's corrected store-day totals — `dm_ext_cc` since mig `961`: the EXTERNAL-CREDIT portion OF `dm_store_cc`, total-preserving) | `verified_overlay.build_overlay_map` (summary/tender/cash-position overlays), `closing_submissions` dm fields, ops_chargebacks missed_dm_verify detection; `dm_epay_cash` also replaces verified days in `_billpay_position_core` (mig `942`) |
| `commcalc.daily_closing_verification_audit` (mig `935`, append-only; +`dm_ext_cc`/`prior_dm_ext_cc` mig `961`) | `POST /closing/verify` via `verification_audit.build_audit_row` (one revision per changed save; `edited_after_verify` flags a money change on an already-verified day) | audit/history readers only — no report sums these rows |
| `commcalc.closing_tender_def` (mig `111` tenant tender registry; +`processor_key` mig `960`) | tender-setup editor (`/closing/tender-config`) | closing tender fields + `_closing_amt`; **card-settlement recon leg routing** (`external_credit_recon.tender_processor_map`/`role_columns` — NULL/no row ⇒ the house map, §12) |
| `commcalc.merchant_settlement_day` (mig `955`, portal-scrape side — org × source × merchant × business_date × card_brand) | the daily merchant-portal scrape | **CARD SETTLEMENT RECON read-only** — resolved by NAME through `report_pull_map` report_key `merchant_settlement` (never hardcoded), normalized by `external_credit_recon.normalize_settlement_rows`, store via `store_merchant_id` (mig 902); a NULL `store_code` is SURFACED as unmapped, never counted as $0 (§12). The sibling `merchant_settlement_batch` (FUNDING grain) is deliberately NOT read by this tally |
| `commcalc.envelope_count` (mig `936`, one row per envelope = daily_closing row) | `POST /closing/envelope-count` (upsert on `org_id,closing_row_id`; links `chargeback_id`) | `GET /closing/envelope-report`, notify `closing_envelope_report` |
| `commcalc.ops_chargeback` (mig `504`) | detection sweeps (`ops_chargebacks.py`: missed_closing/missed_dm_verify) **+ `POST /closing/envelope-count`** (reason `envelope_short`, parent rows only, amount = actual shortage) | policy editor (reasons-in-the-wild), decide endpoints, commission settlement `_settle_ops_chargebacks`/`_ops_chargeback_deductions` (`commcalc/router.py:11265-11550`) |
| `commcalc.name_map` | name-map UI | `calc_rep_commissions` (login→storeops name), rep-employee-map |
| `commcalc.management_incentive_*` | `/management-incentive/plans` `28534`, `/compute` `28613` | MI engine, payouts, resolve |
| `commcalc.discrepancy_results` | Boost engine `discrepancy_engine.run_discrepancy` (`source='boost'`/NULL) + MA recon `ma_recon.run_ma_discrepancy` (`source='ma'`, `comp_type='MA_ACTIVATION'`) — each delete-then-inserts ONLY its own `(org, period, source)` slice; canonical DDL + attribution columns (`rule_id/rule_key/rule_reason/evidence/source/order_number`) in mig `312` (table pre-dates migrations, console-created); APPEAL columns (`appeal_status/appeal_note/appealed_by/appealed_at`) mig `947` — written ONLY by `PATCH /discrepancy-appeals/{row_id}` (pure state machine `discrepancy_appeals.py`), never by the engines | `GET /discrepancy/{period}` `router.py:19099` (selects `*`, optional `source` filter), Pay Discrepancy page; `GET /discrepancy-appeals` (period-range + filters) → Commission Discrepancy hub page (§15) |
| `commcalc.ma_payment_rule` | `/ma-payment-rules` POST/PATCH/DELETE `router.py:19214-19270` (upsert by `org_id,rule_key`; mig `312`) | `ma_recon.load_rules` → `match_rules` (first match by ascending priority; case/trim-insensitive; `effective_from/to` windows; bad regex skipped) |
| `commcalc.accessory_config` (per-org classification config, mig `208`; columns added by `214` `billpay_products`, `313` `activation_details_rules`, `944` `billpay_card_tenders`/`billpay_cash_tenders`) | `PUT /accessory-config` (Sales Report → Classification settings) | `_accessory_config(_uncached)` (accessory/billpay/blank-ct classification for `_sales_cell_agg`); `_activation_details_rules` (mig 313 — Activation-Details bucket token rules, own defensive read, house defaults via `activation_bucketing.resolve_rules`); `_billpay_tender_tokens` (mig 944 — bill-pay tender vocabulary for the §12 3-way split, own defensive read, defaults `metric_recon.DEFAULT_CARD/CASH_TENDERS`) |
| `commcalc.report_pull_map` (mig `207` — report_key → `target_table` + `column_map` + `param_spec`, org row over the house row) | `POST /commcalc/report-mappings` (`/commcalc/report-mappings`); mig `955` seeds `merchant_settlement` / `merchant_funding` | `report_pull` portal ingest; **card-settlement recon feed resolution** (`closing/router._settlement_feed_spec` → `external_credit_recon.SETTLEMENT_REPORT_KEY`, §12 — this is HOW the tally finds the scraped table without hardcoding it) |
| `commcalc.metric_source_of_truth` (per-metric basis-of-truth config, mig `923`; columns added by `944` `processor_order_types`/`processor_product_tokens` — the bill-payment row filter for the daily-TX processor feed) | `PUT /metric-source-config` | `_metric_source` (consumed by Exec MTD activation override, `/metric-recon`, `/billpay-coverage`, `_pos_billpay_for_days`/`_billpay_processor_by_store(_day)` — §12 3-way Leg C; NULL columns = `metric_recon` house defaults) |
| `commcalc.exec_metric_config` (per-org Exec-MTD metric DEFINITIONS, mig `204`; **`carrier` preset column mig `962`, `applicable` flag mig `963`**; seed fn `seed_exec_metric_config`) | `GET/PUT /exec-metric-config` `router.py` (upsert by `org_id,bucket`); 2026-09-02: LuxeLink `bill_payment` rules gained `product_desc_contains:["wallet funding"]`; **mig `962`** corrects the HOUSE `bill_payment` rules + seeds the boost carrier PRESET | `_exec_metric_config` → **`exec_metric_defs.resolve`** (tenant row > house carrier preset > built-in default) → `_sales_cell_agg` exec metrics via `exec_metric_defs.line_match` |
| `commcalc.ui_label_override` (mig `068` — one table, scope-multiplexed DISPLAY config) | `POST /nav-labels` (scopes `nav`/`group`/`cap`), `POST /nav-layout` (scope `layout`, key `__nav__`) — both now gated on the `menu_layout` settings area; `PUT /tile-layout` (scope `tiles`, key `<module>`, tenant row or HOUSE platform-default row per `tile_layout.tile_write_gate`); `PUT /report-labels` (scopes `report_col`/`report_banner`/`report_term` at the TENANT org — overrides; gated on `classification`); mig `945` seeds the HOUSE carrier-preset rows (scopes `report_col:<carrier>`/`report_banner:<carrier>`); mig `953` seeds the HOUSE carrier VOCABULARY-TERM presets (scope `report_term:<carrier>` — boost: ePay/VIP Wireless/ACIMA/b2bsoft, total: VidaPay/T-CETRA/Edge/marketplace feed, §3); mig `954` seeds the HOUSE distributor-payable BASIS presets (NEW scope `finance_basis:<carrier>`, key `distributor_payable` — boost: `asset_ledger`, total: `marketplace_due`; read by `statement_engine.carrier_payable_preset`, §4); mig `947` seeds the HOUSE Incentives tile layout (scope `tiles` key `incentives`) + HOUSE nav-label presets (NEW scopes `nav_default`/`group_default`, e.g. `/commcalc/commission-legs` → 'Commission received over M1-M12'); mig `948` seeds the HOUSE Management Overview (`tiles` key `management-overview` — incl. the `/commcalc/exec` item-label 'Rep Incentive') + Flags & Compliance (`tiles` key `flags-compliance`) layouts (§14 mig 948) | `GET /nav-config` (house `nav_default`/`group_default` presets first, then the caller org's `nav`/`group` nicknames overlay per key — tenant > house preset > built-in, since mig 947; caps/layout stay caller-org-only), `GET /tile-layout` (`tile_layout.load_tile_layout`: tenant ∪ HOUSE in one query, tenant wins), `GET /report-labels` (`report_labels.load_report_labels`: tenant ∪ HOUSE, tenant override > carrier preset > built-in — §3 carrier column labels) |
| `storeops.org_units/levels/managers` | org-hierarchy UI (storeops) | `org_span_for_manager` RPC → RBAC span, MI store set |
| `storeops.shifts` | scheduling UI (storeops) | `_fetch_shifts:17447` → Targets only (NOT pay); W3 scheduled workforce reports (via the storeops payroll/attendance handlers, §14 W3) |
| `storeops.employees` / `stores` | storeops roster | calc, targets, resolution; **market column: one of the TWO market vocabularies — store→market resolution reads it ONLY through `core.scope.market_index`/`store_market_resolver`/`market_by_code` (§13a, CI guard `harness_market_resolution_guard.py`); market OPTION lists compose ONLY through `canonical_markets`+`merge_market_options`/`org_market_options` (§13c, CI guard `harness_market_enumeration_guard.py`)** |
| `commcalc.store_mapping` / `store_aliases` | Store-Matching UI, store setup sync | attribution joins (salesforce_id / street-number: GP, residual-subs, carrier legs), store-string→code resolution (§13), **market vocabulary #2 — same §13a canonical-resolution + §13c canonical-enumeration rules + CI guards** |
| `storeops.timelog` / `manual_hours` / `payroll_settings` / `payroll_approval` (migs `045`,`431`) | timeclock, manual-hours UI, W-4 form, approvals board | payroll/payroll-raw/approvals handlers — now ALSO reached in-process by the W3 scheduled workforce reports (`notify/workforce_reports.py`, §14 W3); no second query path |
| `storeops.store_lease` (mig `946` — one row per org×store: landlord/site contact, rent links + ACH (SENSITIVE), `current_rent`/`rent_effective_from`/`escalation_pct`/`rent_schedule`/`rent_due`, lease dates, insurance + `insurance_premium_due`/`_frequency`) | `PUT /storeops/store-lease` (gated `can_see_lease`, upsert on org+store) | `GET /storeops/store-lease`; the finance rents-due/recurring-expenses reader `GET /account/liabilities-due` (`account/liabilities_due.rent_due_rows`/`insurance_due_rows` computing FROM `store_lease.rent_for_month`/`resolve_rent_due`/`rent_due_window` — the §14 read contract honored, never re-derived; gated `can_see_lease`, ACH columns never selected) |
| `storeops.store_document` (mig `946` — append-only lease/COI versions; files in PRIVATE bucket `store-docs`) | `POST /storeops/store-lease/doc` (gated; INSERT only, prior versions kept) | `GET /storeops/store-lease` version lists (path never echoed), `GET /storeops/store-lease/doc-url`/`doc-view` (org-scoped by id → signed URL) |
| `storeops.insurance_policy` + `insurance_policy_store` (mig `964` — ONE policy covering MANY stores; `premium` here is INFORMATIONAL, no money reader reads this table) | `POST/PUT/DELETE /storeops/insurance-policies`, `PUT /storeops/insurance-policies/stores` (all gated `can_see_lease`, store codes validated against this org's `storeops.stores`) | `GET /storeops/insurance-policies`; `GET /storeops/store-lease` (`policies` covering that store); `router._expiry_subjects` → expiry notices + the `storeops_doc_expiry` attention providers |
| `storeops.document_extraction` (mig `965` — the AI DRAFT: per-field value + confidence + VERBATIM source snippet/page, clauses with clause number + plain English, extra items, contacts, `applied` audit) | `POST /storeops/document-extract` (gated; `run_in_threadpool`, INSERT only — re-reading appends), `POST /storeops/document-extraction/accept` (stamps `applied`/`status`) | `GET /storeops/document-extraction`; **`doc_intel.apply_plan` is the ONLY door from here to a live column** — refuses ACH targets always and every `MONEY_GUARDED` field without an explicit human money confirmation, so `account/liabilities_due.py` only ever books human-accepted dollars |
| `storeops.document_contact` (mig `966` — MULTIPLE expiry contacts; `subject_kind` 'lease' + store_code covers that store's lease AND certificate, 'insurance_policy' + policy id) | `PUT /storeops/document-contacts` (gated; replace-set per subject) | `GET /storeops/document-contacts`, `GET /storeops/store-lease` (`contacts`), `GET /storeops/insurance-policies`, and the expiry sweep's recipient list (`doc_intel.expiry_alerts`) |
| `storeops.tenants.rent_due_default` + `lease_visible_roles` (mig `946` config columns) | `PUT /storeops/store-lease/tenant-defaults` (due default); roles column set per-org via SQL/admin | `store_lease.tenant_lease_config` (adaptive — pre-946 = house first-week + market-manager-and-above), `can_see_lease` gate |
| `storeops.google_review_config` / `google_review_sweep_config` / `google_review_store` / `google_review_snapshot` / `google_review_item` (migs `411`/`412`, service-role-only; sequence grants mig `951`) | config: `PUT /storeops/google-reviews/config` + `/sweep-config` + `/store-config/{code}`; data: `google_reviews.sweep_store` (place pin upsert, snapshot insert, item dedupe-insert) via run-now/run-due; sweep status: `_gr_set_sweep_status` (`last_detail` carries error samples since 2026-09-04) | `/google-reviews/my`, `/dm-dashboard`, `/store/{code}`, `/stores`, `/employee/{id}`, `/employee-summary` (§14 Google Reviews); below-target → `storeops.action_plan` rows |

---
| `core.platform_operator` (mig `980` — the SEPARATED identity: keyed by auth_id, **no org_id column at all**; scoped `operator_role` + optional per-row capability overrides + `expires_at` for just-in-time elevation) | `POST/DELETE /core/operator/roster` (never creates a login, never touches `app_users.super_admin`); SEEDED by mig 980 from the existing `super_admin` flag | `operator_api._authority` → `operator.resolve_authority` (unioned with the legacy flag), `GET /core/operator/roster` |
| `core.platform_operator_policy` (mig `980`, singleton — RULE TWO config; `legacy_membership_flag_honored` IS THE CUTOVER, `require_entry_session` is the access-cutting proposal, both default to today's behaviour) | `POST /core/operator/policy` (refuses the cutover while zero active operators exist) | `operator.effective_policy` (an absent/garbage row ⇒ POLICY_DEFAULTS ⇒ today) |
| `core.operator_action` (mig `980` — append-only AND hash-chained; UPDATE/DELETE revoked. Records the OPERATOR's own auth id + email, never the tenant's) | `operator_api._write_action` (FAIL-CLOSED on every mutating action: a 503 rather than an unrecorded operator act) | `GET /core/operator/audit` (+ `operator.verify_chain` over the WHOLE chain), `GET /core/operator/anomalies` |
| `core.operator_entry_session` (mig `980` — the record the cross-tenant switcher never had: who entered which company, why, from what IP, hard `expires_at`) | `POST /core/operator/enter` / `/exit` | the in-tenant banner (`GET /core/operator/entry`), `GET /core/operator/entry-log`, and the TENANT's own read `GET /core/tenant-operator-access` |
| `core.platform_notice` (mig `981` — operator→tenants status broadcast; audience by org_id, never by tenant name) | `POST /core/operator/notices`, `/notices/withdraw` | `GET /core/platform-notice` (tenant-facing; org resolved from the VERIFIED membership, `org_ids` stripped from the response) |
| `core.restore_drill` (mig `981` — backup/restore ATTESTATION; `verified_at` is the heartbeat column) | `POST /core/operator/restore-drill` (refuses a record that is not evidence) | `GET /core/operator/restore-drill` → `operator.drill_lamp`; and the control box with NO code change via a `core.system_check` heartbeat row (COMMENTED OUT in mig 981) |

## 17. Cross-reference: by ENDPOINT (high-value)

| Endpoint | Handler line | Section |
|----------|-------------|---------|
| _every endpoint filtering/grouping by MARKET_ | — | §13a canonical resolution (`core.scope.store_market_resolver`/`market_by_code`); inventory pinned in `harness_market_resolution_guard.py` |
| _every endpoint OFFERING market options (dropdown/enumeration)_ | — | §13c canonical vocabulary (`core.scope.canonical_markets` composed via `merge_market_options`/`org_market_options`); inventory pinned in `harness_market_enumeration_guard.py`; B-1115/LI truth table `harness_market_vocabulary_truth.py` (owner 2026-09-04) |
| `GET /commcalc/exec-mtd/{period}` (returns `metric_coverage` — the silent-zero detector) · `GET/PUT /commcalc/exec-metric-config` | `router.py` `exec_mtd` / `get_exec_metric_config` / `put_exec_metric_config` | §3 Exec-MTD metric definitions (carrier presets + detector, mig `962`) |
| `POST /commcalc/data-sources/sweep/run-due` | `router.py:data_sources_run_due` | §12a — the ONE portal-pull scheduler (VidaPay, b2bsoft, and the three merchant portals); cron self-registered by mig `956` |
| `GET /commcalc/merchant-portals/catalog` | `router.py:merchant_portal_catalog` | §12a portal descriptors for the connector settings page |
| `GET /commcalc/merchant-portals/health` | `router.py:merchant_portal_health` | §12a durable-session health roll-up |
| `GET /core/control-box` (the red/green board; `deep=1` runs heavy providers) · `GET /core/control-box/checks` (effective registry) · `GET /core/control-box/history` · `GET /core/control-box/platform` (the ONE cross-org surface — lamps + counts ONLY, no tenant figures) | `core/control_box_api.py` | §20 super-admin control box |
| `GET /billing/ai-usage` · `GET/PUT /billing/ai-margin` (append-only, effective-dated = its own audit) · `POST /billing/ai-usage/close` (freeze) | `billing/usage_api.py`; pure `billing/ai_usage.py` | §21 AI usage + margin (migs `972`/`973`) |
| `GET/PUT /billing/module-pricing` (the plan x module grid, DERIVED from the entitlement catalog) · `GET /billing/module-usage` | `billing/usage_api.py`; pure `billing/statement.pricing_grid` / `billing/module_usage.py` | §21 module pricing (migs `974`/`975`) |
| `GET /billing/statement` (itemized: monthly fee + per-module + AI usage) · `POST /billing/statement/close` (freeze) · `GET /billing/usage-overview` (cross-org — MONEY AND COUNTS ONLY, no tenant business data) | `billing/usage_api.py`; pure `billing/statement.py` | §21 itemized statement |
| `POST /core/control-box/run` (manual, deep) · `POST /core/control-box/run-due` (pg_cron entrypoint, `x-notify-secret`, self-scheduled by mig `971`; enumerates TENANTS so a never-checked org is never invisible) | `core/control_box_api.py` (`run_now` / `run_due`) | §20 daily check |
| `GET /core/control-box/fix-task/{check_key}` (deterministic, NO AI — the copy-into-Claude-Code bundle) · `POST /core/control-box/ai-triage` (super-admin only, purpose-locked, no prompt passthrough, rate + budget capped, fully audited) | `core/control_box_api.py` (`get_fix_task` / `ai_triage`); pure guard `core/control_box.ai_guard_decision` | §20 AI path (mig `972`) |
| `POST /commcalc/data-sources/{sid}/live-login/submit-totp` | `router.py:live_login_submit_totp` | §12a authenticator-app code into the live session (never SMS/email OTP) |
| `POST /calculate/{period}` | `router.py:8968` | §6 rep commission |
| `GET /commissions/{period}` | `10222` | §6 — the Rep Incentive Report read; market stamped per row via §13a (2026-09-03 fix) |
| `GET /commcalc/processor-ledger` | `commcalc/processor_ledger_api.py` | §15 Processor Daily Debits & Credits — day × transaction type, DEBITS/CREDITS/NET; store-span gated; serves the canonical §13c `market_options` |
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
| `GET /account/residual-per-sub` | `account/router.py:~1042` → `account/residual_subs.compute` | §7a Residual per Subscriber — per-tenant residual source (Boost `raw_mi` / MA `raw_ma_daily_tx` mig-309 union); MA stores via the mig-314 account→store index; §13c `market_options`; grant `residual_per_sub` OR `account_trends` |
| `GET /payables/forecast` | `payables/router.py` `forecast` | §15 (module 095) |
| `GET /payables/payables` | `payables/router.py` `list_payables` | §15 (module 095) |
| `GET /payables/filter-options` | `payables/router.py` `payables_filter_options` (canonical §13c) | §15 (module 095) |
| `POST /payables/rebuild` | `payables/router.py` → `engine.build_ledger` | §15 (module 095) |
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
| `GET /discrepancy-appeals` (period-RANGE + source/status/appeal_status/store/date filters; distinct path so `/discrepancy/{period}` can't swallow it) | `commcalc/router.py` (`list_discrepancy_appeals`, beside the discrepancy block) | §15 Commission Discrepancy hub |
| `PATCH /discrepancy-appeals/{row_id}` (appeal state machine — validate against CURRENT row state, who/when stamped; org-scoped read-then-update) | `commcalc/router.py` (`set_discrepancy_appeal`) | §15 Commission Discrepancy hub |
| `GET/POST /ma-payment-rules`, `PATCH/DELETE /ma-payment-rules/{rule_id}` | `19200-19270` | §15 B2B ↔ MA recon |
| `GET /activation-counts/{period}` (b2b Activation-Details store/market counts; buckets via `activation_bucketing`, mig 313 — `total_activation` excludes BOTH Upgrade families) | `activation_counts` (search `@router.get("/activation-counts/`) | §15 Activation-Details basis |
| `GET /tile-layout` (`?module=` — resolved tenant>house tile layout, dashboard-builder D1) | `commcalc/router.py` (`get_tile_layout`, beside nav-config) | §14 D1 |
| `PUT /tile-layout` (fail-closed: house/foreign → super-admin; own org → `menu_layout` grant) | `commcalc/router.py` (`put_tile_layout`) | §14 D1 |
| `POST /nav-labels`, `POST /nav-layout` (RETROFIT 2026-09-01: were UNGATED — now fail-closed `menu_layout` gate, non-super pinned to own org) | `commcalc/router.py` (`set_nav_label`/`set_nav_layout`) | §14 D1 |
| `POST /notify/send`, `POST /notify/run-due` → report keys `storeops_payroll` / `storeops_hours_approval` / `storeops_payroll_tax` / `storeops_payroll_expenses` / `storeops_attendance` / `storeops_lateness` (W3 scheduled workforce reports) | `notify/router.py` `_dispatch` → `report_registry.build_payload` → `notify/workforce_reports.py` builders | §14 W3 |
| `GET /storeops/payroll-raw` (payroll-tax page inputs; mig-434 pay gate, FAIL-CLOSED 403 — ALL-money feed, §19.12 closed 2026-09-01; route `payroll_raw_route`, shared `payroll_raw()` stays ungated for pre-gated in-process callers) | `storeops/router.py` (`payroll_raw_route`) | §14 W3 |
| `GET /storeops/payroll-expenses/{period}`, `GET /storeops/payroll/approvals`, `GET /storeops/timeclock/attendance-exceptions`, `GET /storeops/accountability` | `storeops/router.py:7703` / `payroll_approval.py:469` / `storeops/router.py:4294` / `:4318` | §14 W3 |
| `GET/PUT /storeops/store-lease`, `PUT /storeops/store-lease/tenant-defaults`, `POST /storeops/store-lease/doc`, `GET /storeops/store-lease/doc-url` + `/doc-view` (ALL gated fail-closed by `store_lease.can_see_lease` — mig 946 lease/landlord/ACH/insurance + document versions) | `storeops/router.py` (`get_store_lease`/`put_store_lease`/`put_lease_tenant_defaults`/`upload_store_lease_doc`/`store_lease_doc_url`/`store_lease_doc_view`) | §14 mig 946 |
| `GET/POST/PUT/DELETE /storeops/insurance-policies`, `PUT /storeops/insurance-policies/stores`, `POST /storeops/insurance-policies/doc` (one policy, many stores — ALL gated `can_see_lease`) | `storeops/router.py` (`list_insurance_policies`/`create_insurance_policy`/`update_insurance_policy`/`delete_insurance_policy`/`set_insurance_policy_stores`/`upload_insurance_policy_doc`) | §14 migs 964-967 |
| `POST /storeops/document-extract` (AI reads an uploaded lease/policy/COI → a DRAFT; `async def` + `run_in_threadpool`, SEV-1 2026-07-30 rule), `GET /storeops/document-extraction`, `POST /storeops/document-extraction/accept` (THE money gate — `doc_intel.apply_plan`) | `storeops/router.py` (`post_document_extract`/`get_document_extraction`/`accept_document_extraction`) | §14 mig 965 |
| `GET/PUT /storeops/document-contacts` (multiple expiry-notification contacts per lease/store or policy) | `storeops/router.py` (`get_document_contacts`/`put_document_contacts`) | §14 mig 966 |
| `GET /storeops/doc-expiry` (what expires, its resolved notice window, who would be told), `POST /storeops/doc-expiry/run-now` (DRY RUN by default), `POST /storeops/doc-expiry/run-due` (NOTIFY_RUN_SECRET pg_cron entrypoint, daily) | `storeops/router.py` (`get_doc_expiry`/`doc_expiry_run_now`/`doc_expiry_run_due` → `_run_doc_expiry`; cron RPC `_ensure_doc_expiry_alert_cron`) | §14 mig 967 |

| `GET /account/pl/{period}`, `GET /account/balance-sheet/{period}` (`?scope=&stores=&markets=` — stored snapshot when unfiltered; store/market-filtered view via `statement_filter.filtered_statement`: canonical-union market resolution + company-scope AND-composition, 2026-09-02) | `account/router.py` (`get_pl`/`get_bs` → `_filtered_read`) | §4 P&L filter |
| `GET /account/statement/{period}` (`?scope=&kinds=pl,balance_sheet,cash_flow` — FRESH on-demand statements, nothing persisted; the platform statement service) | `account/router.py` (`on_demand_statement` → `statement_engine.statement`) | §4 statement engine |
| `GET /account/cash-flow/{period}` (stored derived Cash Flow snapshot, statement_type `cash_flow`) | `account/router.py` (`get_cf`) | §4 statement engine |
| `GET /account/inventory-recon` (per-store emailed-report ↔ unsold-phone-ledger ↔ manual ↔ effective tie-out + ghost counts) | `account/router.py` (`inventory_recon` → `statement_engine.inventory_reconciliation`) | §4 balance-sheet truths |
| `POST /account/compute/{period}`, `POST /account/run-due` → `statement_engine.compute_and_store` (P&L + BS + Cash Flow snapshots; supersedes `engine.compute_and_store`, 2026-09-02). run-due is SELF-SCHEDULED since mig `940`: pg_cron job `account-recompute-run-due` (every 2h) via `commcalc.ensure_account_recompute_cron`, re-registered on every backend boot (`main.py` startup → `router._ensure_account_recompute_cron`) | `account/router.py` (`compute`), `account/autocompute.py` (`recompute_due`) | §4 statement engine |
| `POST /notify/send` / `run-due` → report key `financial_statement` (fresh P&L+BS+CF at send time, any period/scope) | `notify/finance_reports.py` (`_financial_statement` → `statement_engine.statement`) | §4 statement engine |
| `GET /account/analysis` (`?months=N` — chart-ready monthly trend/margins/OPEX composition/per-company+store comparison from STORED snapshots; `account_trends` grant; company series fail-closed via `own_company_ids`) | `account/router.py` (`financial_analysis` → pure `analysis.assemble`) | §4 financial-analysis series |
| `GET /account/overview/{period}` (headline scopes + THE company/scope dropdown source for dashboard/P&L/BS/Cash-Flow; company scopes fail-closed against `coa.org_companies` per §13b) | `account/router.py` (`overview`) | §4, §13b |
| `GET /account/companies` (canonical company picker — journal + companies pages) | `account/router.py` (`list_companies` → `coa.org_companies`) | §13b |
| `GET /account/projection` (`?months=&horizon=` — deterministic linear/seasonal-naive P&L projection + cash runway, per-org `projection_config` mig `941`; rows flagged `projected:true`; `account_trends` grant) | `account/router.py` (`financial_projection` → pure `projection_engine.project`) | §4 projection engine |
| `GET /account/valuation` (assumption-driven ESTIMATE range: TTM multiples + asset floor + projection-fed DCF w/ sensitivity grid; per-org `valuation_config` mig `941`; own default-closed `company_valuation` grant; disclaimer always in payload) | `account/router.py` (`company_valuation` → pure `valuation.valuation`) | §4 company valuation |
| `GET/PUT /accessory-config` — now also carries `gp_acc_basis` ('sales' house default / 'gp' opt-back, mig 932) | `commcalc/router.py` (`get_accessory_config`/`put_accessory_config`) | §4 Acc Sales basis |
| `GET /report-labels` (resolved carrier-aware report column labels + banner on/off + VOCABULARY TERMS per carrier: tenant override > house carrier preset (migs 945/953) > built-in/neutral; consumed by Exec MTD + Activations headers/exports, the `unrecognized_ct_recon` banner gate, and the closing surfaces' processor/financing labels), `PUT /report-labels` (tenant overrides only, registry-validated keys incl. `terms`, ''=revert-to-inheritance; `classification` settings gate) | `commcalc/router.py` (`get_report_labels`/`put_report_labels` → `report_labels.py`, beside `/accessory-config`) | §3 carrier column labels + vocabulary terms |
| `POST /closing/verify` (upsert + mig-935 audit append), `GET /closing/submissions` (now carries `dm_*` modified values + `envelope_view_url`), `GET /closing/summary` (now carries `totals_original`), `GET /closing/envelope-view?row_id=` (sign + 302 redirect) | `closing/router.py` (`verify_store`/`closing_submissions`/`closing_summary`/`closing_envelope_view`) | §12 DM-verification audit |
| `GET /closing/envelope-report`, `POST /closing/envelope-count`, `POST /closing/envelope-chargeback/decide`; notify report key `closing_envelope_report` | `closing/router.py` (`envelope_report`/`save_envelope_count`/`decide_envelope_chargeback`); `notify/closing_reports.py` | §12 Envelope report |
| `GET /closing/external-credit-recon` (CARD SETTLEMENT RECON — declared closing card figures, incl. the external credit machine, vs each processor's scraped daily settlement; RULE FIVE filters + `role`/`status`; GATED market-manager-and-above via `billpay_pickup.can_see_cash_recon`, fail-closed 403, plus the manager keyset); W3 report key `closing_external_credit_recon` | `closing/router.py` (`external_credit_recon`; feed resolution `_settlement_feed_spec`/`_settlement_rows_for_days` through mig-207 `report_pull_map`, tolerance `_settlement_tolerance` through mig-923 `metric_source_of_truth`); pure `closing/external_credit_recon.py`; `notify/closing_reports.py` | §12 external credit machine + card settlement recon |
| `GET /closing/entry-quality`, `GET /closing/entry-quality/me`, `POST /closing/entry-quality/run-due` + `/run` | `closing/router.py` (`entry_quality_report`/`entry_quality_me`/`entry_quality_run_due`) | §12 entry-quality coaching |
| `GET /closing/billpay-pickups` (envelopes carry `credit` = declared bill-pay-on-card + `total_credit`, mig `944`; POS comparison base = declared cash+credit; `market=` resolves via the shared `_resolve_market_filter` — comma-joined multi-market grants match per-component, 2026-09-02 DM-envelopes fix, same as `GET /closing/pickups`), `POST /closing/billpay-pickup` (+`/undo`, `/deposit`), `GET/PUT /closing/billpay-pickup-config` (mig `942` — the cash-pickup machinery, parameterized, on the sibling `billpay_pickup` table) | `closing/router.py` (`billpay_pickups`/`billpay_confirm_pickup`/`billpay_undo_pickup`/`billpay_record_deposit`; core `_billpay_position_core`, pure `closing/billpay_pickup.py`) | §12 Bill Payment Pickup / §12 3-way recon / §12 multi-market-grant filter |
| `GET /closing/cash-recon-management` (GATED market-manager-and-above via `billpay_pickup.can_see_cash_recon`, fail-closed 403; declared vs pickups vs POS on one screen, bill-pay mismatch flag; since mig `944` ALSO the 3-WAY bill-pay recon — declared vs sales-tx (tender-split) vs processor, `three_way_status` per row + `three_way` summary); W3 scheduled report key `closing_billpay_recon` | `closing/router.py` (`cash_recon_management`; POS sides via the shared `_pos_tenders_for_days`/`_pos_billpay_for_days`, sales side via `_sales_billpay_for_days` → `commcalc.router._billpay_sales_by_store_day`; pure math `metric_recon.reconcile_billpay_three_way_days`); `notify/closing_reports.py` | §12 management cash recon / §12 3-way recon |
| `GET /closing/deposit-accountability` (keyset-scoped green-day board; `can_confirm` flag; since mig `949` day rows also carry `pickup_short_rows`/`pickup_over_rows`/`pickup_variance_total` + summary `short_pickup_days`), `POST /closing/deposit-mgmt-confirm` (GATED `can_see_cash_recon`, fail-closed 403) | `closing/router.py` (`deposit_accountability_board`/`deposit_mgmt_confirm`; pure `closing/deposit_accountability.py`, mig `943`; variance via `closing/pickup_actual.py`, mig `949`) | §12 deposit accountability / §12 actual cash picked |
| `GET /billpay-coverage/{period}` (per store/day: bill-pay ≤ cash+card, exceptions surfaced) | `commcalc/router.py` (`billpay_coverage` → `metric_recon.reconcile_billpay_coverage`) | §4 bill-pay carve-out / §15 |
| `GET /kpi-failing/{period}` (failing-KPI overview: /coaching target resolution + in-process `/dlar-store` store rows + `rep_commissions.kpi_values`; pure `kpi_failing.py`) | `commcalc/router.py` (`get_kpi_failing`, beside `/dlar-store`) | §10 failing-KPI report |
| `GET /compliance-summary` (per-queue open counts over the existing flag/exception surfaces; failed probe = null, never 0; pure `compliance_summary.py`) | `commcalc/router.py` (`get_compliance_summary`, beside `/kpi-failing`) | §14 mig 948 Flags & Compliance |
| `GET /account/liabilities-due` (owed-to-distributor + due-this-week payments/payroll/payroll-tax/rents/insurance per store; mig-434 + mig-946 gates fail closed; pure `account/liabilities_due.py`; distributor side = the SAME as-of-parameterized derivation the BS books, mig `954`) | `account/router.py` (`liabilities_due_endpoint` → `_liabilities_due_impl`) | §4 Current Monetary Liabilities |
| `GET /account/config` / `PUT /account/config` (this tenant's finance config: accessory COGS %, service-fee products, payroll names/routes, device-COGS mode, **and the mig-954 distributor-payable tenant mapping** — resolved basis + source + valid target-line options) | `account/router.py` (`get_config` → `_distributor_payable_config`, `put_config`) | §4 balance-sheet truths / tenant onboarding contract |

| `POST /storeops/google-reviews/sweep/run-now` + `/sweep/run-due` → `_do_google_reviews_sweep` → `google_reviews.sweep_org/sweep_store`. run-due is SELF-SCHEDULED since mig `950`: pg_cron job `google-reviews-sweep-run-due` (every 15 min; per-org `next_run_at` gates actual sweeps) via `storeops.ensure_google_reviews_sweep_cron`, re-registered on every backend boot (`main.py` startup → `storeops/router._ensure_google_reviews_sweep_cron`) — before 950 NOTHING ever invoked run-due | `storeops/router.py` | §14 Google Reviews |

(Full 468-endpoint list: `grep -nE '@router\.(get|post|put|patch|delete)\(' backend/app/modules/commcalc/router.py`.)

---

| `GET /core/operator/me` (the operator persona: capabilities, scoped role, **why** they are authorized, effective policy, console nav) · `/roster` `GET/POST/DELETE` · `/policy` `GET/POST` (the CUTOVER) · `/audit` · `/anomalies` · `/entry-log` · `/notices` · `/restore-drill` | `core/operator_api.py`; pure decisions `core/operator.py` | §22 platform operator console |
| `POST /core/operator/enter` / `POST /core/operator/exit` (the AUDITED, time-boxed tenant entry — wraps the EXISTING cross-tenant switcher, adds no second bypass) · `GET /core/operator/entry` (drives the in-tenant banner) | `core/operator_api.py` (`enter_tenant`/`exit_tenant`); pure `operator.entry_decision`/`banner_payload` | §22 tenant entry |
| `GET /core/platform-notice` (tenant-facing status banner — any signed-in user; org from the VERIFIED membership) · `GET /core/tenant-operator-access` (a TENANT admin's "who from the platform was in my company", gated by the existing `_require_setting(..., 'security')`) | `core/operator_api.py` `public_router` (mounted WITHOUT the /operator prefix) | §22 tenant-facing transparency |

## 18. Cross-reference: by METRIC / KPI

| Metric | Source table.column | Reader function |
|--------|--------------------|-----------------|
| Tenant AI cost / billable price | `ai_usage.exact_cost` (in x rate_in + out x rate_out from `core.token_rates` — EXACT split, not mig 718's blend) + `apply_margin`; unpriceable models are stated, never $0 | `GET /billing/ai-usage`, `core.ai_usage_period`; §21 |
| Tenant billable total (itemized) | `statement.build_statement` = monthly fee + per-module (billable calls x price) + AI usage; lines quantised once and summed so the document adds up | `GET /billing/statement`, `core.billing_statement`; §21 |
| Platform health lamp (per subsystem, per tenant, and the roll-up) | `control_box.evaluate_check` → `roll_up`; composed from `import_health.collect_attention` + `portal_session_health.summarize` + scheduler heartbeats. Ladder `green < unmonitored < amber < unknown < red`; `unmonitored` is NEVER counted as green and the coverage fraction is stated out loud | `GET /core/control-box`, `core.system_check_run.lamp`; §20 |
| External credit-card settled $ (per store-day) | `merchant_settlement_day.net_amount` where `settlement_role='external_cc'` | `merchant_portals.totals_by_store_day`; tallied against `daily_closing.t_ext_cc` by `closing/external_credit_recon` (§12a) |
| POS-merchant settled $ (per store-day) | `merchant_settlement_day.net_amount` where `settlement_role='pos_merchant'` | same reader, `pos_merchant` role (§12a) |
| Exec-MTD LINE metrics (Bill Payment Qty/$ · Total Phones · Activation Fee · Total Protect) | `raw_sales.department` / `.category` / `.product_desc` matched against `commcalc.exec_metric_config.rules` (EXACT membership for dept/cat, substring for `product_desc_contains`) | `exec_metric_defs.resolve` (tenant row > house `carrier` PRESET > `CODE_DEFAULTS`, mig `962`) → `exec_metric_defs.line_match` inside `_sales_cell_agg`; silent-zero detector `bucket_coverage` → `GET /exec-mtd/*` key `metric_coverage` → Exec-MTD banner. Also the SECONDARY basis for `/metric-recon` and Leg B of the mig-`944` 3-way bill-pay recon; the mig-`939` P&L carve-out does NOT read it. Proof `harness_exec_metric_defs.py` (§3) |
| Activation counts (premium/byod/upgrade) | `raw_sales.contract_type` | `classify_contract_type` `calculator.py:40`; display via `_sales_cell_agg` `router.py:17842` |
| Accessory $ ("acc_gp") | `raw_sales.ext_price` (+ device set-up fee; NOT gp, NOT Ondigo) | `_compute_feed_actuals_py` `router.py:18678` |
| GP report accessory column ("Acc Sales" / legacy "Acc GP") | `raw_sales.ext_price` of accessory lines (`accessory_config.gp_acc_basis='sales'` — house default, mig 932) or `raw_sales.gp` (`'gp'` opt-back) | `calc_gp_report(acc_basis=…)` `gp_report.py`; label from payload `acc_label` (§4) |
| Edge count | `raw_sales` product tokens | `_mi_classify_sales_row` via `_mi_resolve_numbers` `router.py:28843` (MI only; folded into premium in rep pay) |
| Activation-report column DISPLAY LABELS (e.g. device financing: "Edge" Total-side / "ACIMA" Boost-side) + `unrecognized_ct_recon` banner on/off + carrier VOCABULARY TERMS (processor/distributor/financing/marketplace_feed/pos_system) — TERMINOLOGY ONLY, never a number | `commcalc.ui_label_override` scopes `report_col[:carrier]`/`report_banner[:carrier]`/`report_term[:carrier]` (HOUSE carrier presets migs `945`/`953`; tenant overrides) × the org's `commcalc.carrier` rows (mig `038`) | `report_labels.load_report_labels` → `GET /report-labels`; frontend `lib/report-labels.ts useReportLabels` (Exec MTD + Activations headers/exports; ct-gap banner gate; `term()` on the closing surfaces); settings `components/ReportLabelSettings.tsx`; proofs `harness_report_labels.py` + CI guard `harness_carrier_vocab_guard.py` (§3) |
| Activation TYPE buckets (AD basis: New / Port / BYOD / Tablet / Home Internet / Edge / Upgrade / hidden `BYOD Upgrade`) | `raw_custom_import` Activation-Details sheet (`Contract Type` + SP-PO/product/category name) × `accessory_config.activation_details_rules` token config (mig 313; house defaults: contract-type-only word-boundary Edge, `byod upgrade` → hidden family) | `activation_bucketing.activation_details_bucket` via `_cr_resolve_activation_details`; consumed by `_ad_cells_full`/`_apply_activation_basis` (Exec MTD + Sales Report columns), `_ad_activation_buckets` (recon), `/activation-counts/{period}`; `total_activation` excludes `TOTAL_ACTIVATION_EXCLUDED = (Upgrade, BYOD Upgrade)` |
| Processor DEBITS / CREDITS / NET per day × transaction type | `raw_payment_detail.amount` (>0 credit, <0 debit) and `raw_ma_daily_tx.retail_cost` (>0 debit, <0 credit) — the sign convention is a property of the FEED SHAPE, not the carrier (`processor_ledger.FEED_SHAPES`) | `processor_ledger.classify_amount` → `fold_cells` → `day_type_rollup`; NET = credits − debits at every grain (positive = the processor paid the dealer more than it took). Live-verified + pinned in `harness_processor_ledger.py` (§15) |
| VHI/FIOS / home-internet count | `raw_sales` product tokens / `installment_category.py:82` | `_mi_resolve_numbers` `28843`; `installment_category` (plan-mode, runtime-only) |
| Failing-KPI classification (store/rep below target; `no_data` never fails) | `raw_dlar_store` KPI columns (store grain, `kpi_failing.STORE_KPI_COLUMNS`) + `rep_commissions.kpi_values` (rep grain) vs `payout_config.kpi_*_target` falling back to `carrier_kpi_metric.target_default` | `kpi_failing.evaluate/store_rows/rep_rows` via `GET /kpi-failing/{period}` (§10) |
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
| Residual per subscriber (per store, per month) | Boost: `raw_mi.actual_mi_payout + actual_atu_payout` ÷ distinct paid `phone_number`. MA/VidaPay: (−`retail_cost` on the SAME mig-309 residual union as the row above + `merchant_discount` airtime margin) ÷ `raw_ma_commission` activated lines | `residual_subs.compute` → `GET /account/residual-per-sub` (§7a); store names via `ma_store_pnl.canonical_store_index`; pinned `harness_residual_per_sub.py` |
| MA month-spiff commission M1..M12+ (P&L `carrier_comm`, cash basis) | `raw_ma_daily_tx.retail_cost` sign-flipped on `order_type ∈ pl_ma_spiff_order_types` rows (default `PostPaid Additional Spiff`); month detail `M<n>` from `product_name` via `commission_ledger.parse_payment_month` (no token → 'Spiff (other)') | `ma_store_pnl.ma_tx_bookings` → `coa.build_inputs` (mig `314`; only when `pl_ma_month_spiff_source='daily_tx'`, which also suppresses the `raw_ma_commission.spiff_m1..m6` activation-month booking — never both) |
| MDF / market spiff (P&L `mdf_income`) | `raw_ma_daily_tx.retail_cost` sign-flipped on rows whose `product_name` contains a `pl_mdf_product_tokens` token (luxelink: `premium store spiff`, $1,000/store) | `ma_store_pnl.ma_tx_bookings` → `coa.build_inputs` (mig `314`; `auto_opt` line, per store; retail_cost precedence residual → MDF → month-spiff) |
| MA processor account → store | `raw_ma_fulfillment.tspid` × `business_address` (derived, ambiguous dropped) ∪ `ma_account_store_map` (override wins) | `ma_store_pnl.account_store_index`/`load_store_index` → `coa.build_inputs` `_ma_store` + `device_cogs._ma_sold_cost` (mig `314`; gated by `pl_ma_store_attribution`; unmapped accounts book company-wide) |
| Device-purchase rebate (P&L `device_rebate` contra-COGS OR `rebate_income` revenue) | `raw_ma_commission.rebate` (negative = paid to dealer) + `activation_rebate_ledger.device_rebate_amount` (positive money-in) | `ma_store_pnl.rebate_route` per `commission_org_config.pl_rebate_presentation` (mig `934`: `contra_cogs` default = K1 negative in COGS; `income` = positive revenue, luxelink) → `ma_store_pnl.ma_commission_bookings` + `coa.build_inputs` activation-ledger booking; store grain via the mig-314 account→store index |
| B2B sold vs MA paid (activation discrepancy) | sold: `SALES_DISPLAY_SOURCES` rows with non-blank `contract_type` (no swap/void), keyed on digit-normalized `serial_1`; paid: `raw_ma_commission.spiff_m1`+`rebate`/`device_margin` ∪ `raw_ma_daily_tx` month-1 / activation-order evidence (two-hop join, +1-month lookahead) | `ma_recon.reconcile_ma_activations` via `sale_installment_engine._gate_met_ma_tx` (mig `312`); unpaid rows → `discrepancy_results` `source='ma'` with rule attribution or `'no business rule configured'` |
| Commission not received + APPEAL pipeline (open $ / appeal filed / won / denied / written off, per range) | `discrepancy_results` rows (both engines) + mig-947 appeal columns; buckets computed by the PURE `discrepancy_appeals.summarize_appeals` (`no_rule_count` = the LITERAL `'no business rule configured'` marker only — evidence-first, never inferred) | `GET /discrepancy-appeals` → Commission Discrepancy hub cards (`commission-discrepancy/page.tsx`); chase list = mig-098 `/recovery/claims` (reused) |
| Card settlement recon — store→MARKET + the market option list | THE canonical union index ONLY (`core.scope.market_by_code` / `org_market_options`, §13a/§13c) — the roster read takes ADDRESS only, so no market-vocabulary site exists to pin. Deliberately CANONICAL rather than the closing family's OVERLAY: a settlement-only store has no roster row, and a `store_mapping`-only market would otherwise vanish from the filter | `closing/router.external_credit_recon` (pinned `CANONICAL` in `harness_market_enumeration_guard`; nothing to pin in `harness_market_resolution_guard`); truth table `harness_external_credit_recon.py` §J |
| Distributor payable — WHICH derivation and WHICH line (mig `954`) | `account_config.distributor_payable_basis` / `.distributor_payable_line` / `.asset_ledger_open_statuses`, else the house carrier preset (`ui_label_override` scope `finance_basis:<carrier>`, key `distributor_payable`) over the org's `commcalc.carrier` rows | `balance_sheet.resolve_payable_basis`/`resolve_payable_line` (org > carrier preset > declared mig-933 family > off; target line defaults `asset_ledger`→`owed_vip`, `marketplace_due`→`handset_payable`) → `statement_engine.build_inputs_full` + `GET /account/liabilities-due`; proof `harness_balance_sheet_truths.py` §G |
| Distributor open balance — consignment side (BS liability, mig `954`) | `asset_ledger.owed_to_vip` on rows whose `status` is in `asset_ledger_open_statuses` (default `["Open"]`) with `acquired_date ≤ as-of`; live house org 2026-09-04 = $358,221.13 (past-due $29,839.62 / not-yet-due $328,381.51) | `balance_sheet.asset_ledger_open_bookings` via `statement_engine.build_inputs_full` → the resolved target line (default `owed_vip`); store grain = the ledger's own `store` through `coa.store_resolver`; as-of = `period_as_of` (open period ⇒ today, closed ⇒ period end) |
| Handset payable (BS liability, mig `933`) | `raw_ma_daily_tx.retail_cost` on the org's `handset_payable_order_types` families, `tx_date ≤ as-of < due_date` (the vendor's own terms) | `balance_sheet.handset_payable_bookings` via `statement_engine.build_inputs_full` → BS `handset_payable` line; store grain = the mig-314 account→store index |
| Unsold-phone inventory (BS asset, mig `933`) | `inventory_aging_device.unit_cost` where `on_hand` at the store's latest `as_of_date` (basis `'devices'`); `inventory_value.swept_value` (basis `'report'`, default); `manual_value` always wins | `balance_sheet.device_inventory_cells`/`apply_inventory_basis`; tie-out `GET /account/inventory-recon` |
| Cash-deposit variance | `daily_closing.t_cash` − `bank_deposit.amount` | `deposit_recon` `:147/:179`; MI gate `28895` |
| Bill-pay cash pending remittance (per store) | `daily_closing.epay_on_cash` (DM `dm_epay_cash` winning) − `billpay_pickup.amount` (picked_up) | `_billpay_position_core` → `billpay_pickup.billpay_position` (`GET /closing/billpay-pickups` by_store; mig `942`) |
| Deposit-accountability GREEN day | ≥1 picked-up envelope AND all accounted: (`disposition='deposited'` AND `deposit_slip_path` set) OR (`disposition='handed_to_mgmt'` AND `mgmt_confirmed`) | `deposit_accountability.day_accountability` → `GET /closing/deposit-accountability` (mig `943`) |
| Actual cash picked from envelope (variance vs declared, per pickup) | `cash_pickup`/`billpay_pickup.actual_picked_amount` (mig `949`; NULL = not recorded, never 0) vs the declared `amount` snapshot; short/over/match = `envelope_report.count_fields` (the mig-936 truth table, reused) | `pickup_actual.row_variance` → `GET /closing/pickups`/`/billpay-pickups` variance fields + accountability day chips; RELIEVES `_cash_position_core` (→ mig-938 BS store-cash) ONLY under `cash_pickup_config.pickup_actual_relieves_cash` (default false = declared, byte-identical; `pickup_actual.outflow_amount`) |
| POS-beside-declared status (pickup pages) | store-day declared vs POS (X-report cash / processor bill pay — billpay declared base = `epay_on_cash`+`epay_on_credit` since mig `944`), $1 tolerance; honest `no_pos_data` gaps | `deposit_accountability.pos_next_to` ← `_pos_tenders_for_days`/`_pos_billpay_for_days` (`GET /closing/pickups`, `GET /closing/billpay-pickups`) |
| Declared-vs-POS bill-pay mismatch (per store-day) | `daily_closing.epay_on_cash+epay_on_credit` vs the mig-939 processor feed | `billpay_pickup.billpay_pos_mismatch` (`GET /closing/cash-recon-management`) |
| **External credit machine (declared, per closing row)** | `daily_closing.t_ext_cc` (mig `103` — NOT a new column). DM-corrected days: `dm_store_cc` is the COMBINED card total; `dm_ext_cc` (mig `961`) states the external portion OF it, so `t_credit + t_ext_cc == dm_store_cc` either way — the card total never moves. Its DISPLAY NAME is the mig-`960` carrier label preset (`report_col[:carrier]` key `closing_t_ext_cc`; built-in 'External Credit Card') | `verified_overlay.apply_overlay` (split) + `verification_audit.DM_FIELDS` (audit trail) + `report_labels`/`useReportLabels().colLabel` (name); already inside the mig-939 / mig-944 CARD base — deliberately unchanged. Live 2026-09-04: $62,107.78 house + $1,577.24 LuxeLink |
| **Card settlement variance (per store, day, processor role)** | declared = the tender columns the org's `closing_tender_def.processor_key` routes to a role (house: `t_ext_cc`→external_cc, `t_credit`→pos_merchant), DM-split applied; settled = `merchant_settlement_day` (mig `955`) summed over `card_brand`, reached through the mig-207 registry. variance = settled − declared, so NEGATIVE = SHORT | `external_credit_recon.recon_row` — the verdict IS `envelope_report.count_fields` (mig-936 truth table, reused; tolerance from `metric_source_of_truth` metric `card_settlement`, default 0.00) → `GET /closing/external-credit-recon`; honest gaps `no_processor_data`/`no_declared_data`/`dm_merged` carry `variance = None` and NEVER a dollar (§12); proof `harness_external_credit_recon.py` |
| Bill payment on credit card (declared, pickup column) | `daily_closing.epay_on_credit` (per envelope; credit-only closings display with no checkbox — nothing physical to pick up) | `billpay_pickups` envelope `credit` + `total_credit` (`GET /closing/billpay-pickups`, mig `944`) |
| Bill-pay 3-WAY recon (per store-day) | Leg A `daily_closing.epay_on_cash`+`epay_on_credit` (DM overlay) vs Leg B sales-tx billpay via `_sales_cell_agg` exec `bill_payment` rules + mig-944 tender split (`bill_amt_card/cash/mixed`, `classify_tender`, config `accessory_config.billpay_*_tenders`) vs Leg C processor feed (mig-939 resolution + mig-944 row filter/account fallback) | `metric_recon.reconcile_billpay_three_way_days` via `GET /closing/cash-recon-management` (`_sales_billpay_for_days`/`_pos_billpay_for_days`); W3 report `closing_billpay_recon`; proof `harness_billpay_threeway.py` |
| Store cash on hand (BS asset, mig `938`; symmetry+floor fix 2026-09-02) | DM-verified `daily_closing` declared cash (overlay-corrected) − SAME-verification-rule outflows (`cash_pickup`/`bank_deposit`/`closing_expense`/`envelope_withdrawal`, keyed to their envelope's close_date; under `'verified'` only verified store-days' outflows relieve), floored at ZERO per store (suppressed imbalance in meta `floored`), as-of period end | `balance_sheet.store_cash_cells` via `statement_engine.build_inputs_full` (`account_config.cash_on_hand_basis`: off default / verified / all); CASH in the cash-flow statement (`CF_CASH_KEYS`) |
| Bill-pay pass-through (P&L `billpay_collected`/`billpay_offset`, mig `939`) | `daily_closing.epay_on_cash`+`epay_on_credit` (DM-verified corrections win at store-day grain); pair nets to ZERO | `account/billpay_pl.billpay_cells`/`billpay_bookings` → `coa.build_inputs` (`pl_billpay_presentation='carveout'`; offset label per `pl_billpay_settlement`) |
| Bill-pay coverage (billpay ≤ cash+card per store/day) | processor feed (`raw_epay_daily_tx` per_store_day / `raw_ma_daily_tx` by `tx_date` — mig-944 row filter `ma_billpay_predicate`, accounts via store_merchant_id → mig-314 index) or declared closing split, vs `daily_closing` tender totals (DM-corrected) | `metric_recon.reconcile_billpay_coverage` via `GET /billpay-coverage/{period}` |
| Days-in-stock (aging) | `inventory_aging_device.days_in_stock` (snapshot) | device-cost recon `27338`; MI aging bonus |
| Lateness % (`late_rate` — late shifts ÷ scheduled shifts) | `storeops.timelog` punches vs `storeops.shifts` windows | `attendance_exceptions.compute_attendance_exceptions` → `accountability.aggregate`; surfaced by `/storeops/accountability` ('Lateness %' page, W2 rename) and the `storeops_lateness` scheduled report (§14 W3) |
| Withholding estimate (gross/FICA/federal/state/net) | `storeops.timelog`+`manual_hours` hours × `employees.pay_rate` × `payroll_settings` W-4 | browser: `frontend/src/lib/payroll-tax.ts computePay`; server twin: `storeops/payroll_tax_estimate.compute_pay` (§14 W3 — keep in lockstep) |
| Rent due this month / current-month rent (per store) | `storeops.store_lease.rent_schedule`→`current_rent`×`escalation_pct` (schedule wins); due window from `rent_due` → `tenants.rent_due_default` → house first-week (mig `946`) | `store_lease.rent_for_month` + `resolve_rent_due`/`rent_due_window` (the §14 read contract for the finance rents-due/recurring-expenses build); surfaced on `GET /storeops/store-lease` |
| Insurance premium due (per store, recurring) | `storeops.store_lease.insurance_premium` on `insurance_premium_due`, repeating per `insurance_premium_frequency` (mig `946`) | same read contract — finance recurring-expenses reader computes from these columns |
| Expiry notice window (per lease / policy / COI) | **MAX**(the document's own requirement — `store_lease.lease_notice_days` / `insurance_policy.notice_days` — and the org floor `tenants.doc_expiry_notice_days`, house 60; migs `964`/`966`). MAX, not override: 90/180 beats the floor, 30 never drops below it | `doc_intel.resolve_notice_days` → `doc_intel.expiry_alerts` (ladder `milestones_for`, ASCENDING = the tightest milestone crossed fires) → `GET /storeops/doc-expiry`, the daily sweep `_run_doc_expiry`, and the `storeops_doc_expiry` attention providers; dedupe in `storeops.alert_log` |
| Whether an AI-extracted value may become a booked number | `doc_intel.MONEY_GUARDED` (rent, rent effective-from, escalation, rent schedule, rent due, insurance premium + premium due, policy premium + premium due) + `doc_intel.FORBIDDEN_TARGETS` (every ACH/banking + identity column, no override) | `doc_intel.apply_plan` — the ONLY writer from `document_extraction` to `store_lease`/`insurance_policy`; refusals returned to the UI with a reason. Proof `harness_doc_intel.py` §D |

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
12. ~~`GET /storeops/payroll-raw` is NOT pay-gated~~ **CLOSED (2026-09-01, owner-approved fix, same
    day as found).** The payroll-tax page's input feed served `pay_rate` + W-4 settings to any caller
    who passed span scoping. Now gated on the route (`storeops/router.py::payroll_raw_route`) with
    the mig-434 `can_see_pay` gate, **FAIL-CLOSED (403)** rather than `get_payroll_route`'s strip
    posture — deliberately, and differently from the sibling six: this feed is ALL-money by purpose
    (every row exists to carry rate + W-4 into the browser tax calc; the payroll-tax page
    dereferences `row.settings` unconditionally, so a stripped row is a crashed page, and hours-only
    needs are served by `GET /payroll`). That matches its scheduled twin `storeops_payroll_tax`,
    which already denied loudly (`_require_pay_access` ⇒ ValueError) — live route and scheduled
    report now share one posture. The SHARED `payroll_raw()` function stays undecorated/ungated
    (get_payroll's route-vs-function split) so in-process consumers are byte-identical: the W3
    scheduled builder pre-gates with the caller's own token before calling it, and
    `harness_payroll_data_flow` proves the ungated computation. Proof:
    `harness_pay_visibility.py` §I (exec's the real route source; manager_up / permissioned /
    per-org `pay_visible_roles` / grant / pre-434 adaptive default / broken-token all covered).

13. ~~`POST /account/run-due` has NO pg_cron registration~~ **CLOSED (mig `940`, 2026-09-02)**:
    `commcalc.ensure_account_recompute_cron(url, secret)` — the mig-922 self-scheduling pattern —
    is called by the backend on EVERY boot (`main.py` `_account_recompute_cron_startup` →
    `account/router._ensure_account_recompute_cron`, service_role only), (re)scheduling the ONE
    global job `account-recompute-run-due` (every 2h, `0 */2 * * *`) that POSTs the secret-gated
    `/account/run-due` sweep. The two 2026-09-02 live consequences that motivated it (owner journal
    entries 03:05Z invisible behind an 02:30Z snapshot; Nova Wave MA daily-TX upload 03:50Z landing
    five minutes after the 03:45Z recompute) now self-heal within a tick; the staleness banner's
    Recompute button covers the intra-tick window. Changes WHEN compute runs, never WHAT.
14. **Journal page has no company/store PICKER** — free-text entry is what stranded the owner's
    equity/loan rows (mig-933 matcher now resolves typed designations server-side; the picker is
    the lasting Option-B UI fix, roadmap Phase 2).
15. **Cross-tenant Diversey leak — REMOVED (2026-09-03); the leak CLASS is now CI-pinned.** The
    2026-07-14 incident (a Luxelink sales export ingested under the HOUSE org pre-dating the
    2026-08-09 ambiguous-tenant fix) left 9 rows of Luxelink content in house data that the mig-280
    guard's default `warn` mode never removed: 6 `raw_sales` line items (Espinoza, Carolina @
    4640-A W Diversey Ave, July 2026), the 1 `rep_commissions` July row paid from them ($2.9995),
    1 `flags` MISSING_STORE_PAYMENT row, and 1 `daily_commission_accrual` monthly true-up ($3.00,
    id 98). All 9 deleted surgically by id (org-scoped) and July 2026 recomputed clean via
    `_run_calculation` (49 rows, phantom rep gone). A platform-wide content audit (every org_id
    table in commcalc/storeops/notify/pos/core, per-tenant street-token fingerprints, both
    directions) found NO other cross-tenant content. The class the org-scope guard could not see —
    correctly `.eq('org_id',…)`-scoped writes whose org VALUE was wrong at ingest — is now
    enforced two ways: `harness_org_scope_guard.py` "ingest-screen guard" fails CI on any
    raw_sales/daily_sales_feed insert not fronted by `ingest_store_guard.screen`, and
    `harness_cross_tenant_isolation.py` replays the REAL incident batch through the guard + the
    union/promotion paths (warn/block/off, fail-open, org-airtight promotion, the hourly
    re-insert negative control). Guard mode is still per-org `warn` — moving established tenants
    to `block` is the owner's call (`/ingest-guard/*`).
16. **Closed-month `raw_sales` FREEZES while `report_definitions.sales.auto=false`.** Both live
    tenants switched sales auto-derive OFF on 2026-08-09 (house 19:31Z, luxelink 22:20Z — the
    Diversey incident response); the hourly feed→raw_sales promotion stopped mid-August, so
    August `raw_sales` holds only Aug 1–9 for both orgs (house 8,355 lines vs 24,890 in the
    31-day feed) while the daily feed stayed complete. Consequence: any LEGACY-mode closed-month
    pay read (the Boost calc's `_fetch_sales_unified`, `commission_engine._read_sales` under
    `sales_source='legacy'`) computes from a 9-day month — the owner's "August rep-commission
    activations wrong while Exec MTD is right" (2026-09-03) is exactly this, because Exec MTD
    reads the feed-backed union (§3). Luxelink is already on `sales_source='union'` (complete);
    the house August fix — the module's own promotion + recalculation — RAN 2026-09-03T20:58Z:
    `_promote_feed_to_raw_sales('August 2026')` wrote 24,890 lines (monthly_only 0, ingest guard
    clean), `_run_calculation` regenerated the 42-row snapshot from the full 31-day basis
    (premium/byod/upgrade acts 201/197/215 → 588/642/696), zero cross-tenant content re-entered.
    Durable options remain open: re-enable `sales` auto in the registry, or move the
    org to `sales_source='union'` (money setting, owner's call — and since 2026-09-03 the Boost
    calc honors it too, §6). `⚠` until one of those lands — every future month will freeze the
    same way at rollover. (A month whose raw_sales is fully EMPTY — September at the freeze — is
    safe even in legacy: the all-or-nothing fallback then reads the feed; only PARTIAL is toxic.)
17. ~~Google-reviews sweep never persisted a single row (both orgs 20/20 errors 2026-08-17/20) and
    never ran on schedule~~ **CLOSED (migs `950`/`951`, 2026-09-04)** — root cause was Postgres
    42501 `permission denied for sequence google_review_store_id_seq` (migs 411/412/413 granted
    the tables to service_role, never the BIGSERIAL sequences; mig `951` grants them) and
    run-due's pg_cron job never existed (mig `950` self-schedules it, 922/940 pattern). §14
    Google Reviews. REMAINING DATA GAPS (verified live 2026-09-04, per-store, not code): house org
    has 27/29 active stores with NO address on the store row (sweep skips them until addresses or
    manual place pins land — `/storeops/reviews/config`); `B-60TH` (house) and `QV` + `Lefferts`
    (LuxeLink) fail the `wrong_street_number`/no-result guards on their stored addresses ("1 S
    60th St" vs Google's "11 S 60th St"; "21880 Hempstead Ave" needs the Queens-hyphenated
    "218-80 Hempstead Ave"; "104-08 Lefferts Blvd" needs city/state) — fix the address or set the
    Place ID manually; the guard refusing to cache a neighboring business is by design. `⚠`

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

---

## 20. Super-admin CONTROL BOX — platform red/green board + the daily check

**Owner directive 2026-09-05 (sanjot@):** *"a separate agent is needed to work on the super admin side
control box to monitor the functions of all aspects of the platform, showing red light or green light
of the system and a daily check required to make sure the system is working, the control box will have
a link to those module and a way to fix that problem connected with Claude code so that can be fixed,
must protected from third party misuse of the ai api and only restricted to this module"*.

**IT COMPOSES; IT DOES NOT RE-DERIVE.** The board holds NO second opinion about any subsystem's
health. Everything it shows comes from mechanisms that already existed (duplicate-check build gate):

| Reused mechanism | Where | What the board does with it |
|---|---|---|
| `core.import_health.collect_attention` + `PROVIDERS` (44 live providers across 12 modules) | `core/import_health.py:781` | ONE lamp per provider, derived from the LIVE registry at call time — a module registering a new provider gains a lamp with **no code change and no migration here** |
| `commcalc.portal_session_health.summarize` / `STATES` (§12a) | `commcalc/portal_session_health.py` | Its ladder stays THE ladder for sessions; the board only MAPS it (`control_box.LAMP_FROM_PORTAL_STATE`), and `harness_control_box.py` §B fails if a state is ever added there and left unmapped |
| `core.import_health.feed_health` (mig 717) | `core/import_health.py:745` | Consumed through the `imports` provider above; freshness is never recomputed |
| `GET /health` deployed-commit reporting | `main.py:_deployed_commit` | The `deploy_identity` lamp |
| `core.token_rates` (mig 718) | mig `718` | The ONLY $/MTok source; `core.ai_call_audit` therefore stores TOKENS only and has no cost column |

**WHAT IS GENUINELY NEW** (nothing else answered these):
- **Scheduler LIVENESS.** The four self-registering pg_cron jobs (migs `922`/`940`/`950`/`956`) each
  self-heal their REGISTRATION on boot, but nothing ever noticed a registered job that had stopped
  PRODUCING — the exact failure mig 950 found by accident. `heartbeat_lamp` measures each one from
  the table that job itself stamps (`commcalc.email_sweep_config.last_run_at`,
  `commcalc.data_source.last_run_at`, `storeops.google_review_sweep_config.last_run_at`,
  `commcalc.account_statements.computed_at`) — the SOURCE is config, not a branch per subsystem.
- **The watchman is watched.** `control_box.selfcheck_row` puts the board's own daily-run freshness on
  the board. If the daily check stops, that row goes RED instead of leaving yesterday's green lamps up.
- **A COVERAGE FIX found while building this:** `merchant_portals.is_portal` recognises only the three
  CARD processors (businesstrack / payanywhere / transfirst), so filtering portal-session health by it
  — as `GET /commcalc/merchant-portals/health` correctly does for its own purpose — would have left
  the **VidaPay/T-CETRA and b2bsoft session logins silently unwatched**. The board's lamp covers a
  source when it is a known portal OR the row actually carries session/auth state (§12a, mig 956).

**HONESTY RULES (enforced in the pure layer, so no caller can bypass them).** *"A control box that
shows green for a subsystem it does not actually check is worse than one that says 'not monitored'."*
1. An unrecognised probe kind, a probe that raised, a heavy provider deferred this pass, or missing
   evidence ⇒ `unknown`. **Never green.** Being blind is a state you can see.
2. A disabled check, an automation the tenant does not use, or a declared-but-unprobed subsystem ⇒
   `unmonitored`, reported in `coverage`, never folded into a green headline.
3. `roll_up` over ZERO monitored checks ⇒ `unknown`, never green. An empty board is not a healthy one.
4. The daily check enumerates **tenants** (`storeops.tenants`), not state rows, so a tenant nobody has
   ever checked is `unknown` on the platform view rather than invisible.

**LAMP LADDER** (worst last, `control_box.LAMPS`): `green < unmonitored < amber < unknown < red`.
Deliberately NOT the portal ladder — that alphabet describes one session and prescribes a remedy;
this one describes any subsystem to an operator. `unknown` outranks `amber` (not knowing is more
urgent than one late feed); the headline EXCLUDES `unmonitored` and reports it as coverage instead.

**FILES**
- `backend/app/modules/core/control_box.py` — **PURE** (stdlib only): `LAMPS`/`worst_lamp`/`is_worse`,
  `LAMP_FROM_PORTAL_STATE`, `evaluate_check` (+ per-kind evaluators), `roll_up`, `sort_board`,
  `heartbeat_lamp`, `is_due`/`due_orgs`/`next_run_at`, `selfcheck_row`, `escalations`, `redact`,
  `ai_guard_decision`, `validate_check_key`, `ai_audit_row`, `rollup_usage`, `build_fix_task`,
  `fix_task_bundle`. Check kinds: `attention_provider · portal_sessions · heartbeat · counter ·
  boolean · unmonitored`.
- `backend/app/modules/core/control_box_api.py` — I/O only: `default_specs` (44 provider specs + 7
  platform specs), `effective_registry` (code defaults < HOUSE rows < org rows), `gather_evidence`,
  `build_board`, the endpoints, `_ensure_system_check_cron`.
- `frontend/src/app/(platform)/admin/control-box/page.tsx` — the board (nav: `rbac.ts`, module
  `admin`, super-admin-gated server-side on every endpoint).
- **Harnesses:** `harness_control_box.py` (134 checks — ladder, honesty, portal-adapter drift, daily
  scheduling, AI guard, redaction, fix bundle) and `harness_control_box_board.py` (41 checks — board
  assembly over a fake client: composition, config overrides, honesty under I/O failure, coverage).

**AI PATH — the "protected from third party misuse" half.** `POST /core/control-box/ai-triage` is
OPTIONAL COMMENTARY on a lamp that is ALREADY red. Six protections, in order (pure + proven,
`harness_control_box.py` §D): (1) fail-closed platform-super-admin gate, checked server-side BEFORE
any other state is consulted, so an unauthorized probe learns nothing about budget/usage/key
presence; (2) purpose-locked to `control_box_triage`; (3) **no prompt passthrough** — the only
caller input is a check key re-validated against the server-side registry, and the prompt is
assembled from server-side diagnostics; (4) per-org rate limit then daily call + token budget
(mig `972`, RULE TWO config with house defaults); (5) every attempt audited, allowed AND refused,
org-scoped; (6) ASYNC Anthropic client, awaited, with explicit timeout + `max_retries` — the SEV-1
2026-07-30 event-loop freeze (`account/ai_limits.py`) cannot recur. **Every lamp is deterministic
and computed before any of this runs**, so a refused, throttled, absent or failed AI call can never
change whether a light is red; with `ANTHROPIC_API_KEY` unset the whole board still works and the
`ai_triage_key` lamp reads amber.

**THE FIX PATH IS NOT AN AUTO-APPLY LOOP.** `GET /core/control-box/fix-task/{key}` assembles,
server-side and with NO AI call, a scoped ready-to-run task (which check failed, redacted evidence,
the module link, the index anchor, the files, and the owning CLAUDE.md agent) that a HUMAN copies
into Claude Code and reviews. No web request can apply an AI-authored change to production.

**MIGRATIONS**
- `970_system_control_box.sql` — `core.system_check` (per-tenant registry OVERRIDES over the
  code-derived defaults; a row can retune, disable, or DECLARE a check), `core.system_check_run`
  (run history = the proof the daily check ran + the baseline `escalations` compares against),
  `core.system_check_state` (per-org cadence + `last_run_at`/`next_run_at`). Seeds the house
  daily-check row and three honest `unmonitored` declarations (`db_backup_restore`,
  `frontend_uptime`, `outbound_delivery`).
- `971_system_check_cron.sql` — `core.ensure_system_check_cron(url, secret)`, the 922/940/950/956
  self-registering pattern: hourly tick (`17 * * * *`, off the busy top-of-hour so the check does not
  measure its own contention), per-org `next_run_at` gates the actual DAILY run; re-registered on
  EVERY boot by `main.py:_system_check_cron_startup`; `service_role` EXECUTE only; no literal secret
  in the file; fail-soft when pg_cron/pg_net is absent.
- `972_ai_call_guard.sql` — the **SHARED** AI guard: `core.ai_budget_config` (per `org × purpose`
  ceiling) + `core.ai_call_audit` (per-call meter AND audit trail, tokens only — $ joins
  `core.token_rates`). Generic on purpose: the platform already makes outbound AI calls from
  `account/engine`, `account/recon`, `commcalc/agency`, helpdesk `/ai-assist` and
  `remediation/propose`, each re-solving "who may spend the key"; this is one meter for all of them.

**KNOWN, DECLARED GAPS** (visible as grey lamps, not silently green): Supabase backup/restore drills,
frontend (Vercel) availability, and provider-side email/WhatsApp delivery confirmation — none is
observable from the backend today. Also note `remediation/propose`'s AI diagnosis (mig 097) is
currently org-param-gated only; it is a candidate to adopt this guard.

---

## 21. Billing — per-tenant AI usage, per-module usage, and the itemized statement

**Owner directives 2026-09-05 (sanjot@):** *"For every tenant ai usage counter needs to be built and a
cost assigned at the super admin level, the cost for the tenant will be cost of the super admin /
platform per token paid plus % or flat margin assigned by the super admin"* and *"it should bill each
call on all modules, nothing is for free, and have an itemized statement for the tenant for a clear
visibility including their monthly fee… the billing engine should list all the modules and an option to
assign price against them, a drop down menu to assign what kind of plan could belong to like free,
starter, premium etc"*.

**REUSED, NOT REBUILT** (duplicate-check build gate — most of this already existed):

| Reused | Role here |
|---|---|
| `core.token_rates` (mig `718`) | THE only $/MTok source. No fallback rate anywhere: an unpriceable model reports "no active rate", never $0 |
| `fix_pipeline.rate_for` | Resolves WHICH rate applies (tenant>house, newest `effective_date <= date`). Imported and called — the same resolver idea is used by `ai_usage.margin_for` and `statement.price_for`, three uses, ONE implementation |
| `core.ai_call_audit` (mig `972`) | The per-call AI meter, already carrying in/out tokens separately per org |
| `core.entitlements.MODULE_CATALOG` / `load_module_catalog` | THE module registry. The pricing grid is DERIVED from it, so a new module appears automatically as an UNPRICED cell |
| `storeops.pricing_package` + `storeops.tenants.package_key` (mig `908`) | The plan/tier table and the tenant→plan assignment. "free / starter / premium" are ROWS (RULE TWO), NOT a code enum; no parallel plan table was created, and mig 908's "nothing public by default" posture is untouched (the anonymous `GET /billing/public-pricing` still serves only its published display fields, never `notes`) |
| `core.access_log`'s middleware | The hook point for module counters — it already has the resolved actor + validated acting org and is already off the response path |
| `billing/` module (pricing.py, platform_costs.py, trial.py) | The existing home for tenant billing; the new code lives there, not in a new module |

**WHY THE AI COST FUNCTION IS NOT `fix_pipeline.compute_cost`** (a documented divergence, not a fork):
`compute_cost` prices ONE total token count with a BLENDED rate because agent metadata has no in/out
split. `core.ai_call_audit` DOES carry the split, and output costs ~5x input, so blending would
systematically over-bill input-heavy tenants and under-bill output-heavy ones. `ai_usage.exact_cost`
prices `in x rate_in + out x rate_out`. Harness shows the same 2M-token call as **$30 exact vs $18
blended**.

**METERING COVERAGE — the counter must not lie.** A usage counter fed only by wired call sites
under-reports real spend and UNDER-BILLS while looking authoritative. `ai_usage.AI_CALL_SITES`
declares every outbound Anthropic call site and whether it records usage; `coverage()` turns that into
a stated fraction carried on every usage figure. **All 10 sites are metered** (control box, P&L
narrative, VIP recon, agency OCR, remediation triage, 2x closing OCR, helpdesk assist, POS receipt
OCR, lease/insurance doc extraction). **METERING IS NOT AUTHORIZATION**: `ai_meter.record()` performs
no permission check and grants none — `control_box.ai_guard_decision` still governs who may SPEND, and
was not relaxed to meter anything.

**WHAT IS BILLED vs COUNTED** (owner-overridable, and both numbers are stored so it is reversible):
`billable_calls` = tenant-initiated only. `system_calls` = pg_cron ticks, `*/run-due` sweeps, webhooks,
internal service calls — **counted and shown on the statement, never charged**. Billing a tenant for
our own retry storm is wrong and destroys trust in an invoice. `anonymous_calls` are attributable to no
tenant. Infrastructure prefixes (`core`, `billing`, `vendor-api`) are excluded BY NAME, so their
absence is a decision on the record.

**HONESTY — three states kept distinct, never collapsed:** `included` (plan fee covers it, $0 and
labelled) · `priced` ($0.00 is legitimate **if the operator typed 0**) · **UNPRICED** (nobody set a
price — shown with its usage, EXCLUDED from the total, statement badged INCOMPLETE and unsendable). A
route with no module mapping counts under `unmapped` and is shown, never guessed onto a neighbour —
`main.py:_mounted_modules` exists because a hardcoded module literal went stale and "CONFIDENTLY
MISREPRESENTS the deployment"; the same bug here means a module silently billing nothing.

**MONEY CORRECTNESS**
- **No retroactive change, two mechanisms.** (a) EFFECTIVE DATING — every call/line is priced with the
  rate, margin and price in force ON ITS OWN DAY (live proof in the seeded data: `claude-sonnet-5` is
  $2/$10 from 2026-01-01 and $3/$15 from 2026-09-01). (b) SNAPSHOT ON CLOSE — because
  `token_rates` and `pricing_package.price` can be EDITED IN PLACE, closing freezes the applied rate,
  margin, prices and figures; a closed period/statement is READ, never recomputed. Proven by closing,
  then editing a rate row in place AND changing both the margin and the monthly fee, and re-reading:
  byte-identical.
- **Rounding — deliberately two different rules, each stated.** AI usage produces ONE figure: per-call
  costs are kept at full `Decimal` precision and the TOTAL is quantised once (6 dp cost, 2 dp billed,
  ROUND_HALF_UP; rounding per call would lose ~$5 per 1,000 sub-cent calls). A STATEMENT is a document
  a human checks with a calculator, so each LINE is quantised once and the total is the SUM OF THE
  QUANTISED LINES — the invoice always adds up. Inside a line, `calls x unit_price` is full precision:
  100,000 x $0.000015 = **$1.50 exact vs $0.00** if rounded per call (a 100% billing error).
- **Margin** is per-tenant, effective-dated, append-only (so the row history IS the who/when/old→new
  audit). Modes `percent` · `flat` · `percent_plus_flat`. **"Flat" is defined explicitly for the owner
  to correct**: `flat_basis='period'` (DEFAULT — one fixed amount per tenant per period) or `'call'`.
  A per-TOKEN flat is deliberately NOT offered — that is a rate, and rates live in `core.token_rates`.
  A negative margin is clamped to zero (never sell below cost); no config = pass-through at cost.

**THROUGHPUT — the shape of per-call metering.** A row per API call was REJECTED: it puts a write on
every request path and grows unbounded (the platform took a SEV-1 on 2026-07-30 from inline work, and
`core.access_log` only survives by detaching its write; per-call forensic detail already lives there).
CHOSEN: in-memory counters per (org, module, day) — the request path pays a **dict increment, no I/O**
— drained every `USAGE_FLUSH_SECONDS` (30s) into ONE additive `core.bump_module_usage` RPC on a worker
thread. Growth ≈ 20 modules x 365 days ≈ 7k rows/tenant/year. Honest cost: a hard crash loses at most
one interval, i.e. it UNDER-counts — the right direction for a usage bill. A FAILED flush is restored
and retried.

**FILES**
- `backend/app/modules/billing/ai_usage.py` — PURE: `AI_CALL_SITES`/`coverage`, `exact_cost`,
  `margin_for`/`normalize_margin`/`apply_margin`, `price_period` (+`frozen=`), `snapshot_for_close`,
  `period_bounds`/`in_period`, `summarize_tenants`.
- `backend/app/modules/billing/module_usage.py` — PURE: `DEFAULT_ROUTE_MODULE`/`INFRA_PREFIXES`,
  `module_for_path`, `classify`, `validate_route_map`, `unmapped_prefixes`, `UsageAccumulator`
  (thread-safe add/drain/restore), `rollup_by_module`.
- `backend/app/modules/billing/statement.py` — PURE: `price_for`, `module_line`, `build_statement`
  (+`frozen=`), `freeze_statement`, `pricing_grid`.
- `backend/app/modules/billing/ai_meter.py` — the metering seam (`record()`, never raises, reads
  `tenant_middleware.acting_org()` so no call-site signature changes).
- `backend/app/modules/billing/usage_flush.py` — the accumulator singleton + background flusher
  (`start`/`stop`/`flush_now`), started by `main.py:_usage_flusher_startup`.
- `backend/app/modules/billing/usage_api.py` — the endpoints (all `_require_super_admin`).
- `frontend/src/app/(platform)/admin/billing-usage/page.tsx` — operator screen (nav in `rbac.ts`).
- **Harnesses:** `harness_ai_usage.py` (66) + `harness_module_billing.py` (62).

**MIGRATIONS**
- `973_ai_usage_billing.sql` — `core.ai_margin_config` (per-tenant, effective-dated, append-only =
  its own audit) + `core.ai_usage_period` (frozen close snapshots). Seeds a HOUSE row at ZERO margin
  (pass-through), so applying it cannot charge anyone; the real-tenant margin seed is COMMENTED OUT.
- `974_module_usage_metering.sql` — `core.module_usage_daily` counters + `core.bump_module_usage`
  (SECURITY DEFINER, service_role only, ADDITIVE `calls = calls + excluded.calls` so concurrent
  backends never overwrite) + `core.module_route_map` overrides.
- `975_module_pricing_and_statements.sql` — `core.module_price` (per plan x module, effective-dated;
  `unpriced` is the ABSENCE of a row, so there is exactly one representation) + `core.billing_statement`
  (frozen itemized documents). Ships with NO prices and NO plan assignments: every module reads
  UNPRICED until the owner prices it, and all money-touching seeds are COMMENTED OUT.


---

## 22. PLATFORM OPERATOR CONSOLE — separating the operator persona from the tenant persona

**Owner directive 2026-09-05 (sanjot@):** *"Need to separate the super admin access of
Sanjot@cellfonzrus.com from Cellfonz r us tenant, make a separate view for the super admin but the
option for the super admin to log in to any tenant from it is list of tenants dashboard an option to
log in from there, Tennat billing dashboard will be another module on the super admin side, what
other industry wide super admin controls are missing yet very import do a thorough research and add
those also."*

### The problem, in one line

Platform authority was `storeops.app_users.super_admin` — a boolean on the row that ALSO says *"this
login is an employee of tenant T"*. The owner's power over the whole platform was literally a column
on their own employment record.

### IT REUSES; IT DOES NOT RE-DERIVE (duplicate-check build gate)

| Reused mechanism | Where | What §22 does with it |
|---|---|---|
| `core.router._require_super_admin` — **THE one gate** | `core/router.py:553` | `operator_api._authority` CALLS it, then unions the registry on top. Still exactly one gate; no second, weaker door. |
| `GET /core/tenants` (list + per-tenant user/login counts) | `core/router.py:807` | **IS** the console's tenant directory. No second tenant list exists. |
| the cross-tenant switcher (`x-active-org` + the middleware's super-admin no-rewrite bypass) | `client.ts`, `tenant_middleware.py:928` | **IS** the entry mechanism. §22 adds the reason/expiry/banner/audit it never had — not a new bypass. |
| `core.impersonation*` "view as employee" (mig `730`) | `app/core/impersonation.py` | **UNTOUCHED.** `impersonate` stays DEFAULT-DENY with no super-admin bypass; an entry session grants `("acting_org",)` and nothing else. |
| `core.access_log` (mig `856`) | `app/core/access_log.py` | Stays the per-request trail. `core.operator_action` records INTENT, which a request log cannot express. |
| `core.control_box` `LAMPS`/`redact`/`heartbeat` (§20) | `core/control_box.py` | IMPORTED, not re-implemented. The restore-drill lamp plugs into §20's config-driven heartbeat with **zero** control-box code change. |
| `revoke_super_admin`'s "cannot remove the LAST super-admin" | `core/router.py:944` | The same idea, applied to the cutover (`policy_change_decision`). |

### THE SEPARATION MODEL — an identity, not a flag

`core.platform_operator` is keyed by **auth id** and has **no `org_id` column at all**. A row says
"this human operates the platform" and says nothing about who employs them. It carries a SCOPED role
(`owner` / `support` / `billing` / `engineering` / `readonly` → `operator.OPERATOR_ROLES`) and an
optional `expires_at` (just-in-time, time-boxed elevation).

    authority = (legacy membership flag, while policy honors it)  ∪  (active registry row)

**The union is the whole safety argument.** Shipping can only ADD authority to a login that has it
today, never subtract — so no existing endpoint's answer changes on the day this lands.

### ★ NO LOCKOUT — the property that governed every design choice ★

This is the owner's own account on a live platform, so the change is additive with **no flag day**.
`harness_operator_console.py` §A proves the existing super-admin is authorized in EVERY state:
pre-migration (no tables) · half-applied (tables empty) · garbage/partial policy row · applied+seeded
· registry row expired or deactivated · post-cutover. The only losing state is *post-cutover with no
registry row* — which is exactly the state `policy_change_decision` **refuses to create** (and mig
980 pre-empts by seeding an `owner` row per existing `super_admin`, derived from DATA, no email
literal). The cutover ships **COMMENTED OUT** in mig 980, is refused by the API at zero operators,
warns loudly at one, and is reversible with the same control.

### TENANT ENTRY — what was actually missing

A super-admin could ALWAYS act as any tenant (pick a company in the header switcher → `x-active-org`
→ the middleware honours it without rewriting). What was missing was any record that it happened.
`POST /core/operator/enter` reuses that mechanism and adds the four properties impersonation has had
since mig 730: **audited** (hash-chained, under the operator's OWN identity), **attributable** (the
tenant's own admins read it at `GET /core/tenant-operator-access`), **time-boxed** (server-clamped
hard expiry), **visible** (a persistent banner in the tenant app). It is **not an escalation**:
`ENTRY_GRANTS == ("acting_org",)`, and the harness fails if `impersonate` ever appears there.

### TAMPER-EVIDENT AUDIT

`core.operator_action` is append-only AND hash-chained (`hash = sha256(prev_hash ‖ canonical(sealed
fields))`). An edited row, a deleted row and a duplicated `seq` are each detected at the right
position. The honest limit is stated rather than claimed away: **tail truncation still verifies as a
chain** — closed instead by the dense `seq UNIQUE` column plus the UPDATE/DELETE revoke. Every write
is FAIL-CLOSED: an operator action that cannot be recorded does not happen (503).

### FILES

- `backend/app/modules/core/operator.py` — **PURE** (stdlib only): `resolve_authority`,
  `effective_policy`, `policy_change_decision`, `operator_row_active`, `role_capabilities`,
  `has_capability`, `entry_decision`, `session_state`, `banner_payload`, `chain_hash`/`audit_row`/
  `verify_chain`, `anomalies`, `notice_visible`/`notice_lamp`, `drill_record_valid`/`drill_lamp`,
  `console_sections`. Capability vocabulary + `OPERATOR_ROLES` + `POLICY_DEFAULTS` live here.
- `backend/app/modules/core/operator_api.py` — I/O only. `router` (`/core/operator/*`) +
  `public_router` (the two tenant-facing reads). Mounted at the tail of `core/router.py`.
- `frontend/src/app/(operator)/operator/*` — the console in its **own route group**: a separate shell
  (no tenant sidebar, switcher, period picker or Ask bar), `page` (home + separation status),
  `tenants` (directory + Enter + restore drill), `operators` (roster + the CUTOVER), `audit`,
  `notices`, `billing` (PLACEMENT only — see below).
- `frontend/src/lib/operator.ts` · `operator-ui.tsx` · `operator-context.tsx`;
  `frontend/src/components/PlatformBanners.tsx` (entry banner + status banner, mounted in
  `(platform)/layout.tsx` beside the impersonation banner).
- **Harness:** `backend/harness_operator_console.py` — 149 checks (no-lockout, escalation chain,
  audit tamper-evidence, fail-closed gates, the researched controls). DB-free, stdlib only.

### BILLING IS PLACED, NOT BUILT

`/operator/billing` owns **no** billing logic. Plans/invoices remain `/admin/billing`, pricing/trial
remains `/admin/pricing`, and per-tenant AI + per-module usage remains §21 (`backend/app/modules/
billing/`, migs `973`–`975`). Two surfaces answering "what does this tenant owe" would drift, so the
console contributes navigation only. **Assumption recorded:** §21 keeps its surfaces at
`/admin/billing` and `/admin/pricing`; new operator-facing billing pages belong in
`operator.CONSOLE_SECTIONS` as a nav entry, never as a reimplementation.

### MIGRATIONS

- `980_platform_operator_console.sql` — `core.platform_operator` (the separated identity),
  `core.platform_operator_policy` (singleton config; **the cutover switch**), `core.operator_action`
  (hash-chained, UPDATE/DELETE revoked), `core.operator_entry_session`. Seeds the policy row and one
  `owner` row per existing `super_admin`. Touches `storeops.app_users` **not at all**. The cutover
  and the `require_entry_session` proposal are COMMENTED OUT.
- `981_platform_notice_and_restore_drill.sql` — `core.platform_notice` (operator→tenant status
  broadcast) + `core.restore_drill` (attestation for §20's declared-UNMONITORED backup gap). The
  `core.system_check` row that turns that grey lamp into a real heartbeat is COMMENTED OUT, because
  switching it on makes the board honestly RED until the first drill is recorded.

### RESEARCHED CONTROLS — what already existed, what was built, what is PROPOSED

**Already present (verified in-repo, NOT rebuilt):** session revocation (`POST /core/sessions/revoke`)
· IP blocking (`/core/ip-block`, mig 860) · MFA policy (`tenants.twofa_policy`, mig 711) · password
policy (mig 709) · audit-log retention sweep (`/core/audit/prune/run-due`, mig 857) · export
governance with watermark + row cap (`core.export_event`, mig 862) · per-tenant module entitlements
(`core.module_catalog` + `sync_tenant`) · trial/plan state (mig 908) · AI budget + per-call audit
(mig 972) · the control box (§20) · impersonation audit + policy (mig 730).

**BUILT here:** the separated operator identity + scoped roles · tamper-evident operator audit ·
audited/time-boxed/visible tenant entry · tenant-side transparency (`/core/tenant-operator-access`) ·
just-in-time expiring elevation · anomaly detection over operator actions · platform status/incident
broadcast · backup restore-drill attestation (closing §20's declared gap).

**PROPOSED, deliberately NOT shipped** (each changes authorization semantics, weakens a guarantee, or
is large — ranked in the PR comment): enforcing scoped operator roles on the EXISTING super-admin
endpoints · mandatory entry sessions (`require_entry_session`) · tenant lifecycle (suspend / offboard
/ export / retention-bounded delete) · break-glass dual control / second approver · per-tenant quotas
and rate limits · SSO + domain claim · data residency · DSAR export/delete · API-key and
webhook-secret lifecycle · automatic notification to a tenant's admins when an operator enters.

### KNOWN LIMITS (declared, not hidden)

1. **Entry sessions are RECORDED, not yet REQUIRED.** `require_entry_session` defaults FALSE and is
   not wired into `tenant_middleware`, so the bare switcher still works exactly as before. Making it
   mandatory is access-cutting and is the owner's call (mig 980, commented out).
2. **Scoped roles are not enforced on existing endpoints.** Only the NEW console endpoints gate on
   capabilities; every pre-existing super-admin endpoint still answers as it always did.
3. **The hash chain cannot stop a service-role rewrite** — nothing can, on a database the operator
   administers. It makes one undeniable. Tail truncation is covered by `seq`, not by the hash.
