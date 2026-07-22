'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'

// "My Chargebacks" — self-scoped list for the SIGNED-IN employee (identity comes from their own
// auth token via GET /storeops/my-chargebacks — never a client-supplied employee_id, same rule as
// every other self-view endpoint in this codebase). Reasons: missed store closing / missed DM
// store-visit verification (commcalc.ops_chargeback, owned/created by mod-retail-ops; this card only
// reads). Self-contained + self-fetching so it can be dropped into the portal dashboard and the
// admin Employee Dashboard (self-view only there — see employee/page.tsx's guard) without prop
// plumbing. Renders nothing (not even an empty card) once loaded with zero items, so a tenant/
// employee that has never had one sees no new UI at all.

interface ChargebackItem {
  id: string; reason: string; reason_label: string; store_code?: string
  incident_date?: string; amount?: number; status?: string
}

const STATUS_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  posted: { bg: '#fdeaea', fg: '#b91c1c', label: 'POSTED' },
  pending: { bg: '#fff7e6', fg: '#92400e', label: 'PENDING' },
  waived: { bg: '#e7f6ec', fg: '#166534', label: 'WAIVED' },
}

export default function MyChargebacks({ token }: { token?: string | null }) {
  const auth = useAuth()
  const tok = token ?? auth?.token
  const [items, setItems] = useState<ChargebackItem[] | null>(null)

  useEffect(() => {
    if (!tok) { setItems(null); return }
    api('/api/v1/storeops/my-chargebacks', { headers: { Authorization: `Bearer ${tok}` } })
      .then((r: any) => setItems(Array.isArray(r?.items) ? r.items : []))
      .catch(() => setItems([]))
  }, [tok])

  if (!items || items.length === 0) return null

  return (
    <div className="card" style={{ marginTop: 14 }}>
      <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>⚠️ My Chargebacks</div>
      <table className="table" style={{ fontSize: 13 }}>
        <thead>
          <tr><th>Reason</th><th>Store</th><th>Date</th><th>Amount</th><th>Status</th></tr>
        </thead>
        <tbody>
          {items.map(it => {
            const s = STATUS_STYLE[String(it.status || '').toLowerCase()] || { bg: '#f1f5f9', fg: '#475569', label: (it.status || '—').toUpperCase() }
            return (
              <tr key={it.id}>
                <td>{it.reason_label || it.reason}</td>
                <td>{it.store_code || '—'}</td>
                <td>{it.incident_date || '—'}</td>
                <td>{it.amount != null ? fmt(Number(it.amount)) : '—'}</td>
                <td>
                  <span style={{ background: s.bg, color: s.fg, borderRadius: 6, padding: '2px 8px', fontSize: 11, fontWeight: 700 }}>
                    {s.label}
                  </span>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
