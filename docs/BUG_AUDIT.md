# MetricsPro — System Bug Audit (2026-06-23)

> ## 🔁 RE-AUDIT 2026-06-26 (verified against current code) — read this first
> The ⬜ OPEN rows below are partly STALE. Verified status now:
> - ✅ **FIXED since the audit** (do NOT re-fix): `calculator.py` trade-in login→name (now keyed by
>   `pay_by_login[login]` + resolved via `rep['login']`, calculator.py ~228-252, commit 08dddf1) ·
>   `account/recon.py` period-spelling (now `_period_keys()` IN-clause, ~26-136, 7249fea) ·
>   `coa.py:354` PayGo phantom store (now booked **company-wide** `add(...,None,...)`, ~374-386, 74b814e) ·
>   `coa.py` `vip_fees` now `_in_period`-guarded (~343) · `vip_sweep.py` flaky-detail (now `parsed_ids`
>   gate — only replaces lines/devices when the detail parse succeeded, ~278-344, f3ec9de) ·
>   `coa.py` `owed_vip` "double-count" — INVESTIGATED + CONFIRMED **not** a double-count (PayGo-pending is
>   a real standalone ~$121k liability; asset_ledger owed ~$0), see memory owed-vip-paygo-standalone-liability.
> - ✅ **FIXED 2026-06-26 this pass**: `coa.py:_in_period` 2-digit-year (`MM/DD/YY` → `20YY`; was dropped).
> - ⬜ **STILL OPEN, deliberately NOT auto-fixed** (need user OK / a decision — not safe to do blind):
>   • period-spelling normalizer across commcalc reads — **38** `.eq('period', period)` over **56**
>     period endpoints; a blanket sweep is too broad to land safely autonomously. Recommend a
>     `_canon_period()` (YYYY-MM→"Month YYYY", canonical spelling) applied at each read boundary.
>   • `storeops/router.py` payroll `act==0 → sched` — fixing it would UNDERPAY everyone whose actual hours
>     aren't entered yet (the common case). Needs a real no-show recording mechanism + a product decision.
>   • `asset/router.py` upload atomicity — verified module; a safe fix needs a temp-table swap RPC (migration).
>   • `closing/router.py` count-mapping (new_line+postpaid vs prem+byod) — needs a defined sheet→bucket mapping.

Read-only audit of every backend module (4 parallel auditors) + live health probes. Goal: "test all
systems for being bug-free" before the SaaS/multi-carrier build. Findings are grouped by theme; each
is marked **✅ FIXED** (this session) or **⬜ OPEN** with `file:line` and severity.

Three systemic patterns dominate — fix the *pattern*, not just each instance:
1. **Unguarded delete-then-insert** → an empty/partial pull silently wipes live data.
2. **Period-spelling mismatch** (`"2026-06"` vs `"June 2026"`) → queries silently return `$0`/empty.
3. **Single-tenant / single-carrier hardcoding** → blocks the multi-carrier vision.

Live health at audit time: `account`, `storevisit`, `closing`, `asset`, `notify`, `comp/residual-trend`
all 200. No runtime outages — the risks below are silent-correctness, not crashes.

---

## Theme 1 — Data loss (unguarded delete-then-insert)

