'use client'
// Sold Tally — for RECEIVED units, verify (a) SOLD, matched against commcalc.raw_sales by IMEI/serial
// when captured at receiving (exact confidence), else by a store+model qty-window estimate (explicitly
// labeled, never presented as an exact match); and (b) COMMISSION RECEIVED, checked via IMEI against ePay
// commcalc.raw_payment_detail (payment_categories.category='Commission'). Buckets: sold-with-commission /
// sold-no-commission / unsold.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, fmt } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import ReportShell from '@/components/ReportShell'
import { type ExportColumn } from '@/lib/export'
import PoNav from '../_shared/PoNav'

type TallyRow = {
  po_id: string; po_number: string; po_line_id: string; sku: string | null; device_model: string
  store: string | null; market: string | null; received_date: string
  imei: string | null; confidence: 'exact' | 'estimated'; qty: number
  sold: boolean; commission_amount: number | null; commission_basis: string; bucket: string
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const tile: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, flex: 1, minWidth: 160 }

const BUCKET_LABEL: Record<string, string> = {
  sold_with_commission: '✅ Sold — commission received',
  sold_no_commission: '⚠️ Sold — commission NOT confirmed',
  unsold: '📦 Unsold',
}
const BASIS_LABEL: Record<string, string> = {
  category: 'exact (Commission category)',
  any_payment_fallback: 'any ePay payment (category unconfigured)',
  unknown_no_serial: 'unknown — no serial captured',
}

export default function SoldTallyPage() {
  const [rows, setRows] = useState<TallyRow[]>([])
  const [summary, setSummary] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api('/api/v1/asset/po/tally')
      setRows(d.rows || [])
      setSummary(d.summary || {})
      if (d.migrated === false) setMsg(d.note || 'Purchase Orders migration pending.')
    } catch (e: any) { setMsg('Could not load sold tally: ' + (e?.message || e)) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => rows.filter(r => matchesStandardFilter(r, filt, {
    store: r => r.store, market: r => r.market, date: r => r.received_date,
  })), [rows, filt])

  const tileCounts = useMemo(() => {
    const out = { sold_with_commission: 0, sold_no_commission: 0, unsold: 0 }
    for (const r of filtered) out[r.bucket as keyof typeof out] = (out[r.bucket as keyof typeof out] || 0) + (r.qty || 1)
    return out
  }, [filtered])

  const columns: ExportColumn[] = [
    { header: 'PO #', get: (r: TallyRow) => r.po_number },
    { header: 'Store', get: (r: TallyRow) => r.store || '—', role: 'store' },
    { header: 'Market', get: (r: TallyRow) => r.market || '—' },
    { header: 'Device Model', get: (r: TallyRow) => r.device_model },
    { header: 'IMEI/Serial', get: (r: TallyRow) => r.imei || '(none captured)' },
    { header: 'Qty', get: (r: TallyRow) => r.qty, type: 'number' },
    { header: 'Received Date', get: (r: TallyRow) => r.received_date, type: 'date' },
    { header: 'Match Confidence', get: (r: TallyRow) => r.confidence === 'exact' ? 'Exact (serial matched)' : 'Estimated (no serial)' },
    { header: 'Sold?', get: (r: TallyRow) => r.sold ? 'Yes' : 'No' },
    { header: 'Commission Amount', get: (r: TallyRow) => r.commission_amount, money: true },
    { header: 'Commission Basis', get: (r: TallyRow) => BASIS_LABEL[r.commission_basis] || r.commission_basis },
    { header: 'Bucket', get: (r: TallyRow) => BUCKET_LABEL[r.bucket] || r.bucket },
  ]

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>✅ Sold Tally</h1>
      <PoNav active="/commcalc/asset/purchase-orders/tally" />
      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}

      <div style={card}>
        <StandardFilterBar value={filt} onChange={setFilt}
          show={{ period: true, stores: true, markets: true, reps: false }} periodMode="range"
          optionsUrl="/api/v1/core/filter-options" />
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ ...tile, borderColor: '#16a34a' }}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>Sold — commission received</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{tileCounts.sold_with_commission}</div>
          </div>
          <div style={{ ...tile, borderColor: '#d97706' }}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>Sold — commission NOT confirmed</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{tileCounts.sold_no_commission}</div>
          </div>
          <div style={{ ...tile }}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>Unsold</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{tileCounts.unsold}</div>
          </div>
        </div>
        <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 10 }}>
          "Estimated" rows had no IMEI/serial captured at Receiving — sold status is a qty-level comparison
          against store/model sales since the receive date, and commission status can never be confirmed by
          IMEI for those (shown as "unknown — no serial captured"). Capture serials at Receiving for exact
          matches.
        </div>
        {!loading && (
          <ReportShell title="Sold Tally" filename="po_sold_tally" columns={columns} rows={filtered} />
        )}
      </div>
    </div>
  )
}
