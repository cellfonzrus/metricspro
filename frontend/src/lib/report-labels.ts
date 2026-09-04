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

const orgQS = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

// Fetch-once hook. Degrades to built-in labels (empty maps) on any error — a label service
// hiccup can never blank a report.
export function useReportLabels() {
  const { activeCarrier } = useActiveCarrier()
  const [data, setData] = useState<ReportLabelsData | null>(null)
  const reload = useCallback(() => {
    api(`/api/v1/commcalc/report-labels${orgQS()}`).then(setData).catch(() => setData(null))
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
  return { data, reload, colLabel, bannerOn, term, activeCarrier }
}
