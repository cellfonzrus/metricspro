// THE ONE WAY A PAGE LEARNS WHICH REPORT KINDS THIS TENANT MAY UPLOAD (design §7, 2026-09-20).
//
// `GET /commcalc/report-kinds` (backend report_kinds.payload) carries the registry rows already
// merged per key (house + this org's overrides), the tenant's DECLARATION (read by the one backend
// reader, report_kinds.tenant_declaration), the `kind:` cap overrides, the declared POS standard's
// filename rules and the provenance per row. This hook runs the ONE visibility function over it
// (`reportKindsVisible`, lib/carrier-scope.ts — the twin of the backend's `visible_kinds`) and hands
// every upload surface the same answers: the visible kinds, the kinds for a surface, the upload
// route keys those kinds own, the filename rules, and a label per upload type.
//
// Every surface that renders an upload choice imports THIS hook and nothing else decides what it
// offers — backend/harness_report_kind_lock.py fails the build when one stops. No page keeps a
// list of kinds, patterns, tables or presets, and nothing here names a POS or a carrier.
//
// HONEST BEFORE IT LOADS: `loaded` is false until the payload arrives and a surface renders NO
// upload choice until then; a failed fetch leaves `error` set and the surface says so — it never
// falls back to a list wider than the registry would give (design §7: show less and say why).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { reportKindsVisible, type ReportDeclaration, type ReportKindRow } from '@/lib/carrier-scope'

export type FilenameRule = { pattern: string; upload_type: string; note?: string | null }
/** One reader of a landing table — a ScreenLink screen key + the fields it needs (backend landing_identity.CONSUMERS). */
// `lines` / `source` / `active` (mig 1013): the ledger's P&L consumer carries the P&L LINES the org's buckets
// are linked to (derived from the bucket registry's pl_line_key by the backend — never listed here), which
// source books the P&L today and whether the ledger is that source. ShowsIn renders "P&L Statement → …".
export type PlLine = { key: string; label: string; buckets?: string[] }
export type Consumer = { screen: string; label: string; needs: string[]; gate: boolean; why?: string | null
  lines?: PlLine[]; source?: string | null; active?: boolean; source_label?: string | null }
export type ShowsIn = { table: string | null; consumers: Consumer[]; note: string | null }
export type ReportKindsPayload = {
  registry_ready: boolean; migration: string
  declaration: ReportDeclaration
  kinds: ReportKindRow[]
  hidden: { key: string; label: string; why: string }[]
  surfaces: Record<string, string[]>
  upload_types: string[]
  filename_rules: FilenameRule[]
  standard: { pos_key: string; label: string; source: string; note?: string } | null
  caps: Record<string, boolean | null>
  all_keys: { key: string; label: string; applies_to_pos: string[]; applies_to_carrier: string[] }[]
  /** the ONE consumers map, per landing table (landing_identity.CONSUMERS) — for a surface that knows a table, not a kind */
  consumers?: Record<string, Consumer[]>
}

/**
 * WHERE AN UPLOAD SHOWS UP (owner 2026-09-20: "it should be mentioned on the upload page where this
 * upload will be reflected, with a link"). The answer rides the registry row (`shows_in`, derived by
 * the backend from the row's landing table and the one consumers map); this selector finds the row
 * for a registry key OR the upload route key a tile is posted through. Null = nothing known yet.
 */
export function showsInFor(visible: ReportKindRow[], keyOrUploadType: string): ShowsIn | null {
  const row = visible.find(r => r.key === keyOrUploadType) || visible.find(r => (r.upload_types || []).includes(keyOrUploadType)) || null
  return row?.shows_in || null
}
/**
 * THE WAY BACK FROM A REPORT TO ITS UPLOADS — the inverse of `showsInFor` over the SAME rows: the visible
 * kinds whose `shows_in.consumers` name `screen` (a ScreenLink key such as 'exec_mtd'), each with the page
 * its upload belongs on (`where`, backend landing_identity.where_to_upload). Derived from the one consumers
 * map the backend attached to every row — no page keeps a list of feeds (2026-09-20, the commission
 * structure's "Option 1 pays from the Executive MTD; that report counts what is uploaded as …").
 */
