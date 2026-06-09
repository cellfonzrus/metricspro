'use client'
import { useState, useEffect, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'

type Summary = {
  loaded: boolean
  total_rows: number
  total_fees: number
  total_open_balance: number
  total_reimbursed: number
  total_owed_alltime: number
  on_inventory: number
  by_status: Record<string, { count: number; owed: number; reimbursed: number; fees: number }>
  by_category: Record<string, { count: number; owed: number; fees: number }>
}

function StatCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="card" style={{ padding: '20px 24px' }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, color: color || 'var(--text1)' }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

export default function AssetPage() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => { loadSummary() }, [])

  async function loadSummary() {
    setLoading(true)
    try {
      const d = await api(`/api/v1/asset/summary?org_id=${ORG_ID}`)
      setSummary(d)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadMsg('')
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/v1/asset/upload?org_id=${ORG_ID}`, {
        method: 'POST', body: form,
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Upload failed')
      setUploadMsg(`✅ Imported ${d.rows_imported.toLocaleString()} rows`)
      await loadSummary()
    } catch(e: any) {
      setUploadMsg(`❌ ${e.message}`)
    }
    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
  }

  const STATUS_COLORS: Record<string, string> = {
    'Paid In Full': '#059669',
    'Open': '#dc2626',
    'Partial': '#d97706',
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Asset Ledger</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            VIP/DDP device financing — rebate reconciliation & balance tracking
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {uploadMsg && <span style={{ fontSize: 13 }}>{uploadMsg}</span>}
          <button className="btn btn-primary" onClick={() => fileRef.current?.click()} disabled={uploading}>
            {uploading ? '⏳ Uploading…' : '📤 Upload Asset_Lending.xlsx'}
          </button>
          <input ref={fileRef} type="file" accept=".xlsx,.xls" style={{ display: 'none' }} onChange={handleUpload} />
        </div>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !summary?.loaded ? (
        <div className="card" style={{ textAlign: 'center', padding: 60 }}>
          <div style={{ fontSize: 40, marginBottom: 16 }}>📦</div>
          <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 8 }}>No asset data loaded</div>
          <div style={{ color: 'var(--text2)', fontSize: 14, marginBottom: 24 }}>Upload your Asset_Lending.xlsx to get started</div>
          <button className="btn btn-primary" onClick={() => fileRef.current?.click()}>
            📤 Upload Now
          </button>
        </div>
      ) : (
        <>
          {/* Top stat cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 16, marginBottom: 24 }}>
            <StatCard label="VIP Fees Collected" value={fmt(summary.total_fees)} sub={`${summary.total_rows.toLocaleString()} devices`} color="var(--accent)" />
            <StatCard label="Open Balance Owed" value={fmt(summary.total_open_balance)} sub="still owed to VIP" color="#dc2626" />
            <StatCard label="Total Reimbursed" value={fmt(summary.total_reimbursed)} sub="Boost → VIP credited" color="#059669" />
            <StatCard label="All-Time Owed" value={fmt(summary.total_owed_alltime)} sub="gross financed" />
            <StatCard label="On Inventory" value={fmt(summary.on_inventory)} sub="not yet sold" color="#d97706" />
          </div>

          {/* By Status + By Category side by side */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 24 }}>

            {/* By Status */}
            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
                📊 By Status
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Status','Devices','Open Balance','Fees'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(summary.by_status)
                    .sort((a,b) => b[1].owed - a[1].owed)
                    .map(([status, d], i) => (
                    <tr key={status} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '10px 14px' }}>
                        <span style={{
                          background: STATUS_COLORS[status] ? STATUS_COLORS[status] + '20' : '#f3f4f6',
                          color: STATUS_COLORS[status] || 'var(--text2)',
                          borderRadius: 6, padding: '2px 8px', fontSize: 12, fontWeight: 600
                        }}>{status}</span>
                      </td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{d.count.toLocaleString()}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: d.owed > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(d.owed)}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{fmt(d.fees)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* By Category */}
            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
                📱 By Category
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Category','Devices','Open Balance','Fees'].map(h => (
                      <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(summary.by_category)
                    .sort((a,b) => b[1].owed - a[1].owed)
                    .map(([cat, d], i) => (
                    <tr key={cat} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 500 }}>{cat || '—'}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{d.count.toLocaleString()}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: d.owed > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(d.owed)}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{fmt(d.fees)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Recon callout */}
          <div style={{ background: '#fefce8', border: '1px solid #fde047', borderRadius: 10, padding: '14px 18px', fontSize: 13, color: '#92400e' }}>
            <strong>🔍 3-Way Rebate Reconciliation coming next:</strong> Cross-match Boost payments → VIP reimbursements → open balance to flag missing credits and over-billed devices.
          </div>
        </>
      )}
    </div>
  )
}