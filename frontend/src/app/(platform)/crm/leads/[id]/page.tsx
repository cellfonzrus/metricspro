'use client'
// Lead detail — identity, the immutable timeline, the disposition panel, assignment, follow-ups.
// This is where a rep actually works: every outcome here books the next step automatically, which is
// the difference between a CRM and a list of names.
import { useCallback, useEffect, useState } from 'react'
import { useParams, useRouter } from 'next/navigation'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, fmtPhone, fmtMoney, fmtDateTime, relTime, toLocalInput,
  ACTIVITY_ICON, STATUS_COLOR, PRIORITY_COLOR,
  type Lead, type Stage, type Task, type Activity, type Disposition, type RefRow,
} from '@/lib/crm'

export default function LeadDetailPage() {
  const { id } = useParams<{ id: string }>()
  const router = useRouter()
  const [lead, setLead] = useState<Lead | null>(null)
  const [activity, setActivity] = useState<Activity[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [assignments, setAssignments] = useState<any[]>([])
  const [vocab, setVocab] = useState<{ stages: Stage[]; dispositions: Disposition[]; agencies: RefRow[]; sources: RefRow[]; interests: RefRow[] } | null>(null)
  const [reasons, setReasons] = useState<RefRow[]>([])
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const [dispId, setDispId] = useState('')
  const [reasonId, setReasonId] = useState('')
  const [note, setNote] = useState('')
  const [followupAt, setFollowupAt] = useState('')

  const [logKind, setLogKind] = useState('call')
  const [logBody, setLogBody] = useState('')

  const [assignKind, setAssignKind] = useState<'employee' | 'agency'>('employee')
  const [assignTarget, setAssignTarget] = useState('')
  const [assignReason, setAssignReason] = useState('')

  const [taskTitle, setTaskTitle] = useState('Follow up')
  const [taskDue, setTaskDue] = useState('')

  const load = useCallback(async () => {
    setMsg('')
    try {
      const r = await api(`/api/v1/crm/leads/${id}`)
      setLead(r.lead); setActivity(r.activity || []); setTasks(r.tasks || [])
      setAssignments(r.assignments || []); setVocab(r.vocab)
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [id])

  useEffect(() => { load() }, [load])
  useEffect(() => { api('/api/v1/crm/lists/reason-codes').then(setReasons).catch(() => setReasons([])) }, [])

  const chosen = vocab?.dispositions.find(d => d.id === dispId)

  async function call(path: string, body: any, method = 'POST') {
    setBusy(true); setMsg('')
    try {
      await api(path, { method, body: JSON.stringify(body) })
      await load()
      return true
    } catch (e: any) { setMsg(e?.message || String(e)); return false } finally { setBusy(false) }
  }

  async function dispose() {
    if (!dispId) { setMsg('Pick what happened first.'); return }
    const ok = await call(`/api/v1/crm/leads/${id}/dispose`, {
      disposition_id: dispId, reason_code_id: reasonId || null, note,
      followup_at: followupAt ? new Date(followupAt).toISOString() : null,
    })
    if (ok) { setDispId(''); setReasonId(''); setNote(''); setFollowupAt('') }
  }

  if (!lead) {
    return <div style={{ padding: 20 }}>{msg ? <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div> : 'Loading…'}</div>
  }

  const openStages = (vocab?.stages || []).filter(s => s.is_active !== false)
    .sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))

  return (
    <div style={{ padding: 20, maxWidth: 1300 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 12 }}>
        <Link href="/crm/leads" style={{ fontSize: 13 }}>← Leads</Link>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>{lead.display_name}</h1>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>#{lead.lead_no}</span>
        <span style={{ padding: '2px 9px', borderRadius: 999, fontSize: 11, fontWeight: 700,
                       background: `${STATUS_COLOR[lead.status]}22`, color: STATUS_COLOR[lead.status] }}>
          {lead.status.toUpperCase()}
        </span>
        <span style={{ fontSize: 12, color: PRIORITY_COLOR[lead.priority], fontWeight: 600 }}>
          {lead.priority} · score {lead.score}
        </span>
        <div style={{ flex: 1 }} />
        {lead.phone && <a href={`tel:${lead.phone}`} style={{ ...btnPrimary, textDecoration: 'none' }}>📞 {fmtPhone(lead.phone)}</a>}
        <Link href={`/crm/lookup?phone=${encodeURIComponent(lead.phone || '')}`} style={{ ...btn, textDecoration: 'none' }}>🔎 Customer history</Link>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-start' }}>
        {/* ── left column: work the lead ─────────────────────────────────────────── */}
        <div style={{ flex: '2 1 520px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 10 }}>What happened?</div>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'end' }}>
              <div style={{ flex: '1 1 220px' }}>
                <span style={label}>Outcome</span>
                <select value={dispId} onChange={e => { setDispId(e.target.value); setReasonId('') }} style={input}>
                  <option value="">Pick an outcome…</option>
                  {(vocab?.dispositions || []).map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                </select>
              </div>
              {chosen?.requires_reason && (
                <div style={{ flex: '1 1 200px' }}>
                  <span style={label}>Reason *</span>
                  <select value={reasonId} onChange={e => setReasonId(e.target.value)} style={input}>
                    <option value="">Pick a reason…</option>
                    {reasons.filter(r => !r.disposition_id || r.disposition_id === dispId)
                      .map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </div>
              )}
              {chosen?.requires_followup && (
                <div style={{ flex: '1 1 200px' }}>
                  <span style={label}>Follow up on</span>
                  <input type="datetime-local" value={followupAt} onChange={e => setFollowupAt(e.target.value)}
                         style={input} />
                  <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>
                    Leave blank for the standard {chosen.default_followup_hours ?? 24}h.
                  </div>
                </div>
              )}
            </div>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={2} placeholder="Note (optional)"
                      style={{ ...input, marginTop: 10, resize: 'vertical' }} />
            <div style={{ marginTop: 10, display: 'flex', gap: 10, alignItems: 'center' }}>
              <button onClick={dispose} disabled={busy || !dispId} style={btnPrimary}>Record outcome</button>
              {chosen?.closes_lead && <span style={{ fontSize: 12, color: '#dc2626' }}>This closes the lead.</span>}
              {chosen?.requires_followup && <span style={{ fontSize: 12, color: 'var(--text2)' }}>A follow-up will be booked automatically.</span>}
            </div>
          </div>

          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 10 }}>Log a touch</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <select value={logKind} onChange={e => setLogKind(e.target.value)} style={{ ...input, width: 130 }}>
                <option value="call">Call</option><option value="sms">Text</option>
                <option value="email">Email</option><option value="whatsapp">WhatsApp</option>
                <option value="visit">Visit</option><option value="note">Note</option>
              </select>
              <input value={logBody} onChange={e => setLogBody(e.target.value)} placeholder="What was said?" style={input} />
              <button style={btn} disabled={busy}
                      onClick={async () => { if (await call(`/api/v1/crm/leads/${id}/activity`, { kind: logKind, body: logBody })) setLogBody('') }}>
                Log
              </button>
            </div>
          </div>

          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 10 }}>Timeline</div>
            {activity.length === 0 && <div style={{ fontSize: 13, color: 'var(--text2)' }}>Nothing logged yet.</div>}
            {activity.map(a => (
              <div key={a.id} style={{ display: 'flex', gap: 10, padding: '6px 0', borderBottom: '1px solid var(--border)' }}>
                <div style={{ width: 22 }}>{ACTIVITY_ICON[a.kind] || '•'}</div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13 }}>{a.body || a.kind}</div>
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>
                    {fmtDateTime(a.created_at)}{a.actor_employee_id ? ` · ${a.actor_employee_id}` : ''}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* ── right column: state ────────────────────────────────────────────────── */}
        <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Stage</div>
            <select value={lead.stage_id || ''} disabled={busy} style={input}
                    onChange={e => call(`/api/v1/crm/leads/${id}/stage`, { stage_id: e.target.value })}>
              {openStages.map(s => <option key={s.id} value={s.id}>{s.name} ({s.probability}%)</option>)}
            </select>
            <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>
              A won/lost stage asks for an outcome before it will let go.
            </div>
          </div>

          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Who owns it</div>
            <div style={{ fontSize: 13, marginBottom: 8 }}>
              {lead.agency_name
                ? <>🤝 {lead.agency_name} {lead.agency_accepted_at
                    ? <span style={{ color: '#16a34a' }}>· accepted</span>
                    : <span style={{ color: '#f39c12' }}>· waiting for a response</span>}</>
                : (lead.owner_employee_id || <span style={{ color: '#f39c12' }}>Nobody yet</span>)}
            </div>
            <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
              <select value={assignKind} onChange={e => { setAssignKind(e.target.value as any); setAssignTarget('') }} style={{ ...input, width: 110 }}>
                <option value="employee">Teammate</option><option value="agency">Agency</option>
              </select>
              {assignKind === 'agency' ? (
                <select value={assignTarget} onChange={e => setAssignTarget(e.target.value)} style={input}>
                  <option value="">Pick an agency…</option>
                  {(vocab?.agencies || []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              ) : (
                <input value={assignTarget} onChange={e => setAssignTarget(e.target.value)} placeholder="Employee id" style={input} />
              )}
            </div>
            <input value={assignReason} onChange={e => setAssignReason(e.target.value)} placeholder="Why? (optional)" style={{ ...input, marginBottom: 6 }} />
            <button style={btn} disabled={busy || !assignTarget}
                    onClick={async () => {
                      const body: any = { reason: assignReason }
                      body[assignKind === 'agency' ? 'agency_id' : 'employee_id'] = assignTarget
                      if (await call(`/api/v1/crm/leads/${id}/assign`, body)) { setAssignTarget(''); setAssignReason('') }
                    }}>
              Hand it over
            </button>
            {lead.agency_id && !lead.agency_accepted_at && (
              <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                <button style={btn} disabled={busy}
                        onClick={() => call(`/api/v1/crm/leads/${id}/agency-response`, { accepted: true })}>Agency accepted</button>
                <button style={btn} disabled={busy}
                        onClick={() => call(`/api/v1/crm/leads/${id}/agency-response`, { accepted: false, reason: 'declined' })}>Declined</button>
              </div>
            )}
            {assignments.length > 0 && (
              <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text2)' }}>
                {assignments.slice(0, 4).map(a => (
                  <div key={a.id}>{fmtDateTime(a.created_at)} → {a.to_employee_id || a.to_agency_id || 'queue'}{a.reason ? ` (${a.reason})` : ''}</div>
                ))}
              </div>
            )}
          </div>

          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Follow-ups</div>
            {tasks.filter(t => t.status === 'open' || t.status === 'missed').map(t => (
              <div key={t.id} style={{ borderBottom: '1px solid var(--border)', padding: '6px 0' }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>{t.title}</div>
                <div style={{ fontSize: 11, color: t.status === 'missed' ? '#dc2626' : 'var(--text2)' }}>
                  {fmtDateTime(t.due_at)} · {relTime(t.due_at)}{t.status === 'missed' ? ' · MISSED' : ''}
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 4 }}>
                  <button style={{ ...btn, padding: '3px 8px', fontSize: 12 }} disabled={busy}
                          onClick={() => call(`/api/v1/crm/tasks/${t.id}/complete`, {})}>Done</button>
                  <button style={{ ...btn, padding: '3px 8px', fontSize: 12 }} disabled={busy}
                          onClick={() => call(`/api/v1/crm/tasks/${t.id}/snooze`, { hours: 24 })}>+1 day</button>
                </div>
              </div>
            ))}
            {tasks.filter(t => t.status === 'open' || t.status === 'missed').length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>Nothing scheduled.</div>
            )}
            <div style={{ display: 'flex', gap: 6, marginTop: 10 }}>
              <input value={taskTitle} onChange={e => setTaskTitle(e.target.value)} style={input} />
              <input type="datetime-local" value={taskDue || toLocalInput(new Date(Date.now() + 864e5))}
                     onChange={e => setTaskDue(e.target.value)} style={{ ...input, width: 190 }} />
              <button style={btn} disabled={busy}
                      onClick={() => call('/api/v1/crm/tasks', {
                        lead_id: id, title: taskTitle,
                        due_at: new Date(taskDue || Date.now() + 864e5).toISOString(),
                      })}>Add</button>
            </div>
          </div>

          <div style={panel}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>Details</div>
            <div style={{ fontSize: 13, lineHeight: 1.7 }}>
              <div>📞 {fmtPhone(lead.phone)}</div>
              <div>✉️ {lead.email || '—'}</div>
              <div>🏬 {lead.store_code || '—'}{lead.market ? ` · ${lead.market}` : ''}</div>
              <div>📥 {lead.source_name || '—'}</div>
              <div>🎁 {lead.interest_name || '—'}</div>
              <div>💰 {fmtMoney(lead.value_estimate)} · {lead.lines_estimate} line(s)</div>
              <div>🕒 created {fmtDateTime(lead.created_at)}</div>
              <div>⏱️ last activity {relTime(lead.last_activity_at || lead.created_at)}</div>
            </div>
            {lead.notes && <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8, whiteSpace: 'pre-wrap' }}>{lead.notes}</div>}
            {lead.status === 'won' && !lead.converted_customer_id && (
              <button style={{ ...btnPrimary, marginTop: 10 }} disabled={busy}
                      onClick={() => call(`/api/v1/crm/leads/${id}/convert`, {})}>
                Create the customer record
              </button>
            )}
            {lead.converted_customer_id && (
              <div style={{ fontSize: 12, color: '#16a34a', marginTop: 8 }}>✅ Linked to a customer record.</div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