| Status | Where | Sev | Issue |
|---|---|---|---|
| ✅ FIXED | `commcalc/dlar_sweep.py` run_dlar_sweep | CRIT | Empty/auth-degraded DLAR pull wiped the live commission period and reported "OK — 0 stores" (then auto-recalc'd commissions on empty data). Added empty-abort + per-table partial-collapse guard. |
| ✅ FIXED | `commcalc/router.py` upload_file | CRIT | Delete-by-period ran **before** mapping, so a file that mapped to 0 rows wiped the month. Delete now deferred + `if mapped`-guarded + org-scoped. |
| ✅ FIXED | `commcalc/epay_sweep.py` (prior commit 98f9672) | CRIT | Comp REPLACE could collapse a month to 1 account; added empty + partial-collapse guards + data-derived period. |
| ⬜ OPEN | `commcalc/vip_sweep.py:204-244` | HIGH | A flaky invoice **detail** parse sets lines/devices to `[]` then deletes the invoice's existing lines/devices and inserts nothing → silently strips line items off historical invoices on every overlapping sweep. Fix: only delete-then-insert when the re-parse succeeded. |
| ⬜ OPEN | `asset/router.py:27` | HIGH | Asset upload deletes the entire `asset_ledger` (~30k rows) then re-inserts in a loop with no transaction. Empty case is already guarded (`if not rows: raise`), but a mid-loop insert failure leaves the table partially wiped. Fix: load to temp + swap, or chunked upsert. |
| ⬜ OPEN | `account/engine.py:190`, `recon.py:235`, `commcalc/router.py` (rep_commissions/flags/chargeback) | MED | Purge-then-loop-insert is non-atomic; a transient failure orphans the period (recompute recovers). |
| ⬜ OPEN | `storevisit/router.py:181,196,274,295` | LOW | Per-visit delete-then-insert (scoped, small blast radius); also orphans replaced photos in storage. |

## Theme 2 — Period-spelling (`"2026-06"` vs `"June 2026"`)

| Status | Where | Sev | Issue |
|---|---|---|---|
| ✅ FIXED | `closing/router.py:232,503` | CRIT | **Both** closing reconciliations (count via `daily_sales_actuals` RPC; money via `raw_sales`) were dead — queried `"2026-06"`, data stored `"June 2026"`. Now use the month-name spelling. |
| ⬜ OPEN | `account/recon.py:44,99,121` | CRIT | `reconcile()` filters every table with the bare period string → returns all-zeros on the `YYYY-MM` spelling (verified live: `recon/2026-06` → `mi_atu_total 0`). Unlike `build_inputs`, it doesn't query both. Writes bogus/empty flags. Fix: build `period_keys` list → `_fetch_all` IN-clause (mirror build_inputs). |
| ⬜ OPEN | `commcalc/router.py` (pervasive raw endpoints) | HIGH | Most report endpoints filter raw `.eq('period', period)`; they only work because callers send the matching spelling. Any caller sending `"2026-06"` silently gets `[]`. Standardize a period-normalizer at the boundary. |
| ⬜ OPEN | `account/coa.py:99` `_in_period` | MED | 2-digit-year US dates (`6/15/26`) silently drop out of period (4-digit-year guard). Latent until an upload uses `MM/DD/YY`. |

## Theme 3 — Correctness (money-affecting)

| Status | Where | Sev | Issue |
|---|---|---|---|
| ⬜ OPEN | `commcalc/calculator.py:231` | CRIT | Trade-in commission lookup keyed by `login.upper()` but `rep_map` is keyed by salesperson **name** → lookup misses, `trade_in_comm` ($20/trade-in default) silently understated for ~every rep. **Money missing from payouts.** Needs confirm + fix (key by the same identifier). |
| ⬜ OPEN | `account/coa.py:354` | CRIT | PayGo COGS (~$105k June) resolves the dealer string to a store **not in `store_mapping`** → phantom store with $0 revenue → bucketed to "Default Company"; every per-company/per-store P&L is mis-stated. Fix: add the dealer→store alias (store-match UI). |
| ⬜ OPEN | `account/coa.py:314` | HIGH | `asset_ledger` scanned with no period filter, but `vip_fees` (a P&L COGS line) is booked from that unfiltered scan → this month's COGS includes every period's VIP fees. `vip_reimb` IS period-scoped — inconsistent. |
| ⬜ OPEN | `account/coa.py:331` vs `:357` | HIGH | `owed_vip` summed from BOTH asset_ledger unsold `owed_to_vip` AND paygo pending `amount` — likely the same liability double-counted on the Balance Sheet. Confirm against the data model. |
| ⬜ OPEN | `closing/router.py:241,290` | HIGH | Count-mapping: closing `new_line+postpaid` vs B2B `prem+byod` likely double-buckets postpaid → spurious variances even after the period fix. Needs a defined sheet→RPC bucket mapping. |
| ⬜ OPEN | `storeops/router.py:119` | MED | Payroll `if act == 0: act = sched` treats a real no-show (0 hrs) as "not entered yet" → inflates actual hours/pay. No way to record a true zero. |

## Theme 4 — Residual mislabel (✅ FIXED this session)

The "Residual Trend" summed the **entire** Comprehensive Comp report and called it residual, but that
report is **~76% promo (Commission) + ~20% bounty (SPIFF) + ~2% reimbursement, $0 residual**. Per the
corrected definition, **RESIDUAL = MI + ATU**. Fixed: `comp_trend` now returns `residual_mi_atu` +
`total_comp`; page + nav renamed "Total Compensation"; shows total comp vs. residual(MI+ATU) side by
side. See [SAAS_FRAMEWORK.md](SAAS_FRAMEWORK.md) §2.

## Theme 5 — Multi-tenant / multi-carrier landmines (the SaaS gap)

These are LOW severity *today* (single tenant) but are the work the SaaS vision requires. Detail +
the target abstraction in [SAAS_FRAMEWORK.md](SAAS_FRAMEWORK.md) §3–6.

- **Hardcoded `org_id`** defaulted into ~47 endpoint signatures; resolve from auth instead.
- **Reads not org-scoped** (cross-tenant leak the moment a 2nd org exists): `sales_analyzer.py:112`
  (`raw_mi` no org filter), `coa.py:314` (asset_ledger), `storeops` `/stores`/`/employees`/`/shifts`,
  `storevisit` `stores_in_market`.
- **Boost-specific strings baked in code** → must become `carrier_category_map` / config:
  `calculator.py` (`BYOD_ACT`/`UPGRADE_ACT`/`PREMIUM_ACT`/`DEVICE_DEPTS`/KPI roster + duplicated in the
  `daily_sales_actuals` SQL), `coa.py` (`DEVICE_DEPTS`/`ACCESSORY_DEPT="Ondigo"`/`VIP_FEE_CATS`/
  `ACCESSORY_COGS_PCT`), `asset/router.py` (`MARKET_OVERRIDES`, `CHARGE_GROUPS`, `_promo_type`),
  `closing/router.py:450` (`_tender_class` keyword hints).
- **Single-vendor connectors**: hardcoded `BASE`/`DEFAULT_URL` (`dlar/vip/b2b_sweep`), hardcoded ePay
  report IDs, one `*_sweep_config` row per vendor keyed by org (no `(org, vendor_instance)` dimension),
  bespoke per-connector login with **no 2FA model**. Target: one `connector_instances` +
  `report_definition` table set + a connector interface; generic run-now/run-due dispatch by type.

## Credential security — ✅ CLEAN

No creds in logs; passwords write-only in all `put_config` handlers; `_*_public_cfg` never returns the
password; login exceptions documented to never echo secrets. No finding.

---

## Recommended fix order (post-Phase-0)

1. **`recon.py` period-spelling** (CRIT, mechanical — mirror `build_inputs`). 
2. **`calculator.py:231` trade-in** (CRIT, money) — confirm identifier, fix, recompute.
3. **PayGo dealer alias** (CRIT) — add via store-match UI (no code).
4. **`vip_sweep` per-invoice guard** + **asset upload temp-swap** (HIGH, data-loss).
5. **`coa.py` vip_fees period-scope + owed_vip double-count** (HIGH, P&L correctness).
6. Then the framework Phases 1→5 (category map → connector config → mapping UIs → onboarding wizard →
   multi-tenant), which structurally retires Theme 5.
