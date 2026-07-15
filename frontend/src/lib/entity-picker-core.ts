// entity-picker-core — pure, framework-free logic for <EntityPicker> (RULE THREE: pick, don't type).
//
// Everything here is deterministic and side-effect-free so it is UNIT-PROVABLE without a DOM/React.
// The proof harness `frontend/scratchpad/prove_entity_picker.mjs` re-implements these bodies verbatim
// and greps THIS file for source-parity (same convention as prove_org_append.mjs). Keep the two in sync.
//
// Contract (AGENT_CONTRACT §3b):
//   • options are {id, label, sublabel?} — the picker stores/emits the ID, never the display string.
//   • typing filters (case- AND whitespace-insensitive, contains-match).
//   • PEOPLE: label = "First Last"; when two options share a label, the sublabel (email) is appended
//     to BOTH automatically (same-name disambiguation).
//   • create affordance appears ONLY when creation is allowed AND the typed value matches no existing
//     option — an unmatched value is NEVER silently emitted.

export type EntityOption = { id: string; label: string; sublabel?: string }

export type MenuRow =
  | { kind: 'option'; id: string; display: string; option: EntityOption }
  | { kind: 'suggest'; id: string; display: string; option: EntityOption }
  | { kind: 'create'; value: string }
  | { kind: 'empty'; message: string }

// case- AND whitespace-insensitive key: trim ends, lowercase, collapse internal whitespace runs.
export function normalizeText(s: unknown): string {
  return (s ?? '').toString().trim().toLowerCase().replace(/\s+/g, ' ')
}

// contains-match on the normalized forms. Empty query matches everything (show the full list).
export function matchesQuery(haystack: unknown, query: string): boolean {
  const q = normalizeText(query)
  if (!q) return true
  return normalizeText(haystack).includes(q)
}

// Same-name disambiguation. Any label shared by >1 option gets its sublabel appended to EVERY such
// option. Computed over the FULL option set (not the filtered view) so a name that is ambiguous in the
// dataset always shows its email, even when filtering currently reveals only one of the collisions.
// Returns id -> display string.
export function computeDisplays(options: EntityOption[]): Record<string, string> {
  const counts: Record<string, number> = {}
  for (const o of options) {
    const k = normalizeText(o.label)
    counts[k] = (counts[k] || 0) + 1
  }
  const out: Record<string, string> = {}
  for (const o of options) {
    const k = normalizeText(o.label)
    out[o.id] = counts[k] > 1 && o.sublabel ? `${o.label} — ${o.sublabel}` : o.label
  }
  return out
}

// exact match = the typed value equals an existing option's LABEL (normalized). Uses label, not the
// disambiguated display, so "John Smith" still counts as matching an existing John Smith.
export function hasExactMatch(options: EntityOption[], query: string): boolean {
  const q = normalizeText(query)
  if (!q) return false
  return options.some(o => normalizeText(o.label) === q)
}

// The create affordance is shown ONLY when: creation is allowed, the box is non-empty, and the typed
// value is not already an existing option. This is the guard that stops an unmatched string from being
// silently stored (the "Illinois" != "IL" corruption class).
export function shouldShowCreate(options: EntityOption[], query: string, allowCreate: boolean): boolean {
  return !!allowCreate && normalizeText(query) !== '' && !hasExactMatch(options, query)
}

// ── "closest suggestions" (only used when nothing matches AND create is off) ─────────────────────────
// Sørensen–Dice similarity on character bigrams (0..1). Cheap, deterministic, no deps.
function bigrams(s: string): string[] {
  const b: string[] = []
  for (let i = 0; i < s.length - 1; i++) b.push(s.slice(i, i + 2))
  return b
}
export function similarity(a: string, b: string): number {
  const na = normalizeText(a), nb = normalizeText(b)
  if (na === nb) return 1
  const ba = bigrams(na), bb = bigrams(nb)
  if (ba.length === 0 || bb.length === 0) return 0
  const bag: Record<string, number> = {}
  for (const g of ba) bag[g] = (bag[g] || 0) + 1
  let inter = 0
  for (const g of bb) if (bag[g] > 0) { inter++; bag[g]-- }
  return (2 * inter) / (ba.length + bb.length)
}