export type FeedForScreen = { key: string; label: string; where: NonNullable<ReportKindRow['where']> | null }
export function feedsForScreen(visible: ReportKindRow[], screen: string): FeedForScreen[] {
  return visible
    .filter(r => (r.shows_in?.consumers || []).some(c => c.screen === screen))
    .map(r => ({ key: r.key, label: r.label, where: r.where || null }))
}
export type ReportSurface = 'intake' | 'upload' | 'wizard' | 'email_imports' | 'tiles'
export const INTAKE_LANDINGS = ['sales', 'pos', 'invoice', 'inventory', 'commission', 'x_report', 'merchant_payments', 'bill_payments', 'other']

// ── PURE selectors (proven by frontend/prove_report_kinds.mjs against the real transpiled TS) ────

/** What one surface renders, from the ONE visible set — mirrors backend report_kinds.for_surface. */
export function kindsForSurface(visible: ReportKindRow[], surface: ReportSurface): ReportKindRow[] {
  if (surface === 'intake') return visible.filter(r => INTAKE_LANDINGS.includes(r.landing))
  if (surface === 'wizard') return visible.filter(r => (r.upload_types || []).length > 0)
  return visible.filter(r => (r.upload_types || []).length > 0 || !!r.custom_sheet_label)
}

/** The legacy upload route keys / tile ids the visible kinds are uploaded through, in registry order. */
export function uploadTypesOf(visible: ReportKindRow[]): string[] {
  const out: string[] = []
  for (const r of visible) for (const u of r.upload_types || []) if (!out.includes(u)) out.push(u)
  return out
}

/** The registry's layman label for an upload route key, else null (the page's route metadata is the fallback). */
export function labelForUploadType(visible: ReportKindRow[], uploadType: string): string | null {
  return visible.find(r => (r.upload_types || []).includes(uploadType))?.label || null
}

