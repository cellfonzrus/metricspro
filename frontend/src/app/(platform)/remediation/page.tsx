'use client'
import { useEffect, useState, useCallback } from 'react'
import { api } from '@/lib/client'

// Auto-remediation console. Describe an operational issue → the agent (Claude) classifies it data-vs-
// code, and for a DATA issue picks a WHITELISTED playbook + a dry-run preview, then sends the assignee a
// magic-link to approve. Code issues are escalated, never auto-fixed. Nothing mutates without approval.
const STATUS_TONE: Record<string, string> = {
  awaiting_approval: 'badge-amber', executed: 'badge-green', rejected: 'badge-slate',
  failed: 'badge-red', escalated: 'badge-blue', expired: 'badge-slate',
}

export default function RemediationConsole() {
  const [issue, setIssue] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<any>(null)
  const [err, setErr] = useState('')
  const [requests, setRequests] = useState<any[]>([])
  const [playbooks, setPlaybooks] = useState<any[]>([])

  const load = useCallback(() => {
    api('/api/v1/remediation/requests').then((r: any) => setRequests(r.requests || [])).catch(() => {})
    api('/api/v1/remediation/playbooks').then((r: any) => setPlaybooks(r.playbooks || [])).catch(() => {})
  }, [])
  useEffect(() => { load() }, [load])

  async function propose() {
    if (!issue.trim()) return
    setBusy(true); setErr(''); setResult(null)
    try {
      const r = await api('/api/v1/remediation/propose', {
        method: 'POST',
        body: JSON.stringify({ issue, assignee: email ? { email } : undefined, source: 'manual' }),
      })
      setResult(r); load()
    } catch (e: any) { setErr(e?.message || 'Could not propose a fix.') }
    finally { setBusy(false) }
  }

  const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 12,
    background: 'var(--surface)', padding: 18, marginBottom: 18 }

  return (
    <div style={{ maxWidth: 920, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, margin: '0 0 4px' }}>🤖 Auto-Remediation Agent</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '0 0 18px' }}>
        Describe an issue. The agent proposes a fix from a whitelisted playbook and sends it for a one-tap
        approval — then runs only that one bounded action. Code bugs are escalated to a developer.
      </p>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Report an issue</div>
        <textarea value={issue} onChange={e => setIssue(e.target.value)} rows={3}
          placeholder="e.g. Employee 50 has approved time off on 2026-07-06 that was voided but still can't be scheduled."
          style={{ width: '100%', padding: 10, borderRadius: 8, border: '1px solid var(--border)',
            background: 'var(--surface)', fontSize: 14, resize: 'vertical' }} />
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10 }}>
          <input value={email} onChange={e => setEmail(e.target.value)} placeholder="Approver email (optional)"
            style={{ flex: 1, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--border)',
              background: 'var(--surface)', fontSize: 13 }} />
          <button className="btn btn-primary" disabled={busy || !issue.trim()} onClick={propose}>
            {busy ? 'Diagnosing…' : 'Propose a fix'}</button>
        </div>
        {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{err}</div>}
        {result && (
          <div style={{ marginTop: 14, padding: 12, borderRadius: 8, background: 'var(--surface2, #f6f7f9)' }}>
            {result.escalated ? (
              <div><b>Escalated.</b> {result.message}</div>
            ) : (
              <>
                <div><b>Proposed:</b> {result.request?.proposed_action}</div>
                <div style={{ fontSize: 13, marginTop: 4 }}><b>Preview:</b> {result.preview?.summary}</div>
                <div style={{ fontSize: 13, marginTop: 6 }}>
                  Sent to: {result.notified?.length ? result.notified.join(', ') : 'no channel (use the link)'} ·{' '}
                  <a href={result.approval_url} style={{ color: '#2563eb' }}>Open approval</a>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>Recent requests</div>
        {!requests.length && <div style={{ color: 'var(--text3)', fontSize: 13 }}>None yet.</div>}
        {requests.map(r => (
          <div key={r.id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '8px 0',
            borderTop: '1px solid var(--border)' }}>
            <span className={`badge ${STATUS_TONE[r.status] || 'badge-slate'}`}
              style={{ textTransform: 'capitalize', minWidth: 96, textAlign: 'center' }}>
              {String(r.status).replace('_', ' ')}</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 14 }}>{r.proposed_action || r.title}</div>
              <div style={{ fontSize: 12, color: 'var(--text3)' }}>
                {r.playbook_key || r.issue_class} · {String(r.created_at || '').slice(0, 16).replace('T', ' ')}
                {r.result?.summary ? ` · ${r.result.summary}` : ''}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>What the agent can fix (playbook catalog)</div>
        {playbooks.map(p => (
          <div key={p.key} style={{ padding: '8px 0', borderTop: '1px solid var(--border)', opacity: p.enabled && p.implemented ? 1 : 0.5 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {p.name} <span style={{ fontSize: 11, color: 'var(--text3)' }}>· {p.risk_level} risk
                {!(p.enabled && p.implemented) ? ' · roadmap' : ''}</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)' }}>{p.description}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