export function closest(options: EntityOption[], query: string, limit = 5): EntityOption[] {
  const q = normalizeText(query)
  if (!q) return []
  return options
    .map(o => ({ o, s: similarity(o.label, query) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || normalizeText(a.o.label).localeCompare(normalizeText(b.o.label)))
    .slice(0, limit)
    .map(x => x.o)
}

// The full render model: deterministic function of (options, query, allowCreate). The component just
// maps these rows to DOM + keyboard. Filtering matches label OR sublabel (so a person is findable by
// email); the create/exact-match guard is on LABEL only.
export function buildMenu(options: EntityOption[], query: string, allowCreate: boolean): MenuRow[] {
  const displays = computeDisplays(options)
  const filtered = options.filter(o => matchesQuery(o.label, query) || (o.sublabel ? matchesQuery(o.sublabel, query) : false))
  const rows: MenuRow[] = []
  for (const o of filtered) rows.push({ kind: 'option', id: o.id, display: displays[o.id], option: o })
  const showCreate = shouldShowCreate(options, query, allowCreate)
  if (filtered.length === 0 && !showCreate) {
    rows.push({ kind: 'empty', message: normalizeText(query) ? 'No match' : 'No options' })
    for (const o of closest(options, query)) rows.push({ kind: 'suggest', id: o.id, display: displays[o.id], option: o })
  }
  if (showCreate) rows.push({ kind: 'create', value: query.trim() })
  return rows
}

// Resolve a selected row into the emitted result. Proves the "emit ID, not label" contract: an option
// row yields the canonical id; the create row yields {create:true, value}. 'empty' is not selectable.
export type PickResult =
  | { create: true; value: string }
  | { create: false; id: string; option: EntityOption }
export function resolveRow(row: MenuRow): PickResult | null {
  if (row.kind === 'create') return { create: true, value: row.value }
  if (row.kind === 'option' || row.kind === 'suggest') return { create: false, id: row.id, option: row.option }
  return null
}

// Rows the keyboard can land on (everything except the non-interactive 'empty' header).
export function selectableRows(rows: MenuRow[]): MenuRow[] {
  return rows.filter(r => r.kind !== 'empty')
}

// ── MULTI-select helpers (the `multi` prop) ──────────────────────────────────────────────────────────
// Same id/label/sublabel + allowCreate contract as single-select; the picker emits string[] instead of a
// single id. These stay pure/framework-free so the proof harness can drive them without a DOM.

// Options the menu may still offer = all options MINUS the ones already chosen (no dup-picks).
export function excludeSelected(options: EntityOption[], selectedIds: string[]): EntityOption[] {
  const chosen = new Set(selectedIds)
  return options.filter(o => !chosen.has(o.id))
}

// Multi-select menu model. Identical shape to buildMenu, but (a) the dropdown EXCLUDES already-selected
// options, (b) disambiguation displays are computed over the FULL option set (so chips + menu agree and a
// name stays disambiguated even after its twin is picked), and (c) the create-affordance guard checks the
// FULL set — so an exact match of an ALREADY-selected value never re-offers "create" (no accidental dup).
export function buildMenuMulti(
  options: EntityOption[], query: string, allowCreate: boolean, selectedIds: string[],
): MenuRow[] {
  const remaining = excludeSelected(options, selectedIds)
  const displays = computeDisplays(options)
  const filtered = remaining.filter(o => matchesQuery(o.label, query) || (o.sublabel ? matchesQuery(o.sublabel, query) : false))
  const rows: MenuRow[] = []
  for (const o of filtered) rows.push({ kind: 'option', id: o.id, display: displays[o.id], option: o })
  const showCreate = shouldShowCreate(options, query, allowCreate)
  if (filtered.length === 0 && !showCreate) {
    rows.push({ kind: 'empty', message: normalizeText(query) ? 'No match' : 'No options' })
    for (const o of closest(remaining, query)) rows.push({ kind: 'suggest', id: o.id, display: displays[o.id], option: o })
  }
  if (showCreate) rows.push({ kind: 'create', value: query.trim() })
  return rows
}

// Add an id to the selection (idempotent — never a duplicate, preserves order).
export function addSelection(selectedIds: string[], id: string): string[] {
  return selectedIds.includes(id) ? selectedIds : [...selectedIds, id]
}

// Remove an id from the selection (order-preserving).
export function removeSelection(selectedIds: string[], id: string): string[] {
  return selectedIds.filter(x => x !== id)
}

// The removable chips: one per selected id, in selection order, with the disambiguated display. An id
// that is NOT in the current option set (a preserved off-roster value) keeps the raw id as its display so
// it stays visible/removable rather than silently vanishing — the multi-select twin of single-select's
// "inject the current value as a synthetic option."
export type Chip = { id: string; display: string }
export function selectedChips(options: EntityOption[], selectedIds: string[]): Chip[] {
  const displays = computeDisplays(options)
  return selectedIds.map(id => ({ id, display: displays[id] ?? id }))
}

// ── US states — the canonical first adopter of RULE THREE (state fields). ────────────────────────────
// {id: <USPS code>, label: "<Name> (<code>)"} so filtering matches on BOTH the name AND the code:
// typing "illinois", "IL", or "il" all land on Illinois; the stored/emitted value is always "IL".
export const US_STATES: EntityOption[] = [
  { id: 'AL', label: 'Alabama (AL)' }, { id: 'AK', label: 'Alaska (AK)' },
  { id: 'AZ', label: 'Arizona (AZ)' }, { id: 'AR', label: 'Arkansas (AR)' },
  { id: 'CA', label: 'California (CA)' }, { id: 'CO', label: 'Colorado (CO)' },
  { id: 'CT', label: 'Connecticut (CT)' }, { id: 'DE', label: 'Delaware (DE)' },
  { id: 'DC', label: 'District of Columbia (DC)' }, { id: 'FL', label: 'Florida (FL)' },
  { id: 'GA', label: 'Georgia (GA)' }, { id: 'HI', label: 'Hawaii (HI)' },
  { id: 'ID', label: 'Idaho (ID)' }, { id: 'IL', label: 'Illinois (IL)' },
  { id: 'IN', label: 'Indiana (IN)' }, { id: 'IA', label: 'Iowa (IA)' },
  { id: 'KS', label: 'Kansas (KS)' }, { id: 'KY', label: 'Kentucky (KY)' },
  { id: 'LA', label: 'Louisiana (LA)' }, { id: 'ME', label: 'Maine (ME)' },
  { id: 'MD', label: 'Maryland (MD)' }, { id: 'MA', label: 'Massachusetts (MA)' },
  { id: 'MI', label: 'Michigan (MI)' }, { id: 'MN', label: 'Minnesota (MN)' },
  { id: 'MS', label: 'Mississippi (MS)' }, { id: 'MO', label: 'Missouri (MO)' },
  { id: 'MT', label: 'Montana (MT)' }, { id: 'NE', label: 'Nebraska (NE)' },
  { id: 'NV', label: 'Nevada (NV)' }, { id: 'NH', label: 'New Hampshire (NH)' },
  { id: 'NJ', label: 'New Jersey (NJ)' }, { id: 'NM', label: 'New Mexico (NM)' },
  { id: 'NY', label: 'New York (NY)' }, { id: 'NC', label: 'North Carolina (NC)' },
  { id: 'ND', label: 'North Dakota (ND)' }, { id: 'OH', label: 'Ohio (OH)' },
  { id: 'OK', label: 'Oklahoma (OK)' }, { id: 'OR', label: 'Oregon (OR)' },
  { id: 'PA', label: 'Pennsylvania (PA)' }, { id: 'RI', label: 'Rhode Island (RI)' },
  { id: 'SC', label: 'South Carolina (SC)' }, { id: 'SD', label: 'South Dakota (SD)' },
  { id: 'TN', label: 'Tennessee (TN)' }, { id: 'TX', label: 'Texas (TX)' },
  { id: 'UT', label: 'Utah (UT)' }, { id: 'VT', label: 'Vermont (VT)' },
  { id: 'VA', label: 'Virginia (VA)' }, { id: 'WA', label: 'Washington (WA)' },
  { id: 'WV', label: 'West Virginia (WV)' }, { id: 'WI', label: 'Wisconsin (WI)' },
  { id: 'WY', label: 'Wyoming (WY)' },
  // territories (cellular retail reaches these)
  { id: 'PR', label: 'Puerto Rico (PR)' }, { id: 'GU', label: 'Guam (GU)' },
  { id: 'VI', label: 'U.S. Virgin Islands (VI)' }, { id: 'AS', label: 'American Samoa (AS)' },
  { id: 'MP', label: 'Northern Mariana Islands (MP)' },
]
