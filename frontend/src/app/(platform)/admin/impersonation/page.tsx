'use client'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'

// ── "Sign in as an employee" — audit log + policy (owner directive 2026-08-06) ───────────────────
// Requirement 4 of the impersonation build: the trail must be VIEWABLE, not just written. Every
// session shows who did it, whom they viewed as, when it started and ended, and (on expand) every
// change made while they were wearing that face — each row written BEFORE the change happened, so a
// write that could not be attributed never happened at all.
//
// Everything degrades: before migration 730 runs, the backend answers ready:false and this page says
// so in plain English instead of erroring.

type Session = {
  id: string; org_id?: string
  actor_email?: string | null; actor_name?: string | null
  target_name?: string | null; target_email?: string | null; target_role?: string | null
  started_at?: string | null; expires_at?: string | null; ended_at?: string | null
  end_reason?: string | null; reason?: string | null; ip?: string | null
}
type Action = {
  id: string; kind: string; method?: string | null; path?: string | null; query?: string | null
  status?: number | null; at?: string | null; detail?: any; ip?: string | null
}
type Policy = { enabled: boolean; max_minutes: number; reauth_minutes: number; reauth_token_max_age_s: number }

const when = (s?: string | null) => (s ? new Date(s).toLocaleString() : '—')
const mins = (a?: string | null, b?: string | null) => {
  if (!a || !b) return '—'
  const d = (Date.parse(b) - Date.parse(a)) / 60000
  return Number.isFinite(d) ? `${Math.max(0, Math.round(d))} min` : '—'
}

const KIND_LABEL: Record<string, string> = {
  start: 'Session started', stop: 'Session ended', write: 'Change made',
  reauth: 'Employee entered their password', reauth_used: 'Clock in/out unlocked',
  denied: 'Blocked',
}

