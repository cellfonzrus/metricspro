'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'

// Duplicate-submission report (mig 502, retail-ops-7 item 1) — READ-ONLY: lists suspected duplicate
// daily_closing rows (same org+store+employee+day, 2+ rows), the exact fingerprint of the
// double-submit bug that used to silently DOUBLE a store's declared cash/credit in recon. NEVER
// auto-deletes or auto-merges — management reviews each group and RELEASES the row they want to keep
// editable (POST /closing/row/{id}/release), or deletes a true throwaway duplicate outright.
// Permission-gated the same as /closing/management (DMs excluded).

export default function ClosingDuplicatesPage() {
  const [mode, setMode] = useState<'date' | 'period'>('period')
  const [date, setDate] = useState(() => localToday())
  const [period, setPeriod] = useState(() => localToday().slice(0, 7))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const [msg, setMsg] = useState<Record<string, string>>({})

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const q = mode === 'date' ? `date=${date}` : `period=${encodeURIComponent(period)}`
    api(`/api/v1/closing/duplicates?${q}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [mode, date, period])
  useEffect(() => { load() }, [load])

  async function release(groupKey: string, rowId: string, released: boolean) {
    setBusy(b => ({ ...b, [rowId]: true })); setMsg(m => ({ ...m, [groupKey]: '' }))
    try {
      await api(`/api/v1/closing/row/${rowId}/release`, { method: 'POST', body: JSON.stringify({ released }) })
      setMsg(m => ({ ...m, [groupKey]: released ? '✅ Released — that row can now be corrected (one resubmit).' : '✅ Re-locked.' }))
      load()
    } catch (e: any) { setMsg(m => ({ ...m, [groupKey]: '❌ ' + (e?.message || e) })) }
    finally { setBusy(b => ({ ...b, [rowId]: false })) }
  }
  async function deleteRow(groupKey: string, rowId: string) {
    if (!confirm('Delete this duplicate row permanently? This cannot be undone.')) return
    setBusy(b => ({ ...b, [rowId]: true }))
    try {
      await api(`/api/v1/closing/row/${rowId}`, { method: 'DELETE' })
      setMsg(m => ({ ...m, [groupKey]: '✅ Deleted.' })); load()
    } catch (e: any) { setMsg(m => ({ ...m, [groupKey]: '❌ ' + (e?.message || e) })) }
    finally { setBusy(b => ({ ...b, [rowId]: false })) }
  }

  const groups: any[] = data?.groups || []
  const forbidden = /permission|403|restricted/i.test(err)
  const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Duplicate Closing Submissions</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Same store + rep + day submitted more than once — before migration 502 this silently{' '}
            <strong>doubled</strong> that store&apos;s declared cash/credit in every recon. Nothing here is
            auto-deleted or auto-merged: review each group, then either <strong>Release</strong> the row you
            want to keep so it can be corrected in place, or <strong>Delete</strong> a true throwaway extra.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'date' ? 'var(--accent)' : 'transparent', color: mode === 'date' ? 'white' : 'var(--text2)' }} onClick={() => setMode('date')}>Day</button>
            <button className="btn" style={{ borderRadius: 0, border: 'none', background: mode === 'period' ? 'var(--accent)' : 'transparent', color: mode === 'period' ? 'white' : 'var(--text2)' }} onClick={() => setMode('period')}>Month</button>
          </div>
          {mode === 'date'
            ? <input className="select" style={sel} type="date" value={date} onChange={e => setDate(e.target.value)} />
            : <input className="select" style={{ ...sel, width: 110 }} placeholder="2026-07" value={period} onChange={e => setPeriod(e.target.value)} />}
          <Link href="/closing/management" className="btn btn-secondary" style={{ fontSize: 12 }}>🛡️ Management Review</Link>
        </div>
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : forbidden ? (
        <div className="card" style={{ padding: 24, textAlign: 'center' }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>🔒 Restricted</div>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 6 }}>This report is limited to company-wide leadership (the same access as Management Review).</div>
        </div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : groups.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>
          ✅ No duplicate submissions found for {mode === 'date' ? date : period}.
        </div>
      ) : (
        <div style={{ display: 'grid', gap: 10 }}>
          <div style={{ fontSize: 12, color: 'var(--text3)' }}>{data.total_groups} group(s) · {data.total_duplicate_rows} row(s) total</div>
          {groups.map((g: any, gi: number) => {
            const gk = `${g.close_date}|${g.store_code}|${g.employee_name}`
            return (
              <div key={gi} className="card" style={{ padding: 0, overflow: 'hidden', border: '1px solid #f3b4b4' }}>
                <div style={{ padding: '10px 14px', background: '#fffafa', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>{g.store_address || g.store_code}</span>
                  <span style={{ fontSize: 13 }}>{g.employee_name || '—'}</span>
                  <span style={{ fontSize: 12, color: 'var(--text3)' }}>{g.close_date}</span>
                  <span className="badge" style={{ fontSize: 11, background: '#fbe4e4', color: '#b42318' }}>{g.row_count} rows{g.any_released ? ' · one released' : ''}</span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <thead><tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                    {['Submitted', 'Source', 'Cash', 'Credit', 'Status', ''].map(h =>
                      <th key={h} style={{ textAlign: 'left', padding: '6px 10px' }}>{h}</th>)}
                  </tr></thead>
                  <tbody>
                    {g.rows.map((r: any) => (
                      <tr key={r.id} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px 10px', fontSize: 12 }}>{r.submitted_at ? new Date(r.submitted_at).toLocaleString() : '—'}</td>
                        <td style={{ padding: '6px 10px', fontSize: 12, color: 'var(--text3)' }}>{r.source || '—'}</td>
                        <td style={{ padding: '6px 10px', fontSize: 12 }}>{fmt(r.cash)}</td>
                        <td style={{ padding: '6px 10px', fontSize: 12 }}>{fmt(r.credit)}</td>
                        <td style={{ padding: '6px 10px', fontSize: 12 }}>
                          {r.released_at ? <span style={{ color: '#1d4ed8' }}>🔓 released by {r.released_by || 'management'}</span> : <span style={{ color: 'var(--text3)' }}>locked</span>}
                        </td>
                        <td style={{ padding: '6px 10px', textAlign: 'right', display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 8px' }} disabled={!!busy[r.id]}
                            onClick={() => release(gk, r.id, !r.released_at)}>
                            {r.released_at ? '🔒 Re-lock' : '🔓 Release'}
                          </button>
                          <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 8px', color: '#dc2626' }} disabled={!!busy[r.id]}
                            onClick={() => deleteRow(gk, r.id)}>
                            🗑 Delete
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {msg[gk] && <div style={{ fontSize: 12, padding: '8px 14px', color: msg[gk].startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{msg[gk]}</div>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
