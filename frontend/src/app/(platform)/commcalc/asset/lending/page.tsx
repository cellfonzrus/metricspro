'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// Asset Lending (VIP PayGo) report — the weekly lent-device billing ledger. Data comes from the VIP
// PayGo sweep (commcalc.vip_paygo_payments) via /commcalc/vip/paygo/summary; this presents it as an
// asset-ledger report: outstanding now, lifetime billed, by-year + by-month rollups, the full weekly
// batch list (filterable by year), and Excel/PDF export. Read-only.

type Batch = { vip_payment_id: number; batch_type: string; dealer?: string; created_on: string
  invoice_count: number; amount: number; amount_overdue?: number | null; status?: string | null; period?: string }

const yearOf = (d?: string) => (d || '').slice(0, 4)

export default function AssetLendingPage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [yearFilter, setYearFilter] = useState('')
  // Funding recon: connect these billed weeks to HOW they were paid (own vs borrowed account),
  // recorded against the consignment distributor (VIP) in commcalc.distributor_payments.
  const [dist, setDist] = useState<any>(null)
  const [funds, setFunds] = useState<any>(null)
  const [form, setForm] = useState<any>({ pay_date: '', period: '', amount: '', funding_source: 'own', account_label: '', ref: '' })
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setLoading(true)
    api('/api/v1/commcalc/vip/paygo/summary')
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }, [])

  const loadFunds = (id: string) => api(`/api/v1/commcalc/distributor-payments?distributor_id=${id}`).then(setFunds).catch(() => {})
  useEffect(() => {
    api('/api/v1/commcalc/distributors').then((r: any) => {
      const d = (r?.distributors || []).find((x: any) => x.has_asset_lending || x.portal_provider === 'vip' || (x.name || '').toUpperCase() === 'VIP')
      setDist(d || null)
      if (d?.id) loadFunds(d.id)
    }).catch(() => {})
  }, [])

  async function addFunding() {
    if (!dist?.id) return
    setBusy(true)
    try {
      await api('/api/v1/commcalc/distributor-payments', { method: 'POST', body: JSON.stringify({ ...form, distributor_id: dist.id, amount: Number(form.amount) || 0 }) })
      setForm({ pay_date: '', period: '', amount: '', funding_source: 'own', account_label: '', ref: '' })
      loadFunds(dist.id)
    } catch { /* ignore */ } finally { setBusy(false) }
  }

  const current: Batch | null = data?.current || null
  const history: Batch[] = data?.history || []
  const totals = data?.totals || {}
  // All batches newest-first (pending row on top), for the ledger + export.
  const allBatches: Batch[] = useMemo(() => (current ? [current, ...history] : history), [current, history])

  const byYear = useMemo(() => {
    const m: Record<string, { weeks: number; devices: number; billed: number }> = {}
    for (const b of history) {
      const y = yearOf(b.created_on); (m[y] ||= { weeks: 0, devices: 0, billed: 0 })
      m[y].weeks++; m[y].devices += b.invoice_count || 0; m[y].billed += Number(b.amount || 0)
    }
    return Object.entries(m).sort((a, b) => b[0].localeCompare(a[0]))
  }, [history])

  const byMonth = useMemo(() => {
    const m: Record<string, { devices: number; billed: number; key: string }> = {}
    for (const b of history) {
      if (yearFilter && yearOf(b.created_on) !== yearFilter) continue
      const k = b.period || b.created_on?.slice(0, 7) || '—'
      ;(m[k] ||= { devices: 0, billed: 0, key: b.created_on || '' })
      m[k].devices += b.invoice_count || 0; m[k].billed += Number(b.amount || 0)
      if ((b.created_on || '') > m[k].key) m[k].key = b.created_on || ''
    }
    return Object.entries(m).sort((a, b) => b[1].key.localeCompare(a[1].key))
  }, [history, yearFilter])

  const years = useMemo(() => Array.from(new Set(history.map(b => yearOf(b.created_on)))).sort().reverse(), [history])
  const rows = useMemo(() => allBatches.filter(b => !yearFilter || yearOf(b.created_on) === yearFilter), [allBatches, yearFilter])
  const lifetimeDevices = history.reduce((s, b) => s + (b.invoice_count || 0), 0)
  const grandTotal = (totals.lifetime_paid || 0) + (totals.current_owed || 0)
  const dealer = current?.dealer || history[0]?.dealer || ''

  function buildPayload(): ExportPayload {
    return {
      title: 'Asset Lending (Distributor PayGo)', subtitle: `${dealer}${yearFilter ? ` · ${yearFilter}` : ''}`,
      filename: `asset-lending${yearFilter ? `-${yearFilter}` : ''}`,
      sheets: [
        { name: 'Weekly batches', rows, columns: [
          { header: 'Week of', get: (r: Batch) => r.created_on },
          { header: 'Period', get: (r: Batch) => r.period || '' },
          { header: 'Type', get: (r: Batch) => r.batch_type === 'pending' ? 'PENDING' : 'approved' },
          { header: 'Devices', get: (r: Batch) => r.invoice_count },
          { header: 'Amount', get: (r: Batch) => Number(r.amount || 0), money: true },
          { header: 'Overdue', get: (r: Batch) => Number(r.amount_overdue || 0), money: true },
          { header: 'Status', get: (r: Batch) => r.status || '' },
          { header: 'Batch ID', get: (r: Batch) => r.vip_payment_id },
        ] },
        { name: 'By year', rows: byYear.map(([y, v]) => ({ y, ...v })), columns: [
          { header: 'Year', get: (r: any) => r.y },
          { header: 'Weeks', get: (r: any) => r.weeks },
          { header: 'Devices', get: (r: any) => r.devices },
          { header: 'Billed', get: (r: any) => r.billed, money: true },
        ] },
      ],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📲 Asset Lending (Distributor PayGo)</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Weekly lent-device billing ledger from the Distributor PayGo portal{dealer ? <> — <strong>{dealer}</strong></> : null}.
            The <strong>pending</strong> batch is the current week owed; approved batches are settled weekly bills.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <select className="select" value={yearFilter} onChange={e => setYearFilter(e.target.value)}>
            <option value="">All years</option>
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          {allBatches.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : data?.configured === false ? (
        <div className="card" style={{ padding: 16, color: 'var(--text2)' }}>Distributor PayGo lending isn&apos;t configured yet (no batches found).</div>
      ) : (
        <div style={{ display: 'grid', gap: 16 }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            <Tile label={`Outstanding now${current?.created_on ? ` — wk ${current.created_on}` : ''}`} value={fmt(totals.current_owed || 0)} accent="#b45309" />
            <Tile label="Devices on the open week" value={current?.invoice_count ?? 0} />
            <Tile label={`Lifetime billed (${totals.weeks || 0} wks)`} value={fmt(totals.lifetime_paid || 0)} accent="#15803d" />
            <Tile label="Devices billed (lifetime)" value={lifetimeDevices.toLocaleString()} />
            <Tile label="Grand total (incl. open week)" value={fmt(grandTotal)} />
          </div>

          {/* funding recon — how these bills were paid (own vs borrowed account) */}
          <div className="card" style={{ padding: 16 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>💵 How these bills were funded</div>
              {dist
                ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>recorded against distributor <strong>{dist.name}</strong> ({dist.arrangement})</span>
                : <span style={{ fontSize: 12, color: '#b45309' }}>No consignment distributor found — add one on the Distributors page to track funding.</span>}
              {funds?.totals && (
                <span style={{ fontSize: 13, marginLeft: 'auto' }}>
                  funded so far: <strong style={{ color: '#15803d' }}>own {fmt(funds.totals.own)}</strong> · <strong style={{ color: '#b45309' }}>borrowed {fmt(funds.totals.borrowed)}</strong> · total {fmt(funds.totals.total)}
                  {' '}<span style={{ color: 'var(--text3)' }}>vs {fmt(totals.lifetime_paid || 0)} billed</span>
                </span>
              )}
            </div>
            {dist && (
              <>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
                  <input className="select" style={{ width: 130 }} type="date" value={form.pay_date} onChange={e => setForm({ ...form, pay_date: e.target.value })} />
                  <input className="select" style={{ width: 110 }} placeholder="Period" value={form.period} onChange={e => setForm({ ...form, period: e.target.value })} />
                  <input className="select" style={{ width: 100 }} type="number" placeholder="Amount" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} />
                  <select className="select" value={form.funding_source} onChange={e => setForm({ ...form, funding_source: e.target.value })}>
                    <option value="own">Own account</option><option value="borrowed">Borrowed account</option>
                  </select>
                  <input className="select" style={{ width: 130 }} placeholder="Account label" value={form.account_label} onChange={e => setForm({ ...form, account_label: e.target.value })} />
                  <input className="select" style={{ width: 110 }} placeholder="Ref / batch ID" value={form.ref} onChange={e => setForm({ ...form, ref: e.target.value })} />
                  <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={busy} onClick={addFunding}>Record payment</button>
                </div>
                {funds?.payments?.length > 0 && (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
                      <thead><tr style={{ fontSize: 11, color: 'var(--text2)' }}>{['Date', 'Period', 'Amount', 'Funding', 'Account', 'Ref'].map(h => <th key={h} style={{ textAlign: 'left', padding: '5px 8px' }}>{h}</th>)}</tr></thead>
                      <tbody>
                        {funds.payments.slice(0, 12).map((p: any) => (
                          <tr key={p.id} style={{ borderTop: '1px solid var(--border)' }}>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>{p.pay_date || '—'}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>{p.period || '—'}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12, fontWeight: 600 }}>{fmt(p.amount)}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12, color: p.funding_source === 'borrowed' ? '#b45309' : '#15803d' }}>{p.funding_source}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>{p.account_label || '—'}</td>
                            <td style={{ padding: '5px 8px', fontSize: 12 }}>{p.ref || '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
            {/* by year */}
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By year (billed)</div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Year</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Weeks</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Devices</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Billed</th>
                </tr></thead>
                <tbody>
                  {byYear.map(([y, v]) => (
                    <tr key={y} onClick={() => setYearFilter(yearFilter === y ? '' : y)} style={{ borderTop: '1px solid var(--border)', cursor: 'pointer', background: yearFilter === y ? 'var(--surface2)' : undefined }}>
                      <td style={{ padding: '7px 12px', fontWeight: 600 }}>{y}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right' }}>{v.weeks}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right' }}>{v.devices.toLocaleString()}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 600 }}>{fmt(v.billed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* by month */}
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>By month{yearFilter ? ` — ${yearFilter}` : ''} (billed)</div>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>Month</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Devices</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Billed</th>
                </tr></thead>
                <tbody>
                  {byMonth.slice(0, 14).map(([m, v]) => (
                    <tr key={m} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '7px 12px', fontWeight: 600 }}>{m}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right' }}>{v.devices.toLocaleString()}</td>
                      <td style={{ padding: '7px 12px', textAlign: 'right', fontWeight: 600 }}>{fmt(v.billed)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* full weekly ledger */}
          <div className="card" style={{ padding: 0, overflow: 'auto' }}>
            <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>
              Weekly batches{yearFilter ? ` — ${yearFilter}` : ''} ({rows.length}) — newest first
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
              <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Week of</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Period</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Type</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Devices</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Amount</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Overdue</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Status</th>
              </tr></thead>
              <tbody>
                {rows.map(b => (
                  <tr key={b.vip_payment_id} style={{ borderTop: '1px solid var(--border)', background: b.batch_type === 'pending' ? '#fffbeb' : undefined }}>
                    <td style={{ padding: '6px 12px', fontSize: 13, fontWeight: 600 }}>{b.created_on}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, color: 'var(--text2)' }}>{b.period || '—'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12 }}>{b.batch_type === 'pending' ? <span style={{ color: '#b45309', fontWeight: 600 }}>PENDING</span> : 'approved'}</td>
                    <td style={{ padding: '6px 12px', fontSize: 13, textAlign: 'right' }}>{b.invoice_count}</td>
                    <td style={{ padding: '6px 12px', fontSize: 13, textAlign: 'right', fontWeight: 600 }}>{fmt(b.amount)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 13, textAlign: 'right', color: (b.amount_overdue || 0) > 0 ? '#b91c1c' : 'var(--text3)' }}>{fmt(b.amount_overdue || 0)}</td>
                    <td style={{ padding: '6px 12px', fontSize: 12, color: b.status === 'Failed' ? '#b91c1c' : 'var(--text2)' }}>{b.status || (b.batch_type === 'pending' ? 'open' : '')}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No lending batches{yearFilter ? ` in ${yearFilter}` : ''}.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

function Tile({ label, value, accent }: { label: string; value: any; accent?: string }) {
  return (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: accent || 'var(--text)' }}>{value}</div>
    </div>
  )
}