export default function ImpersonationAuditPage() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [ready, setReady] = useState(true)
  const [policy, setPolicy] = useState<Policy | null>(null)
  const [canEdit, setCanEdit] = useState(false)
  const [open, setOpen] = useState<string>('')
  const [actions, setActions] = useState<Action[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [d, p] = await Promise.all([
        api('/api/v1/core/impersonation/log?limit=200'),
        api('/api/v1/core/impersonation/policy').catch(() => null),
      ])
      setSessions(d?.sessions || [])
      setReady(d?.ready !== false)
      if (p) { setPolicy(p.policy || null); setCanEdit(!!p.can_edit) }
      else if (d?.policy) setPolicy(d.policy)
    } catch (e: any) {
      setErr(e?.message || 'Could not load the audit log')
    }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])

  async function expand(id: string) {
    if (open === id) { setOpen(''); setActions([]); return }
    setOpen(id); setActions([])
    try {
      const d = await api(`/api/v1/core/impersonation/log?limit=1&session_id=${encodeURIComponent(id)}`)
      setActions(d?.actions || [])
    } catch { setActions([]) }
  }

  async function savePolicy(patch: Partial<Policy>) {
    if (!policy) return
    const next = { ...policy, ...patch }
    setPolicy(next); setMsg('')
    try {
      await api('/api/v1/core/impersonation/policy', { method: 'PUT', body: JSON.stringify({ policy: next }) })
      setMsg('Saved.')
    } catch (e: any) { setMsg(e?.message || 'Could not save') }
  }

  const live = sessions.filter(s => !s.ended_at)

  return (
    <div>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🕵️ Sign-in-as Audit</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820, lineHeight: 1.55 }}>
          Every time an administrator opens the app as one of your employees, it is recorded here — who
          did it, whom they viewed as, for how long, and every change they made while doing so. Start a
          session from <Link href="/admin/roles" style={{ fontWeight: 600 }}>Roles &amp; Access</Link>;
          the permission is off for every role until someone turns it on.
        </p>
      </div>

      {!ready && (
        <div className="card" style={{ padding: 14, marginBottom: 16, background: '#fffbeb', borderLeft: '5px solid #f59e0b' }}>
          <b>Not set up yet.</b> The audit tables have not been created in the database (migration 730).
          Until they are, nobody can sign in as an employee at all — the feature refuses to start rather
          than run without a record.
        </div>
      )}

      {policy && (
        <div className="card" style={{ padding: 16, marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 8 }}>⚙️ Policy for this company</div>
          <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', alignItems: 'flex-end' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 600 }}>
              <input type="checkbox" checked={policy.enabled} disabled={!canEdit}
                onChange={e => savePolicy({ enabled: e.target.checked })} />
              Allow administrators to view the app as an employee
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 11, color: 'var(--text2)', fontWeight: 600 }}>
              Session ends automatically after (minutes)
              <input type="number" min={5} max={240} value={policy.max_minutes} disabled={!canEdit}
                onChange={e => setPolicy({ ...policy, max_minutes: Number(e.target.value) })}
                onBlur={e => savePolicy({ max_minutes: Number(e.target.value) })}
                style={{ padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, width: 110 }} />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 3, fontSize: 11, color: 'var(--text2)', fontWeight: 600 }}>
              Clock in/out unlock lasts (minutes)
              <input type="number" min={1} max={30} value={policy.reauth_minutes} disabled={!canEdit}
                onChange={e => setPolicy({ ...policy, reauth_minutes: Number(e.target.value) })}
                onBlur={e => savePolicy({ reauth_minutes: Number(e.target.value) })}
                style={{ padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, width: 110 }} />
            </label>
            {msg && <span style={{ fontSize: 12.5, color: 'var(--text2)' }}>{msg}</span>}
          </div>
          {!canEdit && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
            Read-only — ask an administrator to grant your role the “Sign in as an employee — policy &amp;
            audit log” setting on Roles &amp; Access.
          </div>}
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8, maxWidth: 780, lineHeight: 1.5 }}>
            An unlock is good for exactly ONE clock in or clock out, and the employee has to enter their
            password again for the next one.
          </div>
        </div>
      )}

      {live.length > 0 && (
        <div className="card" style={{ padding: 14, marginBottom: 16, background: '#fef2f2', borderLeft: '5px solid #7f1d1d' }}>
          <b>{live.length} session{live.length === 1 ? '' : 's'} open right now.</b>{' '}
          {live.map(s => `${s.actor_email || 'an admin'} → ${s.target_name || s.target_email || 'employee'}`).join(' · ')}
        </div>
      )}

      {err && <div className="card" style={{ padding: 14, marginBottom: 16, color: '#b91c1c' }}>{err}</div>}

      <div className="card" style={{ padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ background: 'var(--surface)', textAlign: 'left' }}>
              <th style={{ padding: '9px 12px' }}>Started</th>
              <th style={{ padding: '9px 12px' }}>Administrator</th>
              <th style={{ padding: '9px 12px' }}>Viewed as</th>
              <th style={{ padding: '9px 12px' }}>Why</th>
              <th style={{ padding: '9px 12px' }}>Length</th>
              <th style={{ padding: '9px 12px' }}>Ended</th>
              <th style={{ padding: '9px 12px' }}>From</th>
              <th style={{ padding: '9px 12px' }}></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={8} style={{ padding: 24, color: 'var(--text3)' }}>Loading…</td></tr>}
            {!loading && sessions.length === 0 && (
              <tr><td colSpan={8} style={{ padding: 24, color: 'var(--text3)' }}>
                Nobody has signed in as an employee yet.
              </td></tr>
            )}
            {sessions.map(s => (
              <tr key={s.id} style={{ borderTop: '1px solid var(--border)',
                background: s.ended_at ? undefined : '#fef2f2' }}>
                <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>{when(s.started_at)}</td>
                <td style={{ padding: '9px 12px' }}>{s.actor_name || s.actor_email || '—'}</td>
                <td style={{ padding: '9px 12px' }}>
                  <b>{s.target_name || s.target_email || '—'}</b>
                  {s.target_role ? <span style={{ color: 'var(--text3)' }}> · {s.target_role}</span> : null}
                </td>
                <td style={{ padding: '9px 12px', color: 'var(--text2)' }}>{s.reason || '—'}</td>
                <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>{mins(s.started_at, s.ended_at)}</td>
                <td style={{ padding: '9px 12px', whiteSpace: 'nowrap' }}>
                  {s.ended_at ? `${when(s.ended_at)}${s.end_reason ? ` (${s.end_reason})` : ''}`
                              : <b style={{ color: '#7f1d1d' }}>still open</b>}
                </td>
                <td style={{ padding: '9px 12px', color: 'var(--text3)' }}>{s.ip || '—'}</td>
                <td style={{ padding: '9px 12px' }}>
                  <button className="btn" onClick={() => expand(s.id)} style={{ fontSize: 12 }}>
                    {open === s.id ? 'Hide changes' : 'Changes'}
                  </button>
                </td>
              </tr>
            ))}
            {open && (
              <tr>
                <td colSpan={8} style={{ padding: '10px 16px', background: 'var(--surface)' }}>
                  {actions.length === 0
                    ? <span style={{ fontSize: 12.5, color: 'var(--text3)' }}>
                        Nothing was changed during that session (viewing only).
                      </span>
                    : (
                      <div style={{ display: 'grid', gap: 4 }}>
                        {actions.map(a => (
                          <div key={a.id} style={{ fontSize: 12.5, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                            <span style={{ color: 'var(--text3)', minWidth: 150 }}>{when(a.at)}</span>
                            <span style={{ fontWeight: 600, minWidth: 190 }}>{KIND_LABEL[a.kind] || a.kind}</span>
                            <span style={{ fontFamily: 'ui-monospace, monospace' }}>
                              {a.method ? `${a.method} ` : ''}{a.path || ''}
                            </span>
                            {a.status ? <span style={{ color: a.status < 400 ? '#059669' : '#b91c1c' }}>{a.status}</span> : null}
                          </div>
                        ))}
                      </div>
                    )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
