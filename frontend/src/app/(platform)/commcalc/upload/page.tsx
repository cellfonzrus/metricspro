'use client'
import { useState, useEffect, useCallback } from 'react'
import { ORG_ID, api, apiUpload } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { readUploadOutcome, UploadGuardBanner, type UploadOutcome } from '../_lib/uploadGuard'
import { WhereAreMyRowsButton } from '../_lib/UploadTracePanel'
import { LastUploadLine, useLastUploads } from '../_lib/lastUpload'
import { MODE_UI, modeVerb, PERIOD_ROUTES, PERIODLESS, MODULE_ROUTES, LINK_ROUTES, ALL_TRACE_KEYS, type ModuleRoute } from '../_lib/uploadRoutes'
import { useActiveCarrier } from '@/lib/auth-context'
import { carrierCode } from '@/lib/rbac'
import { useReportLabels } from '@/lib/report-labels'
import { useReportKinds } from '@/lib/report-kinds'
import type { ReportKindRow } from '@/lib/carrier-scope'

// ── WHAT THIS PAGE OFFERS IS COMPUTED, NEVER LISTED (owner directives 2026-09-20; design §7) ──────
// Owner: "it is very important that we don't have extra file upload paths for a new tenant who does
// not need those based on the carrier they pick … Currently in the Verizon tenant we have all the
// table uploads for B2B when it has been declared that the POS is not B2B, it is RQ."
//
// This page used to carry FOUR hardcoded arrays of what a tenant may upload, a `carrier` tag per
// tile, and a POS vendor name as a constant (`CUSTOM_REPORTS_POS`) — the one place that gated on the
// tenant's POS, by a name in code. All of it is gone:
//   · WHICH tiles show comes from the REPORT-KIND REGISTRY (GET /commcalc/report-kinds → lib/report-
//     kinds.ts → the ONE visibility function). A tile renders only when a visible registry row names
//     its route key in `upload_types` (or its custom sheet label). The registry rows carry the POS /
//     carrier codes as DATA; this file names none.
//   · HOW a route posts (endpoint, period-vs-day grain, what the write does) is metadata keyed by
//     route key in ../_lib/uploadRoutes.ts, shared with the wizard.
//   · The connector registry's carrier scope (`report_definitions.carrier_id`, GET /upload-registry,
//     `tileVisible`) still applies — the two gates AND together; neither is a second copy of the other.
// HONEST BEFORE THE REGISTRY LOADS: nothing is offered until /report-kinds answers; a failed read
// says so instead of showing every tile (show less and say why).
const PERIOD_META = PERIOD_ROUTES

// Auto-import sources + the period granularities the user asked for, per source. These are the
// portal SWEEPS (connector registry, carrier-scoped by `connector_instances.carrier_id` through
// GET /upload-registry → tileVisible), not upload choices; the shipped `carrier` tag is the same
// last-resort fallback the registry overrides. No vendor is named for the POS row: the tenant's
// own POS word comes from the vocabulary term.
const AUTO_SOURCES = [
  { id: 'dlar', name: 'Metrics Rep/Store (carrier KPI portal)', icon: '📊', desc: 'Store + Rep KPI reports',
    cfg: 'dlar/sweep/config', run: 'dlar/sweep/run-now', configure: '/commcalc/dlar/sweep',
    scopes: [{ v: 'mtd', l: 'Month-to-date' }, { v: 'full', l: 'Full month' }] },
  { id: 'epay', name: 'Payment Processor Portal', icon: '💰', desc: 'MI · ATU · Commission · Comprehensive · Reconciliation',
    cfg: 'epay/sweep/config', run: 'epay/sweep/run-now', configure: '/commcalc/epay/sweep', carrier: 'boost',
    scopes: [{ v: 'daily', l: 'Daily' }, { v: 'mtd', l: 'Month-to-date' }, { v: 'full', l: 'Full month' }] },
  { id: 'b2b', name: 'POS portal', icon: '📦', desc: 'Sales transactions · inventory aging — configure the portal login (2FA) under Data Sources',
    cfg: 'b2b/sweep/config', run: 'b2b/sweep/run-now', configure: '/commcalc/email-imports#portal-logins',
    scopes: [{ v: 'day', l: 'Single day' }, { v: 'month', l: 'Month' }, { v: 'custom', l: 'Custom range' }] },
  { id: 'vip', name: 'Distributor portal', icon: '🧾', desc: 'Invoices · PayGo · Credit memos',
    cfg: 'vip/sweep/config', run: 'vip/sweep/run-now', configure: '/commcalc/vip/sweep', carrier: 'boost',
    scopes: [{ v: 'recent', l: 'Recent (lookback)' }, { v: 'full', l: 'Full history' }] },
]

