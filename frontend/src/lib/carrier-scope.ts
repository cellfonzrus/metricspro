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

// ── WHICH POS DOES THIS TENANT RUN (owner bug report 2026-09-13) ───────────────────────────────────
// Owner: "since we declared that the pos is not b2b anymore it is rq that shoud not give the option
// for b2b any more … since the pos was never updated at the ground level it did not propogate to the
// other attched modules."
//
// The tenant's POS is ALREADY a single piece of config: the `pos_system` vocabulary term (mig 953
// house presets + a tenant override, resolved by report_labels.py → useReportLabels().term). Nothing
// new is stored here and no second POS record is introduced — these two helpers just let a surface
// ASK that one answer, which is exactly what no surface was doing.
//
// The comparison must be tolerant because the value is operator-editable free text: the same POS is
// spelled 'b2bsoft', 'B2B Soft' and 'B2BSoft' across the house presets and tenant overrides, and a
// gate that treated those as three different systems would hide a tenant's own imports.

/** Case- and punctuation-insensitive key for a POS system name ('B2B Soft' → 'b2bsoft'). */
export function posSquash(name: string | undefined | null): string {
  return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '')
}

/**
 * Is a POS-SPECIFIC surface shown to a tenant running `currentPos`?
 *
 * Deliberately the same shape as `implementation_spine.carrier_visible`, one axis over, so the two
 * gates can never drift into disagreeing about what a tenant sees:
 *   · a surface with NO pos tag is POS-agnostic and always shows;
 *   · a tenant whose `pos_system` is unresolved hides NOTHING — a lookup that fails must never
 *     withhold an upload somebody needs;
 *   · otherwise it shows only to the POS it belongs to.
 */
export function posVisible(tilePos: string | undefined | null, currentPos: string | undefined | null): boolean {
  const want = posSquash(tilePos)
  if (!want) return true
  const have = posSquash(currentPos)
  if (!have) return true
  return want === have
}

// ── THE OVERRIDE (owner directive 2026-09-13) ─────────────────────────────────────────────────────
// Owner: "if they need them then the super admin should have a full role permission exclusiveluy for
// super admin to assign to the new or existing tenants which have been gated out due to carrier or
// pos settings."
//
// A gate with no way out is a support ticket waiting to happen, and this one is new, so it gets the
// escape hatch the carrier gate has always had — THE SAME ONE. `caps['carrier:<href>']` (rbac.carrierOK)
// is per-tenant `ui_label_override` scope 'cap', written by POST /commcalc/nav-labels and edited at
// /admin/labels. `pos:<surface>` is ONE MORE KEY NAMESPACE in that existing mechanism: no second
// store, no second endpoint, no second admin screen.
//
// WHO MAY WIDEN is enforced SERVER-SIDE, in that endpoint, not here: hiding a surface or resetting it
// to follow the tenant's own settings stays open to any menu-layout admin, while turning a gated-out
// surface back ON is refused for anyone but a platform super-admin. The asymmetry is the safety
// property — the override can never take away something a tenant already had, only decline to hand
// out something new.

/** The POS-gated surfaces that can be re-granted, for the admin screen to list. Same posture as
 *  rbac.NAV_CARRIERS: a small registry, so a surface cannot be gated without being overridable. */
export const POS_GATED_SURFACES: { key: string; label: string; why: string }[] = [
  // Since 2026-09-20 the Upload page's email-report tiles are registry rows gated per kind
  // (`kind:<key>` caps, reportKindsVisible below); this surface-level key stays as the documented
  // legacy namespace so an existing 'pos:' override row still resolves through posOK unchanged.
  { key: 'upload_email_reports', label: 'Email-report upload tiles (Upload page) — legacy surface key',
    why: 'Superseded by the per-kind overrides in the report-kind registry (Admin → Labels → report kinds).' },
]

/**
 * Is a POS-gated surface shown — override first, gate second?
 *
 * Mirrors `rbac.carrierOK` clause for clause, deliberately: two gates whose override ladders differed
 * would be two things for an administrator to learn, and one of them would be learned wrong.
 */
export function posOK(
  surfaceKey: string,
  tilePos: string | undefined | null,
  currentPos: string | undefined | null,
  caps: Record<string, boolean | null> | undefined,
): boolean {
  const ov = (caps || {})['pos:' + surfaceKey]
  if (ov === true) return true
  if (ov === false) return false
  return posVisible(tilePos, currentPos)
}

