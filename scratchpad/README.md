# mod-commission live diagnostic artifacts — 2026-08-04

READ-ONLY production pulls (GET only; the sole POST was the Supabase password grant).
Written by `mp.py`. Nothing here wrote to the database or triggered a recompute.

- `mp.py` .................. GET-only live API helper (bearer + `x-active-org`; org=`lux`|`house`)
- `lux_plans.json` ......... luxelink commission plans / rules / tiers / assignments
- `lux_comm_July.json` ..... STORED `rep_commissions` for luxelink July 2026 (calc'd 2026-08-01 18:37Z)
- `lux_preview_july.json` .. live engine `/commission-plans/preview` — as configured today
- `ud.json` / `ex.json` / `sc.json` .. unit-dedup / exclusion / rule-scope impact (engine driven twice)
- `cov.json`, `cov_june.json` ....... `/commission-plans/coverage` July + June (unassigned reps, orphans, stores)
- `exp_nava.json`, `exp_caro.json` .. `/commission-explain` drilldowns (the two verification samples)
- `acc_audit.json` ......... `/accessory-cost-audit/July 2026` (current vs guarded-basis options)
- `preview_forced_chicago.json`, `preview_forced_ny.json` .. "what if this plan applied" previews
- `file_stores.json`, `file_emps.json` .. extracted from `NJ MTD July 2026.xlsx`
- `house_execmtd_july.json`, `house_gp_july.json`, `house_salesrep_july.json`, `house_agreement.json`
- `recon_summary.txt` ...... the per-store Task-B reconciliation table

No credentials or bearer tokens are stored here (the `.token` cache is transient and was deleted).
