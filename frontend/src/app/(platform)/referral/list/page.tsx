'use client'
// Referrals list — every referral, filterable by state / fraud / search. The same filter set the
// dashboard and detail share, so a referral reads the same everywhere.
import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, cell, th, input, btn, btnPrimary, fmtMoney, fmtDate, fmtPhone,
  referrerName, customerName, STATUS_LABEL, type Referral,
} from '@/lib/referral'
import { Pill } from '../page'

const STATES = ['', 'created', 'sent', 'redeemed', 'sale_logged', 'activated',
  'commission_pending', 'approved', 'paid', 'expired', 'rejected', 'void', 'flagged_fraud']

export default function ReferralsListPage() {
  const [rows, setRows] = useState<Referral[]>([])
  const [status, setStatus] = useState('')
  const [q, setQ] = useState('')
  const [fraudOnly, setFraudOnly] = useState(false)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true)
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (q) params.set('q', q)
    if (fraudOnly) params.set('fraud_only', 'true')
    try {
      const r = await api('/api/v1/referral/referrals?' + params.toString())
      setRows(r.rows || []); setMsg(r.note || '')
    } catch (e: any) { setMsg(e?.message || String(e)) }
    setLoading(false)
  }
  useEffect(() => { load() }, [status, fraudOnly]) // eslint-disable-line

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>📇 Referrals</h1>
        <Link href="/referral/new" style={{ ...btnPrimary, textDecoration: 'none' }}>➕ New Referral</Link>
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
        <select value={status} onChange={e => setStatus(e.target.value)} style={{ ...input, width: 'auto' }}>
          {STATES.map(s => <option key={s} value={s}>{s ? (STATUS_LABEL[s] || s) : 'All states'}</option>)}
        </select>
        <form onSubmit={e => { e.preventDefault(); load() }} style={{ display: 'flex', gap: 6 }}>
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Name / phone / #" style={{ ...input, width: 200 }} />
          <button style={btn}>Search</button>
        </form>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
          <input type="checkbox" checked={fraudOnly} onChange={e => setFraudOnly(e.target.checked)} /> Fraud flags only
        </label>
      </div>

      {msg && <div style={{ ...panel, borderColor: '#f39c12', color: 'var(--text2)', marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      <div style={{ ...panel, padding: 0, overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
          <thead><tr>
            <th style={th}>#</th><th style={th}>Referrer</th><th style={th}>Customer</th>
            <th style={th}>Products</th><th style={th}>Amount</th><th style={th}>Payout</th>
            <th style={th}>Status</th><th style={th}>Created</th>
          </tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id}>
                <td style={cell}><Link href={`/referral/list/${r.id}`}>#{r.referral_no}</Link></td>
                <td style={cell}>{referrerName(r)}<div style={{ fontSize: 11, color: 'var(--text2)' }}>{fmtPhone(r.referrer_phone)}</div></td>
                <td style={cell}>{customerName(r)}</td>
                <td style={cell}>{(r.products || []).join(', ') || '—'}</td>
                <td style={cell}>{fmtMoney(r.commission_amount_effective ?? r.commission_amount)}</td>
                <td style={cell}>{fmtDate(r.payout_date)}</td>
                <td style={cell}><Pill status={r.status} /></td>
                <td style={cell}>{fmtDate(r.created_at)}</td>
              </tr>
            ))}
            {!loading && rows.length === 0 && (
              <tr><td style={{ ...cell, color: 'var(--text2)' }} colSpan={8}>No referrals match.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
