'use client'
// Referral detail — the record, the QR to hand the referrer, the immutable audit timeline, and the
// state-machine action buttons (each gated by the current status + the caller's permission). This is
// where a referral is walked created → sent → redeemed → sale_logged → activated → approved → paid,
// and where a manager approves the payout (never the rep who created it — segregation of duties).
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { supabase, api, activeOrgHeader } from '@/lib/client'
import {
  panel, input, label, btn, btnPrimary, fmtMoney, fmtPhone, fmtDate, fmtDateTime,
  referrerName, customerName, STATUS_COLOR, STATUS_LABEL, type Referral, type ReferralAudit,
} from '@/lib/referral'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

export default function ReferralDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [r, setR] = useState<Referral | null>(null)
  const [audit, setAudit] = useState<ReferralAudit[]>([])
  const [qrUrl, setQrUrl] = useState('')
  const [qrImg, setQrImg] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [amount, setAmount] = useState('')
  const [payoutDate, setPayoutDate] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await api(`/api/v1/referral/referrals/${id}`)
      setR(d.referral); setAudit(d.audit || [])
      setAmount(String(d.referral.commission_amount_effective ?? ''))
      setPayoutDate(d.referral.payout_date || '')
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }, [id])
  useEffect(() => { load() }, [load])

  // Fetch the QR image (authed blob) + the redeem URL while the referral is still scannable.
  useEffect(() => {
    if (!r || !['created', 'sent'].includes(r.status)) { setQrImg(''); return }
    (async () => {
      try {
        const { data } = await supabase.auth.getSession().catch(() => ({ data: { session: null } } as any))
        const tok = data?.session?.access_token
        const res = await fetch(`${API_URL}/api/v1/referral/referrals/${id}/qr.png`,
          { headers: { ...(tok ? { Authorization: `Bearer ${tok}` } : {}), ...activeOrgHeader() } })
        if (res.ok) { const b = await res.blob(); setQrImg(URL.createObjectURL(b)) }
        const u = await api(`/api/v1/referral/referrals/${id}/redeem-url`).catch(() => null)
        if (u?.url) setQrUrl(u.url)
      } catch { /* QR signing may be unconfigured — the rest of the page still works */ }
    })()
  }, [r, id])

  async function act(path: string, body: any = {}) {
    setBusy(true); setMsg('')
    try { await api(`/api/v1/referral/referrals/${id}/${path}`, { method: 'POST', body: JSON.stringify(body) }); await load() }
    catch (e: any) { setMsg(e?.message || String(e)) }
    setBusy(false)
  }

  if (msg && !r) return <div style={{ padding: 20 }}><div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div></div>
  if (!r) return <div style={{ padding: 20, color: 'var(--text2)' }}>Loading…</div>

  return (
    <div style={{ padding: 20, maxWidth: 960 }}>
      <Link href="/referral/list" style={{ fontSize: 13 }}>← All referrals</Link>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', margin: '8px 0 14px' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>Referral #{r.referral_no}</h1>
        <span style={{ fontSize: 13, padding: '3px 12px', borderRadius: 999, color: '#fff', background: STATUS_COLOR[r.status] || '#6b7280' }}>
          {STATUS_LABEL[r.status] || r.status}
        </span>
      </div>

      {r.status === 'flagged_fraud' && (
        <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14 }}>
          🚩 Flagged as suspected fraud: {r.fraud_reason || '—'}
        </div>
      )}
      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14 }}>{msg}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 14 }}>
        {/* Parties */}
        <div style={panel}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Referring party</div>
          <Field k="Name" v={referrerName(r)} />
          <Field k="Phone" v={fmtPhone(r.referrer_phone)} />
          <Field k="Email" v={r.referrer_email || '—'} />
          <div style={{ fontWeight: 600, margin: '14px 0 8px' }}>Referred customer</div>
          <Field k="Name" v={customerName(r)} />
          <Field k="Phone" v={fmtPhone(r.customer_phone)} />
          <Field k="Products" v={(r.products || []).join(', ') || '—'} />
        </div>

        {/* Money + QR */}
        <div style={panel}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Commission</div>
          <Field k="Amount" v={fmtMoney(r.commission_amount_effective ?? r.commission_amount)} />
          <Field k="Payout date" v={fmtDate(r.payout_date)} />
          <Field k="Approved by" v={r.approver_employee_id || '—'} />
          {qrImg && (
            <div style={{ marginTop: 12, textAlign: 'center' }}>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>QR for the referrer to show:</div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qrImg} alt="Referral QR" style={{ width: 200, height: 200 }} />
              {qrUrl && <div style={{ fontSize: 10, color: 'var(--text2)', wordBreak: 'break-all', marginTop: 6 }}>{qrUrl}</div>}
            </div>
          )}
        </div>
      </div>

      {/* Actions — each gated by the current status (the backend state machine is the real gate) */}
      <div style={{ ...panel, marginTop: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Actions</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {r.status === 'created' && <button disabled={busy} onClick={() => act('send')} style={btnPrimary}>📤 Send QR to referrer</button>}
          {r.status === 'sent' && <button disabled={busy} onClick={() => act('send')} style={btn}>📤 Re-send QR</button>}
          {r.status === 'redeemed' && <button disabled={busy} onClick={() => act('log-sale')} style={btnPrimary}>🧾 Log the sale</button>}
          {r.status === 'sale_logged' && <button disabled={busy} onClick={() => act('activate')} style={btnPrimary}>📶 Mark line activated</button>}
          {r.status === 'activated' && <button disabled={busy} onClick={() => act('submit')} style={btnPrimary}>✅ Submit for approval</button>}
          {r.status === 'approved' && r.can_approve && <button disabled={busy} onClick={() => act('pay')} style={btnPrimary}>💸 Mark paid</button>}
          {['created', 'sent', 'redeemed', 'sale_logged', 'activated', 'commission_pending', 'approved'].includes(r.status) && (
            <>
              <button disabled={busy} onClick={() => { const reason = prompt('Reason for flagging fraud?') || ''; if (reason) act('flag', { reason }) }} style={{ ...btn, borderColor: '#dc2626', color: '#dc2626' }}>🚩 Flag fraud</button>
              <button disabled={busy} onClick={() => { if (confirm('Void this referral?')) act('void', { reason: 'Voided from detail page' }) }} style={btn}>Void</button>
            </>
          )}
        </div>

        {/* Approval panel — money + segregation of duties */}
        {r.status === 'commission_pending' && (
          <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
            <div style={{ fontWeight: 600, marginBottom: 8 }}>Approve payout</div>
            {r.approval_conflict ? (
              <div style={{ fontSize: 13, color: '#dc2626' }}>{r.approval_conflict}</div>
            ) : r.can_approve ? (
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
                <div><span style={label}>Amount ($)</span><input value={amount} onChange={e => setAmount(e.target.value)} inputMode="decimal" style={{ ...input, width: 120 }} /></div>
                <div><span style={label}>Payout date</span><input type="date" value={payoutDate} onChange={e => setPayoutDate(e.target.value)} style={{ ...input, width: 160 }} /></div>
                <button disabled={busy} onClick={() => act('approve', { commission_amount: amount === '' ? null : Number(amount), payout_date: payoutDate || null })} style={btnPrimary}>Approve</button>
                <button disabled={busy} onClick={() => { const reason = prompt('Reason for rejecting?') || ''; act('reject', { reason }) }} style={{ ...btn, borderColor: '#dc2626', color: '#dc2626' }}>Reject</button>
              </div>
            ) : (
              <div style={{ fontSize: 13, color: 'var(--text2)' }}>You do not have permission to approve payouts.</div>
            )}
          </div>
        )}
      </div>

      {/* Immutable audit timeline */}
      <div style={{ ...panel, marginTop: 14 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Timeline</div>
        {audit.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>No activity yet.</div> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {audit.map(a => (
              <div key={a.id} style={{ display: 'flex', gap: 10, fontSize: 13 }}>
                <div style={{ width: 130, color: 'var(--text2)', flexShrink: 0 }}>{fmtDateTime(a.created_at)}</div>
                <div>
                  <span style={{ fontWeight: 600 }}>{a.action}</span>
                  {a.from_status && a.to_status && a.from_status !== a.to_status && (
                    <span style={{ color: 'var(--text2)' }}> · {STATUS_LABEL[a.from_status] || a.from_status} → {STATUS_LABEL[a.to_status] || a.to_status}</span>
                  )}
                  {a.reason && <div style={{ color: 'var(--text2)' }}>{a.reason}</div>}
                  <div style={{ fontSize: 11, color: 'var(--text2)' }}>{a.actor_kind === 'customer' ? 'customer' : (a.actor_employee_id || a.actor_kind)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function Field({ k, v }: { k: string; v: any }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, padding: '3px 0' }}>
      <span style={{ color: 'var(--text2)' }}>{k}</span>
      <span style={{ fontWeight: 500, textAlign: 'right' }}>{v}</span>
    </div>
  )
}
