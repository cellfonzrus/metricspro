'use client'
// Customer 360 — "type a phone number, see everything we know about this customer".
// OWNER DIRECTIVE 2026-08-12. The gate is SERVER-side (crm `customer_360` / `customer_360_financial`
// data grants); this page only renders what the API chose to return. A withheld money column is
// shown as "hidden", never as a zero — a blank margin reads as "$0 margin" and that is worse than
// saying nothing.
import { useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, input, btn, btnPrimary, th, cell, fmtMoney, fmtDate, fmtPhone } from '@/lib/crm'

interface Section { available: boolean; reason: string | null; rows: any[]; count: number; withheld: string[] }
interface Result {
  phone: string | null
  phone_masked?: string
  error?: string
  money_visible?: boolean
  pii_revealed?: boolean
  can_export_dsar?: boolean
  summary?: {
    name: string | null; is_customer: boolean; purchase_count: number; device_count: number
    line_count: number; open_leads: number; first_purchase: string | null
    last_purchase: string | null; lifetime_value: number | null
  }
  sections?: Record<string, Section>
  suggested_actions?: { key: string; severity: string; label: string; detail: string; lead?: any }[]
}

const SECTION_TITLES: [string, string][] = [
  ['identity', '👤 Customer record'],
  ['purchases', '🧾 Purchase history'],
  ['devices', '📱 Devices'],
  ['activations', '📶 Lines & plans'],
  ['pos_sales', '🛒 Register receipts'],
  ['crm', '🎯 CRM history'],
  ['tickets', '🎫 Support cases'],
]

const COLUMNS: Record<string, [string, string][]> = {
  identity: [['cust_number', 'Cust #'], ['first_name', 'First'], ['last_name', 'Last'], ['company_name', 'Company'], ['email', 'Email'], ['phone_primary', 'Phone'], ['city', 'City'], ['state', 'ST'], ['created_at', 'Since']],
  purchases: [['trans_date', 'Date'], ['store', 'Store'], ['salesperson', 'Sold by'], ['product_desc', 'Item'], ['category', 'Category'], ['contract_type', 'Contract'], ['serial_1', 'IMEI'], ['ext_price', 'Price'], ['gp', 'GP']],
  devices: [['device_model', 'Model'], ['esn_imei', 'IMEI'], ['date_sold', 'Sold'], ['store', 'Store'], ['status', 'Status'], ['category', 'Category'], ['selling_price', 'Price']],
  activations: [['activation_date', 'Date'], ['carrier', 'Carrier'], ['plan_description', 'Plan'], ['monthly_fee', 'MRC'], ['cell_number', 'Number'], ['phone_model', 'Device'], ['store_code', 'Store'], ['status', 'Status']],
  pos_sales: [['created_at', 'Date'], ['transaction_id', 'Receipt'], ['store_code', 'Store'], ['employee_id', 'Rep'], ['total', 'Total'], ['status', 'Status']],
  crm: [['lead_no', 'Lead #'], ['first_name', 'First'], ['last_name', 'Last'], ['status', 'Status'], ['owner_employee_id', 'Owner'], ['created_at', 'Created']],
  tickets: [['subject', 'Subject'], ['status', 'Status'], ['priority', 'Priority'], ['created_at', 'Opened']],
}
const MONEY_COLS = new Set(['ext_price', 'gp', 'total', 'monthly_fee', 'selling_price', 'extended_price', 'unit_price'])
const DATE_COLS = new Set(['trans_date', 'created_at', 'date_sold', 'activation_date', 'acquired_date'])

function renderCell(col: string, v: any) {
  if (v === null || v === undefined || v === '') return '—'
  if (MONEY_COLS.has(col)) return fmtMoney(Number(v))
  if (DATE_COLS.has(col)) return fmtDate(String(v))
  if (col.includes('phone') || col === 'cell_number') return fmtPhone(String(v))
  return String(v)
}

