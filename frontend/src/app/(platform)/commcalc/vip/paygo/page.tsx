'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { SetupNotice } from '@/lib/setupNotice'
import DrillDownPanel, {
  DrillTileLinks, useDrillTile, type DrillExtraColumn, type DrillPanel, type DrillRow,
} from '@/components/DrillDownPanel'
import { agreesToTheCent } from '@/lib/drill-narrow'

// WHERE "LIFETIME BILLED" CAME FROM (index §CORE-17.3p, batch 16).
//
// THE PANEL IS NOT NEW AND NEITHER IS THE FOLD. `GET /vip/paygo/summary` has attached
// `lending_drilldowns` since batch 12 (§CORE-17.3l) for the Asset Lending page, which reads the SAME
// endpoint — and this page threw the panels away, so one figure had evidence on one screen and none
// on the other. Wired, not re-declared: a second catalogue over the same weekly batches would be two
// numbers hoping to agree.
//
// `lib/drill-narrow` is DELIBERATELY NOT WIRED HERE and harness_batch16_drilldown.py §E fails the
// build if it appears: this page has no client-side filter at all, so there is no selection to
// narrow to and the server's fold already covers every row on screen.
//
// THE THREE OTHER TILES GET NO LINK, each for a ruling: "Weeks of history" is a COUNT whose list is
// the table below it, and "Current week owed" / "Currently overdue" are two columns of ONE stored
// batch row with nothing under them here (batch 10's leaf ruling).
const BATCH_DRILL_COLUMNS: DrillExtraColumn[] = [
  { header: 'Detail', get: (r: DrillRow) => String(r.fields?.['detail'] ?? '—'),
    sortValue: (r: DrillRow) => String(r.fields?.['detail'] ?? '') },
  { header: 'Overdue', get: (r: DrillRow) => (r.fields?.['overdue'] == null ? '—' : fmt(Number(r.fields['overdue']))),
    sortValue: (r: DrillRow) => Number(r.fields?.['overdue'] ?? 0) },
]

type Pay = {
  vip_payment_id: number; batch_type: string; dealer: string | null
  created_on: string | null; invoice_count: number | null
  amount: number | null; amount_overdue: number | null; status: string | null; period: string | null
}
type Summary = {
  configured: boolean; detail?: string
  current: Pay | null; history: Pay[]
  totals: { current_owed: number; current_overdue: number; weeks: number; lifetime_paid: number }
  drilldown?: Record<string, DrillPanel>
}

function pretty(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

export default function PaygoPage() {
  const [data, setData] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  // The tile reads the server's own figure, and the drill is offered only where the panel agrees
  // with it to the cent — the one home for that rule (lib/drill-narrow), never a local copy.
  const drill = useDrillTile(data?.drilldown || {},
                             (k, p) => k === 'lifetime_paid'
                                       && agreesToTheCent(p, data?.totals?.lifetime_paid))

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

  const card = (label: string, value: string, sub: string, color: string, drillNode?: React.ReactNode) => (
    <div className="card" style={{ padding: '18px 22px', borderTop: `3px solid ${color}` }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color, marginTop: 6 }}>{value}</div>
      <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>
      {drillNode}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/vip" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Distributor Invoices</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>📦 Distributor Asset Lending (PayGo)</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            What the Distributor bills for lent (Pay-As-You-Go) devices each week, scraped from the dealer portal. Each weekly batch is a group of invoices — the invoice numbers join to your Distributor invoices &amp; device IMEIs.
          </p>
        </div>
        {data?.configured && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !data?.configured ? (
        <div className="card" style={{ padding: 24, color: 'var(--text2)', fontSize: 14 }}>
          <strong>Asset-lending data not loaded yet.</strong>
          <div style={{ marginTop: 8 }}>
            <SetupNotice detail="014_vip_paygo.sql" /> Once it is, on the{' '}
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
            {card('Lifetime Billed', fmt(data.totals.lifetime_paid), 'all approved weeks', '#059669',
                  <DrillTileLinks drills={drill.dl('lifetime_paid', 'weekly batches behind this')}
                                  open={drill.open} onToggle={drill.toggle} />)}
          </div>

          {drill.panel && <div style={{ marginBottom: 20 }}>
            <DrillDownPanel panel={drill.panel} extraColumns={BATCH_DRILL_COLUMNS} />
          </div>}

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
                          ? <span style={{ color: 'var(--c-fg-dc2626)', fontWeight: 700 }}>PENDING</span>
                          : <span style={{ color: 'var(--text3)' }}>approved</span>}
                      </td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right' }}>{(r.invoice_count ?? 0).toLocaleString()}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right', fontWeight: 700 }}>{fmt(r.amount || 0)}</td>
                      <td style={{ padding: '9px 14px', fontSize: 13, textAlign: 'right', color: (r.amount_overdue || 0) > 0 ? 'var(--c-fg-d97706)' : 'var(--text3)' }}>{fmt(r.amount_overdue || 0)}</td>
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
