'use client'
import { useEffect, useState } from 'react'
import { useParams, useSearchParams } from 'next/navigation'
import { api } from '@/lib/client'

// Magic-link approval target. The WhatsApp/email link lands here with ?token=…; the token (not the
// session) authorizes the Approve/Reject decision. On Approve the backend runs ONE whitelisted,
// bounded playbook and reports the result. Nothing runs until the human taps Approve.
export default function ApproveRemediationPage() {
  const params = useParams()
  const search = useSearchParams()
  const id = String(params?.id || '')
  const token = search?.get('token') || ''
  const [req, setReq] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<any>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api(`/api/v1/remediation/requests/${id}`)
      .then((r: any) => setReq(r.request))
      .catch((e: any) => setErr(e?.message || 'Could not load this request.'))
  }, [id])

  async function decide(decision: 'approve' | 'reject') {
    setBusy(true); setErr('')
    try {
      const r = await api(`/api/v1/remediation/requests/${id}/decision`, {
        method: 'POST', body: JSON.stringify({ decision, token }),
      })
      setDone(r.request)
    } catch (e: any) { setErr(e?.message || 'The decision could not be recorded.') }
    finally { setBusy(false) }
  }

  const box: React.CSSProperties = { maxWidth: 640, margin: '40px auto', padding: 24,
    border: '1px solid var(--border)', borderRadius: 12, background: 'var(--surface)' }
  const label: React.CSSProperties = { fontSize: 11, fontWeight: 700, color: 'var(--text3, #6b7280)',
    textTransform: 'uppercase', letterSpacing: '.04em', margin: '14px 0 4px' }

  if (err && !req) return <div style={box}><p style={{ color: '#dc2626' }}>{err}</p></div>
  if (!req) return <div style={box}>Loading…</div>

  const settled = done || (req.status !== 'awaiting_approval' ? req : null)

  return (
    <div style={box}>
      <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>🤖 Approve an automatic fix</div>
      <div style={{ color: 'var(--text2)', fontSize: 13 }}>{req.title}</div>

      <div style={label}>Issue</div>
      <div style={{ fontSize: 14 }}>{req.issue || '—'}</div>
      {req.diagnosis && (<><div style={label}>Diagnosis</div><div style={{ fontSize: 14 }}>{req.diagnosis}</div></>)}
      <div style={label}>Proposed fix</div>
      <div style={{ fontSize: 14, fontWeight: 600 }}>{req.proposed_action}</div>
      <div style={label}>What will happen (dry-run)</div>
      <div style={{ fontSize: 14, background: 'var(--surface2, #f6f7f9)', padding: '10px 12px', borderRadius: 8 }}>
        {req.preview || 'No preview available.'}
      </div>

      {settled ? (
        <div style={{ marginTop: 20, padding: '12px 14px', borderRadius: 8,
          background: settled.status === 'executed' ? 'rgba(16,185,129,.12)'
            : settled.status === 'rejected' ? 'rgba(107,114,128,.12)' : 'rgba(220,38,38,.12)' }}>
          <b style={{ textTransform: 'capitalize' }}>{settled.status}</b>
          {settled.result?.summary && <div style={{ fontSize: 13, marginTop: 4 }}>{settled.result.summary}</div>}
          {settled.error && <div style={{ fontSize: 13, marginTop: 4, color: '#dc2626' }}>{settled.error}</div>}
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 10, marginTop: 22 }}>
          <button className="btn btn-primary" disabled={busy || !token} onClick={() => decide('approve')}
            style={{ background: '#16a34a', flex: 1 }}>{busy ? '…' : '✓ Approve & run'}</button>
          <button className="btn btn-secondary" disabled={busy} onClick={() => decide('reject')}
            style={{ flex: 1 }}>✕ Reject</button>
        </div>
      )}
      {!token && !settled && <div style={{ color: '#dc2626', fontSize: 12, marginTop: 10 }}>
        This link is missing its approval token — open the exact link from your notification.</div>}
      {err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{err}</div>}
    </div>
  )
}
