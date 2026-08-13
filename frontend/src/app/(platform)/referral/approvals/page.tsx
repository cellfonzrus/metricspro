'use client'
// Approvals queue — the manager's inbox of referrals that reached `commission_pending` (activated and
// submitted) and the approved-but-unpaid list. Approving is money, so it is permission-gated on the
// backend (_can_approve) AND segregation-of-duties-gated (you can't approve your own). This page just
// surfaces the queue; the approve/pay actions live on the detail page where the amount + date are set.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, cell, th, fmtMoney, fmtDate, referrerName, customerName, type Referral } from '@/lib/referral'
import { Pill } from '../page'

export default function ApprovalsPage() {
  const [pending, setPending] = useState<Referral[]>([])
  const [payouts, setPayouts] = useState<Referral[]>([])
  const [msg, setMsg] = useState('')

  useEffect(() => {
    (async () => {
      try {
        const d = await api('/api/v1/referral/dashboard')
        setPending(d.pending_approvals || []); setPayouts(d.pending_payouts || [])
      } catch (e: any) { setMsg(e?.message || String(e)) }
    })()
  }, [])

  return (
    <div style={{ padding: 20, maxWidth: 1000 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>✅ Referral Approvals</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        Referrals whose line is activated and awaiting your sign-off. Open one to set the amount + payout
        date and approve — you can't approve a referral you created yourself.
      </div>
      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 12 }}>{msg}</div>}

      <Queue title="⏳ Awaiting approval" rows={pending} empty="Nothing waiting on approval." />
      <Queue title="💸 Approved · awaiting payout" rows={payouts} empty="Nothing approved-but-unpaid." />
    </div>
  )
}

function Queue({ title, rows, empty }: { title: string; rows: Referral[]; empty: string }) {
  return (
    <div style={{ ...panel, marginBottom: 16 }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{title} <span style={{ color: 'var(--text2)', fontWeight: 400 }}>({rows.length})</span></div>
      {rows.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>{empty}</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>#</th><th style={th}>Referrer</th><th style={th}>Customer</th>
              <th style={th}>Amount</th><th style={th}>Payout</th><th style={th}>Status</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td style={cell}>#{r.referral_no}</td>
                  <td style={cell}>{referrerName(r)}</td>
                  <td style={cell}>{customerName(r)}</td>
                  <td style={cell}>{fmtMoney(r.commission_amount_effective ?? r.commission_amount)}</td>
                  <td style={cell}>{fmtDate(r.payout_date)}</td>
                  <td style={cell}><Pill status={r.status} /></td>
                  <td style={cell}><Link href={`/referral/list/${r.id}`}>Review →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
