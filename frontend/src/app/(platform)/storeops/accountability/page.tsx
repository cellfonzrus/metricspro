'use client'
import { useState, useEffect } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'

// Accountability lens: per-employee attendance patterns, flagged against policy, with POSITIVE coaching
// recommendations. Surfaces patterns for a manager conversation — never proposes discipline.
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const FLAG_LABEL: Record<string, string> = { punctuality: 'Punctuality', attendance: 'Attendance', early_departure: 'Leaves early' }

function monthRange(period: string): [string, string] {
  let y = 0, m = 0
  const iso = /^(\d{4})-(\d{2})/.exec(period)
  if (iso) { y = +iso[1]; m = +iso[2] }
  else { const p = period.split(' '); const mi = MONTHS.indexOf(p[0]); if (mi >= 0 && p[1]) { m = mi + 1; y = +p[1] } }
  if (!y || !m) { const d = new Date(); y = d.getFullYear(); m = d.getMonth() + 1 }
  const last = new Date(y, m, 0).getDate()
  const mm = String(m).padStart(2, '0')
  return [`${y}-${mm}-01`, `${y}-${mm}-${String(last).padStart(2, '0')}`]
}

export default function AccountabilityPage() {
  const { period } = usePeriod()
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => { const [s, e] = monthRange(period); setStart(s); setEnd(e) }, [period])

  async function load(s = start, e = end) {
    if (!s || !e) return
    setLoading(true); setMsg('')
    try {
      const r: any = await api(`/api/v1/storeops/accountability?start=${s}&end=${e}&org_id=${ORG_ID}`)
      setData(r)
      if (r.limit_hit) setMsg('Large range — results may be capped; narrow the dates for a complete picture.')
    } catch (e2: any) { setMsg('❌ ' + (e2?.message || e2)); setData(null) } finally { setLoading(false) }
  }
  useEffect(() => { if (start && end) load(start, end) }, [start, end]) // eslint-disable-line

  const emps: any[] = data?.employees || []
  const recs: any[] = data?.recommendations || []
  const th = data?.thresholds || {}

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Accountability</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Attendance patterns to <b>coach</b> on — surfaced for a supportive conversation, not a penalty. You decide any action.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>From <input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
        <label style={{ fontSize: 13, color: 'var(--text2)' }}>To <input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
        <button className="btn" disabled={loading} onClick={() => load()}>{loading ? '…' : 'Refresh'}</button>
        <a href="/storeops/timeclock" className="btn btn-secondary" style={{ fontSize: 12 }}>⚙️ Policy (grace periods)</a>
      </div>

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', marginBottom: 12 }}>{msg}</div>}

      {data && (
        <>
          {recs.length > 0 ? (
            <div className="card" style={{ padding: 14, marginBottom: 14 }}>
              <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 8 }}>Coaching opportunities</div>
              {recs.map((rec, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, padding: '6px 0', borderTop: i ? '1px solid var(--border)' : 'none' }}>
                  <span style={{ fontSize: 16 }}>🎓</span>
                  <span style={{ fontSize: 13 }}>{rec.text}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="card" style={{ padding: 14, marginBottom: 14, color: 'var(--green)', fontSize: 13 }}>✓ No attendance patterns cross the coaching thresholds for this range.</div>
          )}

          <div className="card" style={{ padding: 14 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>By employee</div>
            <div style={{ overflowX: 'auto', marginTop: 10 }}>
              <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                <thead><tr>{['Employee', 'Shifts', 'Late', 'Late %', 'No-show', 'Left early', 'Excused', 'Flags'].map((h, i) => <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '4px 12px 8px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
                <tbody>
                  {emps.map((r, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '5px 12px 5px 0', fontWeight: 500 }}>{r.employee}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.total_shifts}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.late}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: r.late_rate >= (th.late_rate_flag ?? 0.25) ? '#b45309' : 'var(--text2)' }}>{Math.round(r.late_rate * 100)}%</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: r.no_show >= (th.no_show_flag ?? 2) ? '#dc2626' : 'var(--text2)' }}>{r.no_show}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.left_early}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: 'var(--text3)' }}>{r.excused}</td>
                      <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>
                        {(r.flags || []).map((f: string) => <span key={f} className="badge badge-amber" style={{ fontSize: 10, marginLeft: 4 }}>{FLAG_LABEL[f] || f}</span>)}
                      </td>
                    </tr>
                  ))}
                  {emps.length === 0 && <tr><td colSpan={8} style={{ padding: 12, color: 'var(--text3)' }}>No attendance exceptions in this range.</td></tr>}
                </tbody>
              </table>
            </div>
            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
              Flags at ≥{Math.round((th.late_rate_flag ?? 0.25) * 100)}% late, ≥{th.no_show_flag ?? 2} unexcused no-shows, or ≥{Math.round((th.left_early_rate_flag ?? 0.25) * 100)}% left-early (min {th.min_shifts ?? 5} shifts). Excused time-off is never counted against anyone.
            </div>
          </div>
        </>
      )}
    </div>
  )
}
