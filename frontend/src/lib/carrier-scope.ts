// Carrier-scoping compliance — DISPLAY-ONLY helpers (frontend lens).
//
// The house org holds BOTH Boost and Total. No screen may reveal that. These helpers are the
// display-side of the active-carrier lens: they select the active carrier's slice and neutralize any
// wording that would name the other carrier or a carrier-revealing vendor brand. NONE of this changes
// money math — the backend keeps computing everything and returning both carriers' data; the frontend
// simply shows one carrier at a time. Every helper is PURE so the proof harness can exercise it.

// Human display name for an active carrier code (for the switcher + copy).
export function carrierDisplayName(code: string | undefined): string {
  const c = (code || '').toLowerCase().trim()
  if (c === 'boost') return 'Boost'
  if (c === 'total') return 'Total'
  if (c === 'cricket') return 'Cricket'
  if (!c) return 'Carrier'
  return c.charAt(0).toUpperCase() + c.slice(1).replace(/-/g, ' ')
}

// Which carrier (if any) a piece of free text names. Used to hide a carrier-named preset/template
// (e.g. the seeded "Total Wireless default" management-incentive plan) that isn't the active carrier.
export function textCarrier(text: string | undefined): 'boost' | 'total' | 'cricket' | null {
  const s = (text || '').toLowerCase()
  if (/\bboost\b/.test(s)) return 'boost'
  if (/total\s*wireless|\btotal\b|vidapay/.test(s)) return 'total'
  if (/\bcricket\b/.test(s)) return 'cricket'
  return null
}

// A carrier-named preset is visible when: the tenant is single-carrier (unchanged), OR the preset
// names no carrier, OR it names the active carrier. Hides the other carrier's presets in a dual tenant.
export function presetVisibleForCarrier(name: string | undefined, activeCarrier: string, multi: boolean): boolean {
  if (!multi) return true
  const c = textCarrier(name)
  return !c || c === (activeCarrier || '').toLowerCase().trim()
}

// ATU commission carry for the active carrier — the backend returns BOTH boost_carry_monthly and
// total_carry_monthly plus the combined carry_monthly; this picks ONE carrier's carry and NEVER the
// combined, so the ATU page can never sum the two carriers into one figure.
export function atuActiveCarry(
  money: { boost_carry_monthly?: number; total_carry_monthly?: number; carry_monthly?: number } | undefined,
  activeCarrier: string,
): number {
  if (!money) return 0
  return (activeCarrier || '').toLowerCase().trim() === 'total'
    ? (money.total_carry_monthly || 0)
    : (money.boost_carry_monthly || 0)
}

// ── Financing vendor relabelling (owner: "keep per-vendor breakdown but NEUTRAL names") ────────────
// The seeds ship carrier-revealing brands (ACIMA on Boost, Edge/TW on Total). The report keeps the
// per-vendor breakdown but must NEVER print ACIMA / TW / Edge. This maps known vendor keys to neutral
// display labels and scrubs any custom label that still names a brand/carrier.
const NEUTRAL_VENDOR_LABELS: Record<string, string> = {
  acima: 'Lease-to-own',
  edge: 'Carrier financing',
  tw: 'Carrier financing',
  't-w': 'Carrier financing',
}
// Words that reveal the real vendor brand or a carrier — a label containing any is neutralized.
const VENDOR_LEAK_WORDS = /\b(acima|edge|t-?w|total\s*wireless|total|boost|cricket|vidapay)\b/i

// Neutral display label for a financing vendor. Known carrier-revealing keys map to a generic label;
// a custom label that names a brand/carrier collapses to "Financing"; anything else passes through.
export function financingVendorLabel(vendorKey: string | undefined, rawLabel?: string): string {
  const key = (vendorKey || '').toLowerCase().trim()
  if (NEUTRAL_VENDOR_LABELS[key]) return NEUTRAL_VENDOR_LABELS[key]
  const raw = (rawLabel || '').trim()
  if (!raw) return 'Financing'
  if (VENDOR_LEAK_WORDS.test(raw)) return 'Financing'
  return raw
}

// Does a financing vendor serve the active carrier? A vendor with no carrier assignment is carrier-
// neutral ("any carrier") and always matches. Carrier rows carry a name/id like "Total"/"Boost".
export function vendorServesCarrier(
  carriers: { carrier_name?: string | null; carrier_id?: string | null }[] | undefined,
  activeCarrier: string,
): boolean {
  const cs = carriers || []
  if (cs.length === 0) return true
  const a = (activeCarrier || '').toLowerCase().trim()
  if (!a) return true
  return cs.some(c => {
    const t = ((c.carrier_name || c.carrier_id || '') as string).toLowerCase()
    if (!t) return true
    return t.includes(a) || a.includes(t)
  })
}

// Does a carrier lookup row (id/name/code) belong to the active carrier? Used to filter payout-schedule
// and MRC lists by the active carrier's carrier_id. A null carrier_id ("Any carrier") is neutral.
export function carrierRowIds(
  carriers: { id?: string; name?: string; code?: string }[] | undefined,
  activeCarrier: string,
): Set<string> {
  const a = (activeCarrier || '').toLowerCase().trim()
  const out = new Set<string>()
  for (const c of carriers || []) {
    const t = ((c.code || c.name || '') as string).toLowerCase()
    if (a && t && (t.includes(a) || a.includes(t)) && c.id) out.add(c.id)
  }
  return out
}