type UploadRecord ={ id: string; file_type: string; period: string | null; filename: string | null; rows_saved: number; uploaded_at: string }

// A report REGISTERED for this tenant in `report_definitions` (projected by GET /upload-registry).
// Deliberately open types: the whole point is that a report this file has never heard of can appear.
type RegistryReport = { report_key: string; label?: string | null; target_table?: string | null
                        upload_endpoint?: string | null; sort_order?: number; carrier_code?: string | null }

function fmtWhen(iso: string) {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function UploadPage() {
  const { period, setPeriod } = usePeriod()
  // Active-carrier lens: a dual-carrier tenant sees only the active carrier's upload tiles, and a
  // SINGLE-carrier tenant never sees another carrier's tiles at all (owner 2026-09-04: no Total/MA
  // vocabulary on the Boost side and vice versa). No carrier chosen yet → hide nothing (unchanged).
  const { activeCarrier, multi, carrierList } = useActiveCarrier()
  const haveCarriers = (carrierList || []).map(c => carrierCode(c)).filter(Boolean)
  // The registry's answer to "whose carrier owns this tile", keyed by the tile's own id (a
  // report_key for the manual tiles, a sweep_kind for the auto sources). Best-effort: a failed load
  // leaves the map empty, which shows everything rather than hiding an upload somebody needs.
  const [carrierScope, setCarrierScope] = useState<Record<string, { carrier_code?: string }>>({})
  // Every report REGISTERED for this tenant (report_definitions rows), whether or not this page
  // ships a tile for it. See the section at the bottom: a report that exists only as a row had no
  // way to be reached from here, which is where a new carrier's onboarding dead-ended.
  const [registryReports, setRegistryReports] = useState<RegistryReport[]>([])
  useEffect(() => {
    api('/api/v1/commcalc/upload-registry')
      .then((r: any) => { setCarrierScope(r?.scope || {}); setRegistryReports(r?.reports || []) })
      .catch(() => { setCarrierScope({}); setRegistryReports([]) })
  }, [])
  // REGISTRY FIRST, shipped tag second, show-it third. `fallback` is the tile's own default tag and
  // is consulted only when the tenant's registry has no row for that id — so registering a report
  // is always what decides, and a tenant can re-scope any tile without a deploy.
  const tileVisible = (id: string, fallback?: string) => {
    const code = carrierScope[id]?.carrier_code || fallback
    if (!code) return true                       // unclassified either way ⇒ never hidden
    if (haveCarriers.length === 0) return true   // nothing to scope BY ⇒ hide nothing
    return haveCarriers.includes(code) && (!multi || code === activeCarrier)
  }
  // The tenant's POS vocabulary term (mig 953) — for COPY only ("<POS> email reports"); which tiles
  // show is the registry's answer below, never a comparison against this string.
  const { term, pos } = useReportLabels()
  // THE REGISTRY: which report kinds this tenant may upload, with provenance — the one visibility
  // function, run in the hook. `allows(routeKey)` gates every tile block on this page.
  const kinds = useReportKinds()
  const [uploading, setUploading] = useState<string | null>(null)
  const [statuses, setStatuses] = useState<Record<string, 'idle'|'uploading'|'done'|'error'|'warn'>>({})
  const [messages, setMessages] = useState<Record<string, string>>({})
  // Full interpreted outcome per file type — drives the amber "what the importer actually saw" panel.
  const [outcomes, setOutcomes] = useState<Record<string, UploadOutcome | null>>({})
  const [history, setHistory] = useState<UploadRecord[]>([])
  const [showHistory, setShowHistory] = useState(false)
  // "when was the last set of data uploaded", per report — folds upload_trace (every ingest path,
  // incl. the hourly email sweep) with upload_log. Reloaded after every upload on this page.
  const { last: lastData, loaded: lastLoaded, hint: lastHint, reload: reloadLast } = useLastUploads(ALL_TRACE_KEYS)

  // The newest landed record across a module upload's trace keys (vip logs under two different keys).
  const moduleLast = (entry: ModuleRoute) => {
    const recs = (entry.traceKeys || []).map(k => lastData[k]).filter(Boolean)
    if (!recs.length) return null
    return recs.reduce((a, b) => (!a?.last_at ? b : !b?.last_at ? a : (a.last_at! >= b.last_at! ? a : b)))
  }

  // auto-import panel state
  const [cfgs, setCfgs] = useState<Record<string, any>>({})
  const [scope, setScope] = useState<Record<string, string>>({})
  const [adate, setAdate] = useState<Record<string, string>>({})
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [autoMsg, setAutoMsg] = useState<Record<string, string>>({})
  const [modDate, setModDate] = useState<Record<string, string>>({})
  // Self-serve custom import sheets (mig 099) — the POS email reports added on Email Imports (Activation Details,
  // Bill Payments, Sales by Product). Uploadable here too so the owner isn't forced onto the Email Imports
  // page. Each uses the SAME proven handleUpload → /upload/<report_key> capture as the built-in reports.
  const [customTypes, setCustomTypes] = useState<any[]>([])
  const loadCustomTypes = useCallback(async () => {
    try { const r = await api('/api/v1/commcalc/custom-import-types'); setCustomTypes(Array.isArray(r) ? r : []) }
    catch { /* best-effort */ }
  }, [])

  const loadHistory = useCallback(async () => {
    // via api() so scopeOrg() rewrites org_id to the signed-in tenant (multi-tenant): a new tenant
    // sees ITS upload history, not the house org's old uploads.
    try { setHistory(await api(`/api/v1/commcalc/upload/history?org_id=${ORG_ID}&limit=200`)) }
    catch { /* best-effort */ }
  }, [])

  const loadCfgs = useCallback(async () => {
    const out: Record<string, any> = {}
    await Promise.all(AUTO_SOURCES.map(async s => {
      try { out[s.id] = await api(`/api/v1/commcalc/${s.cfg}?org_id=${ORG_ID}`) }
      catch { /* ignore */ }
    }))
    setCfgs(out)
  }, [])

  useEffect(() => { loadHistory(); loadCfgs(); loadCustomTypes() }, [loadHistory, loadCfgs, loadCustomTypes])

  function lastUpload(fileType: string): UploadRecord | undefined {
    return history.find(h => h.file_type === fileType &&
      (PERIODLESS.has(fileType) || (!!period.trim() && !!h.period && h.period.includes(period.trim()))))
  }

  async function runAuto(s: typeof AUTO_SOURCES[number]) {
    setRunning(r => ({ ...r, [s.id]: true })); setAutoMsg(m => ({ ...m, [s.id]: '' }))
    const sc = scope[s.id] || s.scopes[0].v
    const dt = adate[s.id] || ''
    try {
      const qs = `scope=${encodeURIComponent(sc)}${dt ? '&date=' + encodeURIComponent(dt) : ''}&period=${encodeURIComponent(period)}&org_id=${ORG_ID}`
      await api(`/api/v1/commcalc/${s.run}?${qs}`, { method: 'POST' })
      setAutoMsg(m => ({ ...m, [s.id]: `▶ Started (${s.scopes.find(x => x.v === sc)?.l}). Refresh status in a moment.` }))
      setTimeout(loadCfgs, 4000)
    } catch (e: any) {
      setAutoMsg(m => ({ ...m, [s.id]: `❌ ${e.message}` }))
    }
    setRunning(r => ({ ...r, [s.id]: false }))
  }

  async function handleUpload(fileType: string, file: File) {
    const rowDated = fileType === 'daily_sales' || fileType.startsWith('ma_')
    if (!period.trim() && !rowDated && !PERIODLESS.has(fileType)) { alert('Enter the period this data is for first'); return }
    setUploading(fileType); setStatuses(s => ({ ...s, [fileType]: 'uploading' }))
    const form = new FormData(); form.append('file', file)
    try {
      const data = await apiUpload(
        `/api/v1/commcalc/upload/${fileType}?${!rowDated ? 'period=' + encodeURIComponent(period) + '&' : ''}org_id=${ORG_ID}`,
        form)
      // A price-guard refusal (saved:0, skipped:'price_guard'), an X-report that parsed nothing
      // (saved:0, skipped:'header_not_found'|…), or a shrink warning all come back HTTP-200 — surface
      // them honestly instead of a green "✅ 0 rows saved" that looks (or lies) like a clean upload.
      const o = readUploadOutcome(data, fileType === 'x_report' ? 'tender row(s)' : 'rows', pos)
      setStatuses(s => ({ ...s, [fileType]: o.tone === 'ok' ? 'done' : 'warn' }))
      setMessages(m => ({ ...m, [fileType]: (o.tone === 'ok' ? '✅ ' : '⚠️ ') + o.text }))
      setOutcomes(p => ({ ...p, [fileType]: o }))
      loadHistory(); reloadLast()
    } catch (e: any) {
      setStatuses(s => ({ ...s, [fileType]: 'error' }))
      setMessages(m => ({ ...m, [fileType]: `❌ ${e.message}` }))
      setOutcomes(p => ({ ...p, [fileType]: null }))
    }
    setUploading(null)
  }

  async function handleModuleUpload(entry: ModuleRoute, file: File) {
    if (entry.needsDate && !(modDate[entry.id] || '').trim()) { alert('Pick an effective date for the hotsheet first'); return }
    setUploading(entry.id); setStatuses(s => ({ ...s, [entry.id]: 'uploading' }))
    const form = new FormData(); form.append('file', file)
    if (entry.needsDate) form.append('effective_date', modDate[entry.id])
    try {
      const data = await apiUpload(`/api/v1/${entry.endpoint}?org_id=${ORG_ID}`, form)
      const n = data.rows_uploaded ?? data.saved ?? data.rows_saved ?? data.inserted ?? data.count ?? data.rows
      setStatuses(s => ({ ...s, [entry.id]: 'done' }))
      setMessages(m => ({ ...m, [entry.id]: `✅ ${n != null ? Number(n).toLocaleString() + ' rows' : 'Uploaded'}` }))
      loadHistory(); reloadLast()
    } catch (e: any) {
      setStatuses(s => ({ ...s, [entry.id]: 'error' }))
      setMessages(m => ({ ...m, [entry.id]: `❌ ${e.message}` }))
    }
    setUploading(null)
  }

  // A registry kind captured as a self-serve custom-import sheet (mig 099). The tile auto-PROVISIONS
  // the sheet on first upload (POST /custom-import-types) so there is no separate setup step, then posts
  // the file through the SAME /upload/<report_key> capture as every other tile. Status/messages keyed by
  // the sheet label the registry row carries.
  async function uploadCustomReport(rep: ReportKindRow, file: File) {
    const label = rep.custom_sheet_label || rep.label
    if (!period.trim()) { alert('Enter the period this data is for first'); return }
    setStatuses(s => ({ ...s, [label]: 'uploading' }))
    try {
      let key = customTypes.find((c: any) => (c.label || '').trim().toLowerCase() === label.toLowerCase())?.report_key
      if (!key) {
        const r: any = await api('/api/v1/commcalc/custom-import-types', { method: 'POST', body: JSON.stringify({ label }) })
        key = r.report_key; await loadCustomTypes()
      }
      const form = new FormData(); form.append('file', file)
      const data = await apiUpload(`/api/v1/commcalc/upload/${encodeURIComponent(key)}?period=${encodeURIComponent(period)}&org_id=${ORG_ID}`, form)
      const o = readUploadOutcome(data, 'rows', pos)
      setStatuses(s => ({ ...s, [label]: o.tone === 'ok' ? 'done' : 'warn' }))
      setMessages(m => ({ ...m, [label]: (o.tone === 'ok' ? '✅ ' : '⚠️ ') + o.text }))
      loadHistory(); reloadLast(); loadCustomTypes()
    } catch (e: any) {
      setStatuses(s => ({ ...s, [label]: 'error' }))
      setMessages(m => ({ ...m, [label]: `❌ ${e.message || e}` }))
    }
  }

  // THE FIVE TILE SETS, each derived from the registry — a route with no visible registry row is not
  // rendered, and before the registry answers nothing is (design §7: show less and say why).
  const periodTiles = kinds.loaded && !kinds.error ? Object.values(PERIOD_META).filter(t => kinds.allows(t.id) && tileVisible(t.id)) : []
  const moduleTiles = kinds.loaded && !kinds.error ? Object.values(MODULE_ROUTES).filter(m => kinds.allows(m.id) && tileVisible(m.id)) : []
  const linkTiles = kinds.loaded && !kinds.error ? Object.values(LINK_ROUTES).filter(l => kinds.allows(l.id) && tileVisible(l.id)) : []
  const customTiles = kinds.loaded && !kinds.error ? kinds.forSurface('upload').filter(k => !!k.custom_sheet_label) : []

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Data Imports</h1>
          <WhereAreMyRowsButton period={period} />
        </div>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Auto-imports pull from the portals on a schedule; manual upload below for anything not automated. Uploaded a file and a page still shows nothing? Open <strong>Where are my rows?</strong> — it traces every ingest and the org it landed in.</p>
      </div>

      {/* Period — applies to manual uploads + the period auto-imports tag their data with */}
      <div className="card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ fontWeight: 600, fontSize: 14 }}>Period:</label>
        <input className="input" style={{ width: 200 }} value={period} onChange={e => setPeriod(e.target.value)} placeholder="April 2026" />
        <span style={{ color: 'var(--text3)', fontSize: 13 }}>Which period this data is for. What an upload does differs per report — each tile says so (day-grain feeds add days; period reports replace the selected period).</span>
      </div>

      {/* ── Unified Auto-Imports panel ─────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 700, fontSize: 14 }}>
          🤖 Auto-Imports <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— pick the period to pull, then Run now (or let the schedule run it)</span>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {AUTO_SOURCES.filter(s => tileVisible(s.id, s.carrier)).map(s => {
              const c = cfgs[s.id] || {}
              // ROUTE GATE (mig 998): a portal sweep whose login route is switched off by config must
              // not read as "pending" or as a fault, and must not offer Run now. `route_policy` is
              // computed server-side per (org, connector) — no vendor name is decided on here.
              const off = c.route_policy && c.route_policy.allowed === false
              const stColor = off ? 'var(--text3)' : c.last_status === 'ok' ? '#15803d' : c.last_status === 'error' ? '#b91c1c' : c.last_status === 'running' ? '#b45309' : 'var(--text3)'
              const needsDate = (scope[s.id] || s.scopes[0].v).match(/day|custom/)
              return (
                <tr key={s.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px', verticalAlign: 'top', width: 270 }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{s.icon} {s.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{off ? c.route_policy.reason : s.desc}</div>
                    <div style={{ fontSize: 11, marginTop: 4, color: stColor }}>
                      {off ? `🚫 automatic portal login switched off${c.route_policy.remedy_label ? ` · use ${c.route_policy.remedy_label}` : ''}`
                        : (c.has_credentials ? (c.enabled ? '● scheduled' : '○ creds set, schedule off') : '○ not configured')}
                      {!off && c.last_status ? ` · last: ${c.last_status}` : ''}
                      {!off && c.last_run_at ? ` · ${fmtWhen(c.last_run_at)}` : ''}
                    </div>
                    {!off && (c.next_run_at || c.frequency) && (
                      <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>
                        {c.frequency ? `🗓 ${c.frequency}` : ''}
                        {c.next_run_at ? `${c.frequency ? ' · ' : ''}next: ${fmtWhen(c.next_run_at)}` : ''}
                        <a href={s.configure} style={{ marginLeft: 6, fontSize: 11 }}>edit time</a>
                      </div>
                    )}
                    {c.last_detail && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{String(c.last_detail).slice(0, 90)}</div>}
                  </td>
                  <td style={{ padding: '10px 16px', verticalAlign: 'top' }}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                      <select className="select" value={scope[s.id] || s.scopes[0].v} onChange={e => setScope(p => ({ ...p, [s.id]: e.target.value }))} style={{ fontSize: 12 }}>
                        {s.scopes.map(x => <option key={x.v} value={x.v}>{x.l}</option>)}
                      </select>
                      {needsDate && <input type="date" className="input" style={{ fontSize: 12, width: 150 }} value={adate[s.id] || ''} onChange={e => setAdate(p => ({ ...p, [s.id]: e.target.value }))} />}
                      <button className="btn btn-secondary" title={off ? c.route_policy.reason : undefined} style={{ fontSize: 12 }} disabled={running[s.id] || !c.has_credentials || off} onClick={() => runAuto(s)}>
                        {running[s.id] ? '…' : '▶ Run now'}
                      </button>
                      <a href={off ? (c.route_policy.remedy_href || s.configure) : s.configure} className="btn" style={{ fontSize: 12 }}>
                        {off ? `📨 Use ${c.route_policy.remedy_label || 'the supported route'}` : '⚙️ Configure'}
                      </a>
                    </div>
                    {autoMsg[s.id] && <div style={{ fontSize: 11, color: autoMsg[s.id].startsWith('❌') ? '#b91c1c' : 'var(--text2)', marginTop: 6 }}>{autoMsg[s.id]}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text3)', borderTop: '1px solid var(--border)' }}>
          A sweep marked 🚫 has its automatic portal login switched off by config — that is a decision, not a fault, and the row says which route carries its data instead. Each portal pulls its own date range — the period selector sets the target.
        </div>
      </div>

      {/* Upload history */}
      <div className="card" style={{ marginBottom: 20, padding: 0, overflow: 'hidden' }}>
        <button onClick={() => setShowHistory(v => !v)} style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '12px 16px', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600, fontSize: 14, color: 'var(--text1)', textAlign: 'left' }}>
          <span style={{ transform: showHistory ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>▸</span>
          📋 Upload history <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 13 }}>({history.length}{history.length === 200 ? '+' : ''} files)</span>
        </button>
        {showHistory && (
          <div style={{ borderTop: '1px solid var(--border)', maxHeight: 320, overflowY: 'auto' }}>
            {history.length === 0 ? <div style={{ padding: 16, color: 'var(--text3)', fontSize: 13 }}>No uploads recorded yet.</div> : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}><tbody>
                {history.map(h => { const meta = PERIOD_META[h.file_type]; return (
                  <tr key={h.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 14px', whiteSpace: 'nowrap' }}><span style={{ marginRight: 6 }}>{meta?.icon || '📄'}</span>{kinds.labelFor(h.file_type, meta?.label || h.file_type)}</td>
                    <td style={{ padding: '8px 14px', color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h.period || '—'}</td>
                    <td style={{ padding: '8px 14px', color: 'var(--text3)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.filename || ''}</td>
                    <td style={{ padding: '8px 14px', color: 'var(--text2)', textAlign: 'right', whiteSpace: 'nowrap' }}>{h.rows_saved.toLocaleString()} rows</td>
                    <td style={{ padding: '8px 14px', color: 'var(--text3)', whiteSpace: 'nowrap', textAlign: 'right' }}>{fmtWhen(h.uploaded_at)}</td>
                  </tr>
                )})}
              </tbody></table>
            )}
          </div>
        )}
      </div>

      <div style={{ fontWeight: 700, fontSize: 14, margin: '0 0 10px' }}>📁 Manual upload</div>
      {/* WHAT DECIDES THE TILES BELOW — said out loud. The registry's declaration, what it withheld and
          why, and whether it is the live table or the shipped mirror (mig 1010 not yet applied). */}
      {!kinds.loaded ? (
        <div style={{ color: 'var(--text3)', fontSize: 12, margin: '0 0 12px' }}>Reading which report kinds this company may upload…</div>
      ) : kinds.error ? (
        <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#991b1b', margin: '0 0 12px' }}>
          ⚠️ The report-kind registry could not be read ({kinds.error}) — the upload tiles are withheld rather than guessed. Reload, or ask the platform team.
        </div>
      ) : (
        <div style={{ color: 'var(--text3)', fontSize: 12, margin: '0 0 12px' }}>
          Offered for {kinds.declaration?.pos?.length ? <>POS <b>{kinds.declaration.pos.join(' / ')}</b></> : 'no declared POS'} · {kinds.declaration?.carriers?.length ? <>carrier <b>{kinds.declaration.carriers.join(' / ')}</b></> : 'no declared carrier'}
          {!kinds.ready && <> · registry table not applied yet — showing the house defaults ({kinds.payload?.migration})</>}
          {kinds.withheld && <> · {kinds.withheld}</>}
        </div>
      )}
      {/* If an ingest journal is missing (mig 202 / 007 not run on this deployment) the "Last upload"
          lines below are INCOMPLETE — say so rather than letting a tile read as "never uploaded". */}
      {lastHint && (
        <div style={{ background: '#fffbeb', border: '1px solid #fcd34d', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#92400e', margin: '0 0 12px' }}>
          ⚠️ Last-upload history is incomplete on this deployment — {lastHint}
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {periodTiles.map(({ id, label, icon, required, desc, mode }) => {
          const status = statuses[id] || 'idle'; const msg = messages[id] || ''; const prior = lastUpload(id)
          // "has data already" for the BUTTON wording = anything this report ever ingested (not just the
          // selected period) — a day-grain feed has no period badge at all.
          const everLanded = !!prior || !!lastData[id]?.last_at
          return (
            <div key={id} className="card" style={{ border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : status === 'warn' ? '1px solid #fcd34d' : undefined, background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : status === 'warn' ? '#fffbeb' : undefined }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{kinds.labelFor(id, label)}</span>
                    {required && <span style={{ fontSize: 10, background: '#fee2e2', color: '#dc2626', padding: '1px 6px', borderRadius: 999, fontWeight: 600 }}>Required</span>}
                    {prior && <span style={{ fontSize: 10, background: '#dcfce7', color: '#15803d', padding: '1px 7px', borderRadius: 999, fontWeight: 600 }}>✓ Uploaded</span>}
                  </div>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 6px' }}>{desc}</div>
                  {/* What THIS report's upload does to stored data — mirrors the backend's write path. */}
                  <div style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>{MODE_UI[mode].explain}</div>
                  {status === 'uploading' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}><div className="spinner" />Uploading...</div>
                  ) : (
                    <label style={{ cursor: 'pointer' }}>
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>{modeVerb(mode, everLanded)}</div>
                      <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(id, f) }} />
                    </label>
                  )}
                  {/* When the last SET OF DATA landed (any path: manual, email sweep, portal pull). */}
                  <LastUploadLine rec={lastData[id]} loaded={lastLoaded} />
                  {msg && <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : status === 'warn' ? '#b45309' : '#dc2626' }}>{msg}</div>}
                  {/* Honest amber panel: WHY an upload saved 0 rows (X-report parser forensics,
                      price-guard refusal, shrink warning). Renders nothing on a clean save. */}
                  <UploadGuardBanner outcome={outcomes[id] || null} />
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Module uploads (files that load into other modules) ──────────── */}
      <div style={{ fontWeight: 700, fontSize: 14, margin: '24px 0 10px' }}>
        📦 Module uploads <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— files that feed the asset, Distributor, hotsheet &amp; daily-closing modules</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {moduleTiles.map(entry => {
          const status = statuses[entry.id] || 'idle'; const msg = messages[entry.id] || ''
          return (
            <div key={entry.id} className="card" style={{ border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : undefined, background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : undefined }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{entry.icon}</span>
                <div style={{ flex: 1 }}>
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{kinds.labelFor(entry.id, entry.label)}</span>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 6px' }}>{entry.desc}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>{MODE_UI[entry.mode].explain}</div>
                  {entry.needsDate && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                      <label style={{ fontSize: 12, color: 'var(--text2)' }}>Effective date:</label>
                      <input type="date" className="input" style={{ fontSize: 12, width: 160 }} value={modDate[entry.id] || ''} onChange={e => setModDate(p => ({ ...p, [entry.id]: e.target.value }))} />
                    </div>
                  )}
                  {status === 'uploading' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}><div className="spinner" />Uploading...</div>
                  ) : (
                    <label style={{ cursor: 'pointer' }}>
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>{modeVerb(entry.mode, !!moduleLast(entry)?.last_at)}</div>
                      <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleModuleUpload(entry, f) }} />
                    </label>
                  )}
                  <LastUploadLine rec={moduleLast(entry)} loaded={lastLoaded} tracked={entry.tracked !== false} />
                  {msg && <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : '#dc2626' }}>{msg}</div>}
                </div>
              </div>
            </div>
          )
        })}
        {linkTiles.map(link => (
          <a key={link.id} href={link.href} className="card" style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <span style={{ fontSize: 28 }}>{link.icon}</span>
              <div style={{ flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{kinds.labelFor(link.id, link.label)} <span style={{ fontSize: 11, color: 'var(--text3)' }}>↗</span></span>
                <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 0' }}>{link.desc}</div>
              </div>
            </div>
          </a>
        ))}
      </div>

      {/* ── Registry kinds captured as custom-import sheets (the POS email reports). Each posts through
          the SAME /upload/<report_key> capture as the built-in tiles, auto-provisioning its sheet on first
          upload. Period-scoped (re-uploading a period replaces it). WHICH of these show is the registry's
          answer — a kind whose applies-to POS is not the declared one is simply not a row here. */}
      {customTiles.length > 0 && (<>
      <div style={{ fontWeight: 700, fontSize: 14, margin: '24px 0 10px' }}>
        📥 {term('pos_system', 'POS')} email reports <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— upload the exports your POS emails here too</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {customTiles.map(rep => {
          const label = rep.custom_sheet_label || rep.label
          const status = statuses[label] || 'idle'; const msg = messages[label] || ''
          const landed = customTypes.find((c: any) => (c.label || '').trim().toLowerCase() === label.toLowerCase())
          return (
            <div key={rep.key} className="card" style={{ border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : status === 'warn' ? '1px solid #fcd34d' : undefined, background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : status === 'warn' ? '#fffbeb' : undefined }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>📥</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{rep.label}</span>
                    {(landed?.rows || 0) > 0 && <span style={{ fontSize: 10, background: '#dcfce7', color: '#15803d', padding: '1px 7px', borderRadius: 999, fontWeight: 600 }}>{Number(landed.rows).toLocaleString()} rows</span>}
                    <span style={{ fontSize: 10, color: 'var(--text3)' }}>{rep.provenance_text}</span>
                  </div>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 6px' }}>{rep.what_in_it}{rep.source_hint ? ` — ${rep.source_hint}` : ''}</div>
                  <div style={{ color: 'var(--text2)', fontSize: 12, margin: '0 0 10px' }}>Captured as-is; re-uploading a period replaces it (a cumulative MTD export is safe to re-upload).</div>
                  {status === 'uploading' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}><div className="spinner" />Uploading...</div>
                  ) : (
                    <label style={{ cursor: 'pointer' }}>
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>{landed?.rows ? '⬆️ Upload additional file' : '📂 Choose File'}</div>
                      <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) uploadCustomReport(rep, f) }} />
                    </label>
                  )}
                  {msg && <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : status === 'warn' ? '#b45309' : '#dc2626' }}>{msg}</div>}
                </div>
              </div>
            </div>
          )
        })}
      </div>
      </>)}

      {/* ── CARRIER REPORTS REGISTERED FOR THIS TENANT (owner 2026-09-12) ───────────────────────────
          THE DEAD END THIS FIXES. Every tile above is a HARDCODED entry in this file, and each posts
          to the legacy /upload/<file_type> capture. So a tenant who onboarded on a carrier whose
          reports are registered as `report_definitions` ROWS — which is the whole mechanism mig 291
          added, and what mig 1004/1005 seed — had no way to reach them from the Upload page. They
          fell back to a Custom Import, the rows landed in the generic JSONB capture, and nothing
          counted them. PR #230 made the tiles carrier-SCOPED; this makes a registered report
          REACHABLE, which is the other half.

          It is a LINK, not a fourth upload widget. These reports go through the mapped ingest
          (/commcalc/upload-mapped), which requires a human-confirmed column mapping first — three
          headers in a real carrier export lie about what they hold, so a file dropped here with no
          mapping step would land confidently wrong. The Implementation Wizard already owns that
          flow (detect → propose with sample values → confirm → import), so this points at it rather
          than growing a second copy of it.

          RULE TWO: nothing here names a carrier. The list, the labels and the ordering are all rows. */}
      {registryReports.filter(r => !PERIOD_META[r.report_key] && tileVisible(r.report_key, r.carrier_code || undefined)).length > 0 && (
        <>
          <div style={{ fontWeight: 700, fontSize: 14, margin: '24px 0 10px' }}>
            🧾 Your carrier&apos;s reports <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— registered for this tenant; mapped once, then imported</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
            {registryReports
              .filter(r => !PERIOD_META[r.report_key] && tileVisible(r.report_key, r.carrier_code || undefined))
              .map(r => (
                <a key={r.report_key} href="/commcalc/implementation" className="card"
                   style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}>
                  <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                    <span style={{ fontSize: 28 }}>📑</span>
                    <div style={{ flex: 1 }}>
                      <span style={{ fontWeight: 600, fontSize: 14 }}>{r.label || r.report_key} <span style={{ fontSize: 11, color: 'var(--text3)' }}>↗</span></span>
                      <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 0' }}>
                        Map this report&apos;s columns once in the Implementation Wizard, then import the file there.
                        {r.target_table ? <> Lands in <code style={{ fontSize: 11 }}>{r.target_table}</code>.</> : null}
                      </div>
                      <div style={{ color: 'var(--text3)', fontSize: 11, margin: '4px 0 0' }}>({r.report_key})</div>
                    </div>
                  </div>
                </a>
              ))}
          </div>
        </>
      )}
    </div>
  )
}
