'use client'
// Referral Dashboard — the state funnel, the money an operator watches ($ awaiting approval, $ approved
// but unpaid), and the fraud flags. Everything the owner asked to see at a glance: where referrals are
// in the pipeline, what is owed, and what looks wrong.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, cell, th, btn, btnPrimary, fmtMoney, fmtDate, referrerName, customerName,
  STATUS_COLOR, STATUS_LABEL, type Referral,
} from '@/lib/referral'

export default function ReferralDashboard() {
  const [data, setData] = useState<any>(null)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    (async () => {
      try { setData(await api('/api/v1/referral/dashboard')) }
      catch (e: any) { setMsg(e?.message || String(e)) }
    })()
  }, [])

  if (msg) return <div style={{ padding: 20 }}><div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626' }}>{msg}</div></div>
  if (!data) return <div style={{ padding: 20, color: 'var(--text2)' }}>Loading…</div>

  const funnel: { status: string; label: string; count: number }[] = data.funnel || []
  const maxCount = Math.max(1, ...funnel.map(f => f.count))

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🎁 Referral Dashboard</h1>
        <Link href="/referral/new" style={{ ...btnPrimary, textDecoration: 'none' }}>➕ New Referral</Link>
      </div>

      {/* Money + counts row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, marginBottom: 16 }}>
        <Stat label="Awaiting approval" value={fmtMoney(data.pending_approval_amount)} sub={`${data.pending_approval_count} referral(s)`} color="#f39c12" />
        <Stat label="Approved · unpaid" value={fmtMoney(data.approved_unpaid_amount)} sub={`${data.approved_unpaid_count} referral(s)`} color="#16a34a" />
        <Stat label="Paid to date" value={fmtMoney(data.paid_amount)} sub={`${data.paid_count} referral(s)`} color="#15803d" />
        <Stat label="Fraud flags" value={String(data.fraud_flag_count)} sub="need review" color="#dc2626" />
      </div>

      {/* Funnel */}
      <div style={{ ...panel, marginBottom: 16 }}>
        <div style={{ fontWeight: 600, marginBottom: 10 }}>Pipeline funnel</div>
        {funnel.map(f => (
          <div key={f.status} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <div style={{ width: 130, fontSize: 12, color: 'var(--text2)' }}>{f.label}</div>
            <div style={{ flex: 1, background: 'var(--surface)', borderRadius: 5, overflow: 'hidden', height: 20 }}>
              <div style={{ width: `${(f.count / maxCount) * 100}%`, minWidth: f.count ? 4 : 0, height: '100%', background: STATUS_COLOR[f.status] || '#6b7280' }} />
            </div>
            <div style={{ width: 36, textAlign: 'right', fontWeight: 600, fontSize: 13 }}>{f.count}</div>
          </div>
        ))}
      </div>

      <QueueTable title="⏳ Pending approvals" rows={data.pending_approvals || []} empty="No referrals waiting on approval." />
      <QueueTable title="💸 Pending payouts" rows={data.pending_payouts || []} empty="Nothing approved-but-unpaid." />
      {(data.fraud_flags || []).length > 0 && (
        <QueueTable title="🚩 Fraud flags" rows={data.fraud_flags} empty="" danger />
      )}
    </div>
  )
}

function Stat({ label, value, sub, color }: { label: string; value: string; sub: string; color: string }) {
  return (
    <div style={panel}>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color, margin: '2px 0' }}>{value}</div>
      <div style={{ fontSize: 11, color: 'var(--text2)' }}>{sub}</div>
    </div>
  )
}

function QueueTable({ title, rows, empty, danger }: { title: string; rows: Referral[]; empty: string; danger?: boolean }) {
  return (
    <div style={{ ...panel, marginBottom: 16, ...(danger ? { borderColor: '#dc2626' } : {}) }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{title} <span style={{ color: 'var(--text2)', fontWeight: 400 }}>({rows.length})</span></div>
      {rows.length === 0 ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>{empty}</div> : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
            <thead><tr>
              <th style={th}>#</th><th style={th}>Referrer</th><th style={th}>Customer</th>
              <th style={th}>Amount</th><th style={th}>Payout</th><th style={th}>Status</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.id}>
                  <td style={cell}><Link href={`/referral/list/${r.id}`}>#{r.referral_no}</Link></td>
                  <td style={cell}>{referrerName(r)}</td>
                  <td style={cell}>{customerName(r)}</td>
                  <td style={cell}>{fmtMoney(r.commission_amount_effective ?? r.commission_amount)}</td>
                  <td style={cell}>{fmtDate(r.payout_date)}</td>
                  <td style={cell}><Pill status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function Pill({ status }: { status: string }) {
  return <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 999, color: '#fff', background: STATUS_COLOR[status] || '#6b7280' }}>{STATUS_LABEL[status] || status}</span>
}
