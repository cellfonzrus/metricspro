'use client'
import { useState, useEffect } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { MultiSelect } from '@/lib/multiselect'

// Phones flagged "physically missing" during Inventory Aging investigation — show in aging but not in
// the store. The list to investigate + the owed-to-distributor exposure at risk.
export default function MissingPhonesPage() {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selStores, setSelStores] = useState<string[]>([])

  function load() {
    setLoading(true)
    api(`/api/v1/asset/missing-phones?org_id=${ORG_ID}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) })).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [])

  const allRows: any[] = data?.rows || []
  const storeOpts = Array.from(new Set(allRows.map(r => r.store).filter(Boolean))).sort().map(s => ({ value: s as string }))
  const rows = allRows.filter(r => !selStores.length || selStores.includes(r.store))

  function buildPayload(): ExportPayload {
    return {
      title: 'Missing Phones (aging investigation)', subtitle: `${data?.count || 0} devices`,
      filename: 'missing-phones',
      sheets: [{ name: 'Missing phones', rows, columns: [
        { header: 'Store', get: (r: any) => r.store || '(not in ledger)' },
        { header: 'Market', get: (r: any) => r.market },
        { header: 'Device', get: (r: any) => r.device_model },
        { header: 'IMEI/ESN', get: (r: any) => r.esn_imei },
        { header: 'Acquired', get: (r: any) => r.acquired_date },
        { header: 'Owed to distributor', get: (r: any) => r.owed_to_vip, money: true },
        { header: 'Remark', get: (r: any) => r.remark },
        { header: 'Flagged by', get: (r: any) => r.investigated_by },
      ] }],
    }
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <a href="/commcalc/asset/aging" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none' }}>← Inventory Aging</a>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '6px 0 0' }}>📵 Missing Phones</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Devices flagged in Inventory Aging as showing in the system but <strong>not physically in the store</strong>.
            The list to investigate, with the distributor exposure at risk.
          </p>
        </div>
        {allRows.length > 0 && <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,1fr)', gap: 14, marginBottom: 16 }}>
            <Stat label="Missing phones flagged" value={`${data.count || 0}`} color="#dc2626" />
            <Stat label="Owed-to-distributor at risk" value={fmt(data.owed_total)} color="#d97706" />
          </div>

          <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
            <MultiSelect allLabel="All stores" width={170} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} device(s)</span>
          </div>

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
              No phones flagged missing. Tick "Missing?" on a device in Inventory Aging to add it here.
            </div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  {['Store','Device','IMEI/ESN','Acquired','Owed','Remark','Flagged by'].map(h =>
                    <th key={h} style={{ textAlign: 'left', padding: '8px 12px', whiteSpace: 'nowrap' }}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={(r.esn_imei || '') + i} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.store || <span style={{ color: '#dc2626' }}>not in ledger</span>}{r.market ? ` · ${r.market}` : ''}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.device_model || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 11, fontFamily: 'monospace' }}>{r.esn_imei || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{r.acquired_date || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontWeight: 600 }}>{r.owed_to_vip == null ? '—' : fmt(r.owed_to_vip)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.remark || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text3)' }}>{r.investigated_by || '—'}</td>
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

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 22, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
