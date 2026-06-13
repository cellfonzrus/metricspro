'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

type Totals = {
  invoices: number; sub_total: number; grand_total: number; fees_total: number
  shipping: number; discount: number; other_cost: number; other_deductions: number; tax: number
}
type StoreRow = {
  location: string; invoices: number; sub_total: number; grand_total: number
  shipping: number; discount: number; other_cost: number; other_deductions: number; tax: number
}
type Summary = { totals: Totals; fees_by_type: Record<string, number>; by_store: StoreRow[] }
type Invoice = {
  vip_id: number; invoice_number: string; order_number: string | null; location: string
  status: string; created_on: string | null; due_date: string | null
  sub_total: number; shipping: number; discount: number; other_cost: number
  other_deductions: number; tax: number; grand_total: number; period: string | null
}

const FEE_LABELS: Record<string, string> = {
  shipping: 'Shipping', discount: 'Discount', other_cost: 'Other Cost',
  other_deductions: 'Other Deductions', tax: 'Tax',
}
const d10 = (s: string | null) => (s ? String(s).slice(0, 10) : '—')
const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

export default function VipInvoicesPage() {
  const [period, setPeriod] = useState('')
  const [location, setLocation] = useState('')
  const [status, setStatus] = useState('')
  const [periods, setPeriods] = useState<string[]>([])
  const [locations, setLocations] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState('')

  useEffect(() => {
    api(`/api/v1/commcalc/vip/filter-options?org_id=${ORG_ID}`)
      .then((d: any) => { setPeriods(d.periods || []); setLocations(d.locations || []); setStatuses(d.statuses || []) })
      .catch(console.error)
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ org_id: ORG_ID })
      if (period) qs.set('period', period)
      if (location) qs.set('location', location)
      if (status) qs.set('status', status)
      const [s, inv] = await Promise.all([
        api(`/api/v1/commcalc/vip/summary?${qs.toString()}`),
        api(`/api/v1/commcalc/vip/invoices?${qs.toString()}`),
      ])
      setSummary(s); setInvoices(inv)
    } catch (e) { console.error(e) }
    setLoading(false)
  }, [period, location, status])

  useEffect(() => { load() }, [load])

  async function handleImport(file: File) {
    setImporting(true); setImportMsg('')
    const form = new FormData(); form.append('file', file)
    try {
      const res = await fetch(`${API}/api/v1/commcalc/vip/upload?org_id=${ORG_ID}`, { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.detail || 'Import failed')
      setImportMsg(`✅ ${data.invoices.toLocaleString()} invoices · ${data.lines.toLocaleString()} lines · ${data.devices.toLocaleString()} devices`)
      api(`/api/v1/commcalc/vip/filter-options?org_id=${ORG_ID}`)
        .then((d: any) => { setPeriods(d.periods || []); setLocations(d.locations || []); setStatuses(d.statuses || []) })
      load()
    } catch (e: any) {
      setImportMsg(`❌ ${e.message}`)
    }
    setImporting(false)
  }

  function buildPayload(): ExportPayload {
    const invCols: ExportColumn[] = [
      { header: 'Invoice #', get: r => r.invoice_number },
      { header: 'Date', get: r => d10(r.created_on) },
      { header: 'Store', get: r => r.location },
      { header: 'Status', get: r => r.status },
      { header: 'Subtotal', get: r => r.sub_total, money: true },
      { header: 'Shipping', get: r => r.shipping, money: true },
      { header: 'Discount', get: r => r.discount, money: true },
      { header: 'Other Cost', get: r => r.other_cost, money: true },
      { header: 'Other Deductions', get: r => r.other_deductions, money: true },
      { header: 'Tax', get: r => r.tax, money: true },
      { header: 'Grand Total', get: r => r.grand_total, money: true },
    ]
    const storeCols: ExportColumn[] = [
      { header: 'Store', get: r => r.location },
      { header: 'Invoices', get: r => r.invoices, align: 'right' },
      { header: 'Shipping', get: r => r.shipping, money: true },
      { header: 'Discount', get: r => r.discount, money: true },
      { header: 'Other Cost', get: r => r.other_cost, money: true },
      { header: 'Other Deductions', get: r => r.other_deductions, money: true },
      { header: 'Tax', get: r => r.tax, money: true },
      { header: 'Grand Total', get: r => r.grand_total, money: true },
    ]
    const filterLabel = [period || null, location || null, status || null].filter(Boolean).join(' · ') || 'All invoices'
    return {
      title: 'VIP Wireless Invoices',
      subtitle: filterLabel,
      filename: `vip-invoices${location ? '-' + location.replace(/[^a-z0-9]+/gi, '-').toLowerCase() : ''}`,
      sheets: [
        { name: 'Invoices', rows: invoices, columns: invCols },
        { name: 'Fees by Store', rows: summary?.by_store || [], columns: storeCols },
      ],
    }
  }

  const t = summary?.totals
  const tiles = t ? [
    { label: 'Invoices', val: t.invoices.toLocaleString(), color: 'var(--accent)' },
    { label: 'Subtotal', val: fmt(t.sub_total), color: '#0369a1' },
    { label: 'Fees (all types)', val: fmt(t.fees_total), color: '#d97706' },
    { label: 'Grand Total', val: fmt(t.grand_total), color: '#059669' },
  ] : []

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 VIP Wireless Invoices</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Scraped from the VIP dealer portal. Fees-by-type uses the invoice money buckets.
          </p>
        </div>
        {summary && <ExportButtons payload={buildPayload} />}
      </div>

      {/* Import workbook */}
      <div className="card" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Import VIP workbook</span>
        <span style={{ color: 'var(--text3)', fontSize: 12, flex: 1 }}>
          The <code>vip_invoices.xlsx</code> from tools/vip_scraper (Invoices / Lines / Devices). Full replace.
        </span>
        {importing ? (
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}>
            <span className="spinner" /> Importing…
          </span>
        ) : (
          <label style={{ cursor: 'pointer' }}>
            <span className="btn btn-secondary" style={{ display: 'inline-flex' }}>📂 Choose workbook</span>
            <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) handleImport(f) }} />
          </label>
        )}
        {importMsg && <div style={{ fontSize: 12, color: importMsg.startsWith('✅') ? '#16a34a' : '#dc2626', width: '100%' }}>{importMsg}</div>}
      </div>

      {/* Filters */}
      <div className="card" style={{ padding: 14, marginBottom: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)' }}>Filters:</span>
        <select style={selStyle} value={period} onChange={e => setPeriod(e.target.value)}>
          <option value="">All periods</option>
          {periods.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select style={selStyle} value={location} onChange={e => setLocation(e.target.value)}>
          <option value="">All stores</option>
          {locations.map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <select style={selStyle} value={status} onChange={e => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {statuses.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !summary || summary.totals.invoices === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No VIP invoices. Import the workbook above to load data.
        </div>
      ) : (
        <>
          {/* Summary tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 20 }}>
            {tiles.map(c => (
              <div key={c.label} className="card" style={{ padding: '18px 22px', borderTop: `3px solid ${c.color}` }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c.label}</div>
                <div style={{ fontSize: 24, fontWeight: 700, color: c.color, marginTop: 6 }}>{c.val}</div>
              </div>
            ))}
          </div>

          {/* Fees by type */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>Fees by Type</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 12 }}>
              {Object.keys(FEE_LABELS).map(k => (
                <div key={k} style={{ padding: '12px 14px', background: 'var(--surface2)', borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{FEE_LABELS[k]}</div>
                  <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>{fmt(summary.fees_by_type[k] || 0)}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Per-store fees */}
          <div className="card" style={{ padding: 0, marginBottom: 20 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              Fees & Totals by Store ({summary.by_store.length})
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store', 'Inv', 'Shipping', 'Discount', 'Other Cost', 'Other Ded.', 'Tax', 'Grand Total'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summary.by_store.map((s, i) => (
                    <tr key={s.location} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{s.location}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{s.invoices}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.shipping)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.discount)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.other_cost)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.other_deductions)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.tax)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 600 }}>{fmt(s.grand_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Invoice list */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              Invoices ({invoices.length.toLocaleString()})
            </div>
            <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Invoice #', 'Date', 'Store', 'Status', 'Subtotal', 'Fees', 'Grand Total'].map((h, i) => (
                      <th key={h} style={{ textAlign: i < 4 ? 'left' : 'right', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: 'var(--surface2)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {invoices.map((r, i) => {
                    const fees = (r.shipping || 0) + (r.discount || 0) + (r.other_cost || 0) + (r.other_deductions || 0) + (r.tax || 0)
                    return (
                      <tr key={r.vip_id} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                        <td style={{ padding: '8px 12px', fontSize: 12, fontFamily: 'monospace' }}>{r.invoice_number || '—'}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{d10(r.created_on)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.location || '—'}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.status || '—'}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(r.sub_total)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: fees ? '#d97706' : 'var(--text3)' }}>{fmt(fees)}</td>
                        <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 600 }}>{fmt(r.grand_total)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
