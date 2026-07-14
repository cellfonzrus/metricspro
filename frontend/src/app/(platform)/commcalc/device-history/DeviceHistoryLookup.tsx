'use client'
// DEVICE HISTORY LOOKUP (commission-16) — reusable, self-contained widget.
// Enter an IMEI or phone number → device + sale (B2B sales), activation + tenure (residual/MI),
// a sell-NEW vs offer-UPGRADE prompt (ALWAYS shown — the salesperson-facing core), and an admin-only
// per-period money table (COMMISSION + REBATE shown as SEPARATE categories). DISPLAY only, org-scoped.
//
// Self-fetching so it drops in anywhere: the commcalc page below renders it, and the employee portal
// can render the same component as a widget (see the handoff coordination note for mod-people). The
// money section is gated by the 'device_commission' DATA_GRANT — the backend is the source of truth
// (returns `commission_visible` + either `money` or `money_locked`); `hasDataGrant` is the frontend
// mirror used to keep the pre-response UI honest.
import { useState } from 'react'
import { api, fmt } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { hasDataGrant } from '@/lib/rbac'

const cell: React.CSSProperties = { padding: '6px 10px', borderTop: '1px solid var(--border)', fontSize: 13 }
const cellR: React.CSSProperties = { ...cell, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

function MoneySection({ title, section }: { title: string; section: any }) {
  const rows: any[] = section?.rows || []
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', margin: '4px 0' }}>
        {title} <span style={{ color: 'var(--text3)', fontWeight: 500 }}>· {section?.source}</span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <tbody>
          {rows.length === 0 && (
            <tr><td style={{ ...cell, color: 'var(--text3)' }} colSpan={3}>No {title.toLowerCase()} recorded.</td></tr>
          )}
          {rows.map((r, i) => (
            <tr key={i}>
              <td style={cell}>{r.period || '—'}</td>
              <td style={{ ...cell, color: 'var(--text3)', fontSize: 12 }}>{r.label}</td>
              <td style={cellR}>{fmt(r.amount)}</td>
            </tr>
          ))}
          <tr>
            <td style={{ ...cell, fontWeight: 700 }} colSpan={2}>{title} subtotal</td>
            <td style={{ ...cellR, fontWeight: 700 }}>{fmt(section?.subtotal || 0)}</td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}

export default function DeviceHistoryLookup() {
  const { permissions } = useAuth()
  const grantedClientHint = hasDataGrant(permissions, 'device_commission')
  const [q, setQ] = useState('')
  const [res, setRes] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const run = async () => {
    const term = q.trim()
    if (!term) return
    setLoading(true); setErr(''); setRes(null)
    try {
      const r = await api(`/api/v1/commcalc/device-history?q=${encodeURIComponent(term)}`)
      setRes(r)
    } catch (e: any) {
      setErr(e?.message || 'Lookup failed')
    } finally { setLoading(false) }
  }

  const p = res?.prompt
  const promptStyle: React.CSSProperties = p?.kind === 'upgrade'
    ? { background: '#e6f7ec', color: '#16794a', border: '1px solid #b7e4c7' }
    : { background: '#fff4e5', color: '#b45309', border: '1px solid #fed7aa' }

  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '11px 16px', borderBottom: '1px solid var(--border)', fontWeight: 700, fontSize: 14 }}>
        🔎 Device History Lookup
      </div>
      <div style={{ padding: 16 }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') run() }}
            placeholder="Enter IMEI or phone number"
            style={{ flex: 1, padding: '8px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 14, background: 'var(--surface)' }}
          />
          <button className="btn btn-primary" onClick={run} disabled={loading || !q.trim()}>
            {loading ? '…' : 'Look up'}
          </button>
        </div>
        {res?.detected && res.detected !== 'unknown' && (
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 5 }}>
            Detected as {res.detected === 'imei' ? 'an IMEI / serial' : 'a phone number'} — searching both keys.
          </div>
        )}

        {err && <div style={{ marginTop: 12, color: 'var(--red, #dc2626)', fontSize: 13 }}>⚠ {err}</div>}

        {res && !res.found && !err && (
          <div style={{ marginTop: 14 }}>
            <div style={{ ...promptStyle, padding: '12px 14px', borderRadius: 10, fontWeight: 700, fontSize: 15 }}>
              {p?.icon} {p?.text}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>
              No sales, residual, or payment records found for “{res.query}”. Double-check the IMEI/number.
            </div>
          </div>
        )}

        {res && res.found && (
          <div style={{ marginTop: 14, display: 'grid', gap: 14 }}>
            {/* PROMPT — the salesperson-facing core, always visible regardless of $ gating */}
            <div style={{ ...promptStyle, padding: '12px 14px', borderRadius: 10, fontWeight: 700, fontSize: 15 }}>
              {p?.icon} {p?.text}
            </div>

            {/* DEVICE + SALE */}
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 4 }}>Device &amp; sale</div>
              {res.device ? (
                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                  <tbody>
                    <tr><td style={cell}>Phone model</td><td style={{ ...cell, textAlign: 'right', fontWeight: 600 }}>{res.device.phone_model || '—'}</td></tr>
                    <tr><td style={cell}>Date sold</td><td style={{ ...cell, textAlign: 'right' }}>{res.device.sold_date || '—'}</td></tr>
                    <tr><td style={cell}>Sale price</td><td style={cellR}>{fmt(res.device.sale_price || 0)}</td></tr>
                    {res.device.store && <tr><td style={cell}>Store</td><td style={{ ...cell, textAlign: 'right' }}>{res.device.store}</td></tr>}
                    {res.device.salesperson && <tr><td style={cell}>Sold by</td><td style={{ ...cell, textAlign: 'right' }}>{res.device.salesperson}</td></tr>}
                    {res.device.contract_type && <tr><td style={cell}>Contract</td><td style={{ ...cell, textAlign: 'right' }}>{res.device.contract_type}</td></tr>}
                    <tr><td style={cell}>MDN / IMEI</td><td style={{ ...cell, textAlign: 'right', fontFamily: 'monospace', fontSize: 12 }}>{[res.device.mdn, res.device.imei].filter(Boolean).join(' · ') || '—'}</td></tr>
                  </tbody>
                </table>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text3)' }}>Not sold by us — no B2B sale on file for this line.</div>
              )}
              {res.device?.sale_source === 'daily_sales_feed' && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>Sale from the daily feed (current month — not yet promoted to the monthly basis).</div>
              )}
            </div>

            {/* ACTIVATION + TENURE (residual months) */}
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 4 }}>Activation &amp; tenure</div>
              {res.tenure?.months_active > 0 ? (
                <>
                  <div style={{ display: 'flex', gap: 18, flexWrap: 'wrap' }}>
                    <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Activated (in our system)</div><div style={{ fontSize: 18, fontWeight: 700 }}>{res.tenure.activation_period}</div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Months active</div><div style={{ fontSize: 18, fontWeight: 700 }}>{res.tenure.months_active} mo <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text3)' }}>({res.tenure.basis})</span></div></div>
                    <div><div style={{ fontSize: 11, color: 'var(--text3)' }}>Last seen</div><div style={{ fontSize: 18, fontWeight: 700 }}>{res.tenure.last_seen_period}</div></div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>{res.tenure.note}</div>
                </>
              ) : (
                <div style={{ fontSize: 13, color: 'var(--text3)' }}>{res.tenure?.note || 'No residual history on file.'}</div>
              )}
            </div>

            {/* MONEY TABLE — admin-only (gated). Commission vs Rebate = SEPARATE categories. */}
            <div>
              <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 4 }}>Money received on this line</div>
              {res.commission_visible && res.money ? (
                <>
                  <MoneySection title="Commission" section={res.money.commission} />
                  <MoneySection title="Rebate" section={res.money.rebate} />
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <tbody>
                      <tr>
                        <td style={{ ...cell, borderTop: '2px solid var(--border)', fontWeight: 800 }}>Grand total</td>
                        <td style={{ ...cellR, borderTop: '2px solid var(--border)', fontWeight: 800, color: 'var(--accent)' }}>{fmt(res.money.grand_total || 0)}</td>
                      </tr>
                    </tbody>
                  </table>
                  {res.money.excluded?.payment_detail_other && (
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                      ⓘ {res.money.excluded.payment_detail_other.note}
                    </div>
                  )}
                </>
              ) : (
                <div style={{ background: 'var(--surface2)', border: '1px dashed var(--border)', borderRadius: 8, padding: '12px 14px', fontSize: 13, color: 'var(--text3)' }}>
                  🔒 {res.money_locked?.note || 'Commission details are restricted.'}
                  {!grantedClientHint && <div style={{ fontSize: 11, marginTop: 4 }}>Ask an admin for the “Device commission” data grant to see per-period commission &amp; rebate.</div>}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
