# metric_source_of_truth + reconciliation — proof summary (2026-08-26)

Verified (all passing):
- metric_recon.reconcile_activations: match / mismatch / missing_in_primary (rerun_sweep vs assign_upload) /
  tolerance absorption / no_primary — 17/17 assertions.
- _ad_activation_buckets: bucket->exec-key mapping (Tablet/HomeInternet/New -> activation; Port/BYOD/Upgrade
  own columns); MTD cut drops post-cutoff rows, keeps undated; date-range windows both bases, excludes undated;
  by_store total == by_rep total (the two Exec MTD tables reconcile).
- _metric_source: empty config -> disabled/sales_agg/configured=False (byte-identical path); missing table
  (pre-923) -> safe default, no raise; configured+enabled -> activation_details basis.

Byte-identical guarantee: with no metric_source_of_truth row, _act_override=False -> _ta_excl_upgrade=False ->
_row uses activation+port+byod+upgrade (unchanged) and _apply_ad never runs. Override + Upgrade-exclusion light
up ONLY for an org that enables the row.
