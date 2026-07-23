'use client'
// Unsold Inventory Aging — THIS module's own report (received-but-unsold PO units aged from
// received_date), separate from /commcalc/asset/aging (asset_ledger/VIP-consignment based). Flags a unit
// when age_days exceeds the management-configurable threshold (commcalc.po_settings.aging_flag_days,
// default 10) — editable below, admin-gated server-side.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, matchesStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import ReportShell from '@/components/ReportShell'
import { type ExportColumn } from '@/lib/export'
import PoNav from '../_shared/PoNav'

type AgingRow = {
  po_id: string; po_number: string; po_line_id: string; sku: string | null; device_model: string
  store: string | null; market: string | null; received_date: string
  imei: string | null; confidence: 'exact' | 'estimated'; qty: number
  age_days: number | null; flagged: boolean
}

const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12, padding: 16, background: 'var(--surface)', marginBottom: 16 }
const tile: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14, flex: 1, minWidth: 160 }
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, width: 80 }

export default function UnsoldAgingPage() {
  const { user, permissions } = useAuth()
  const isAdmin = !!(user?.super_admin || (permissions as any)?.scope === 'all' || (user?.role || '').toLowerCase() === 'admin')

  const [rows, setRows] = useState<AgingRow[]>([])
  const [threshold, setThreshold] = useState(10)
  const [thresholdInput, setThresholdInput] = useState('10')
  const [flagged, setFlagged] = useState(0)
  const [totalUnsold, setTotalUnsold] = useState(0)
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [saving, setSaving] = useState(false)
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const d = await api('/api/v1/asset/po/aging')
      setRows(d.rows || [])
      setThreshold(d.threshold_days ?? 10)
      setThresholdInput(String(d.threshold_days ?? 10))
      setFlagged(d.flagged || 0)
      setTotalUnsold(d.total_unsold || 0)
      if (d.migrated === false) setMsg(d.note || 'Purchase Orders migration pending.')
    } catch (e: any) { setMsg('Could not load unsold aging: ' + (e?.message || e)) }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  async function saveThreshold() {
    const days = Math.max(1, Math.min(365, parseInt(thresholdInput, 10) || 10))
    setSaving(true); setMsg('')
    try {
      await api('/api/v1/asset/po/settings', { method: 'PUT', body: JSON.stringify({ aging_flag_days: days }) })
      setMsg(`Aging threshold set to ${days} days.`)
      load()
    } catch (e: any) { setMsg('Could not save threshold: ' + (e?.message || e)) }
    setSaving(false)
  }

  const filtered = useMemo(() => rows.filter(r => matchesStandardFilter(r, filt, {
    store: r => r.store, market: r => r.market, date: r => r.received_date,
  })), [rows, filt])

  const columns: ExportColumn[] = [
    { header: 'PO #', get: (r: AgingRow) => r.po_number },
    { header: 'Store', get: (r: AgingRow) => r.store || '—', role: 'store' },
    { header: 'Market', get: (r: AgingRow) => r.market || '—' },
    { header: 'Device Model', get: (r: AgingRow) => r.device_model },
    { header: 'IMEI/Serial', get: (r: AgingRow) => r.imei || '(none captured)' },
    { header: 'Qty', get: (r: AgingRow) => r.qty, type: 'number' },
    { header: 'Received Date', get: (r: AgingRow) => r.received_date, type: 'date' },
    { header: 'Match Confidence', get: (r: AgingRow) => r.confidence === 'exact' ? 'Exact' : 'Estimated' },
    { header: 'Age (days)', get: (r: AgingRow) => r.age_days ?? '—', type: 'number' },
    { header: 'Flagged', get: (r: AgingRow) => r.flagged ? `Yes (> ${threshold}d)` : 'No' },
  ]

  return (
    <div style={{ padding: 20, maxWidth: 1200, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>⏳ Unsold Inventory Aging</h1>
      <PoNav active="/commcalc/asset/purchase-orders/aging" />
      {msg && <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>{msg}</div>}

      <div style={card}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginBottom: 14, flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>Flag threshold (days unsold since receiving)
            <div><input type="number" min={1} max={365} style={sel} value={thresholdInput}
              disabled={!isAdmin} onChange={e => setThresholdInput(e.target.value)} /></div>
          </label>
          <button className="btn btn-secondary" disabled={!isAdmin || saving} onClick={saveThreshold}>
            {saving ? 'Saving…' : 'Save threshold'}
          </button>
          {!isAdmin && <span style={{ fontSize: 11, color: 'var(--text3)' }}>Admin only — current threshold: {threshold} days.</span>}
        </div>

        <StandardFilterBar value={filt} onChange={setFilt}
          show={{ period: true, stores: true, markets: true, reps: false }} periodMode="range"
          optionsUrl="/api/v1/core/filter-options" />

        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ ...tile, borderColor: '#dc2626' }}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>Flagged (&gt; {threshold} days)</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{flagged}</div>
          </div>
          <div style={tile}>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>Total unsold units</div>
            <div style={{ fontSize: 24, fontWeight: 700 }}>{totalUnsold}</div>
          </div>
        </div>

        {!loading && (
          <ReportShell title="Unsold Inventory Aging" filename="po_unsold_aging" columns={columns} rows={filtered} />
        )}
      </div>
    </div>
  )
}
