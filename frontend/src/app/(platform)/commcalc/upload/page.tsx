'use client'
import { useState, useEffect, useCallback } from 'react'
import { ORG_ID, api, apiUpload } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const FILE_TYPES = [
  { id: 'sales',          label: 'Sales Transactions',    icon: '🛍️', required: true,  desc: 'EPay Sales Transaction Details (78-col, all columns)' },
  { id: 'daily_sales',    label: 'Daily Sales Upload',      icon: '📅', required: false, desc: 'Append daily transactions — no period wipe, deduped by Trans ID' },
  { id: 'payment_detail', label: 'Payment Detail',        icon: '💳', required: true,  desc: 'EPay Commission Payment Detail' },
  { id: 'dlar_rep',       label: 'DLAR Rep Report',       icon: '📊', required: true,  desc: 'Elevate Go Rep KPI Report' },
  { id: 'dlar_store',     label: 'DLAR Store Report',     icon: '🏪', required: false, desc: 'Elevate Go Store Level Data' },
  { id: 'mi_report',      label: 'MI & ATU Report',       icon: '💰', required: false, desc: 'Monthly Incentive + ATU Payout' },
  { id: 'catalog',        label: 'Product Catalog',       icon: '📱', required: false, desc: 'Device catalog with cost prices' },
  { id: 'master_cats',    label: 'Payment Categories',    icon: '🗂️', required: false, desc: 'Payment type → category mapping' },
  { id: 'comp_report',    label: 'Comprehensive Comp Report', icon: '🏦', required: false, desc: 'Boost store-level rebates & MDF' },
]
const PERIODLESS = new Set(['catalog', 'master_cats'])
const TYPE_META = Object.fromEntries(FILE_TYPES.map(t => [t.id, t]))

// Auto-import sources + the period granularities the user asked for, per source.
const AUTO_SOURCES = [
  { id: 'dlar', name: 'DLAR (Boost Elevate GO)', icon: '📊', desc: 'Store + Rep KPI reports',
    cfg: 'dlar/sweep/config', run: 'dlar/sweep/run-now', configure: '/commcalc/dlar/sweep',
    scopes: [{ v: 'mtd', l: 'Month-to-date' }, { v: 'full', l: 'Full month' }] },
  { id: 'epay', name: 'ePay Owner Portal', icon: '💰', desc: 'MI · ATU · Commission · Comprehensive · Reconciliation',
    cfg: 'epay/sweep/config', run: 'epay/sweep/run-now', configure: '/commcalc/epay/sweep',
    scopes: [{ v: 'daily', l: 'Daily' }, { v: 'mtd', l: 'Month-to-date' }, { v: 'full', l: 'Full month' }] },
  { id: 'b2b', name: 'b2bsoft (wsreports)', icon: '📦', desc: 'Inventory Aging · Sales Transaction',
    cfg: 'b2b/sweep/config', run: 'b2b/sweep/run-now', configure: '/accounts/inventory',
    scopes: [{ v: 'day', l: 'Single day' }, { v: 'month', l: 'Month' }, { v: 'custom', l: 'Custom range' }] },
  { id: 'vip', name: 'VIP Wireless portal', icon: '🧾', desc: 'Invoices · PayGo · Credit memos',
    cfg: 'vip/sweep/config', run: 'vip/sweep/run-now', configure: '/commcalc/vip/sweep',
    scopes: [{ v: 'recent', l: 'Recent (lookback)' }, { v: 'full', l: 'Full history' }] },
]

// Module uploads — files that load into other modules (their own endpoints, not the generic
// /commcalc/upload/{file_type}). Each posts a multipart file to its own endpoint.
const MODULE_UPLOADS = [
  { id: 'hotsheet',      label: 'Pricing Hotsheet',     icon: '🏷️', endpoint: 'commcalc/hotsheet/upload', needsDate: true,
    desc: 'Carrier promo pricing by device — powers the Hotsheet expected-vs-paid recon. Pick the effective date.' },
  { id: 'vip_workbook',  label: 'VIP Wireless Workbook', icon: '🧾', endpoint: 'commcalc/vip/upload', needsDate: false,
    desc: 'VIP scraper workbook (Invoices / Lines / Devices sheets). Full-replace of VIP history.' },
  { id: 'asset_ledger',  label: 'Asset Ledger',         icon: '📒', endpoint: 'asset/upload', needsDate: false,
    desc: 'Asset_Lending.xlsx — wipes & re-inserts all asset rows, then backfills market + flags.' },
  { id: 'daily_closing', label: 'Daily Closing Sheet',  icon: '🧮', endpoint: 'closing/upload', needsDate: false,
    desc: 'Google "Envelopes Data" export — one row per rep per day; idempotent per day.' },
]
// Structured (non-file) uploads that live on their own page — linked, not inlined here.
const MODULE_LINKS = [
  { id: 'b2b_inventory', label: 'b2bsoft Inventory', icon: '📦', href: '/commcalc/asset/inventory-recon',
    desc: 'On-hand inventory by store & category — structured entry/recon, not a single file. Opens its page.' },
]

