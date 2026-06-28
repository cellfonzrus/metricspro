'use client'
import { useState, useEffect } from 'react'
import Link from 'next/link'
import { api, apiUpload, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const enc = encodeURIComponent

// Each report: where it comes from, its EXACT name on the portal, whether it auto-sweeps, and how
// to upload it manually. `ft` = the upload_log file_type used to show an "already uploaded" badge.
type Step = {
  id: string; label: string; icon: string; source: string; report: string; url?: string
  auto: boolean; kind: 'period' | 'module'; endpoint: string; needsDate?: boolean; ft?: string; note?: string
}

// The wizard is now driven by the connector registry (report_definitions). These two maps only hold
// the polish the registry doesn't carry — an icon and the curated upload note — keyed by report_key.
const ICONS: Record<string, string> = {
  sales: '🛍️', payment_detail: '💳', mi_report: '💰', comp_report: '🏦', dlar_rep: '📊',
  dlar_store: '🏪', hotsheet: '🏷️', vip_workbook: '🧾', asset_ledger: '📒', daily_closing: '🧮',
}
const NOTES: Record<string, string> = {
  sales: 'Daily B2B Sales-Transaction-Details now arrive automatically via the email subscription (→ daily feed + Sales Feed Recon). Set the “Sales” connector to auto (Connectors page) to also auto-build the monthly commission basis from that feed — then this manual upload is only a fallback.',
  comp_report: 'Posts in arrears — a month is often empty until the carrier publishes it. The sweep replaces the open month daily and freezes it at month-end.',
  hotsheet: 'Pick the date it became effective.',
  asset_ledger: 'Auto-swept with the VIP sweep (GET /paygodashboard/DownloadAssetLanding). Manual upload still available.',
  daily_closing: 'Auto-imports via a Google service account once set up — configure on Daily Closing → Auto-Import. Manual upload still available.',
}
const URL_OVERRIDE: Record<string, string> = { daily_closing: '/closing/imports' }

// Map the registry (GET /connectors → nested report_definitions) into wizard steps. Only reports with
// an upload_endpoint are shown (sweep-only reports like chargebacks/inventory aren't manually uploaded).
// `kind` is derived from the endpoint: the generic commcalc/upload/* route = a period upload.
function stepsFromRegistry(conns: any[]): Step[] {
  const out: Step[] = []
  for (const c of conns || []) {
    for (const r of (c.reports || [])) {
      const ep: string = r.upload_endpoint
      if (!ep) continue
      const kind: 'period' | 'module' = ep.startsWith('commcalc/upload/') ? 'period' : 'module'
      out.push({
        id: r.report_key, label: r.label || r.report_key, icon: ICONS[r.report_key] || '📄',
        source: c.vendor_name + (c.label ? ` — ${c.label}` : ''),
        report: (r.source_name || r.label || r.report_key) + (r.report_id ? ` (report #${r.report_id})` : ''),
        url: URL_OVERRIDE[r.report_key] || r.source_url || c.portal_url,
        auto: !!r.auto, kind, endpoint: ep,
        needsDate: r.report_key === 'hotsheet',
        ft: kind === 'period' ? r.report_key : undefined,
        note: NOTES[r.report_key],
      })
    }
  }
  return out
}

const FALLBACK_STEPS: Step[] = [
  { id: 'sales', label: 'Sales Transactions', icon: '🛍️', kind: 'period', endpoint: 'commcalc/upload/sales', ft: 'sales',
    source: 'b2bsoft — wsreports.b2bsoft.com', url: 'https://wsreports.b2bsoft.com', auto: false,
    report: 'Sales Transaction Details — the 78-column export (ALL columns, NOT the grouped variant)',
    note: 'Daily B2B Sales-Transaction-Details now arrive automatically via the email subscription (→ daily feed + Sales Feed Recon). Set the “Sales” connector to auto (Connectors page) to also auto-build the monthly commission basis from that feed — then this manual upload is only a fallback.' },
  { id: 'payment_detail', label: 'Commission Payment Detail', icon: '💳', kind: 'period', endpoint: 'commcalc/upload/payment_detail', ft: 'payment_detail',
    source: 'ePay Owner Portal — ownerportal.epayworldwide.com', url: 'https://ownerportal.epayworldwide.com', auto: true,
    report: 'Commission Payment Detail (report #50273)' },
  { id: 'mi_report', label: 'MI & ATU Report', icon: '💰', kind: 'period', endpoint: 'commcalc/upload/mi_report', ft: 'mi_report',
    source: 'ePay Owner Portal', url: 'https://ownerportal.epayworldwide.com', auto: true,
    report: 'Monthly Incentive & ATU Subscriber Details (report #102817)' },
  { id: 'comp_report', label: 'Comprehensive Comp', icon: '🏦', kind: 'period', endpoint: 'commcalc/upload/comp_report', ft: 'comp_report',
    source: 'ePay Owner Portal', url: 'https://ownerportal.epayworldwide.com', auto: true,
    report: 'Comprehensive Compensation Report (report #100614)',
    note: 'Posts in arrears — a month is often empty until the carrier publishes it. The sweep replaces the open month daily and freezes it at month-end.' },
  { id: 'dlar_rep', label: 'DLAR Rep KPI', icon: '📊', kind: 'period', endpoint: 'commcalc/upload/dlar_rep', ft: 'dlar_rep',
    source: 'Boost Elevate GO — boostelevatego.com', url: 'https://boostelevatego.com', auto: true,
    report: 'DLAR — Rep report' },
  { id: 'dlar_store', label: 'DLAR Store KPI', icon: '🏪', kind: 'period', endpoint: 'commcalc/upload/dlar_store', ft: 'dlar_store',
    source: 'Boost Elevate GO', url: 'https://boostelevatego.com', auto: true,
    report: 'DLAR — Store / Advocate report' },
  { id: 'hotsheet', label: 'Pricing Hotsheet', icon: '🏷️', kind: 'module', endpoint: 'commcalc/hotsheet/upload', needsDate: true,
    source: 'Yoobic — Knowledge Library', url: 'https://app.yoobic.com', auto: false,
    report: 'Boost pricing hotsheet (latest version)', note: 'Pick the date it became effective.' },
  { id: 'vip_workbook', label: 'VIP Wireless Workbook', icon: '🧾', kind: 'module', endpoint: 'commcalc/vip/upload',
    source: 'VIP Wireless portal — vipwireless.com', url: 'https://vipwireless.com', auto: true,
    report: 'Invoices / PayGo workbook' },
  { id: 'asset_ledger', label: 'Asset Ledger', icon: '📒', kind: 'module', endpoint: 'asset/upload',
    source: 'VIP Wireless portal', url: 'https://vipwireless.com', auto: true,
    report: 'Asset Lending — the download icon on /account/dashboard (Asset_Lending.xlsx)',
    note: 'Auto-swept with the VIP sweep (GET /paygodashboard/DownloadAssetLanding). Manual upload still available.' },
  { id: 'daily_closing', label: 'Daily Closing Sheet', icon: '🧮', kind: 'module', endpoint: 'closing/upload',
    source: 'Google — "Envelopes Data (Responses)"', auto: true, url: '/closing/imports',
    report: 'Daily closing envelopes export (.xlsx / .csv)',
    note: 'Auto-imports via a Google service account once set up — configure on Daily Closing → Auto-Import. Manual upload still available.' },
]

type Rec = { file_type: string; period: string | null; uploaded_at: string }

export default function UploadWizardPage() {
  const { period } = usePeriod()
  const [history, setHistory] = useState<Rec[]>([])
  const [conns, setConns] = useState<any[]>([])
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<Record<string, string>>({})
  const [dates, setDates] = useState<Record<string, string>>({})

  function loadHistory() {
    api(`/api/v1/commcalc/upload/history?org_id=${ORG_ID}&limit=200`)
      .then((d: any) => setHistory(Array.isArray(d) ? d : [])).catch(() => setHistory([]))
  }
  useEffect(() => { loadHistory() }, [])
  useEffect(() => { api('/api/v1/commcalc/connectors').then((d: any) => setConns(Array.isArray(d) ? d : [])).catch(() => setConns([])) }, [])

  // Registry is the source of truth; the hardcoded list is only a fallback if it's empty/unreachable.
  const registrySteps = stepsFromRegistry(conns)
  const STEPS = registrySteps.length ? registrySteps : FALLBACK_STEPS
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
      const rows = res?.rows_saved ?? res?.rows ?? res?.count
      setMsg(m => ({ ...m, [s.id]: `✓ Uploaded${rows != null ? ` — ${rows} rows` : ''}.` }))
      loadHistory()
    } catch (e: any) {
      setMsg(m => ({ ...m, [s.id]: `Error: ${e?.message || e}` }))
    } finally { setBusy('') }
  }

  const periodSteps = STEPS.filter(s => s.kind === 'period')
  const done = periodSteps.filter(s => lastUpload(s)).length

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧭 Upload Wizard</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            One guided place to get every report in — the exact report name, where to pull it, and whether it
            already auto-sweeps. Working period: <strong>{period}</strong>. Core monthly reports loaded: <strong>{done}/{periodSteps.length}</strong>.
            {fromRegistry && <span style={{ color: 'var(--text3)' }}> The list and auto/manual badges come from the connector registry.</span>}
          </p>
        </div>
        <Link href="/commcalc/connectors" className="btn btn-secondary" style={{ fontSize: 13, whiteSpace: 'nowrap' }}>🔌 Manage in Connectors</Link>
      </div>

      <div style={{ display: 'grid', gap: 12 }}>
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
                  </div>
                  <div style={{ fontSize: 13, color: 'var(--text)', marginTop: 4 }}><strong>Report:</strong> {s.report}</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
                    <strong>From:</strong> {s.url ? <a href={s.url} target="_blank" rel="noreferrer" style={{ color: '#2563eb' }}>{s.source} ↗</a> : s.source}
                  </div>
                  {s.note && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4, fontStyle: 'italic' }}>{s.note}</div>}
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
                {m && <span style={{ fontSize: 12, color: m.startsWith('Error') ? '#b91c1c' : '#15803d' }}>{m}</span>}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
