'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, apiUpload, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { readUploadOutcome } from '../../_lib/uploadGuard'
import { PERIOD_ROUTES, MODULE_ROUTES, ROUTE_NOTES, ROUTE_URL_OVERRIDE } from '../../_lib/uploadRoutes'
import ShowsIn from '@/components/ShowsIn'
import { useReportKinds } from '@/lib/report-kinds'
import { useConnectors } from '@/lib/connectors'
import type { ReportKindRow } from '@/lib/carrier-scope'

const enc = encodeURIComponent

// Each report: where it comes from, its EXACT name on the portal, whether it auto-sweeps, and how
// to upload it manually. `ft` = the upload_log file_type used to show an "already uploaded" badge.
type Step = {
  id: string; label: string; icon: string; source: string; report: string; url?: string
  auto: boolean; kind: 'period' | 'module'; endpoint: string; needsDate?: boolean; ft?: string; note?: string
  provenance?: string
}

// ── WHAT THIS WIZARD OFFERS IS COMPUTED, NEVER LISTED (design §7, 2026-09-20) ────────────────────
// The list used to fall back to a hardcoded set of ten steps that named a POS vendor, a processor
// and a distributor by brand and URL. That fallback is gone. Two data sources remain, both rows:
//   · the connector registry (GET /connectors → report_definitions with an upload_endpoint) supplies
//     the portal name, the report's exact name and the auto/manual badge;
//   · the REPORT-KIND REGISTRY (GET /commcalc/report-kinds → lib/report-kinds.ts → the one visibility
//     function) decides WHICH of those steps this tenant is offered, and supplies the steps for a
//     tenant whose connector registry is empty — from the visible kinds' route keys, with the route
//     metadata in ../_lib/uploadRoutes.ts (shared with the Upload page). A route no visible kind
//     names is not offered, whatever the connector registry says.
function stepsFromRegistry(conns: Connector[], allows: (u: string) => boolean, labelFor: (u: string, f: string) => string): Step[] {
  const out: Step[] = []
  for (const c of conns || []) {
    for (const r of (c.reports || [])) {
      const ep = r.upload_endpoint || ''
      if (!ep) continue
      if (!allows(r.report_key)) continue
      const kind: 'period' | 'module' = ep.startsWith('commcalc/upload/') ? 'period' : 'module'
      const meta = PERIOD_ROUTES[r.report_key] || MODULE_ROUTES[r.report_key]
      out.push({
        id: r.report_key, label: labelFor(r.report_key, r.label || r.report_key), icon: meta?.icon || '📄',
        source: c.vendor_name + (c.label ? ` — ${c.label}` : ''),
        report: (r.source_name || r.label || r.report_key) + (r.report_id ? ` (report #${r.report_id})` : ''),
        url: ROUTE_URL_OVERRIDE[r.report_key] || r.source_url || c.portal_url || undefined,
        auto: !!r.auto, kind, endpoint: ep,
        needsDate: !!(MODULE_ROUTES[r.report_key]?.needsDate),
        ft: kind === 'period' ? r.report_key : undefined,
        note: ROUTE_NOTES[r.report_key],
      })
    }
  }
  return out
}

// A tenant with no connector rows yet: the visible kinds' routes, labelled by the registry, sourced
// by the registry's own hint — no portal URL is invented.
function stepsFromKinds(kindRows: ReportKindRow[]): Step[] {
  const out: Step[] = []
  for (const k of kindRows) {
    for (const u of k.upload_types || []) {
      const pr = PERIOD_ROUTES[u]; const mr = MODULE_ROUTES[u]
      if (!pr && !mr) continue
      out.push({
        id: u, label: k.label, icon: (pr || mr)!.icon, source: k.source_hint || 'your system',
        report: k.what_in_it || (pr || mr)!.desc, auto: false, kind: pr ? 'period' : 'module',
        endpoint: pr ? `commcalc/upload/${u}` : mr!.endpoint, needsDate: !!mr?.needsDate,
        ft: pr ? u : undefined, note: ROUTE_NOTES[u], provenance: k.provenance_text,
      })
    }
  }
  return out
}

