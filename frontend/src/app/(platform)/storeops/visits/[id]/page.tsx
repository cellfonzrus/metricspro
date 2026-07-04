'use client'
import { useState, useEffect } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import VisitActions from './VisitActions'

const row: React.CSSProperties = { padding: '8px 0', borderBottom: '1px solid var(--border)', display: 'flex', gap: 10, alignItems: 'flex-start' }

function fmtDateTime(s?: string | null) {
  if (!s) return '—'
  const d = new Date(s)
  return isNaN(d.getTime()) ? s : d.toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' })
}

const CATS: [string, string][] = [
  ['appearance', 'Appearance'], ['facilities', 'Facilities'], ['security', 'Security'],
  ['supplies', 'Supplies'], ['accessories', 'Accessories'], ['general', 'Other'],
]

export default function VisitDetailPage() {
  const params = useParams()
  const id = params?.id as string
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api(`/api/v1/storevisit/visits/${id}`).then(setData).catch(console.error).finally(() => setLoading(false))
  }, [id])

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
  if (!data?.visit) return <div className="card" style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Visit not found.</div>

  const v = data.visit
  const responses: any[] = data.responses || []
  const accessories: any[] = data.accessories || []
  const mismatch = v.actual_rep && v.scheduled_rep && v.actual_rep !== v.scheduled_rep
  // Action-item rollup is keyed by the visit's calendar month.
  const period = (v.check_in_at || '').slice(0, 7) || new Date().toISOString().slice(0, 7)

  function payload(): ExportPayload {
    return {
      title: 'Store Visit Checklist',
      subtitle: `${v.store_address || v.store_code || ''} · ${fmtDateTime(v.check_in_at)} · DM ${v.dm_name || ''}`,
      filename: `store-visit-${(v.store_code || 'visit')}-${(v.check_in_at || '').slice(0, 10)}`,
      sheets: [{
        name: 'Checklist',
        rows: responses,
        columns: [
          { header: 'Item', get: r => r.label_snapshot || r.item_key },
          { header: 'OK?', get: r => (r.checked ? 'Yes' : 'No') },
          { header: 'Note', get: r => r.note || '' },
        ],
      }, {
        name: 'Accessories',
        rows: accessories,
        columns: [
          { header: 'Accessory', get: r => r.accessory_name },
          { header: 'Qty', get: r => r.qty },
          { header: 'Note', get: r => r.note || '' },
        ],
      }],
    }
  }

  const Info = ({ label, children }: { label: string; children: React.ReactNode }) => (
    <div><div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div><div style={{ fontSize: 14, marginTop: 2 }}>{children}</div></div>
  )

  return (
    <div style={{ maxWidth: 860 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6, flexWrap: 'wrap', gap: 10 }}>
        <Link href="/storeops/visits" style={{ fontSize: 13, color: 'var(--accent)' }}>← All visits</Link>
        <><ExportButtons payload={payload} /><SendReportButton exportPayload={payload} compact /></>
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 4px' }}>{v.store_address || v.store_code || 'Store visit'}</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 18px' }}>
        {v.status === 'submitted' ? '✅ Submitted' : '🟡 In progress'} · {fmtDateTime(v.check_in_at)}
      </p>

      <div className="card" style={{ padding: 18, marginBottom: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 16 }}>
        <Info label="Market">{v.market || '—'}</Info>
        <Info label="DM">{v.dm_name || '—'}</Info>
        <Info label="Checked in">{fmtDateTime(v.check_in_at)}</Info>
        <Info label="Checked out">{fmtDateTime(v.check_out_at)}</Info>
        <Info label="Scheduled rep">{v.scheduled_rep || '—'}</Info>
        <Info label="Actual rep">{v.actual_rep || '—'}{mismatch ? ' ⚠️' : ''}</Info>
        {mismatch && <Info label="Discrepancy reason">{v.rep_discrepancy_reason || '—'}</Info>}
        <Info label="GPS">{v.check_in_lat != null ? <a href={`https://maps.google.com/?q=${v.check_in_lat},${v.check_in_lng}`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>{Number(v.check_in_lat).toFixed(5)}, {Number(v.check_in_lng).toFixed(5)}</a> : '—'}</Info>
      </div>

      {v.store_code && <VisitActions visitId={v.id} storeCode={v.store_code} period={period} dmName={v.dm_name} />}

      <div className="card" style={{ padding: 18, marginBottom: 16 }}>
        <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 10px' }}>Inspection checklist</h2>
        {responses.length === 0 ? <div style={{ color: 'var(--text3)', fontSize: 13 }}>No checklist responses recorded.</div> :
          CATS.map(([key, label]) => {
            const known = CATS.map(c => c[0])
            const list = responses.filter(r => {
              const cat = r.category_snapshot || 'general'
              return key === 'general' ? !known.includes(cat) || cat === 'general' : cat === key
            })
            return list.length === 0 ? null : (
              <div key={label} style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>{label}</div>
                {list.map((r, i) => (
                  <div key={i} style={row}>
                    <span style={{ fontSize: 16 }}>{r.checked ? '✅' : '⬜'}</span>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: 14 }}>{r.label_snapshot || r.item_key}</div>
                      {r.note && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>📝 {r.note}</div>}
                      {r.photo_url && <a href={r.photo_url} target="_blank" rel="noreferrer" style={{ fontSize: 12, color: 'var(--accent)' }}>📷 photo</a>}
                    </div>
                  </div>
                ))}
              </div>
            )
          })}
      </div>

      {accessories.length > 0 && (
        <div className="card" style={{ padding: 18, marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 10px' }}>Accessories to order</h2>
          {accessories.map((a, i) => (
            <div key={i} style={row}><span style={{ flex: 1, fontSize: 14 }}>{a.accessory_name}</span><span style={{ fontSize: 13, color: 'var(--text2)' }}>×{a.qty}{a.note ? ` · ${a.note}` : ''}</span></div>
          ))}
          <a href={data.vaccessorize_url || 'https://www.vaccessorize.com'} target="_blank" rel="noreferrer" className="btn btn-secondary" style={{ fontSize: 13, marginTop: 10 }}>🛒 Order on vAccessorize.com ↗</a>
        </div>
      )}

      {v.extra_notes && (
        <div className="card" style={{ padding: 18, marginBottom: 16 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>Other items noted</h2>
          <div style={{ fontSize: 14, whiteSpace: 'pre-wrap' }}>{v.extra_notes}</div>
        </div>
      )}

      {v.clean_store_photo_url && (
        <div className="card" style={{ padding: 18, marginBottom: 40 }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 10px' }}>Clean-store photo</h2>
          <img src={v.clean_store_photo_url} alt="clean store" style={{ maxWidth: '100%', borderRadius: 8, border: '1px solid var(--border)' }} />
        </div>
      )}
    </div>
  )
}
