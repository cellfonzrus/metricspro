'use client'
import { useState, useEffect, useRef } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

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

type CatStatus = { count: number; owed: number; reimbursed: number; fees: number }
type CatRow = {
  id: number
  store: string | null
  esn_imei: string | null
  phone_number: string | null
  device_model: string | null
  contract_type: string | null
  status: string | null
  date_sold: string | null
  sfid: string | null
  owed_to_vip: number | null
  reimbursement: number | null
  commissions: number | null
  selling_price: number | null
  notes: string | null
  vip_invoice_number: string | null
  vip_invoice_date: string | null
}
type CatDetail = {
  category: string
  total_in_category: number
  by_status: Record<string, CatStatus>
  rows: CatRow[]
  offset: number
  limit: number
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

const STATUS_COLORS: Record<string, string> = {
  'Paid In Full': '#059669',
  'Open': '#dc2626',
  'Partial': '#d97706',
}

function statusPill(status: string) {
  return (
    <span style={{
      background: STATUS_COLORS[status] ? STATUS_COLORS[status] + '20' : '#f3f4f6',
      color: STATUS_COLORS[status] || 'var(--text2)',
      borderRadius: 6, padding: '2px 8px', fontSize: 12, fontWeight: 600
    }}>{status}</span>
  )
}

const PAGE_SIZE = 100

export default function AssetPage() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  // Drill-down state
  const [openCat, setOpenCat] = useState<string | null>(null)
  const [detail, setDetail] = useState<CatDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => { loadSummary() }, [])

  async function loadSummary() {
    setLoading(true)
    try {
      const d = await api(`/api/v1/asset/summary?org_id=${ORG_ID}`)
      setSummary(d)
    } catch(e) { console.error(e) }
    setLoading(false)
  }

  async function toggleCategory(cat: string) {
    // Clicking the open category closes it
    if (openCat === cat) {
      setOpenCat(null)
      setDetail(null)
      return
    }
    setOpenCat(cat)
    setDetail(null)
    setDetailLoading(true)
    try {
      const d = await api(`/api/v1/asset/category-detail?org_id=${ORG_ID}&category=${encodeURIComponent(cat)}&limit=${PAGE_SIZE}&offset=0`)
      setDetail(d)
    } catch(e) {
      console.error(e)
    }
    setDetailLoading(false)
  }

  async function loadMore() {
    if (!detail || !openCat) return
    setLoadingMore(true)
    try {
      const nextOffset = detail.rows.length
      const d: CatDetail = await api(`/api/v1/asset/category-detail?org_id=${ORG_ID}&category=${encodeURIComponent(openCat)}&limit=${PAGE_SIZE}&offset=${nextOffset}`)
      setDetail({ ...d, rows: [...detail.rows, ...d.rows] })
    } catch(e) {
      console.error(e)
    }
    setLoadingMore(false)
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadMsg('')
    try {
      const form = new FormData()
      form.append('file', file)
      const res = await fetch(`https://metricspro-production.up.railway.app/api/v1/asset/upload?org_id=${ORG_ID}`, {
        method: 'POST', body: form,
      })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Upload failed')
      setUploadMsg(`✅ Imported ${d.rows_imported.toLocaleString()} rows`)
      setOpenCat(null)
      setDetail(null)
      await loadSummary()
    } catch(e: any) {
      setUploadMsg(`❌ ${e.message}`)
    }
    setUploading(false)
    if (fileRef.current) fileRef.current.value = ''
  }

  async function handleRefreshPrices() {
    setUploading(true); setUploadMsg('')
    try {
      const res = await fetch(`https://metricspro-production.up.railway.app/api/v1/asset/backfill-selling-price?org_id=${ORG_ID}`, { method: 'POST' })
      const d = await res.json()
      if (!res.ok) throw new Error(d.detail || 'Refresh failed')
      setUploadMsg(`✅ Priced ${(/*rows_priced*/d.rows_priced ?? 0).toLocaleString()} devices · ${(/*flags*/d.undercharge_flags_written ?? 0).toLocaleString()} undercharge flags`)
      setOpenCat(null); setDetail(null)
      await loadSummary()
    } catch (e: any) {
      setUploadMsg(`❌ ${e.message}`)
    }
    setUploading(false)
  }

  function buildPayload(): ExportPayload {
    const sheets: ExportPayload['sheets'] = []
    if (summary?.loaded) {
      sheets.push({ name: 'By Status', rows: Object.entries(summary.by_status).map(([status, d]) => ({ status, ...d })), columns: [
        { header:'Status', get:r=>r.status },
        { header:'Devices', get:r=>r.count, align:'right' },
        { header:'Open Balance', get:r=>r.owed, money:true },
        { header:'Reimbursed', get:r=>r.reimbursed, money:true },
        { header:'Fees', get:r=>r.fees, money:true },
      ]})
      sheets.push({ name: 'By Category', rows: Object.entries(summary.by_category).map(([category, d]) => ({ category, ...d })), columns: [
        { header:'Category', get:r=>r.category },
        { header:'Devices', get:r=>r.count, align:'right' },
        { header:'Open Balance', get:r=>r.owed, money:true },
        { header:'Fees', get:r=>r.fees, money:true },
      ]})
    }
    if (detail && openCat) {
      sheets.push({ name: (openCat || 'Devices').slice(0, 28), rows: detail.rows, columns: [
        { header:'Store', get:r=>r.store },
        { header:'IMEI/ESN', get:r=>r.esn_imei },
        { header:'Device', get:r=>r.device_model },
        { header:'Phone', get:r=>r.phone_number },
        { header:'Contract', get:r=>r.contract_type },
        { header:'Status', get:r=>r.status },
        { header:'Date Sold', get:r=> r.date_sold ? String(r.date_sold).slice(0,10) : '' },
        { header:'Owed', get:r=>r.owed_to_vip, money:true },
        { header:'Reimbursed', get:r=>r.reimbursement, money:true },
        { header:'Selling Price', get:r=>r.selling_price, money:true },
        { header:'Uncovered', get:r=> (r.selling_price==null ? '' : Math.max(0, (r.owed_to_vip||0)-(r.reimbursement||0)-(r.selling_price||0))), money:true },
        { header:'Fees', get:r=>r.commissions, money:true },
        { header:'VIP Invoice #', get:r=>r.vip_invoice_number },
        { header:'VIP Invoice Date', get:r=> r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '' },
      ]})
    }
    return { title: 'Asset Ledger', subtitle: openCat ? `Category: ${openCat}` : 'VIP/DDP device financing summary',
      filename: openCat ? `asset-${openCat.replace(/[^a-z0-9]+/gi,'-').toLowerCase()}` : 'asset-ledger', sheets }
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Asset Ledger</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            VIP/DDP device financing — rebate reconciliation & balance tracking
          </p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          {uploadMsg && <span style={{ fontSize: 13 }}>{uploadMsg}</span>}
          {summary?.loaded && <ExportButtons payload={buildPayload} />}
          {summary?.loaded && <SendReportButton reportKey="asset_ledger" filters={{}} />}
          <a className="btn" href="/commcalc/asset/owed-weekly" style={{ textDecoration: 'none' }}>📅 Weekly Owed to VIP</a>
          <a className="btn" href="/commcalc/asset/aging" style={{ textDecoration: 'none' }}>⏳ Inventory Aging</a>
          <a className="btn" href="/commcalc/asset/on-inventory" style={{ textDecoration: 'none' }}>🏪 On-Inventory by Store</a>
          <a className="btn" href="/commcalc/asset/dashboard" style={{ textDecoration: 'none' }}>🧾 Charges</a>
          <a className="btn" href="/commcalc/asset/charges/rma" style={{ textDecoration: 'none' }}>🔁 RMA</a>
          <button className="btn" onClick={handleRefreshPrices} disabled={uploading} title="Re-pull selling prices from sales and re-sync undercharge flags">
            🔄 Refresh prices &amp; flags
          </button>
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
                      <td style={{ padding: '10px 14px' }}>{statusPill(status)}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{d.count.toLocaleString()}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: d.owed > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(d.owed)}</td>
                      <td style={{ padding: '10px 14px', fontSize: 13 }}>{fmt(d.fees)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* By Category — click a row to drill down */}
            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
                📱 By Category <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— click a category to drill down</span>
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
                    .map(([cat, d], i) => {
                      const isOpen = openCat === cat
                      return (
                        <tr key={cat}
                          onClick={() => toggleCategory(cat)}
                          style={{
                            borderTop: '1px solid var(--border)',
                            background: isOpen ? 'var(--accent)' + '12' : (i % 2 === 0 ? 'transparent' : 'var(--surface2)'),
                            cursor: 'pointer',
                          }}>
                          <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 500 }}>
                            <span style={{ display: 'inline-block', width: 14, color: 'var(--text3)' }}>{isOpen ? '▾' : '▸'}</span>
                            {cat || '—'}
                          </td>
                          <td style={{ padding: '10px 14px', fontSize: 13 }}>{d.count.toLocaleString()}</td>
                          <td style={{ padding: '10px 14px', fontSize: 13, fontWeight: 600, color: d.owed > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(d.owed)}</td>
                          <td style={{ padding: '10px 14px', fontSize: 13 }}>{fmt(d.fees)}</td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Drill-down panel */}
          {openCat && (
            <div className="card" style={{ padding: 0, marginBottom: 24 }}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span>🔎 {openCat || '—'} {detail && <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>· {detail.total_in_category.toLocaleString()} devices</span>}</span>
                <button className="btn" style={{ fontSize: 12, padding: '4px 10px' }} onClick={() => { setOpenCat(null); setDetail(null) }}>✕ Close</button>
              </div>

              {detailLoading ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>Loading category…</div>
              ) : !detail ? (
                <div style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>No data.</div>
              ) : (
                <div style={{ padding: 18 }}>
                  {/* Status breakdown within this category */}
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>Status breakdown</div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 24 }}>
                    <thead>
                      <tr style={{ background: 'var(--surface2)' }}>
                        {['Status','Devices','Open Balance','Reimbursed','Fees'].map(h => (
                          <th key={h} style={{ textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(detail.by_status)
                        .sort((a,b) => b[1].owed - a[1].owed)
                        .map(([status, s], i) => (
                        <tr key={status} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                          <td style={{ padding: '8px 14px' }}>{statusPill(status)}</td>
                          <td style={{ padding: '8px 14px', fontSize: 13 }}>{s.count.toLocaleString()}</td>
                          <td style={{ padding: '8px 14px', fontSize: 13, fontWeight: 600, color: s.owed > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(s.owed)}</td>
                          <td style={{ padding: '8px 14px', fontSize: 13, color: '#059669' }}>{fmt(s.reimbursed)}</td>
                          <td style={{ padding: '8px 14px', fontSize: 13 }}>{fmt(s.fees)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>

                  {/* Device rows */}
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 10 }}>
                    Devices <span style={{ fontWeight: 400, color: 'var(--text3)' }}>({detail.rows.length.toLocaleString()} of {detail.total_in_category.toLocaleString()} shown)</span>
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
                      <thead>
                        <tr style={{ background: 'var(--surface2)' }}>
                          {['Store','ESN / IMEI','Device','Phone','Contract','Status','Date Sold','Owed','Reimbursed','Selling','Fees','VIP Invoice #','Invoice Date'].map(h => (
                            <th key={h} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {detail.rows.map((r, i) => (
                          <tr key={r.id} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, fontFamily: 'monospace' }}>{r.esn_imei || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.phone_number || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.contract_type || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.status ? statusPill(r.status) : '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.date_sold ? String(r.date_sold).slice(0,10) : '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600, color: (r.owed_to_vip || 0) > 0 ? '#dc2626' : 'var(--text2)' }}>{fmt(r.owed_to_vip || 0)}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, color: '#059669' }}>{fmt(r.reimbursement || 0)}</td>
                            {(() => {
                              const under = r.selling_price != null && (r.owed_to_vip || 0) - (r.reimbursement || 0) - (r.selling_price || 0) > 0.01
                              return (
                                <td title={under ? `Undercharge: cost ${fmt(r.owed_to_vip||0)} > reimbursed ${fmt(r.reimbursement||0)} + selling ${fmt(r.selling_price||0)}` : ''}
                                    style={{ padding: '8px 12px', fontSize: 12, fontWeight: under ? 700 : 400, color: under ? '#dc2626' : 'var(--text1)' }}>
                                  {r.selling_price == null ? '—' : fmt(r.selling_price)}{under ? ' ⚠️' : ''}
                                </td>
                              )
                            })()}
                            <td style={{ padding: '8px 12px', fontSize: 12 }}>{fmt(r.commissions || 0)}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, fontFamily: 'monospace' }}>{r.vip_invoice_number || '—'}</td>
                            <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.vip_invoice_date ? String(r.vip_invoice_date).slice(0,10) : '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {detail.rows.length < detail.total_in_category && (
                    <div style={{ textAlign: 'center', marginTop: 16 }}>
                      <button className="btn" onClick={loadMore} disabled={loadingMore}>
                        {loadingMore ? 'Loading…' : `Load more (${(detail.total_in_category - detail.rows.length).toLocaleString()} remaining)`}
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Recon callout */}
          <div style={{ background: '#fefce8', border: '1px solid #fde047', borderRadius: 10, padding: '14px 18px', fontSize: 13, color: '#92400e' }}>
            <strong>🔍 3-Way Rebate Reconciliation coming next:</strong> Cross-match Boost payments → VIP reimbursements → open balance to flag missing credits and over-billed devices.
          </div>
        </>
      )}
    </div>
  )
}