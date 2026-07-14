'use client'
// "WHERE ARE MY ROWS?" — the debug-first upload-trace surface (mig 202). One record per ingest attempt
// (manual upload, email sweep, FTP sweep, feed→raw_sales promotion), newest first, showing which ORG the
// rows landed in, rows-in vs saved, per-period + per-day saved counts, the guard outcome, duration, and
// any error. Linked from every upload UI + email-imports + the Sales Report, so "I uploaded a file and
// the page shows nothing" is answerable from data, not guesswork (owner mandate 2026-07-14).
//
// It sends the active tenant (getActiveOrg) as an explicit org_id so a super-admin viewing a tenant sees
// THAT tenant's traces — and the echoed `org_id` on each response makes a wrong-tenant read self-evident
// (the sales-report incident: a no-org_id read silently defaults to the HOUSE org).
import { useState, useEffect, useCallback } from 'react'
import { api, getActiveOrg } from '@/lib/client'

const tones: Record<string, { bg: string; fg: string; label: string }> = {
  ok:      { bg: '#dcfce7', fg: '#166534', label: 'ok' },
  partial: { bg: '#fef9c3', fg: '#854d0e', label: 'partial' },
  skipped: { bg: '#fef3c7', fg: '#92400e', label: 'skipped' },
  error:   { bg: '#fee2e2', fg: '#991b1b', label: 'error' },
}

function orgParam(): string {
  const o = getActiveOrg()
  return o ? `&org_id=${encodeURIComponent(o)}` : ''
}

export function UploadTracePanel({ period, uploadType, onClose }:
  { period?: string; uploadType?: string; onClose: () => void }) {
  const [data, setData] = useState<any>(null)
  const [busy, setBusy] = useState(true)
  const [ut, setUt] = useState(uploadType || '')

  const load = useCallback(() => {
    setBusy(true)
    const q = `limit=100${period ? `&period=${encodeURIComponent(period)}` : ''}${ut ? `&upload_type=${encodeURIComponent(ut)}` : ''}${orgParam()}`
    api(`/api/v1/commcalc/upload-trace?${q}`)
      .then(setData).catch(e => setData({ ok: false, records: [], hint: String(e?.message || e) }))
      .finally(() => setBusy(false))
  }, [period, ut])
  useEffect(() => { load() }, [load])

  const recs: any[] = data?.records || []

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1100, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
      <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(960px,98vw)', maxHeight: '90vh', overflowY: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>🔎 Where are my rows? · upload trace</div>
          <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={onClose}>✕</button>
        </div>
        <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
          Every ingest attempt for this tenant — manual upload, email sweep, FTP sweep, and the daily-feed → monthly promotion.
          Each row shows which <b>org</b> the data landed in, rows-in vs saved, the per-day counts, the guard outcome, and any error.
          {data?.org_id && <> Reading org <code style={{ fontSize: 11 }}>{data.org_id}</code>.</>}
        </p>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
          <select value={ut} onChange={e => setUt(e.target.value)} style={{ padding: '4px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }}>
            <option value="">All types</option>
            {['daily_sales', 'sales', 'inventory_aging', 'mi_report', 'payment_detail', 'dlar_rep', 'dlar_store', 'x_report', 'comp_report'].map(t => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={load}>↻ Refresh</button>
          {period && <span style={{ fontSize: 12, color: 'var(--text3)' }}>period: {period}</span>}
        </div>
        {data && data.ok === false && (
          <div className="card" style={{ padding: 12, background: '#fef3c7', color: '#92400e', fontSize: 13, marginBottom: 10 }}>
            {data.hint || 'Upload trace not available yet — run migration 202.'}
          </div>
        )}
        {busy ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div>
        ) : recs.length === 0 ? (
          <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>
            No upload traces recorded {ut ? `for ${ut} ` : ''}yet for this tenant. If you just uploaded and see nothing here, the upload
            likely landed under a DIFFERENT org — check the org shown above matches the tenant you expect.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                {['When', 'Source', 'Type', 'Table', 'In→Saved', 'Status', 'Days / Periods', 'Detail'].map(h =>
                  <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {recs.map((r: any) => {
                  const tone = tones[r.status] || tones.ok
                  const days = r.date_counts && Object.keys(r.date_counts).length
                    ? Object.keys(r.date_counts).sort() : []
                  const periods = r.periods ? Object.keys(r.periods) : []
                  const detail = r.error || (r.guard && (r.guard.note || (Array.isArray(r.guard) ? (r.guard[0]?.reason) : JSON.stringify(r.guard)))) || r.note || ''
                  return (
                    <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{String(r.created_at || '').replace('T', ' ').slice(0, 16)}</td>
                      <td style={{ padding: '6px 8px' }}>{r.source}</td>
                      <td style={{ padding: '6px 8px' }}>{r.upload_type || '—'}</td>
                      <td style={{ padding: '6px 8px' }}>{r.target_table || '—'}</td>
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>{r.rows_in ?? '—'} → <b>{r.rows_saved ?? 0}</b></td>
                      <td style={{ padding: '6px 8px' }}><span style={{ background: tone.bg, color: tone.fg, borderRadius: 10, padding: '1px 8px', fontWeight: 600 }}>{r.status}</span>{r.skipped ? <span style={{ color: 'var(--text3)' }}> · {r.skipped}</span> : ''}</td>
                      <td style={{ padding: '6px 8px', maxWidth: 180 }}>
                        {days.length ? <span title={days.map(d => `${d}: ${r.date_counts[d]}`).join('\n')}>{days.length} day(s): {days[0]}…{days[days.length - 1]}</span>
                          : periods.length ? periods.join(', ') : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', maxWidth: 280, color: r.status === 'error' ? '#991b1b' : 'var(--text3)' }}>{String(detail).slice(0, 220)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

/** Small inline button that opens the trace panel. Drop it on any upload/report surface. */
export function WhereAreMyRowsButton({ period, uploadType, style }:
  { period?: string; uploadType?: string; style?: React.CSSProperties }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className="btn btn-secondary" style={{ fontSize: 12, ...style }} onClick={() => setOpen(true)}>🔎 Where are my rows?</button>
      {open && <UploadTracePanel period={period} uploadType={uploadType} onClose={() => setOpen(false)} />}
    </>
  )
}
