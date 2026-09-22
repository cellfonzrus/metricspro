// ═══════════════════════════════════════════════════════════════════════════════════════════════
// POS WIZARD — "Plans from your carrier data", the PURE part: what the card's editable words are seeded
// from and the exact body the save sends. No React, no fetch — `frontend/prove_plan_sources_card.mjs`
// transpiles this file with the project's own TypeScript and drives it over the backend resolver's real
// preview payload.
//
// THE RULES (owner 2026-09-22 — "we have enough plans in the system to bring over but it does not give
// an option to bring over"; the same shape as the 2.5a activation words, index §30.12):
//   · the sources come from the payload (`preview.sources` — the backend registry resolved over the org's
//     rules). This file lists NO table and NO source key; a new source is a registry entry, not a deploy.
//   · the editable words are seeded from `source.suggest.proposal` ONLY (the engine's answer over this
//     tenant's own vocabulary: the words in force minus any refused as too broad, plus the hint hits).
//     The hint lists never reach the card — the payload does not carry them.
//   · the save body carries, per source the person touched, `enabled` and the words parsed, plus
//     `broad_ok` = the too-broad words the person attested by name ('<source>:<word>').
// ═══════════════════════════════════════════════════════════════════════════════════════════════
export type Candidate = { field: string; token: string; lines: number; ratio: number; samples: string[] }
export type ExcludeCandidate = { token: string; removes: number; samples: string[] }
export type Refused = { source: string; word: string; lines: number; ratio: number; scanned: number; label?: string }
export type Suggest = {
  scanned: number; distinct: Record<string, number>; include: Candidate[]; too_broad: Candidate[]
  exclude: ExcludeCandidate[]; proposal: { include: string[]; exclude: string[] }
  preview: { lines: number; plans: number }; current: { lines: number; plans: number }
}
export type PlanSource = {
  key: string; kind: 'catalogue' | 'subscribers' | 'lines'; table: string; label: string; where?: string
  enabled: boolean; declared?: boolean; rows: number | null; plans: number | null; error?: string | null
  period?: string | null; truncated?: boolean
  name_field?: string; fields?: string[]; include?: string[]; exclude?: string[]; matched?: number | null
  refused?: Refused[]; refusal_note?: string | null; suggest?: Suggest | null
}
export type PlanPreview = {
  count: number; sample: any[]; sources?: PlanSource[]; configurable?: boolean; mrc_next?: string
  config?: { home: string; key: string; source: 'house' | 'tenant'; broad_attested?: string[] }
  empty_reason?: string; empty_next?: string
}

export const csv = (a: string[] | undefined) => (a || []).join(', ')
export const parseWords = (s: string) => {
  const out: string[] = []
  for (const w of s.split(',').map(x => x.trim().toLowerCase())) if (w && !out.includes(w)) out.push(w)
  return out
}
export const attestKey = (source: string, word: string) => `${source}:${word.trim().toLowerCase()}`

export type SourceEdit = { enabled: boolean; include: string; exclude: string }
export type Seed = { edits: Record<string, SourceEdit>; source: 'proposal' }

/** The editable state of the card, from the preview: every source's on/off as resolved, and for a
 *  line-level source the PROPOSAL's words (never a hint list, never anything typed in this file). */
export function seedFromPreview(preview: PlanPreview): Seed {
  const edits: Record<string, SourceEdit> = {}
  for (const s of preview.sources || []) {
    const prop = s.kind === 'lines' ? (s.suggest?.proposal || { include: [], exclude: [] }) : { include: [], exclude: [] }
    edits[s.key] = { enabled: !!s.enabled, include: csv(prop.include), exclude: csv(prop.exclude) }
  }
  return { edits, source: 'proposal' }
}

/** What the card renders next to a candidate plan: its charge when the source reports one, else the
 *  pointer to where it gets priced; its provenance when it came from a line-level source. */
export function planLine(p: any, mrcNext: string | undefined, labelOf: Record<string, string>): string {
  const name = String(p?.plan_name || '')
  const fee = p?.monthly_fee != null ? ` — $${p.monthly_fee}/mo` : (mrcNext ? ` — no charge on file, ${mrcNext}` : '')
  // provenance: the source's label from the payload (never a key or a name typed here)
  const label = labelOf[p?.source] || p?.source_label || ''
  const from = p?.lines ? ` (${p.lines.toLocaleString()} line(s) in ${label || p?.source})`
    : p?.subscribers ? ` (${p.subscribers.toLocaleString()} subscribers${label ? `, ${label}` : ''})`
      : label ? ` (${label})` : ''
  return `${name}${fee}${from}`
}

/** True when a source's edit differs from what the card last SEEDED (`base` = seedFromPreview(preview).edits)
 *  — only those are sent. Measured against the seed, not against the rules in force: a fresh proposal
 *  differs from the (empty) rules in force by design, and that is not something the person changed. */
export function changedKeys(preview: PlanPreview, edits: Record<string, SourceEdit>, base: Record<string, SourceEdit>): string[] {
  const out: string[] = []
  for (const s of preview.sources || []) {
    const e = edits[s.key], b = base[s.key]
    if (!e || !b) continue
    if (e.enabled !== b.enabled) { out.push(s.key); continue }
    if (s.kind === 'lines' && (csv(parseWords(e.include)) !== csv(parseWords(b.include)) || csv(parseWords(e.exclude)) !== csv(parseWords(b.exclude)))) out.push(s.key)
  }
  return out
}

/** The exact PUT body: per changed source, `enabled` and (line-level) the words parsed; `broad_ok` the
 *  attested keys. A source the person did not touch is NOT in the body (the save merges per source). */
export function buildConfigBody(preview: PlanPreview, edits: Record<string, SourceEdit>, base: Record<string, SourceEdit>, attested: string[]) {
  const sources: Record<string, { enabled: boolean; include?: string[]; exclude?: string[] }> = {}
  const kinds: Record<string, string> = {}
  for (const s of preview.sources || []) kinds[s.key] = s.kind
  for (const key of changedKeys(preview, edits, base)) {
    const e = edits[key]
    sources[key] = kinds[key] === 'lines'
      ? { enabled: e.enabled, include: parseWords(e.include), exclude: parseWords(e.exclude) }
      : { enabled: e.enabled }
  }
  const broad_ok = attested.filter(k => k.includes(':'))
  return broad_ok.length ? { sources, broad_ok } : { sources }
}

/** The refusal the save returns (400 with a structured detail), as the card renders it. */
export function refusalOf(err: any): { message: string; keys: string[]; refused: Refused[] } | null {
  const d = err?.detail ?? err
  if (!d || typeof d !== 'object' || !Array.isArray(d.attest_keys)) return null
  return { message: String(d.message || ''), keys: d.attest_keys.map(String), refused: Array.isArray(d.refused) ? d.refused : [] }
}

/** The count "Bring over N" should promise after a save: the resolver's count (never computed here). */
export const bringOverCount = (preview: PlanPreview | null) => preview?.count ?? 0
