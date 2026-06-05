'use client'
import { useState } from 'react'
import { ORG_ID } from '@/lib/client'

const FILE_TYPES = [
  { id: 'sales',          label: 'Sales Transactions',    icon: '🛍️', required: true,  desc: 'EPay Sales Transaction Details' },
  { id: 'payment_detail', label: 'Payment Detail',        icon: '💳', required: true,  desc: 'EPay Commission Payment Detail' },
  { id: 'dlar_rep',       label: 'DLAR Rep Report',       icon: '📊', required: true,  desc: 'Elevate Go Rep KPI Report' },
  { id: 'dlar_store',     label: 'DLAR Store Report',     icon: '🏪', required: false, desc: 'Elevate Go Store Level Data' },
  { id: 'mi_report',      label: 'MI & ATU Report',       icon: '💰', required: false, desc: 'Monthly Incentive + ATU Payout' },
  { id: 'catalog',        label: 'Product Catalog',       icon: '📱', required: false, desc: 'Device catalog with cost prices' },
  { id: 'master_cats',    label: 'Payment Categories',    icon: '🗂️', required: false, desc: 'Payment type → category mapping' },
]

export default function UploadPage() {
  const [period, setPeriod] = useState('April 2026')
  const [uploading, setUploading] = useState<string | null>(null)
  const [statuses, setStatuses] = useState<Record<string, 'idle'|'uploading'|'done'|'error'>>({})
  const [messages, setMessages] = useState<Record<string, string>>({})

  async function handleUpload(fileType: string, file: File) {
    if (!period.trim()) { alert('Enter a period first'); return }
    setUploading(fileType)
    setStatuses(s => ({ ...s, [fileType]: 'uploading' }))

    const form = new FormData()
    form.append('file', file)

    const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
    try {
      const res = await fetch(
        `${API}/api/v1/commcalc/upload/${fileType}?period=${encodeURIComponent(period)}&org_id=${ORG_ID}`,
        { method: 'POST', body: form }
      )
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Upload failed')
      setStatuses(s => ({ ...s, [fileType]: 'done' }))
      setMessages(m => ({ ...m, [fileType]: `✅ ${data.saved} rows saved` }))
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

      {/* Info banner */}
      <div style={{
        background: '#eff6ff', border: '1px solid #bfdbfe', borderRadius: 10,
        padding: '12px 16px', marginBottom: 20, fontSize: 13, color: '#1d4ed8',
      }}>
        💡 Upload order: Sales → Payment Detail → DLAR Rep → other files. After uploading, go to Dashboard and click <strong>Run Calculation</strong>.
      </div>

      {/* File upload cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
        {FILE_TYPES.map(({ id, label, icon, required, desc }) => {
          const status = statuses[id] || 'idle'
          const msg = messages[id] || ''
          return (
            <div key={id} className="card" style={{
              border: status === 'done' ? '1px solid #86efac' : status === 'error' ? '1px solid #fca5a5' : undefined,
              background: status === 'done' ? '#f0fdf4' : status === 'error' ? '#fef2f2' : undefined,
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <span style={{ fontSize: 28 }}>{icon}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{label}</span>
                    {required && <span style={{ fontSize: 10, background: '#fee2e2', color: '#dc2626', padding: '1px 6px', borderRadius: 999, fontWeight: 600 }}>Required</span>}
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
                        📂 Choose File
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