type Rec = { file_type: string; period: string | null; uploaded_at: string }
type RegistryReport = { report_key: string; label?: string | null; source_name?: string | null; report_id?: string | number | null; upload_endpoint?: string | null; source_url?: string | null; auto?: boolean }
type Connector = { vendor_name: string; label?: string | null; portal_url?: string | null; sweep_kind?: string | null; reports?: RegistryReport[] }

export default function UploadWizardPage() {
  const { period } = usePeriod()
  const [history, setHistory] = useState<Rec[]>([])
  const [conns, setConns] = useState<Connector[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [dates, setDates] = useState<Record<string, string>>({})
  const kinds = useReportKinds()

  function loadHistory() {
    api(`/api/v1/commcalc/upload/history?org_id=${ORG_ID}&limit=200`)
      .then((d: Rec[]) => setHistory(Array.isArray(d) ? d : [])).catch(() => setHistory([]))
  }
  useEffect(() => { loadHistory() }, [])
  useEffect(() => { api('/api/v1/commcalc/connectors').then((d: Connector[]) => setConns(Array.isArray(d) ? d : [])).catch(() => setConns([])) }, [])

  // Registry first; the visible kinds' routes when the connector registry is empty; NOTHING before
  // the report-kind registry has answered or when it could not be read.
  // THE CONNECTOR REGISTRY (mig 1014): a connector instance whose connector does not apply to this tenant's
  // declared POS / carrier contributes no step (its reports are gated per KIND by useReportKinds already —
  // the two gates AND together, the same posture as the Upload page's tiles).
  const connectors = useConnectors()
  const registrySteps = kinds.loaded && !kinds.error && connectors.loaded
    ? stepsFromRegistry(conns.filter(c => connectors.allows(c.sweep_kind)), kinds.allows, kinds.labelFor) : []
  const kindSteps = kinds.loaded && !kinds.error && registrySteps.length === 0 ? stepsFromKinds(kinds.forSurface('wizard')) : []
  const STEPS = registrySteps.length ? registrySteps : kindSteps
  const fromRegistry = registrySteps.length > 0

  function lastUpload(s: Step): Rec | undefined {
    if (!s.ft) return undefined
    return history.find(h => h.file_type === s.ft && (s.kind !== 'period' || h.period === period))
  }

  async function upload(s: Step, file: File) {
    setBusy(s.id); setMsg(m => ({ ...m, [s.id]: '' }))
    try {
      const form = new FormData(); form.append('file', file)
      let q = `org_id=${ORG_ID}`
      if (s.kind === 'period') q = `period=${enc(period)}&` + q
      if (s.needsDate) {
        const d = dates[s.id]
        if (!d) { setMsg(m => ({ ...m, [s.id]: 'Pick an effective date first.' })); setBusy(''); return }
        q = `effective_date=${enc(d)}&` + q
      }
      const res = await apiUpload(`/api/v1/${s.endpoint}?${q}`, form)
      // A price-guard refusal / shrink warning returns HTTP-200 — tell the truth instead of "✓ Uploaded".
      const o = readUploadOutcome(res, 'rows')
      setMsg(m => ({ ...m, [s.id]: (o.tone === 'ok' ? '✓ ' : '⚠ ') + o.text }))
      loadHistory()
    } catch (e: unknown) {
      setMsg(m => ({ ...m, [s.id]: `Error: ${(e as Error)?.message || e}` }))
    } finally { setBusy('') }
  }

  const periodSteps = STEPS.filter(s => s.kind === 'period')
  const done = periodSteps.filter(s => lastUpload(s)).length

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧭 Upload Wizard</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            One guided place to get every report in — the exact report name, where to pull it, and whether it
            already auto-sweeps. Working period: <strong>{period}</strong>. Core monthly reports loaded: <strong>{done}/{periodSteps.length}</strong>.
            {fromRegistry && <span style={{ color: 'var(--text3)' }}> The list and auto/manual badges come from the connector registry; which reports are offered comes from the report-kind registry.</span>}
          </p>
          {/* What decides the list — said out loud (design §7). */}
          {!kinds.loaded ? (
            <div style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>Reading which report kinds this company may upload…</div>
          ) : kinds.error ? (
            <div style={{ color: '#991b1b', fontSize: 12, marginTop: 4 }}>⚠️ The report-kind registry could not be read ({kinds.error}) — the steps are withheld rather than guessed.</div>
          ) : (
            <div style={{ color: 'var(--text3)', fontSize: 12, marginTop: 4 }}>
              Offered for {kinds.declaration?.pos?.length ? <>POS <b>{kinds.declaration.pos.join(' / ')}</b></> : 'no declared POS'} · {kinds.declaration?.carriers?.length ? <>carrier <b>{kinds.declaration.carriers.join(' / ')}</b></> : 'no declared carrier'}
              {!kinds.ready && <> · registry table not applied yet — house defaults ({kinds.payload?.migration})</>}
              {kinds.withheld && <> · {kinds.withheld}</>}
            </div>
          )}
        </div>
        <Link href="/commcalc/connectors" className="btn btn-secondary" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>🔌 Manage in Connectors</Link>
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
        {kinds.loaded && !kinds.error && STEPS.length === 0 && (
          <div className="card" style={{ padding: 14, color: 'var(--text2)', fontSize: 13 }}>
            No upload steps are offered yet: no visible report kind names a manual upload route for this company&apos;s declared POS and carrier. Declare them in onboarding, or register the reports in Connectors.
          </div>
        )}
        {STEPS.map(s => {
          const last = lastUpload(s)
          const m = msg[s.id]
          return (
            <div key={s.id} className="card" style={{ padding: 14, display: 'grid', gap: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'flex-start' }}>
                <div style={{ minWidth: 260 }}>
                  <div style={{ fontWeight: 700, fontSize: 15 }}>
                    <span style={{ marginRight: 6 }}>{s.icon}</span>{s.label}
                    {s.auto
                      ? <span style={{ marginLeft: 8, fontSize: 11, color: '#15803d', background: '#f0fdf4', padding: '2px 7px', borderRadius: 10 }}>auto-sweeps</span>
                      : <span style={{ marginLeft: 8, fontSize: 11, color: '#b45309', background: '#fffbeb', padding: '2px 7px', borderRadius: 10 }}>manual upload</span>}
                    {s.provenance && <span style={{ marginLeft: 8, fontSize: 11, color: 'var(--text3)' }}>{s.provenance}</span>}
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text)', marginTop: 4 }}><strong>Report:</strong> {s.report}</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
                    <strong>From:</strong> {s.url ? <a href={s.url} target="_blank" rel="noreferrer" style={{ color: '#2563eb' }}>{s.source} ↗</a> : s.source}
                  </div>
                  {s.note && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4, fontStyle: 'italic' }}>{s.note}</div>}
                  {/* WHERE THIS UPLOAD SHOWS UP — the registry row's consumers, linked (owner 2026-09-20). */}
                  <ShowsIn info={kinds.showsIn(s.id)} loaded={kinds.loaded} compact />
                </div>
                <div style={{ textAlign: 'right', fontSize: 12, color: last ? '#15803d' : 'var(--text3)', whiteSpace: 'nowrap' }}>
                  {last ? `✓ loaded ${String(last.uploaded_at).slice(0, 10)}` : (s.ft ? 'not loaded for this period' : '')}
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                {s.needsDate && (
                  <input type="date" className="input" value={dates[s.id] || ''} onChange={e => setDates(d => ({ ...d, [s.id]: e.target.value }))} style={{ width: 160 }} />
                )}
                <label className="btn" style={{ padding: '6px 12px', fontSize: 13, cursor: 'pointer' }}>
                  {busy === s.id ? 'Uploading…' : '📤 Choose file & upload'}
                  <input type="file" hidden disabled={busy === s.id}
                    onChange={e => { const f = e.target.files?.[0]; if (f) upload(s, f); e.currentTarget.value = '' }} />
                </label>
                {m && <span style={{ fontSize: 12, color: m.startsWith('Error') ? '#b91c1c' : m.startsWith('⚠') ? '#b45309' : '#15803d' }}>{m}</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
