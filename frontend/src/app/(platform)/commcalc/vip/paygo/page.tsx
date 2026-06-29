'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'

type Pay = {
  vip_payment_id: number; batch_type: string; dealer: string | null
  created_on: string | null; invoice_count: number | null
  amount: number | null; amount_overdue: number | null; status: string | null; period: string | null
}
type Summary = {
  configured: boolean; detail?: string
  current: Pay | null; history: Pay[]
  totals: { current_owed: number; current_overdue: number; weeks: number; lifetime_paid: number }
}

function pretty(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function PaygoPage() {
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/commcalc/vip/paygo/summary?org_id=${ORG_ID}`)
      .then((d: any) => setData(d))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  function buildPayload(): ExportPayload {
    const rows = [...(data?.current ? [data.current] : []), ...(data?.history || [])]
    return {
      title: 'Distributor Asset Lending — PayGo Weekly Billing',
      subtitle: data?.current ? `Current week owed ${fmt(data.totals.current_owed)} (${pretty(data.current.created_on)})` : 'PayGo weekly ledger',
      filename: 'vip-paygo-weekly',
      sheets: [{
        name: 'Weekly PayGo',
        rows,
        columns: [
          { header: 'Week (Created On)', get: (r: Pay) => r.created_on ? String(r.created_on).slice(0, 10) : '' },
          { header: 'Type', get: (r: Pay) => r.batch_type },
          { header: 'Invoices', get: (r: Pay) => r.invoice_count, align: 'right' },
          { header: 'Amount', get: (r: Pay) => r.amount, money: true },
          { header: 'Overdue', get: (r: Pay) => r.amount_overdue, money: true },
          { header: 'Status', get: (r: Pay) => r.status },
        ],
      }],
    }
  }

  const card = (label: string, value: string, sub: string, color: string) => (
    <div className="card" style={{ padding: '18px 22px', borderTop: `3px solid ${color}` }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color, marginTop: 6 }}>{value}</div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/vip" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Distributor Invoices</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>📦 Distributor Asset Lending (PayGo)</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            What the Distributor bills for lent (Pay-As-You-Go) devices each week, scraped from the dealer portal. Each weekly batch is a group of invoices — the invoice numbers join to your Distributor invoices &amp; device IMEIs.
          </p>
        </div>
        {data?.configured && <ExportButtons payload={buildPayload} />}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !data?.configured ? (
        <div className="card" style={{ padding: 24, color: 'var(--text2)', fontSize: 14 }}>
          <strong>Asset-lending data not loaded yet.</strong>
          <div style={{ marginTop: 8 }}>
            Run migration <code>014_vip_paygo.sql</code> in Supabase, then on the{' '}
            <a href="/commcalc/vip/sweep">Distributor Auto-sweep</a> page tick <strong>Asset lending (PayGo)</strong>, Save, and Run now.
          </div>
          {data?.detail && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text3)' }}>({data.detail})</div>}
        </div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 24 }}>
            {card('Current Week Owed', fmt(data.totals.current_owed), data.current ? `${data.current.invoice_count ?? 0} invoices · ${pretty(data.current.created_on)}` : 'no pending batch', '#dc2626')}
            {card('Currently Overdue', fmt(data.totals.current_overdue), 'on the current batch', '#d97706')}
            {card('Weeks of History', String(data.totals.weeks), 'approved batches', '#2563eb')}
            {card('Lifetime Billed', fmt(data.totals.lifetime_paid), 'all approved weeks', '#059669')}
          </div>

          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              📅 Weekly PayGo Billing
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Week', 'Type', 'Invoices', 'Amount', 'Overdue', 'Status'].map((h, i) => (
                      <th key={h} style={{ textAlign: i >= 2 && i <= 4 ? 'right' : 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {[...(data.current ? [data.current] : []), ...data.history].map((r, i) => (
                    <tr key={r.vip_payment_id} style={{ borderTop: '1px solid var(--border)', background: r.batch_type === 'pending' ? '#fef2f2' : (i % 2 === 0 ? 'transparent' : 'var(--surface2)') }}>
                      <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>{pretty(r.created_on)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 12 }}>
                        {r.batch_type === 'pending'
                          ? <span style={{ color: '#dc2626', fontWeight: 700 }}>PENDING</span>
                          : <span style={{ color: 'var(--text3)' }}>approved</span>}
                      </td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right' }}>{(r.invoice_count ?? 0).toLocaleString()}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right', fontWeight: 700 }}>{fmt(r.amount || 0)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right', color: (r.amount_overdue || 0) > 0 ? '#d97706' : 'var(--text3)' }}>{fmt(r.amount_overdue || 0)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 12, color: 'var(--text2)' }}>{r.status || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
