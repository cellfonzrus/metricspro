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
}
export type ReportSurface = 'intake' | 'upload' | 'wizard' | 'email_imports' | 'tiles'
export const INTAKE_LANDINGS = ['sales', 'pos', 'inventory', 'commission', 'x_report', 'merchant_payments', 'bill_payments', 'other']

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
  return {
    payload, loaded, error, reload, visible, uploadTypes, forSurface, allows, labelFor, byKey,
    declaration: payload?.declaration || null,
    filenameRules: payload?.filename_rules || [],
    standard: payload?.standard || null,
    ready: !!payload?.registry_ready,
    withheld: withheldSummary(payload),
  }
}