/** A filename glob ('*Sales*Transaction*Details*') as a case-insensitive RegExp. */
export function globToRegex(glob: string): RegExp {
  const esc = (glob || '').split('*').map(s => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')
  return new RegExp('^' + esc + '$', 'i')
}

/**
 * Suggest a mailbox rule for an attachment name FROM THE REGISTRY'S RULES (the declared POS
 * standard, restricted to visible kinds) — never from a list in the page. A name no rule matches
 * gets a glob built from its distinctive tokens and NO upload type: the person picks one, the
 * page does not guess a default that may not even be offered to this tenant.
 */
export function suggestRuleFrom(name: string, rules: FilenameRule[]): { pattern: string; upload_type: string; matched: boolean } {
  for (const r of rules || []) {
    if (r.pattern && globToRegex(r.pattern).test(name || '')) return { pattern: r.pattern, upload_type: r.upload_type, matched: true }
  }
  const base = (name || '').replace(/\.[a-z0-9]+$/i, '')
  const toks = base.split(/[^a-zA-Z0-9]+/).filter(t => t.length > 2).slice(0, 3)
  return { pattern: toks.length ? '*' + toks.join('*') + '*' : '*' + base + '*', upload_type: '', matched: false }
}

/** A one-line explanation of what is withheld, for a surface to print instead of nothing. */
export function withheldSummary(p: ReportKindsPayload | null): string {
  if (!p) return ''
  const n = (p.hidden || []).length
  const why = (p.declaration?.reasons || []).join('; ')
  if (!n && !why) return ''
  const decl = [p.declaration?.pos?.length ? `POS ${p.declaration.pos.join('/')}` : 'no POS declared',
                p.declaration?.carriers?.length ? `carrier ${p.declaration.carriers.join('/')}` : 'no carrier declared'].join(' · ')
  return `${n} report kind${n === 1 ? '' : 's'} not offered — you declared ${decl}.${why ? ' ' + why : ''}`
}

// ── APPLIES? / LOADED? — the two-step every feed-dependent page asks (owner 2026-09-20) ─────────
// A page whose purpose depends on a feed kind the tenant does not have must SAY SO instead of inviting
// the wrong upload ("if we have declared there is no b2b in verizon why does this message show").
// Step 1 (here): is the feed's report kind visible for the declared POS / carrier? Step 2 (the page's
// own payload): has a file landed? The copy for step 1 is authored ONCE below; the POS name in it is
// the tenant's term (lib/report-labels.ts usePosTerm), never a vendor spelled in code.

/** 'checking' until the registry payload arrives; 'unknown' when it failed (never hide a page on a
 *  lookup failure); 'not_defined' when no visible kind matches; 'defined' when one does. */
export type FeedApplies = 'checking' | 'unknown' | 'not_defined' | 'defined'

/** The visible kind for a feed, by registry key OR by the upload route key it is uploaded through. */
export function feedKindFor(visible: ReportKindRow[], keyOrUploadType: string): ReportKindRow | null {
  return visible.find(r => r.key === keyOrUploadType) || visible.find(r => (r.upload_types || []).includes(keyOrUploadType)) || null
}

/** Step 1 as a state, from the hook's own loaded / error flags and the matched kind. */
export function feedApplies(loaded: boolean, error: string | null, kind: ReportKindRow | null): FeedApplies {
  if (!loaded) return 'checking'
  if (error) return 'unknown'
  return kind ? 'defined' : 'not_defined'
}

/**
 * The ONE "this page does not apply to your POS" sentence. `feedNoun` names the feed in the page's
 * words ('daily sales feed', 'Activation Details report'); `purpose` completes "there is nothing to …".
 */
export function notApplicableCopy(a: { pos: string; posDeclared: boolean; feedNoun: string; purpose: string; kindNoun?: string }): string {
  const kind = a.kindNoun || a.feedNoun
  if (a.posDeclared) {
    return `You declared ${a.pos} as your POS. No ${kind} report kind is defined for ${a.pos} yet, so there is ` +
      `nothing to ${a.purpose}. When ${a.pos} exports a ${a.feedNoun}, add it under Onboarding → Intake and this page will use it.`
  }
  return `No POS is declared for this tenant yet, and no ${kind} report kind is offered, so there is nothing to ` +
    `${a.purpose}. Declare your POS in the Implementation wizard, then add the ${a.feedNoun} under Onboarding → Intake ` +
    `and this page will use it.`
}

const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

/** Fetch-once hook. See the header: nothing is offered until it loads, and a failure says so. */
export function useReportKinds() {
  const [payload, setPayload] = useState<ReportKindsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const reload = useCallback(() => {
    api(`/api/v1/commcalc/report-kinds${orgQS()}`)
      .then((p: ReportKindsPayload) => { setPayload(p); setError(null) })
      .catch((e: unknown) => { setPayload(null); setError((e as Error)?.message || 'could not read the report-kind registry') })
      .finally(() => setLoaded(true))
  }, [])
  useEffect(() => { reload() }, [reload])
  // THE ONE VISIBILITY FUNCTION, run here and nowhere else in a page.
  const visible = useMemo(() => payload ? reportKindsVisible(payload.kinds, payload.declaration, payload.caps) : [], [payload])
  const uploadTypes = useMemo(() => uploadTypesOf(visible), [visible])
  const forSurface = useCallback((s: ReportSurface) => kindsForSurface(visible, s), [visible])
  const allows = useCallback((uploadType: string) => uploadTypes.includes(uploadType), [uploadTypes])
  const labelFor = useCallback((uploadType: string, fallback: string) => labelForUploadType(visible, uploadType) || fallback, [visible])
  const byKey = useCallback((key: string) => visible.find(r => r.key === key) || null, [visible])
  // feedFor: step 1 of the applies?/loaded? two-step for a page whose subject is one feed kind.
  const feedFor = useCallback((keyOrUploadType: string): { kind: ReportKindRow | null; applies: FeedApplies } => {
    const kind = feedKindFor(visible, keyOrUploadType)
    return { kind, applies: feedApplies(loaded, error, kind) }
  }, [visible, loaded, error])
  // showsIn: "this upload will show in …" for a registry key or an upload route key — the ONE way a
  // surface learns a kind's consumers (rendered by components/ShowsIn.tsx; the lock pins every surface).
  const showsIn = useCallback((keyOrUploadType: string): ShowsIn | null => showsInFor(visible, keyOrUploadType), [visible])
  // feedsFor: the inverse — which visible kinds show in a given report screen, and where each is uploaded.
  const feedsFor = useCallback((screen: string): FeedForScreen[] => feedsForScreen(visible, screen), [visible])
  return {
    payload, loaded, error, reload, visible, uploadTypes, forSurface, allows, labelFor, byKey, feedFor, showsIn, feedsFor,
    consumers: payload?.consumers || {},
    declaration: payload?.declaration || null,
    filenameRules: payload?.filename_rules || [],
    standard: payload?.standard || null,
    ready: !!payload?.registry_ready,
    withheld: withheldSummary(payload),
  }
}
