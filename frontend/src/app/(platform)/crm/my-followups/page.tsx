'use client'
// The rep's follow-up inbox — today, overdue, upcoming. Finishing a task here asks for the outcome,
// which is what books the NEXT step: the loop only closes if closing it is the easy path.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, fmtPhone, fmtDateTime, relTime,
  type Task, type Disposition,
} from '@/lib/crm'

export default function MyFollowupsPage() {
  const [rows, setRows] = useState<Task[]>([])
  const [dispositions, setDispositions] = useState<Disposition[]>([])
  const [reasons, setReasons] = useState<any[]>([])
  const [scope, setScope] = useState<'mine' | 'team'>('mine')
  const [status, setStatus] = useState<'open' | 'missed' | 'done'>('open')
  const [days, setDays] = useState(14)
  const [msg, setMsg] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState('')
  const [openId, setOpenId] = useState('')
  const [dispId, setDispId] = useState('')
  const [reasonId, setReasonId] = useState('')

  const load = useCallback(async () => {
    setMsg('')
    try {
      const p = new URLSearchParams({ scope, status, days: String(days) })
      const r = await api(`/api/v1/crm/tasks?${p}`)
      setRows(r.rows || [])
      if (r.note) setMsg(r.note)
    } catch (e: any) { setMsg(e?.message || String(e)); setRows([]) }
  }, [scope, status, days])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    Promise.all([api('/api/v1/crm/lists/dispositions'), api('/api/v1/crm/lists/reason-codes')])
      .then(([d, r]) => { setDispositions(d || []); setReasons(r || []) })
      .catch(() => { /* config unreachable — completing without an outcome still works */ })
  }, [])

  const chosen = dispositions.find(d => d.id === dispId)

  async function complete(t: Task, withOutcome: boolean) {
    setBusy(t.id); setMsg('')
    try {
      const body: any = {}
      if (withOutcome && dispId) {
        body.disposition_id = dispId
        body.reason_code_id = reasonId || null
        body.note = note
      }
      await api(`/api/v1/crm/tasks/${t.id}/complete`, { method: 'POST', body: JSON.stringify(body) })
      setOpenId(''); setDispId(''); setReasonId(''); setNote('')
      await load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy('')
  }

  async function snooze(t: Task, hours: number) {
    setBusy(t.id); setMsg('')
    try {
      await api(`/api/v1/crm/tasks/${t.id}/snooze`, { method: 'POST', body: JSON.stringify({ hours }) })
      await load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy('')
  }

  const overdue = rows.filter(t => t.is_overdue)
  const today = rows.filter(t => !t.is_overdue && t.is_today)
  const upcoming = rows.filter(t => !t.is_overdue && !t.is_today)

  const Section = ({ title, list, tint }: { title: string; list: Task[]; tint?: string }) => (
    <div style={{ ...panel, marginBottom: 14, borderColor: tint }}>
      <div style={{ fontWeight: 700, marginBottom: 8, color: tint }}>{title} ({list.length})</div>
      {list.length === 0 && <div style={{ fontSize: 13, color: 'var(--text2)' }}>Nothing here.</div>}
      {list.map(t => (
        <div key={t.id} style={{ borderBottom: '1px solid var(--border)', padding: '8px 0' }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'baseline' }}>
            <Link href={`/crm/leads/${t.lead_id}`} style={{ fontWeight: 600 }}>
              #{t.lead_no} {t.lead_name}
            </Link>
            {t.lead_phone && <a href={`tel:${t.lead_phone}`} style={{ fontSize: 13 }}>📞 {fmtPhone(t.lead_phone)}</a>}
            <span style={{ fontSize: 13 }}>{t.title}</span>
            <span style={{ fontSize: 12, color: t.is_overdue ? '#dc2626' : 'var(--text2)' }}>
              {fmtDateTime(t.due_at)} · {relTime(t.due_at)}
            </span>
            {t.lead_store && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{t.lead_store}</span>}
            {scope === 'team' && t.assigned_employee_id && (
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>→ {t.assigned_employee_id}</span>
            )}
          </div>
          {t.body && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>{t.body}</div>}
          {t.status !== 'done' && (
            <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
              <button style={{ ...btnPrimary, padding: '4px 10px', fontSize: 12 }} disabled={!!busy}
                      onClick={() => setOpenId(openId === t.id ? '' : t.id)}>
                {openId === t.id ? 'Cancel' : 'Record outcome'}
              </button>
              <button style={{ ...btn, padding: '4px 10px', fontSize: 12 }} disabled={!!busy}
                      onClick={() => complete(t, false)}>Done, no outcome</button>
              <button style={{ ...btn, padding: '4px 10px', fontSize: 12 }} disabled={!!busy}
                      onClick={() => snooze(t, 4)}>+4h</button>
              <button style={{ ...btn, padding: '4px 10px', fontSize: 12 }} disabled={!!busy}
                      onClick={() => snooze(t, 24)}>Tomorrow</button>
            </div>
          )}
          {openId === t.id && (
            <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'end' }}>
              <div style={{ flex: '1 1 200px' }}>
                <span style={label}>What happened?</span>
                <select value={dispId} onChange={e => { setDispId(e.target.value); setReasonId('') }} style={input}>
                  <option value="">Pick…</option>
                  {dispositions.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              {chosen?.requires_reason && (
                <div style={{ flex: '1 1 180px' }}>
                  <span style={label}>Reason *</span>
                  <select value={reasonId} onChange={e => setReasonId(e.target.value)} style={input}>
                    <option value="">Pick…</option>
                    {reasons.filter((r: any) => !r.disposition_id || r.disposition_id === dispId)
                      .map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </div>
              )}
              <input value={note} onChange={e => setNote(e.target.value)} placeholder="Note" style={{ ...input, flex: '1 1 200px' }} />
              <button style={btnPrimary} disabled={!dispId || !!busy} onClick={() => complete(t, true)}>Save</button>
            </div>
          )}
        </div>
      ))}
    </div>
  )

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🔔 My Follow-ups</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        Finish a follow-up by saying what happened — the next step gets booked for you.
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, alignItems: 'end', flexWrap: 'wrap', marginBottom: 14 }}>
        <div><span style={label}>Whose</span>
          <select value={scope} onChange={e => setScope(e.target.value as any)} style={{ ...input, width: 130 }}>
            <option value="mine">Mine</option><option value="team">My team</option>
          </select>
        </div>
        <div><span style={label}>Status</span>
          <select value={status} onChange={e => setStatus(e.target.value as any)} style={{ ...input, width: 130 }}>
            <option value="open">Open</option><option value="missed">Missed</option><option value="done">Done</option>
          </select>
        </div>
        <div><span style={label}>Next</span>
          <select value={days} onChange={e => setDays(Number(e.target.value))} style={{ ...input, width: 130 }}>
            <option value={1}>Today</option><option value={7}>7 days</option>
            <option value={14}>14 days</option><option value={90}>90 days</option>
          </select>
        </div>
        <button onClick={load} style={btn}>Refresh</button>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14 }}>{msg}</div>}

      <Section title="Overdue" list={overdue} tint="#dc2626" />
      <Section title="Today" list={today} tint="#f39c12" />
      <Section title="Coming up" list={upcoming} />
    </div>
  )
}
