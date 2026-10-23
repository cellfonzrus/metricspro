// WHY A DEVICE CARRIES NO COST, in words — the ONE home for that sentence on the frontend.
//
// `owed_source` is stamped on every ledger row by the backend (payables/engine.py, the labels in
// payables/declared_source.py). Two of its values look alike and mean opposite things, which is the
// defect this file exists for (§COMMCALC-28): `unconfigured` means nobody told the platform where
// this carrier's device costs come from, while `price_source_unreadable` means somebody did and the
// declaration is broken. Rendering the raw value made the second read as the first.
export const OWED_SOURCE_LABEL: Record<string, string> = {
  unconfigured: 'no cost source set up for this carrier',
  price_source_unreadable: 'the cost source set up for this carrier could not be read — needs fixing',
  unit_price_map: 'priced from the handset-ordering sheet',
  asset_ledger: "the carrier's own statement",
}

export function owedSourceLabel(v: unknown): string {
  const k = (v == null ? '' : String(v)).trim()
  if (!k) return 'not priced'
  return OWED_SOURCE_LABEL[k] ?? k
}