export default function CustomerLookupPage() {
  const [phone, setPhone] = useState('')
  const [data, setData] = useState<Result | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')

  async function search(e?: React.FormEvent, reveal = false) {
    e?.preventDefault()
    if (!phone.trim()) return
    setLoading(true); setMsg(''); if (!reveal) setData(null)
    try {
      setData(await api(`/api/v1/crm/customer-360?phone=${encodeURIComponent(phone.trim())}${reveal ? '&reveal=true' : ''}`))
    } catch (err: any) { setMsg(err?.message || String(err)) }
    setLoading(false)
  }

  async function dsarExport() {
    if (!phone.trim()) return
    try {
      const r = await api(`/api/v1/crm/customer-360/dsar?phone=${encodeURIComponent(phone.trim())}`)
      const blob = new Blob([JSON.stringify(r, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `dsar-${(phone.trim().replace(/\D/g, '') || 'customer')}.json`
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e: any) { setMsg(e?.message || String(e)) }
  }

  async function startLead(action: any) {
    if (!data?.summary) return
    const ident = data.sections?.identity?.rows?.[0]
    try {
      const r = await api('/api/v1/crm/leads', {
        method: 'POST',
        body: JSON.stringify({
          phone,
          first_name: ident?.first_name || (data.summary.name || '').split(' ')[0] || null,
          last_name: ident?.last_name || (data.summary.name || '').split(' ').slice(1).join(' ') || null,
          email: ident?.email || null,
          matched_customer_id: ident?.id || null,
          interest_key: action?.lead?.interest_key,
          source_key: 'phone_in',
          notes: `Started from a customer lookup: ${action?.label || ''}`,
        }),
      })
      window.location.href = `/crm/leads/${r.lead.id}`
    } catch (err: any) { setMsg(err?.message || String(err)) }
  }

  const s = data?.summary

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🔎 Customer Lookup</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        Type a phone number to see everything we know about that customer. Access is permission-based and every lookup is recorded.
      </div>

      <form onSubmit={search} style={{ ...panel, display: 'flex', gap: 10, alignItems: 'end', marginBottom: 14, maxWidth: 560 }}>
        <div style={{ flex: 1 }}>
          <span style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 3 }}>Phone number</span>
          <input value={phone} onChange={e => setPhone(e.target.value)} placeholder="(516) 555-0134"
                 autoFocus inputMode="tel" style={{ ...input, fontSize: 16 }} />
        </div>
        <button type="submit" disabled={loading} style={btnPrimary}>{loading ? 'Looking…' : 'Look up'}</button>
      </form>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14, maxWidth: 720 }}>{msg}</div>}
      {data?.error && <div style={{ ...panel, borderColor: '#f39c12', marginBottom: 14, maxWidth: 720 }}>{data.error}</div>}

      {s && (
        <>
          <div style={{ ...panel, marginBottom: 14 }}>
            <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', alignItems: 'baseline' }}>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{s.name || 'Unknown name'}</div>
              <div style={{ fontSize: 13, color: 'var(--text2)' }}>{data.phone ? fmtPhone(data.phone) : (data.phone_masked || '••••')}</div>
              {data.pii_revealed
                ? <span style={{ fontSize: 11, color: '#b45309' }}>👁 contact info revealed (recorded)</span>
                : <button type="button" onClick={() => search(undefined, true)} disabled={loading}
                    style={{ fontSize: 11, border: '1px solid var(--border)', background: 'var(--surface2)', borderRadius: 6, padding: '2px 8px', cursor: 'pointer' }}
                    title="Show full phone & email — this reveal is recorded in the lookup audit">🔒 Reveal contact info</button>}
              <div style={{ padding: '2px 8px', borderRadius: 999, fontSize: 11, fontWeight: 600,
                            background: s.is_customer ? '#16a34a22' : '#6b728022',
                            color: s.is_customer ? '#16a34a' : '#6b7280' }}>
                {s.is_customer ? 'EXISTING CUSTOMER' : 'NOT A CUSTOMER YET'}
              </div>
              {data.can_export_dsar && (
                <button type="button" onClick={dsarExport} style={{ marginLeft: 'auto', fontSize: 11, border: '1px solid var(--border)', background: 'var(--surface2)', borderRadius: 6, padding: '3px 10px', cursor: 'pointer' }}
                  title="Download everything we hold about this customer (data-subject access request). Recorded in the audit trail.">⬇️ DSAR export</button>
              )}
            </div>
            <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap', marginTop: 10, fontSize: 13 }}>
              <span>🧾 {s.purchase_count} purchase line(s)</span>
              <span>📱 {s.device_count} device(s)</span>
              <span>📶 {s.line_count} line(s)</span>
              <span>🎯 {s.open_leads} open lead(s)</span>
              <span>First: {fmtDate(s.first_purchase)}</span>
              <span>Last: {fmtDate(s.last_purchase)}</span>
              <span>Lifetime: {s.lifetime_value === null ? <em style={{ color: 'var(--text2)' }}>hidden</em> : fmtMoney(s.lifetime_value)}</span>
            </div>
            {!data.money_visible && (
              <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 8 }}>
                🔒 Money columns are hidden for your role. Ask an administrator for the “Customer lookup — money columns” permission.
              </div>
            )}
          </div>

          {(data.suggested_actions || []).length > 0 && (
            <div style={{ ...panel, marginBottom: 14 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>What to do next</div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                {(data.suggested_actions || []).map(act => (
                  <div key={act.key} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10, maxWidth: 340,
                                              borderLeft: `3px solid ${act.severity === 'opportunity' ? '#16a34a' : '#6b7280'}` }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{act.label}</div>
                    <div style={{ fontSize: 12, color: 'var(--text2)', margin: '3px 0 8px' }}>{act.detail}</div>
                    {act.lead && <button onClick={() => startLead(act)} style={btn}>Start a lead</button>}
                    {act.key === 'open_lead' && <Link href="/crm/leads" style={{ fontSize: 12 }}>Open the lead list →</Link>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {SECTION_TITLES.map(([key, title]) => {
            const sec = data.sections?.[key]
            if (!sec) return null
            const cols = COLUMNS[key] || []
            return (
              <div key={key} style={{ ...panel, marginBottom: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontWeight: 700 }}>{title}</span>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>{sec.available ? `${sec.count}` : 'unavailable'}</span>
                  {sec.withheld.length > 0 && <span style={{ fontSize: 11, color: '#f39c12' }}>🔒 hidden: {sec.withheld.join(', ')}</span>}
                </div>
                {!sec.available && <div style={{ fontSize: 12, color: 'var(--text2)' }}>{sec.reason}</div>}
                {sec.available && sec.rows.length === 0 && <div style={{ fontSize: 12, color: 'var(--text2)' }}>Nothing on record.</div>}
                {sec.available && sec.rows.length > 0 && (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                      <thead><tr>{cols.filter(([c]) => !sec.withheld.includes(c)).map(([c, l]) => <th key={c} style={th}>{l}</th>)}</tr></thead>
                      <tbody>
                        {sec.rows.slice(0, 100).map((r, i) => (
                          <tr key={i}>
                            {cols.filter(([c]) => !sec.withheld.includes(c)).map(([c]) => (
                              <td key={c} style={cell}>{renderCell(c, r[c])}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {sec.rows.length > 100 && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>Showing the 100 most recent of {sec.rows.length}.</div>}
                  </div>
                )}
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
