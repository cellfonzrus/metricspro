'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// Devices still in Inventory Aging but with a rebate received → effectively sold, pull from inventory.
// Each rebate is matched to a sale by IMEI; a rebate with NO matching sale is flagged to investigate.
export default function AgingRebatePage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selStores, setSelStores] = useState<string[]>([])
  const [unmatchedOnly, setUnmatchedOnly] = useState(false)

  useEffect(() => {
    setLoading(true)
    api(`/api/v1/asset/aging-rebate?org_id=${ORG_ID}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }, [])

  const allRows: any[] = data?.rows || []
  const storeOpts = Array.from(new Set(allRows.map(r => r.store).filter(Boolean))).sort().map(s => ({ value: s as string }))
  const rows = allRows.filter(r => (!selStores.length || selStores.includes(r.store)) && (!unmatchedOnly || r.unmatched))
  const t = data?.totals || {}

  function buildPayload(): ExportPayload {
    return {
      title: 'Aging — Rebate Received (pull from inventory)', subtitle: `${data?.count || 0} devices`,
      filename: 'aging-rebate-received',
      sheets: [{ name: 'Rebate received', rows, columns: [
        { header: 'Store', get: (r: any) => r.store },
        { header: 'Market', get: (r: any) => r.market },
        { header: 'Device', get: (r: any) => r.device_model },
        { header: 'IMEI/ESN', get: (r: any) => r.esn_imei },
        { header: 'Acquired', get: (r: any) => r.acquired_date },
        { header: 'Owed to distributor', get: (r: any) => r.owed_to_vip, money: true },
        { header: 'Rebate received', get: (r: any) => r.rebate, money: true },
        { header: 'Rebate date', get: (r: any) => r.rebate_date },
        { header: 'Sale found', get: (r: any) => r.sale_found ? 'Yes' : 'No' },
        { header: 'Sale date', get: (r: any) => r.sale_date },
        { header: 'Status', get: (r: any) => r.unmatched ? 'REBATE, NO SALE' : 'Sold — remove from inventory' },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset/aging" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Inventory Aging</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>💵 Aging — Rebate Received</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 740 }}>
            Devices still sitting in Inventory Aging but for which a <strong>rebate was received</strong> — they were
            effectively sold/activated, so pull them from inventory. Each rebate is matched to a sale by IMEI;
            a <strong>rebate with no matching sale</strong> is flagged to investigate.
          </p>
        </div>
        {allRows.length > 0 && <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></div>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Aging devices with rebate" value={`${data.count || 0}`} color="var(--accent)" />
            <Stat label="Rebate received (total)" value={fmt(t.rebate)} color="#16a34a" />
            <Stat label="Owed-to-distributor (in aging)" value={fmt(t.owed)} color="#d97706" />
            <Stat label="Rebate, NO sale (investigate)" value={`${t.unmatched || 0}`} color={t.unmatched ? '#dc2626' : '#059669'} />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <MultiSelect allLabel="All stores" width={170} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
            <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6 }}>
              <input type="checkbox" checked={unmatchedOnly} onChange={e => setUnmatchedOnly(e.target.checked)} /> Rebate‑without‑sale only
            </label>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} device(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
              No aging devices with a rebate received. (These appear once a device that's still On‑Inventory has a Boost reimbursement recorded.)
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 940 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  {['Store', 'Device', 'IMEI/ESN', 'Acquired', 'Owed', 'Rebate', 'Rebate date', 'Sale', 'Sale date', 'Status'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={(r.esn_imei || '') + i} style={{ borderTop: '1px solid var(--border)', background: r.unmatched ? '#fff1f2' : (i % 2 ? 'var(--surface2)' : undefined) }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || '—'}{r.market ? ` · ${r.market}` : ''}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.esn_imei || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.acquired_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{fmt(r.owed_to_vip)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600, color: '#16a34a' }}>{fmt(r.rebate)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.rebate_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.sale_found ? '✓' : '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.sale_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>
                        {r.unmatched
                          ? <span style={{ background: '#fee2e2', color: '#b91c1c', borderRadius: 5, padding: '1px 8px', fontWeight: 600 }}>REBATE, NO SALE</span>
                          : <span style={{ color: '#059669' }}>Sold — remove</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            "Rebate received" = a Boost reimbursement recorded on a device that's still On‑Inventory. Matched to a sale
            by IMEI (raw_sales serial). <b>REBATE, NO SALE</b> = a rebate came in but no sale for that IMEI is on record — check the
            rebate day's transactions. Export: Excel / PDF / Print (buttons) · WhatsApp / email (Send).
          </p>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
