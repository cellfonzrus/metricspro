// ═══════════════════════════════════════════════════════════════════════════════════════════════
// TENANT ONBOARDING — step 2.5a, the PURE part: what the editable words are seeded from, and the
// exact body the save sends. No React, no fetch — `frontend/prove_line_class_step.mjs` transpiles this
// file with the project's own TypeScript and drives it over the backend engine's real block.
//
// THE RULES (owner 2026-09-21, the second class — "the effective rule is not what the person confirmed"):
//   · the editable words are seeded from `suggest.proposal` ONLY. The proposal is the engine's answer
//     over THIS file's own words (the person's declared words minus any refused as too broad, plus the
//     hint hits); the hint lists never reach this component — the block does not even carry them.
//   · the save body carries `metric_rules.<bucket>` for every ticked proposal, the words parsed per
//     class, and `broad_ok` = the too-broad words the person attested by name ('<class>:<word>').
// ═══════════════════════════════════════════════════════════════════════════════════════════════
export type ClassKey = 'activation' | 'upgrade' | 'byod' | 'port' | 'hardware_only'
export type Counts = { scanned: number; skipped: number; unclassified_lines: number; activation_type_lines: number; activation_type_transactions: number
  fields: string[]; source: string; classes: Record<string, { lines: number; transactions: number; label: string }>
  broad?: Refused[]; refused?: Refused[] }
export type Candidate = { field: string; token: string; lines: number; ratio: number; samples: string[] }
export type Refused = { class: string; token: string; lines: number; ratio: number; scanned?: number; attested?: boolean }
export type MetricBucket = { bucket: string; matched: number; source: string; applicable: boolean; rules: Record<string, string[]>
  candidates: Candidate[]; proposal: Record<string, string[]> | null; preview: number | null }
export type LineClassBlock = {
  step: string; classes: { key: ClassKey; label: string }[]; candidate_fields: string[]
  rules: { fields: string[]; tokens: Record<ClassKey, string[]>; exact: Record<string, string>; source: string; declared: Record<string, boolean>; house_fill?: boolean }
  current: Counts; gate_open: boolean; gate_note: string | null
  refused?: Refused[]; rules_ok?: boolean; refusal_note?: string | null
  suggest: { scanned: number; distinct: Record<string, number>; per_class: Record<ClassKey, Candidate[]>
    too_broad: (Candidate & { class: string })[]; proposal: { fields: string[]; tokens: Record<ClassKey, string[]> }; preview: Counts }
  metrics: { scanned: number; buckets: Record<string, MetricBucket>; error?: string }
  error?: string
}

export const csv = (a: string[] | undefined) => (a || []).join(', ')
export const parseWords = (s: string) => s.split(',').map(x => x.trim().toLowerCase()).filter(Boolean)

export type Seed = { fields: string[]; tokens: Record<string, string>; useMetric: Record<string, boolean>; source: 'proposal' }

/** The editable state of the step, from the block: the PROPOSAL's fields and words (never the hint
 *  lists, never the raw rules in force), every metric proposal ticked by default. */
export function seedFromBlock(block: LineClassBlock): Seed {
  const src = block.suggest.proposal
  const tokens: Record<string, string> = {}
  for (const c of block.classes) tokens[c.key] = csv(src.tokens?.[c.key])
  const useMetric: Record<string, boolean> = {}
  for (const [b, m] of Object.entries(block.metrics?.buckets || {})) useMetric[b] = !!m.proposal
  return { fields: [...(src.fields || [])], tokens, useMetric, source: 'proposal' }
}

/** The attestation key the backend records ('<class>:<word>'). */
export const attestKey = (cls: string, token: string) => `${cls}:${token.toLowerCase()}`

export type PutBody = {
  instance_key: string; by: string
  fields: string[]; tokens: Record<string, string[]>
  metric_rules: Record<string, Record<string, string[]>> | null
  no_activations: string | null
  broad_ok: string[] | null
}

/** The PUT /onboarding/intake/line-class body — one place, so the proof can pin its shape. */
export function buildPutBody(args: {
  instanceKey: string; who: string; fields: string[]; tokens: Record<string, string>
  useMetric: Record<string, boolean>; buckets: Record<string, MetricBucket> | undefined
  noAct: string; broadOk: string[]
}): PutBody {
  const metric_rules: Record<string, Record<string, string[]>> = {}
  for (const [b, m] of Object.entries(args.buckets || {})) if (args.useMetric[b] && m.proposal) metric_rules[b] = m.proposal
  return {
    instance_key: args.instanceKey, by: args.who,
    fields: args.fields,
    tokens: Object.fromEntries(Object.entries(args.tokens).map(([k, v]) => [k, parseWords(v)])),
    metric_rules: Object.keys(metric_rules).length ? metric_rules : null,
    no_activations: args.noAct.trim() || null,
    broad_ok: args.broadOk.length ? args.broadOk : null,
  }
}

/** The refusal a save answered (400 with a structured detail) or the block carries for the rules in
 *  force — one shape for the step to render. */
export type Refusal = { message: string; refused: Refused[]; basis?: string; rows?: number }
export function refusalOf(detail: unknown, fallbackMessage: string): Refusal | null {
  const d = detail as { message?: string; refused?: Refused[]; basis?: string; rows?: number } | null
  if (!d || typeof d !== 'object' || !Array.isArray(d.refused) || !d.refused.length) return null
  return { message: d.message || fallbackMessage, refused: d.refused, basis: d.basis, rows: d.rows }
}
