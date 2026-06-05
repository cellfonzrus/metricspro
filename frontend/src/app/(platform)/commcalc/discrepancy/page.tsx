'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { supabase } from '@/lib/client'

interface Txn {
  store: string; storeNum: string; rep: string; date: string
  phone: string; imei: string; transId: string; contractType: string
  extPrice: number; promoType: string; expectedAmt: number; paidAmt: number; status: string
}
interface SummaryRow {
  store: string; storeNum: string; promoType: string; units: number
  totalExpected: number; received: number; shortfall: number; status: string
  transactions: Txn[]
}

const PROMO_RE = /\$(\d+(?:\.\d+)?)/

function parsePromo(desc: string): { promoType: string; expectedAmt: number } | null {
  const m = PROMO_RE.exec(desc || '')
  if (!m) return null
  const keywords = ['Promo', 'Offer', 'Rebate', 'Spiff', 'Incentive']
  const isPromo = keywords.some(k => desc.includes(k))
  if (!isPromo) return null
  const type = desc.split('-')[0].trim().substring(0, 40)
  return { promoType: type, expectedAmt: parseFloat(m[1]) }
}

export default function DiscrepancyPage() {
  const [period] = useState('April 2026')
  const [summaryRows, setSummaryRows] = useState<SummaryRow[]>([])
  const [loading, setLoading] = useState(true)
  const [drillRow, setDrillRow] = useState<SummaryRow | null>(null)
  const [filterStatus, setFilterStatus] = useState('')

  useEffect(() => {
    setLoading(true)
    Promise.all([
      supabase.from('commcalc_raw_sales').select('*').eq('period', period).limit(50000),
      supabase.from('commcalc_raw_payment_detail').select('*').eq('period', period).limit(50000),
      supabase.from('commcalc_payment_categories').select('*'),
    ]).then(([{ data: sales }, { data: payData }, { data: cats }]) => {
      const catMap: Record<string, string> = {}
      ;(cats || []).forEach((c: any) => { catMap[c.description?.trim()] = c.category })
      ;(payData || []).forEach((r: any) => {
        r.category = catMap[String(r.payment_type || '').trim()] || 'Unknown'
      })

      // IMEI-level payment map
      const imeiPayMap: Record<string, number> = {}
      ;(payData || []).forEach((r: any) => {
        if (r.category !== 'Re-imbursement') return
        const imei = String(r.imei || '').replace(/\.0$/, '').trim()
        if (!imei) return
        imeiPayMap[imei] = (imeiPayMap[imei] || 0) + (parseFloat(r.amount) || 0)
      })

      // Payment by store+promoType (for reimbMap)
      const reimbMap: Record<string, Record<string, number>> = {}
      ;(payData || []).forEach((r: any) => {
        if (r.category !== 'Re-imbursement') return
        const num = String(r.business_address || '').split(' ')[0]
        if (!reimbMap[num]) reimbMap[num] = {}
        const pt = String(r.payment_type || '').trim()
        reimbMap[num][pt] = (reimbMap[num][pt] || 0) + (parseFloat(r.amount) || 0)
      })

      // Build transactions
      const txns: Txn[] = []
      ;(sales || []).forEach((r: any) => {
        const dept = String(r.department || '').trim()
        if (!['Android - XP', 'IPHONE - XP', 'TABLET - XP'].includes(dept)) return
        const parsed = parsePromo(r.product_desc || '')
        if (!parsed) return
        const storeNum = String(r.store || '').split(' ')[0]
        const imei = String(r.serial_1 || '').replace(/\.0$/, '').trim()
        txns.push({
          store: String(r.store || '').trim(),
          storeNum,
          rep: String(r.salesperson || '').trim(),
          date: String(r.trans_date || '').split('T')[0],
          phone: String(r.product_desc || '').split(' - ')[0].trim().substring(0, 50),
          imei,
          transId: String(r.trans_id || '').replace(/\.0$/, ''),
          contractType: String(r.contract_type || '').trim(),
          extPrice: parseFloat(r.ext_price) || 0,
          promoType: parsed.promoType,
          expectedAmt: parsed.expectedAmt,
          paidAmt: imeiPayMap[imei] || 0,
          status: '',
        })
      })

      // Build summary
      const summaryMap: Record<string, SummaryRow> = {}
      txns.forEach(t => {
        const key = `${t.storeNum}||${t.promoType}`
        if (!summaryMap[key]) {
          summaryMap[key] = {
            store: t.store, storeNum: t.storeNum, promoType: t.promoType,
            units: 0, totalExpected: 0, received: 0, shortfall: 0, status: '', transactions: [],
          }
        }
        const s = summaryMap[key]
        s.units++
        s.totalExpected += t.expectedAmt
        s.transactions.push(t)
      })

      const rows: SummaryRow[] = Object.values(summaryMap).map(s => {
        const received = (reimbMap[s.storeNum] || {})[s.promoType] || 0
        const shortfall = Math.max(0, s.totalExpected - received)
        const pct = s.totalExpected > 0 ? received / s.totalExpected : 1
        const status = received === 0 ? 'NOT_PAID' : pct < 0.95 ? 'PARTIAL' : 'PAID'
        s.transactions.forEach(t => { t.status = status })
        return { ...s, received, shortfall, status }
      })

      rows.sort((a, b) => b.shortfall - a.shortfall)
      setSummaryRows(rows)
    }).catch(console.error).finally(() => setLoading(false))
  }, [period])

  const totalShortfall = summaryRows.reduce((s, r) => s + r.shortfall, 0)
  const filtered = summaryRows.filter(r => !filterStatus || r.status === filterStatus)

  const StatusBadge = ({ status }: { status: string }) => {
    const map: Record<string, {cls: string; label: string}> = {
      PAID: { cls: 'badge-green', label: 'Paid' },
      PARTIAL: { cls: 'badge-amber', label: 'Partial' },
      NOT_PAID: { cls: 'badge-red', label: 'Not Paid' },
    }
    const { cls, label } = map[status] || { cls: 'badge-slate', label: status }
    return <span className={`badge ${cls}`}>{label}</span>
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 20 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Payment Discrepancy</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            {period} · Total shortfall:&nbsp;
            <strong style={{ color: totalShortfall > 0 ? 'var(--red)' : 'var(--green)' }}>{fmt(totalShortfall)}</strong>
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <select className="select" value={filterStatus} onChange={e => setFilterStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="NOT_PAID">Not Paid</option>
            <option value="PARTIAL">Partial</option>
            <option value="PAID">Paid</option>
          </select>
          <button className="btn btn-secondary" onClick={() => {
            const csv = ['Store,Promo Type,Units,Expected,Received,Shortfall,Status']
            summaryRows.forEach(r => csv.push(`"${r.store}","${r.promoType}",${r.units},${r.totalExpected.toFixed(2)},${r.received.toFixed(2)},${r.shortfall.toFixed(2)},"${r.status}"`))
            const a = document.createElement('a'); a.href = 'data:text/csv,' + encodeURIComponent(csv.join('\n'))
            a.download = `discrepancy-${period.replace(' ','-')}.csv`; a.click()
          }}>📥 Export Claim</button>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Store</th>
                  <th>Promo Type</th>
                  <th style={{ textAlign: 'right' }}>Units</th>
                  <th style={{ textAlign: 'right' }}>Expected</th>
                  <th style={{ textAlign: 'right' }}>Received</th>
                  <th style={{ textAlign: 'right', color: 'var(--red)' }}>Shortfall</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr key={i} style={{ cursor: 'pointer' }} onClick={() => setDrillRow(r === drillRow ? null : r)}>
                    <td style={{ fontWeight: 500 }}>{r.store?.substring(0, 30)}</td>
                    <td style={{ fontSize: 12 }}>{r.promoType}</td>
                    <td style={{ textAlign: 'right' }}>{r.units}</td>
                    <td style={{ textAlign: 'right' }}>{fmt(r.totalExpected)}</td>
                    <td style={{ textAlign: 'right', color: 'var(--green)' }}>{fmt(r.received)}</td>
                    <td style={{ textAlign: 'right', fontWeight: 700, color: r.shortfall > 0 ? 'var(--red)' : 'var(--green)' }}>
                      {r.shortfall > 0 ? fmt(r.shortfall) : '—'}
                    </td>
                    <td><StatusBadge status={r.status} /></td>
                  </tr>
                ))}
                {filtered.length === 0 && (
                  <tr><td colSpan={7} style={{ textAlign: 'center', padding: 40, color: 'var(--text3)' }}>
                    No promo transactions found. Upload sales file with promo products (e.g. "Q2 PIC Offer - $550.00").
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Drill-down */}
          {drillRow && (
            <div className="card" style={{ marginTop: 16 }}>
              <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 12 }}>
                {drillRow.store} — {drillRow.promoType}
              </div>
              <div className="table-wrapper">
                <table>
                  <thead>
                    <tr>
                      <th>Date</th><th>Rep</th><th>Phone</th><th>IMEI</th>
                      <th style={{ textAlign: 'right' }}>Sold At</th>
                      <th style={{ textAlign: 'right' }}>Expected</th>
                      <th style={{ textAlign: 'right', color: 'var(--green)' }}>Paid</th>
                      <th>Trans ID</th>
                    </tr>
                  </thead>
                  <tbody>
                    {drillRow.transactions.map((t, i) => (
                      <tr key={i}>
                        <td style={{ fontSize: 12 }}>{t.date}</td>
                        <td style={{ fontWeight: 500, fontSize: 12 }}>{t.rep}</td>
                        <td style={{ fontSize: 12 }}>{t.phone}</td>
                        <td style={{ fontFamily: 'monospace', fontSize: 11 }}>{t.imei}</td>
                        <td style={{ textAlign: 'right' }}>{fmt(t.extPrice)}</td>
                        <td style={{ textAlign: 'right' }}>{fmt(t.expectedAmt)}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600, color: t.paidAmt > 0 ? 'var(--green)' : 'var(--text3)' }}>
                          {t.paidAmt > 0 ? fmt(t.paidAmt) : '—'}
                        </td>
                        <td style={{ fontFamily: 'monospace', fontSize: 11, color: 'var(--text3)' }}>{t.transId}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
