// Carrier-aware REPORT COLUMN LABELS + banner terminology gates (owner directive 2026-09-02).
//
// The backend resolves tenant override > house carrier preset > built-in default per carrier
// (GET /commcalc/report-labels ← report_labels.py over commcalc.ui_label_override, mig 068/945)
// and the pages render from the payload with their own built-in header as the LAST fallback — the
// mig-932 gp acc_label pattern, so headers, grids and exports can never disagree. RULE TWO: no
// carrier name is branched on here; the active-carrier lens only PICKS which resolved map to read
// ('Edge' on the Total side, 'ACIMA' on the Boost side, both data rows).
//
// pickLabelMap / pickBannerMap are PURE so the fallback ladder is testable without React.
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import { useActiveCarrier } from '@/lib/auth-context'

export type ReportLabelsData = {
  carriers: string[]
  default_carrier: string
  columns: Record<string, Record<string, string>>   // per carrier (+ '_' = no-preset fallback)
  banners: Record<string, Record<string, string>>   // per carrier, 'on'|'off'
  terms?: Record<string, Record<string, string>>    // per carrier — vocabulary terms (mig 953)
  overrides: { columns: Record<string, string>; banners: Record<string, string>; terms?: Record<string, string> }
  presets: Record<string, { columns: Record<string, string>; banners: Record<string, string>; terms?: Record<string, string> }>
  editable_columns: { key: string; default: string }[]
  editable_terms?: { key: string; default: string }[]
  banner_keys: { key: string; default: string; title: string }[]
}

// The resolved map for the ACTIVE carrier: active carrier's map, else the org's default carrier's,
// else the no-preset '_' map (defaults + this org's overrides), else {} (built-ins render).
export function pickLabelMap(data: ReportLabelsData | null, activeCarrier: string): Record<string, string> {
  if (!data?.columns) return {}
  return data.columns[activeCarrier] || data.columns[data.default_carrier] || data.columns['_'] || {}
}

export function pickBannerMap(data: ReportLabelsData | null, activeCarrier: string): Record<string, string> {
  if (!data?.banners) return {}
  return data.banners[activeCarrier] || data.banners[data.default_carrier] || data.banners['_'] || {}
}

// The resolved carrier VOCABULARY TERM map (mig 953 — 'processor'/'distributor'/'financing'/
// 'marketplace_feed'/'pos_system'). Shared copy writes the neutral noun as its fallback; the
// active carrier's preset supplies the brand ('ePay' on Boost, 'VidaPay' on Total), so no page
// ever hardcodes the other carrier's vocabulary (owner directive 2026-09-04).
export function pickTermMap(data: ReportLabelsData | null, activeCarrier: string): Record<string, string> {
  if (!data?.terms) return {}
  return data.terms[activeCarrier] || data.terms[data.default_carrier] || data.terms['_'] || {}
}

// ── THE POS NAME IN PAGE COPY (owner 2026-09-20: "it should customize the message based on what POS
//    is being used"). The tenant's POS is ONE piece of config — the `pos_system` term (mig 953 house
//    presets, mig 1004's 'RQ', a tenant override) — and every sentence that names the POS dereferences
//    it here. `pos` is the declared label ('RQ', 'b2bsoft') or the registry's own NEUTRAL noun (the
//    `editable_terms` default the backend ships, 'POS'); `posDeclared` says which. No page spells a
//    vendor: backend/harness_carrier_vocab_guard.py fails the build when one does.
export const POS_TERM_KEY = 'pos_system'
const POS_LAST_RESORT = 'POS'   // only when the label payload has not arrived / failed — the registry default is the real neutral noun

export type PosTerm = {
  pos: string            // the word to put in copy: the declared POS label, else the neutral noun
  posDeclared: boolean   // true when a preset / override names the POS (false = neutral noun in use)
  posLoaded: boolean     // false until the label payload has been read (or failed)
}

/** The registry's neutral noun for a term key (shipped as `editable_terms[].default`), else the last resort. */
export function neutralTerm(data: ReportLabelsData | null, key: string, lastResort: string): string {
  const hit = (data?.editable_terms || []).find(t => t.key === key)
  return (hit?.default || '').trim() || lastResort
}

/** PURE: the POS term for the active carrier out of a report-labels payload (proven by prove_pos_term_copy.mjs). */
export function pickPosTerm(data: ReportLabelsData | null, activeCarrier: string, loaded = true): PosTerm {
  const declared = (pickTermMap(data, activeCarrier)[POS_TERM_KEY] || '').trim()
  return { pos: declared || neutralTerm(data, POS_TERM_KEY, POS_LAST_RESORT), posDeclared: !!declared, posLoaded: loaded }
}

const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

// Fetch-once hook. Degrades to built-in labels (empty maps) on any error — a label service
// hiccup can never blank a report.
export function useReportLabels() {
  const { activeCarrier } = useActiveCarrier()
  const [data, setData] = useState<ReportLabelsData | null>(null)
  const [loaded, setLoaded] = useState(false)
  const reload = useCallback(() => {
    api(`/api/v1/commcalc/report-labels${orgQS()}`).then(setData).catch(() => setData(null)).finally(() => setLoaded(true))
  }, [])
  useEffect(() => { reload() }, [reload])
  const labels = useMemo(() => pickLabelMap(data, activeCarrier), [data, activeCarrier])
  const banners = useMemo(() => pickBannerMap(data, activeCarrier), [data, activeCarrier])
  const terms = useMemo(() => pickTermMap(data, activeCarrier), [data, activeCarrier])
  // colLabel: the resolved header for a column key, with the page's built-in header as fallback.
  const colLabel = useCallback((key: string, fallback: string) => labels[key] || fallback, [labels])
  // bannerOn: whether a terminology-gated banner should render (default ON = today's behavior).
  const bannerOn = useCallback((key: string) => (banners[key] || 'on') !== 'off', [banners])
  // term: the active carrier's vocabulary for a term key, with the NEUTRAL noun as fallback —
  // shared copy never hardcodes a carrier brand (owner directive 2026-09-04, mig 953).
  const term = useCallback((key: string, fallback: string) => terms[key] || fallback, [terms])
  // pos / posDeclared: the ONE way copy names the tenant's POS (see pickPosTerm above).
  const posTerm = useMemo(() => pickPosTerm(data, activeCarrier, loaded), [data, activeCarrier, loaded])
  return { data, reload, colLabel, bannerOn, term, activeCarrier, ...posTerm }
}

/**
 * The POS term alone, for a page whose only vocabulary need is the POS name:
 * `const { pos, posDeclared } = usePosTerm()` → "the daily {pos} feed", "Swept ({pos})".
 * Same fetch, same resolution as useReportLabels — a wrapper, not a second path.
 */
export function usePosTerm(): PosTerm {
  const { pos, posDeclared, posLoaded } = useReportLabels()
  return { pos, posDeclared, posLoaded }
}
