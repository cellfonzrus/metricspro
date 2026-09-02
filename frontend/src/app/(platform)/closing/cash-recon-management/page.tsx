'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

// Management one-screen cash reconciliation (owner directive 2026-09-02, verbatim): "for the
// management it should show what has been received as per the system in both cash pick up, epay
// pick up and the cash declared and the epay declared fields and the credit fields of what has
// been recorded by the POS reports, this will make it easy for the management to reconcile cash
// on one screen - again the employee is gated out of it, dm is gated out of it only market
// manager and above see it."
//
// The GATE is server-side and fail-closed (closing/billpay_pickup.can_see_cash_recon — the
// mig-434 pay-visibility posture: market manager and above; per-org override via
// storeops.tenants.cash_recon_visible_roles). This page renders the server's 403 message for
// gated roles — it never receives the data. Rows are additionally span-scoped through the same
// keyset every closing surface uses, so a market manager sees only their own span.
const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const cell: React.CSSProperties = { padding: '7px 9px', borderTop: '1px solid var(--border)', fontSize: 12.5, verticalAlign: 'middle', whiteSpace: 'nowrap' }
const th: React.CSSProperties = { textAlign: 'right', padding: '8px 9px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }

export default function CashReconManagementPage() {
  const [rangeMode, setRangeMode] = useState(false)
  const [date, setDate] = useState(localToday())
  const [rangeStart, setRangeStart] = useState(localToday())
  const [rangeEnd, setRangeEnd] = useState(localToday())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [denied, setDenied] = useState<string | null>(null)
  const [mismatchOnly, setMismatchOnly] = useState(false)

  const load = useCallback(() => {
    if (rangeMode ? !(rangeStart && rangeEnd) : !date) return
    setLoading(true); setDenied(null)
    const qs = rangeMode ? `start=${rangeStart}&end=${rangeEnd}` : `date=${date}`
    api(`/api/v1/closing/cash-recon-management?${qs}`)
      .then(setData)
      .catch((e: any) => {
        const m = String(e?.message || e)
        if (m.includes('restricted') || m.includes('403')) { setDenied(m); setData(null) }
        else console.error(e)
      })
      .finally(() => setLoading(false))
  }, [rangeMode, date, rangeStart, rangeEnd])
  useEffect(() => { load() }, [load])

  const rows: any[] = (data?.rows || []).filter((r: any) => !mismatchOnly || r.billpay_status === 'mismatch')
  const t = data?.totals || {}

  function exportPayload(): ExportPayload {
    return {
      title: `Management cash recon — ${rangeMode ? `${rangeStart} → ${rangeEnd}` : date}`,
      filename: `cash-recon-management-${rangeMode ? `${rangeStart}_${rangeEnd}` : date}`,
      sheets: [{
        name: 'Cash Recon',
        columns: [
          { header: 'Day', get: (r: any) => r.day },
          { header: 'Store', get: (r: any) => r.store_name || r.store_code },
          { header: 'Market', get: (r: any) => r.market || '' },
          { header: 'Cash declared', get: (r: any) => r.cash_declared, money: true },
          { header: 'Credit declared', get: (r: any) => r.credit_declared, money: true },
          { header: 'ePay on cash (declared)', get: (r: any) => r.epay_cash_declared, money: true },
          { header: 'ePay on credit (declared)', get: (r: any) => r.epay_credit_declared, money: true },
          { header: 'Cash pickup recorded', get: (r: any) => r.cash_pickup, money: true },
          { header: 'Bill-pay pickup recorded', get: (r: any) => r.billpay_pickup, money: true },
          { header: 'POS cash', get: (r: any) => r.pos_cash ?? '', money: true },
          { header: 'POS card', get: (r: any) => r.pos_card ?? '', money: true },
          { header: 'POS bill payments', get: (r: any) => r.pos_billpay ?? '', money: true },
          { header: 'Bill-pay Δ (declared − POS)', get: (r: any) => r.billpay_delta ?? '' },
          { header: 'Status', get: (r: any) => r.billpay_status },
        ],
        rows,
      }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧮 Cash Recon (Management)</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            One screen per store/day: declared cash, credit and the ePay split (DM-verified corrections winning),
            the cash + bill-pay pickups actually recorded, and what the POS reports show — with a declared-vs-POS
            bill-payment mismatch flag. Market manager and above only.</p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {denied ? (
        <div className="card" style={{ padding: 30, textAlign: 'center', borderLeft: '3px solid #dc2626' }}>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>🔒 Restricted</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', maxWidth: 560, margin: '0 auto' }}>
            This screen is restricted to market managers and above (owner directive 2026-09-02). Employees and
            district managers are gated out; an admin can widen the allowed roles in the tenant settings.
          </div>
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
            <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: !rangeMode ? 'var(--accent)' : 'transparent', color: !rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(false)}>Day</button>
              <button className="btn" style={{ borderRadius: 0, border: 'none', fontSize: 12, background: rangeMode ? 'var(--accent)' : 'transparent', color: rangeMode ? 'white' : 'var(--text2)' }} onClick={() => setRangeMode(true)}>Range</button>
            </div>
            {!rangeMode
              ? <input type="date" style={sel} value={date} onChange={e => setDate(e.target.value)} />
              : <span style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
                  <input type="date" style={sel} value={rangeStart} onChange={e => setRangeStart(e.target.value)} />
                  →<input type="date" style={sel} value={rangeEnd} onChange={e => setRangeEnd(e.target.value)} />
                </span>}
            <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
              <input type="checkbox" checked={mismatchOnly} onChange={e => setMismatchOnly(e.target.checked)} /> mismatches only
            </label>
            {data?.billpay_source && data.billpay_source !== 'none' && (
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>POS bill-pay source: {data.billpay_source}</span>
            )}
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
              {rows.length > 0 && <><ExportButtons payload={exportPayload} compact /><SendReportButton exportPayload={exportPayload} compact /></>}
              <Link href="/closing/pickup" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>💵 Cash Pickup</Link>
              <Link href="/closing/billpay-pickup" style={{ fontSize: 12, color: 'var(--accent)', textDecoration: 'none' }}>🧾 Bill Payment Pickup</Link>
            </div>
          </div>

          {data && (
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
              <Stat label="Cash declared" value={fmt(t.cash_declared || 0)} accent />
              <Stat label="Credit declared" value={fmt(t.credit_declared || 0)} />
              <Stat label="ePay declared" value={fmt(t.epay_declared || 0)} />
              <Stat label="Cash pickups" value={fmt(t.cash_pickup || 0)} />
              <Stat label="Bill-pay pickups" value={fmt(t.billpay_pickup || 0)} />
              <Stat label="POS bill payments" value={fmt(t.pos_billpay || 0)} sub={t.mismatched_store_days ? `⚠ ${t.mismatched_store_days} mismatched store-day${t.mismatched_store_days === 1 ? '' : 's'}` : 'no mismatches'} />
            </div>
          )}

          {data?.note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 12 }}>ℹ️ {data.note}</div>}

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : rows.length === 0 ? (
            <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
              No closings {mismatchOnly ? 'with bill-pay mismatches ' : ''}for {rangeMode ? `${rangeStart} → ${rangeEnd}` : date}.
            </div>
          ) : (
            <div className="card table-wrapper" style={{ padding: 0, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  <th style={{ ...th, textAlign: 'left' }}>Day</th>
                  <th style={{ ...th, textAlign: 'left' }}>Store</th>
                  <th style={th}>Cash declared</th>
                  <th style={th}>Credit declared</th>
                  <th style={th}>ePay on cash</th>
                  <th style={th}>ePay on credit</th>
                  <th style={th}>Cash pickup</th>
                  <th style={th}>Bill-pay pickup</th>
                  <th style={th}>POS cash</th>
                  <th style={th}>POS card</th>
                  <th style={th}>POS bill pay</th>
                  <th style={th}>Bill-pay Δ</th>
                </tr></thead>
                <tbody>
                  {rows.map((r: any, i: number) => (
                    <tr key={`${r.day}|${r.store_code}|${i}`} style={{ background: r.billpay_status === 'mismatch' ? 'rgba(220,38,38,0.06)' : undefined }}>
                      <td style={{ ...cell, color: 'var(--text3)' }}>{r.day}</td>
                      <td style={cell}>{r.store_name || r.store_code}{r.market ? <span style={{ color: 'var(--text3)' }}> · {r.market}</span> : null}</td>
                      <td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{fmt(r.cash_declared)}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(r.credit_declared)}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(r.epay_cash_declared)}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(r.epay_credit_declared)}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(r.cash_pickup)}</td>
                      <td style={{ ...cell, textAlign: 'right' }}>{fmt(r.billpay_pickup)}</td>
                      <td style={{ ...cell, textAlign: 'right', color: 'var(--text2)' }}>{r.pos_cash == null ? '—' : fmt(r.pos_cash)}</td>
                      <td style={{ ...cell, textAlign: 'right', color: 'var(--text2)' }}>{r.pos_card == null ? '—' : fmt(r.pos_card)}</td>
                      <td style={{ ...cell, textAlign: 'right', color: 'var(--text2)' }}>{r.pos_billpay == null ? '—' : fmt(r.pos_billpay)}</td>
                      <td style={{ ...cell, textAlign: 'right', fontWeight: r.billpay_status === 'mismatch' ? 700 : 400, color: r.billpay_status === 'mismatch' ? '#dc2626' : 'var(--text3)' }}>
                        {r.billpay_delta == null ? '—' : `${r.billpay_delta > 0 ? '+' : ''}${fmt(r.billpay_delta)}`}
                        {r.billpay_status === 'mismatch' && ' ⚠'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  )
}

const Stat = ({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) => (
  <div className="card" style={{ padding: '12px 16px', minWidth: 140, flex: '0 1 auto', borderTop: accent ? '3px solid var(--accent)' : undefined }}>
    <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{sub}</div>}
  </div>
)