// ── REPORT KINDS — the visibility family, extended (owner directives 2026-09-20; design §7) ──────
// Owner: "it is very important that we don't have extra file upload paths for a new tenant who does
// not need those based on the carrier they pick … no patchwork, it should work as a design."
//
// THE ONE VISIBILITY FUNCTION for report kinds. `reportKindsVisible` is the clause-for-clause twin
// of backend `report_kinds.visible_kinds` (both harnesses run the same cases): a registry row shows
// iff it is active AND its POS applies-to intersects the declared POS (or is any) AND its carrier
// applies-to intersects the declared carriers (or is any) — with the super-admin `cap` override
// (`kind:<key>` show|hide, the SAME ui_label_override mechanism as carrier:/pos:) applied first and
// RECORDED as provenance. Every upload surface renders from this through lib/report-kinds.ts; no
// page keeps a list of kinds, patterns, tables or presets of its own (locked by
// backend/harness_report_kind_lock.py). Nothing here names a POS or a carrier: codes are data.

export type ReportKindRow = {
  key: string; label: string; what_in_it?: string | null; recognisable_columns?: string[]; source_hint?: string | null
  applies_to_pos?: string[]; applies_to_carrier?: string[]; defined_by?: 'house' | 'tenant'; defined_by_org?: string | null
  statement_type?: string | null; landing: string; layout?: string | null   // a registry token ('commission' | 'residual' | one a row adds)
  signature_fields?: string[]; upload_types?: string[]; custom_sheet_label?: string | null
  sort_order?: number; is_active?: boolean
  provenance?: string; provenance_text?: string; confirmations?: number
}
export type ReportDeclaration = { pos: string[]; pos_source?: string; carriers: string[]; carrier_source?: string; reasons?: string[] }

export const KIND_CAP_PREFIX = 'kind:'
export const KIND_PROVENANCE = {
  house: 'house default', override: 'your override', widened: 'widened by super-admin',
  tenant: 'defined by a tenant on this carrier',
} as const

/** The rows a kind's applies-to and a declaration intersect on (empty applies-to = any). Same squash as posSquash. */
export function kindApplies(row: Pick<ReportKindRow, 'applies_to_pos' | 'applies_to_carrier'>, decl: ReportDeclaration | null | undefined): boolean {
  const pos = new Set((decl?.pos || []).map(posSquash))
  const car = new Set((decl?.carriers || []).map(posSquash))
  const ap = (row.applies_to_pos || []).map(posSquash).filter(Boolean)
  const ac = (row.applies_to_carrier || []).map(posSquash).filter(Boolean)
  const posOk = ap.length === 0 || ap.some(c => pos.has(c))
  const carOk = ac.length === 0 || ac.some(c => car.has(c))
  return posOk && carOk
}

/** The `kind:<key>` cap override: true = show, false = hide, null = auto (follow the declaration). */
export function kindCapOverride(caps: Record<string, boolean | null> | undefined, key: string): boolean | null {
  const v = (caps || {})[KIND_CAP_PREFIX + key]
  return v === true ? true : v === false ? false : null
}

/**
 * THE rule (design §7): registry rows visible to a tenant, each with its provenance. Inactive rows
 * never show; a hide override wins; a show override widens and is recorded; else the declaration
 * decides. An UNKNOWN declaration (no POS / no carrier declared) shows LESS, never more — the
 * POS-specific and carrier-specific rows stay hidden and the payload's `reasons` say why.
 */
export function reportKindsVisible(
  rows: ReportKindRow[] | null | undefined,
  decl: ReportDeclaration | null | undefined,
  caps?: Record<string, boolean | null>,
): ReportKindRow[] {
  const out: ReportKindRow[] = []
  for (const r of rows || []) {
    if (r.is_active === false) continue
    const ov = kindCapOverride(caps, r.key)
    if (ov === false) continue
    const byRule = kindApplies(r, decl)
    if (!byRule && ov !== true) continue
    const prov = ov === true && !byRule ? KIND_PROVENANCE.widened
      : r.provenance || (r.defined_by === 'tenant' ? KIND_PROVENANCE.tenant : KIND_PROVENANCE.house)
    out.push({ ...r, provenance: prov, provenance_text: r.provenance_text || prov })
  }
  return out.sort((a, b) => (a.sort_order ?? 100) - (b.sort_order ?? 100) || a.key.localeCompare(b.key))
}
