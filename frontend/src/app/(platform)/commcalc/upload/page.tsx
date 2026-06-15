'use client'
import { useState, useEffect, useCallback } from 'react'
import { ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

const FILE_TYPES = [
  { id: 'sales',          label: 'Sales Transactions',    icon: '🛍️', required: true,  desc: 'EPay Sales Transaction Details' },
  { id: 'daily_sales',    label: 'Daily Sales Upload',      icon: '📅', required: false, desc: 'Append daily transactions — no period wipe, deduped by Trans ID' },
  { id: 'payment_detail', label: 'Payment Detail',        icon: '💳', required: true,  desc: 'EPay Commission Payment Detail' },
  { id: 'dlar_rep',       label: 'DLAR Rep Report',       icon: '📊', required: true,  desc: 'Elevate Go Rep KPI Report' },
  { id: 'dlar_store',     label: 'DLAR Store Report',     icon: '🏪', required: false, desc: 'Elevate Go Store Level Data' },
  { id: 'mi_report',      label: 'MI & ATU Report',       icon: '💰', required: false, desc: 'Monthly Incentive + ATU Payout' },
  { id: 'catalog',        label: 'Product Catalog',       icon: '📱', required: false, desc: 'Device catalog with cost prices' },
  { id: 'master_cats',    label: 'Payment Categories',    icon: '🗂️', required: false, desc: 'Payment type → category mapping' },
  { id: 'comp_report',    label: 'Comprehensive Comp Report', icon: '🏦', required: false, desc: 'Boost store-level rebates & MDF' },
]

// Catalog + payment categories are not period-scoped (one global copy).
const PERIODLESS = new Set(['catalog', 'master_cats'])
const TYPE_META = Object.fromEntries(FILE_TYPES.map(t => [t.id, t]))

type UploadRecord = {
  id: string
  file_type: string
  period: string | null
  filename: string | null
  rows_saved: number
  uploaded_at: string
}

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

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

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/v1/commcalc/upload/history?org_id=${ORG_ID}&limit=200`)
      if (res.ok) setHistory(await res.json())
    } catch { /* history is best-effort; ignore */ }
  }, [])

  useEffect(() => { loadHistory() }, [loadHistory])

  // Most-recent prior upload of a given file type for the selected period.
  // Period-less files (catalog / categories) match regardless of period.
  function lastUpload(fileType: string): UploadRecord | undefined {
    return history.find(h =>
      h.file_type === fileType &&
      (PERIODLESS.has(fileType) || (!!period.trim() && !!h.period && h.period.includes(period.trim())))
    )
  }

  async function handleUpload(fileType: string, file: File) {
    if (!period.trim() && fileType !== 'daily_sales') { alert('Enter a period first'); return }
    setUploading(fileType)
    setStatuses(s => ({ ...s, [fileType]: 'uploading' }))

    const form = new FormData()
    form.append('file', file)

    try {
      const res = await fetch(
        `${API}/api/v1/commcalc/upload/${fileType}?${fileType !== 'daily_sales' ? 'period=' + encodeURIComponent(period) + '&' : ''}org_id=${ORG_ID}`,
        { method: 'POST', body: form }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Upload failed')
      setStatuses(s => ({ ...s, [fileType]: 'done' }))
      setMessages(m => ({ ...m, [fileType]: `✅ ${data.saved} rows saved` }))
      loadHistory()  // refresh badges + history menu from the server
    } catch (e: any) {
      setStatuses(s => ({ ...s, [fileType]: 'error' }))
      setMessages(m => ({ ...m, [fileType]: `❌ ${e.message}` }))
    }
    setUploading(null)
  }

  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Upload Files</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Upload monthly data files from EPay and DLAR
        </p>
      </div>

      {/* Period selector */}
      <div className="card" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 16 }}>
        <label style={{ fontWeight: 600, fontSize: 14 }}>Period:</label>
        <input
          className="input"
          style={{ width: 200 }}
          value={period}
          onChange={e => setPeriod(e.target.value)}
          placeholder="April 2026"
        />
        <span style={{ color: 'var(--text3)', fontSize: 13 }}>
          Uploads will clear and replace existing data for this period
        </span>
      </div>

      {/* DLAR auto-import banner */}
      <div style={{
        background: '#ecfdf5', border: '1px solid #a7f3d0', borderRadius: 10,
        padding: '12px 16px', marginBottom: 12, fontSize: 13, color: '#047857',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}>
        <span>🤖 <strong>DLAR Rep + Store now import automatically</strong> from boostelevatego.com — no manual upload needed.</span>
        <a href="/commcalc/dlar/sweep" className="btn" style={{ fontSize: 12, padding: '4px 12px', whiteSpace: 'nowrap' }}>⚙️ DLAR Auto-Import →</a>
      </div>

      {/* epay MI/ATU auto-sweep banner */}
      <div style={{
        background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10,
        padding: '12px 16px', marginBottom: 12, fontSize: 13, color: '#1d4ed8',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap',
      }}>
        <span>⚙️ <strong>epay MI + ATU auto-sweep</strong> (headless) — set up the portal login + schedule to replace the manual MI/comp upload.</span>
        <a href="/commcalc/epay/sweep" className="btn" style={{ fontSize: 12, padding: '4px 12px', whiteSpace: 'nowrap' }}>⚙️ epay Auto-Sweep →</a>
      </div>

      {/* Info banner */}
      <div style={{
        background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10,
        padding: '12px 16px', marginBottom: 16, fontSize: 13, color: '#1d4ed8',
      }}>
        💡 Upload order: Sales → Payment Detail → DLAR Rep → other files. After uploading, go to Dashboard and click <strong>Run Calculation</strong>.
      </div>

      {/* Upload history — collapsible "hidden" menu, newest first */}
      <div className="card" style={{ marginBottom: 20, padding: 0, overflow: 'hidden' }}>
        <button
          onClick={() => setShowHistory(v => !v)}
          style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: 8,
            padding: '12px 16px', background: 'none', border: 'none', cursor: 'pointer',
            fontWeight: 600, fontSize: 14, color: 'var(--text1)', textAlign: 'left',
          }}
        >
          <span style={{ transform: showHistory ? 'rotate(90deg)' : 'none', transition: 'transform .15s' }}>▸</span>
          📋 Upload history
          <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 13 }}>
            ({history.length}{history.length === 200 ? '+' : ''} {history.length === 1 ? 'file' : 'files'})
          </span>
        </button>
        {showHistory && (
          <div style={{ borderTop: '1px solid var(--border)', maxHeight: 320, overflowY: 'auto' }}>
            {history.length === 0 ? (
              <div style={{ padding: 16, color: 'var(--text3)', fontSize: 13 }}>
                No uploads recorded yet. Newly uploaded files will appear here, newest first.
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <tbody>
                  {history.map(h => {
                    const meta = TYPE_META[h.file_type]
                    return (
                      <tr key={h.id} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '8px 14px', whiteSpace: 'nowrap' }}>
                          <span style={{ marginRight: 6 }}>{meta?.icon || '📄'}</span>
                          {meta?.label || h.file_type}
                        </td>
                        <td style={{ padding: '8px 14px', color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h.period || '—'}</td>
                        <td style={{ padding: '8px 14px', color: 'var(--text3)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.filename || ''}</td>
                        <td style={{ padding: '8px 14px', color: 'var(--text2)', textAlign: 'right', whiteSpace: 'nowrap' }}>{h.rows_saved.toLocaleString()} rows</td>
                        <td style={{ padding: '8px 14px', color: 'var(--text3)', whiteSpace: 'nowrap', textAlign: 'right' }}>{fmtWhen(h.uploaded_at)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {/* File upload cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {FILE_TYPES.map(({ id, label, icon, required, desc }) => {
          const status = statuses[id] || 'idle'
          const msg = messages[id] || ''
          const prior = lastUpload(id)
          return (
            <div key={id} className="card" style={{
              border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : undefined,
              background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : undefined,
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{label}</span>
                    {required && <span style={{ fontSize: 10, background: '#fee2e2', color: '#dc2626', padding: '1px 6px', borderRadius: 999, fontWeight: 600 }}>Required</span>}
                    {prior && (
                      <span style={{ fontSize: 10, background: '#dcfce7', color: '#15803d', padding: '1px 7px', borderRadius: 999, fontWeight: 600 }}>
                        ✓ Uploaded
                      </span>
                    )}
                  </div>
                  <div style={{ color: 'var(--text3)', fontSize: 12, margin: '2px 0 10px' }}>{desc}</div>

                  {status === 'uploading' ? (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}>
                      <div className="spinner" />
                      Uploading...
                    </div>
                  ) : (
                    <label style={{ cursor: 'pointer' }}>
                      <div className="btn btn-secondary" style={{ display: 'inline-flex' }}>
                        📂 {prior ? 'Replace File' : 'Choose File'}
                      </div>
                      <input
                        type="file"
                        accept=".xlsx,.xls,.csv"
                        style={{ display: 'none' }}
                        onChange={e => {
                          const file = e.target.files?.[0]
                          if (file) handleUpload(id, file)
                        }}
                      />
                    </label>
                  )}

                  {/* Persistent "already uploaded" line — survives reloads (from the server). */}
                  {prior && status !== 'done' && (
                    <div style={{ marginTop: 8, fontSize: 12, color: '#15803d' }}>
                      ✓ Uploaded {fmtWhen(prior.uploaded_at)} · {prior.rows_saved.toLocaleString()} rows
                      {PERIODLESS.has(id) && prior.period ? ` · ${prior.period}` : ''}
                    </div>
                  )}

                  {msg && (
                    <div style={{ marginTop: 8, fontSize: 12, color: status === 'done' ? '#16a34a' : '#dc2626' }}>
                      {msg}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