type UploadRecord ={ id: string; file_type: string; period: string | null; filename: string | null; rows_saved: number; uploaded_at: string }

function fmtWhen(iso: string) {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function UploadPage() {
  const { period, setPeriod } = usePeriod()
  const [uploading, setUploading] = useState<string | null>(null)
  const [statuses, setStatuses] = useState<Record<string, 'idle'|'uploading'|'done'|'error'>>({})
  const [messages, setMessages] = useState<Record<string, string>>({})
  const [history, setHistory] = useState<UploadRecord[]>([])
  const [showHistory, setShowHistory] = useState(false)

  // auto-import panel state
  const [cfgs, setCfgs] = useState<Record<string, any>>({})
  const [scope, setScope] = useState<Record<string, string>>({})
  const [adate, setAdate] = useState<Record<string, string>>({})
  const [running, setRunning] = useState<Record<string, boolean>>({})
  const [autoMsg, setAutoMsg] = useState<Record<string, string>>({})
  const [modDate, setModDate] = useState<Record<string, string>>({})

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

  useEffect(() => { loadHistory(); loadCfgs() }, [loadHistory, loadCfgs])

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
    if (!period.trim() && fileType !== 'daily_sales') { alert('Enter the period this data is for first'); return }
    setUploading(fileType); setStatuses(s => ({ ...s, [fileType]: 'uploading' }))
    const form = new FormData(); form.append('file', file)
    try {
      const data = await apiUpload(
        `/api/v1/commcalc/upload/${fileType}?${fileType !== 'daily_sales' ? 'period=' + encodeURIComponent(period) + '&' : ''}org_id=${ORG_ID}`,
        form)
      setStatuses(s => ({ ...s, [fileType]: 'done' }))
      setMessages(m => ({ ...m, [fileType]: `✅ ${data.saved} rows saved` }))
      loadHistory()
    } catch (e: any) {
      setStatuses(s => ({ ...s, [fileType]: 'error' }))
      setMessages(m => ({ ...m, [fileType]: `❌ ${e.message}` }))
    }
    setUploading(null)
  }

  async function handleModuleUpload(entry: typeof MODULE_UPLOADS[number], file: File) {
    if (entry.needsDate && !(modDate[entry.id] || '').trim()) { alert('Pick an effective date for the hotsheet first'); return }
    setUploading(entry.id); setStatuses(s => ({ ...s, [entry.id]: 'uploading' }))
    const form = new FormData(); form.append('file', file)
    if (entry.needsDate) form.append('effective_date', modDate[entry.id])
    try {
      const data = await apiUpload(`/api/v1/${entry.endpoint}?org_id=${ORG_ID}`, form)
      const n = data.rows_uploaded ?? data.saved ?? data.rows_saved ?? data.inserted ?? data.count ?? data.rows
      setStatuses(s => ({ ...s, [entry.id]: 'done' }))
      setMessages(m => ({ ...m, [entry.id]: `✅ ${n != null ? Number(n).toLocaleString() + ' rows' : 'Uploaded'}` }))
      loadHistory()
    } catch (e: any) {
      setStatuses(s => ({ ...s, [entry.id]: 'error' }))
      setMessages(m => ({ ...m, [entry.id]: `❌ ${e.message}` }))
    }
    setUploading(null)
  }

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Data Imports</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>Auto-imports pull from the portals on a schedule; manual upload below for anything not automated.</p>
      </div>

      {/* Period — applies to manual uploads + the period auto-imports tag their data with */}
      <div className="card" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ fontWeight: 600, fontSize: 14 }}>Period:</label>
        <input className="input" style={{ width: 200 }} value={period} onChange={e => setPeriod(e.target.value)} placeholder="April 2026" />
        <span style={{ color: 'var(--text3)', fontSize: 13 }}>Which period this data is for. Manual uploads clear &amp; replace this period.</span>
      </div>

      {/* ── Unified Auto-Imports panel ─────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20, padding: 0, overflow: 'hidden' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 700, fontSize: 14 }}>
          🤖 Auto-Imports <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— pick the period to pull, then Run now (or let the schedule run it)</span>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <tbody>
            {AUTO_SOURCES.map(s => {
              const c = cfgs[s.id] || {}
              const stColor = c.last_status === 'ok' ? '#15803d' : c.last_status === 'error' ? '#b91c1c' : c.last_status === 'running' ? '#b45309' : 'var(--text3)'
              const needsDate = (scope[s.id] || s.scopes[0].v).match(/day|custom/)
              return (
                <tr key={s.id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px', verticalAlign: 'top', width: 270 }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{s.icon} {s.name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>{s.desc}</div>
                    <div style={{ fontSize: 11, marginTop: 4, color: stColor }}>
                      {c.has_credentials ? (c.enabled ? '● scheduled' : '○ creds set, schedule off') : '○ not configured'}
                      {c.last_status ? ` · last: ${c.last_status}` : ''}
                      {c.last_run_at ? ` · ${fmtWhen(c.last_run_at)}` : ''}
                    </div>
                    {(c.next_run_at || c.frequency) && (
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
                      <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={running[s.id] || !c.has_credentials} onClick={() => runAuto(s)}>
                        {running[s.id] ? '…' : '▶ Run now'}
                      </button>
                      <a href={s.configure} className="btn" style={{ fontSize: 12 }}>⚙️ Configure</a>
                    </div>
                    {autoMsg[s.id] && <div style={{ fontSize: 11, color: autoMsg[s.id].startsWith('❌') ? '#b91c1c' : 'var(--text2)', marginTop: 6 }}>{autoMsg[s.id]}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <div style={{ padding: '8px 16px', fontSize: 11, color: 'var(--text3)', borderTop: '1px solid var(--border)' }}>
          b2bsoft auto-fetch is pending login access; until then use its manual upload below. Each portal pulls its own date range — the period selector sets the target.
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
                {history.map(h => { const meta = TYPE_META[h.file_type]; return (
                  <tr key={h.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 14px', whiteSpace: 'nowrap' }}><span style={{ marginRight: 6 }}>{meta?.icon || '📄'}</span>{meta?.label || h.file_type}</td>
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
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {FILE_TYPES.map(({ id, label, icon, required, desc }) => {
          const status = statuses[id] || 'idle'; const msg = messages[id] || ''; const prior = lastUpload(id)
          return (
            <div key={id} className="card" style={{ border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : undefined, background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : undefined }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{label}</span>
                    {required && <span style={{ fontSize: 10, background: '#fee2e2', color: '#dc2626', padding: '1px 6px', borderRadius: 999, fontWeight: 600 }}>Required</span>}
                    {prior && <span style={{ fontSize: 10, background: '#dcfce7', color: '#15803d', padding: '1px 7px', borderRadius: 999, fontWeight: 600 }}>✓ Uploaded</span>}
                  </div>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 10px' }}>{desc}</div>
                  {status === 'uploading' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}><div className="spinner" />Uploading...</div>
                  ) : (
                    <label style={{ cursor: 'pointer' }}>
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>📂 {prior ? 'Replace File' : 'Choose File'}</div>
                      <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(id, f) }} />
                    </label>
                  )}
                  {prior && status !== 'done' && <div style={{ marginTop: 8, fontSize: 12, color: '#15803d' }}>✓ Uploaded {fmtWhen(prior.uploaded_at)} · {prior.rows_saved.toLocaleString()} rows{PERIODLESS.has(id) && prior.period ? ` · ${prior.period}` : ''}</div>}
                  {msg && <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : '#dc2626' }}>{msg}</div>}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── Module uploads (files that load into other modules) ──────────── */}
      <div style={{ fontWeight: 700, fontSize: 14, margin: '24px 0 10px' }}>
        📦 Module uploads <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— files that feed the asset, VIP, hotsheet &amp; daily-closing modules</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {MODULE_UPLOADS.map(entry => {
          const status = statuses[entry.id] || 'idle'; const msg = messages[entry.id] || ''
          return (
            <div key={entry.id} className="card" style={{ border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : undefined, background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : undefined }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{entry.icon}</span>
                <div style={{ flex: 1 }}>
                  <span style={{ fontWeight: 600, fontSize: 14 }}>{entry.label}</span>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 10px' }}>{entry.desc}</div>
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
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>📂 Choose File</div>
                      <input type="file" accept=".xlsx,.xls,.csv" style={{ display: 'none' }} onChange={e => { const f = e.target.files?.[0]; if (f) handleModuleUpload(entry, f) }} />
                    </label>
                  )}
                  {msg && <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : '#dc2626' }}>{msg}</div>}
                </div>
              </div>
            </div>
          )
        })}
        {MODULE_LINKS.map(link => (
          <a key={link.id} href={link.href} className="card" style={{ textDecoration: 'none', color: 'inherit', display: 'block' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
              <span style={{ fontSize: 28 }}>{link.icon}</span>
              <div style={{ flex: 1 }}>
                <span style={{ fontWeight: 600, fontSize: 14 }}>{link.label} <span style={{ fontSize: 11, color: 'var(--text3)' }}>↗</span></span>
                <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 0' }}>{link.desc}</div>
              </div>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
