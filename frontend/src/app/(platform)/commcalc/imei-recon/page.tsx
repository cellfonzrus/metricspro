'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { apiCached } from '@/lib/cache'
import { usePeriod } from '@/lib/period-context'

// IMEI/serial reconciliation: B2B inventory (inventory_aging_device) vs B2B sales (raw_sales.serial_1).
export default function ImeiReconPage() {
  const { period } = usePeriod()
  const [stores, setStores] = useState<any[]>([])
  const [storeCode, setStoreCode] = useState('')
  const [maxDays, setMaxDays] = useState(10)
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    apiCached('/api/v1/storeops/stores').then((r: any) => {
      const list = Array.isArray(r) ? r : []
      setStores(list)
      if (!storeCode && list[0]?.store_code) setStoreCode(list[0].store_code)
    }).catch(() => {})
  }, [])

  async function load() {
    if (!storeCode) { setMsg('Pick a store.'); return }
    setLoading(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/commcalc/inventory-recon?store_code=${encodeURIComponent(storeCode)}&period=${encodeURIComponent(period)}&max_days=${maxDays}&org_id=${ORG_ID}`)
      setData(r)
      if (!r.counts?.devices && !r.counts?.sales) setMsg('No inventory or sales rows matched this store/period.')
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setData(null) } finally { setLoading(false) }
  }
  useEffect(() => { if (storeCode) load() }, [storeCode, period]) // eslint-disable-line

  const c = data?.counts || {}
  const Tile = ({ label, value, tone }: { label: string; value: any; tone?: string }) => (
    <div style={{ flex: '1 1 130px', background: 'var(--surface2)', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: tone || 'var(--text)' }}>{value ?? '—'}</div>
      <div style={{ fontSize: 11.5, color: 'var(--text3)' }}>{label}</div>
    </div>
  )
  const List = ({ title, rows, cols, note }: { title: string; rows: any[]; cols: [string, (r: any) => any][]; note?: string }) => (
    <div className="card" style={{ padding: 14, marginTop: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 14 }}>{title} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>({rows?.length || 0})</span></div>
      {note && <div style={{ fontSize: 12, color: 'var(--text3)', margin: '2px 0 8px' }}>{note}</div>}
      {rows && rows.length > 0 ? (
        <div style={{ overflowX: 'auto', marginTop: 8 }}>
          <table style={{ fontSize: 12.5, borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr>{cols.map(([h]) => <th key={h} style={{ textAlign: 'left', padding: '3px 12px 6px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 100).map((r, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                {cols.map(([h, get]) => <td key={h} style={{ padding: '3px 12px 3px 0' }}>{get(r) ?? '—'}</td>)}
              </tr>
            ))}</tbody>
          </table>
          {rows.length > 100 && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>Showing first 100 of {rows.length}.</div>}
        </div>
      ) : <div style={{ fontSize: 12.5, color: 'var(--green)', marginTop: 6 }}>✓ none</div>}
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>IMEI Reconciliation</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          {period} · B2B inventory vs B2B sales — is every IMEI accounted for, and sold within {maxDays} days of receiving?
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <select className="select" value={storeCode} onChange={e => setStoreCode(e.target.value)}>
          {stores.length === 0 && <option value="">(no stores)</option>}
          {stores.map((s: any) => <option key={s.store_code || s.id} value={s.store_code}>{s.store_code}{s.address ? ` · ${String(s.address).substring(0, 30)}` : ''}</option>)}
        </select>
        <label style={{ fontSize: 13, color: 'var(--text2)', display: 'flex', alignItems: 'center', gap: 6 }}>
          Sell-through target
          <input type="number" min={1} value={maxDays} onChange={e => setMaxDays(Number(e.target.value) || 1)} style={{ width: 60 }} title="Days a device may age before it's flagged" />
          <span style={{ color: 'var(--text3)' }}>days</span>
        </label>
        <button className="btn" disabled={loading} onClick={load}>{loading ? '…' : 'Refresh'}</button>
      </div>

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{msg}</div>}

      {data && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <Tile label="Devices in inventory" value={c.devices} />
            <Tile label="Sales (serialized)" value={c.sales} />
            <Tile label="Off-shelf, no sale" value={c.unaccounted} tone={c.unaccounted ? '#dc2626' : undefined} />
            <Tile label="Sold, not in inventory" value={c.sold_not_in_inventory} tone={c.sold_not_in_inventory ? '#b45309' : undefined} />
            <Tile label="Uncategorized sales" value={c.uncategorized_sales} tone={c.uncategorized_sales ? '#b45309' : undefined} />
            <Tile label={`Sold ≤ ${maxDays}d`} value={c.sold_within_n} tone={c.sold_within_n ? '#16a34a' : undefined} />
            <Tile label={`Sold > ${maxDays}d`} value={c.sold_over_n} tone={c.sold_over_n ? '#dc2626' : undefined} />
          </div>

          <List title="Off the shelf with no matching sale (possible shrink)" rows={data.unaccounted}
            note="Device left inventory (off-hand) but no sale carries its IMEI."
            cols={[['IMEI', (r) => r.imei], ['Off-hand as of', (r) => r.off_hand_as_of], ['Received', (r) => r.received]]} />
          <List title="Sold but never in inventory" rows={data.sold_not_in_inventory}
            note="A sale carries a serial with no matching received device — inventory feed gap or mismatched serial."
            cols={[['Serial', (r) => r.serial], ['Sold', (r) => String(r.trans_date || '').substring(0, 10)], ['Product', (r) => r.product_desc]]} />
          <List title={`Aged past ${maxDays} days before selling`} rows={data.sold_over_n}
            note="Matched device sold, but it sat longer than the sell-through target."
            cols={[['Serial', (r) => r.serial], ['Aging (days)', (r) => r.aging_days], ['Sold', (r) => String(r.trans_date || '').substring(0, 10)], ['Product', (r) => r.product_desc]]} />
          <List title="Uncategorized sales" rows={data.uncategorized_sales}
            note="Product doesn't classify into a device bucket (SIM/accessory/unknown) — check categorization."
            cols={[['Serial', (r) => r.serial], ['Product', (r) => r.product_desc], ['Sold', (r) => String(r.trans_date || '').substring(0, 10)]]} />
        </>
      )}
    </div>
  )
}
