'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'

// Distributors — who a tenant sources devices/inventory from. "VIP" is just one. Each distributor has
// an ARRANGEMENT: terms (net credit 14/21/30/45/60), consignment (lent devices billed on a cycle =
// Asset Lending, like VIP), or COD. The payment ledger records HOW each payment was funded — from the
// company's OWN account or a BORROWED account — universal across companies. (Migration 058.)

type Dist = { id?: string; name: string; carrier_id?: string | null; arrangement: string; terms_days?: number | null
  billing_cycle: string; has_asset_lending: boolean; default_funding: string; portal_provider?: string | null; is_active: boolean; notes?: string | null }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const lbl: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, fontWeight: 600, color: 'var(--text2)' }
const ARRANGE = [
  { v: 'terms', l: 'Terms (net credit)', d: 'Pay the invoice within N days (14/21/30/45/60).' },
  { v: 'consignment', l: 'Consignment', d: 'Devices lent + billed on a cycle, settled over 60+ days (Asset Lending).' },
  { v: 'cod', l: 'COD', d: 'Cash on delivery — paid up front, no credit.' },
]
const blank = (): Dist => ({ name: '', carrier_id: '', arrangement: 'terms', terms_days: 30, billing_cycle: 'net', has_asset_lending: false, default_funding: 'own', is_active: true, notes: '' })

export default function DistributorsPage() {
  const [carriers, setCarriers] = useState<any[]>([])
  const [dists, setDists] = useState<Dist[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Dist>(blank())
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [payFor, setPayFor] = useState<Dist | null>(null)   // distributor whose payment ledger is open
  const [payments, setPayments] = useState<any>(null)
  const [pay, setPay] = useState<any>({ pay_date: '', period: '', amount: '', funding_source: 'own', account_label: '', ref: '', notes: '' })

  async function load() {
    try {
      setCarriers(await apiCached('/api/v1/commcalc/carriers', LOOKUP).catch(() => []))
      const r = await api('/api/v1/commcalc/distributors')
      setDists(r.distributors || []); setReady(r.ready !== false)
      if (r.ready === false) setMsg(r.note || 'Run migration 058 to enable.')
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
  }
  useEffect(() => { load() }, [])

  function setArrangement(a: string) {
    setDraft(d => ({ ...d, arrangement: a,
      has_asset_lending: a === 'consignment' ? true : d.has_asset_lending,
      billing_cycle: a === 'consignment' ? (d.billing_cycle === 'net' ? 'weekly' : d.billing_cycle) : a === 'cod' ? 'net' : d.billing_cycle }))
  }
  async function save() {
    if (!draft.name.trim()) { setMsg('Name is required.'); return }
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/commcalc/distributors', { method: 'POST', body: JSON.stringify({ ...draft, carrier_id: draft.carrier_id || null, terms_days: Number(draft.terms_days) || null }) })
      setMsg('✅ Saved.'); setDraft(blank()); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function del(id?: string) {
    if (!id || !confirm('Delete this distributor?')) return
    try { await api(`/api/v1/commcalc/distributors/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function openPayments(d: Dist) {
    setPayFor(d); setPayments(null)
    try { setPayments(await api(`/api/v1/commcalc/distributor-payments?distributor_id=${d.id}`)) } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function addPayment() {
    if (!payFor?.id) return
    setBusy(true)
    try {
      await api('/api/v1/commcalc/distributor-payments', { method: 'POST', body: JSON.stringify({ ...pay, distributor_id: payFor.id, amount: Number(pay.amount) || 0 }) })
      setPay({ pay_date: '', period: '', amount: '', funding_source: 'own', account_label: '', ref: '', notes: '' })
      openPayments(payFor)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function delPayment(id: string) {
    try { await api(`/api/v1/commcalc/distributor-payments/${id}`, { method: 'DELETE' }); if (payFor) openPayments(payFor) } catch { /* ignore */ }
  }
  const carrierName = (id?: string | null) => carriers.find(c => c.id === id)?.name || ''
  const arrLabel = (a: string) => ARRANGE.find(x => x.v === a)?.l || a

  return (
    <div style={{ maxWidth: 960 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏬 Distributors</h1>
        <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Who you source devices/inventory from. Each has an <strong>arrangement</strong> — terms (net credit),
          consignment (lent devices billed on a cycle, like Asset Lending), or COD — set at onboarding.
          Record each payment&apos;s <strong>funding source</strong> (own vs borrowed account).
        </p>
      </div>
      {!ready && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {msg || 'Run migration 058_distributors.sql in Supabase to enable.'}</div>}

      {/* editor */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>{draft.id ? '✏️ Edit distributor' : '➕ Add distributor'}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
          <label style={lbl}>Name *<input style={sel} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
          <label style={lbl}>Carrier
            <select style={sel} value={draft.carrier_id || ''} onChange={e => setDraft({ ...draft, carrier_id: e.target.value })}>
              <option value="">Any / N/A</option>
              {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label style={lbl}>Arrangement
            <select style={sel} value={draft.arrangement} onChange={e => setArrangement(e.target.value)}>
              {ARRANGE.map(a => <option key={a.v} value={a.v}>{a.l}</option>)}
            </select>
          </label>
          {draft.arrangement === 'terms' && (
            <label style={lbl}>Net days
              <select style={sel} value={draft.terms_days || 30} onChange={e => setDraft({ ...draft, terms_days: Number(e.target.value) })}>
                {[14, 21, 30, 45, 60, 90].map(n => <option key={n} value={n}>{n} days</option>)}
              </select>
            </label>
          )}
          {draft.arrangement === 'consignment' && (
            <label style={lbl}>Billing cycle
              <select style={sel} value={draft.billing_cycle} onChange={e => setDraft({ ...draft, billing_cycle: e.target.value })}>
                <option value="weekly">Weekly</option><option value="monthly">Monthly</option>
              </select>
            </label>
          )}
          <label style={lbl}>Default funding
            <select style={sel} value={draft.default_funding} onChange={e => setDraft({ ...draft, default_funding: e.target.value })}>
              <option value="own">Own account</option><option value="borrowed">Borrowed account</option>
            </select>
          </label>
        </div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 8 }}>{ARRANGE.find(a => a.v === draft.arrangement)?.d}</div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="checkbox" checked={draft.has_asset_lending} onChange={e => setDraft({ ...draft, has_asset_lending: e.target.checked })} /> Has asset lending (lent devices)
          </label>
          <input style={{ ...sel, flex: 1, minWidth: 200 }} placeholder="Notes" value={draft.notes || ''} onChange={e => setDraft({ ...draft, notes: e.target.value })} />
          <button className="btn btn-primary" disabled={busy} onClick={save}>💾 Save</button>
          {draft.id && <button className="btn btn-secondary" onClick={() => setDraft(blank())}>Cancel</button>}
        </div>
        {msg && ready && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}
      </div>

      {/* list */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>Distributors ({dists.length})</div>
        {dists.map(d => (
          <div key={d.id}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px', borderTop: '1px solid var(--border)', fontSize: 13, flexWrap: 'wrap' }}>
              <span style={{ fontWeight: 700 }}>{d.name}</span>
              <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: d.arrangement === 'consignment' ? '#ede9fe' : d.arrangement === 'cod' ? '#fee2e2' : '#dbeafe', color: d.arrangement === 'consignment' ? '#6d28d9' : d.arrangement === 'cod' ? '#b91c1c' : '#1d4ed8' }}>
                {arrLabel(d.arrangement)}{d.arrangement === 'terms' && d.terms_days ? ` · ${d.terms_days}d` : ''}{d.arrangement === 'consignment' ? ` · ${d.billing_cycle}` : ''}
              </span>
              {d.carrier_id && <span style={{ fontSize: 12, color: 'var(--text3)' }}>{carrierName(d.carrier_id)}</span>}
              {d.has_asset_lending && <span style={{ fontSize: 11, color: '#6d28d9' }}>📲 asset lending</span>}
              <span style={{ fontSize: 12, color: 'var(--text3)' }}>funds: {d.default_funding}</span>
              {!d.is_active && <span style={{ fontSize: 11, color: '#b45309' }}>inactive</span>}
              <span style={{ flex: 1 }} />
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => openPayments(d)}>💵 Payments</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => setDraft({ ...d, carrier_id: d.carrier_id || '' })}>Edit</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => del(d.id)}>Delete</button>
            </div>
            {payFor?.id === d.id && (
              <div style={{ padding: '12px 16px', background: '#f8fafc', borderTop: '1px dashed var(--border)' }}>
                <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Payments — {d.name}
                  {payments?.totals && <span style={{ fontWeight: 400, color: 'var(--text3)' }}> · own {fmt(payments.totals.own)} · borrowed {fmt(payments.totals.borrowed)} · total {fmt(payments.totals.total)}</span>}
                </div>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
                  <input style={{ ...sel, width: 130 }} type="date" value={pay.pay_date} onChange={e => setPay({ ...pay, pay_date: e.target.value })} />
                  <input style={{ ...sel, width: 110 }} placeholder="Period" value={pay.period} onChange={e => setPay({ ...pay, period: e.target.value })} />
                  <input style={{ ...sel, width: 100 }} type="number" placeholder="Amount" value={pay.amount} onChange={e => setPay({ ...pay, amount: e.target.value })} />
                  <select style={sel} value={pay.funding_source} onChange={e => setPay({ ...pay, funding_source: e.target.value })}>
                    <option value="own">Own account</option><option value="borrowed">Borrowed account</option>
                  </select>
                  <input style={{ ...sel, width: 120 }} placeholder="Account label" value={pay.account_label} onChange={e => setPay({ ...pay, account_label: e.target.value })} />
                  <input style={{ ...sel, width: 100 }} placeholder="Ref" value={pay.ref} onChange={e => setPay({ ...pay, ref: e.target.value })} />
                  <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy} onClick={addPayment}>Add payment</button>
                  <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setPayFor(null)}>Close</button>
                </div>
                {payments?.payments?.length > 0 ? (
                  <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 760 }}>
                    <thead><tr style={{ fontSize: 11, color: 'var(--text2)' }}>{['Date', 'Period', 'Amount', 'Funding', 'Account', 'Ref', ''].map(h => <th key={h} style={{ textAlign: 'left', padding: '4px 8px' }}>{h}</th>)}</tr></thead>
                    <tbody>
                      {payments.payments.map((p: any) => (
                        <tr key={p.id} style={{ borderTop: '1px solid var(--border)' }}>
                          <td style={{ padding: '4px 8px', fontSize: 12 }}>{p.pay_date || '—'}</td>
                          <td style={{ padding: '4px 8px', fontSize: 12 }}>{p.period || '—'}</td>
                          <td style={{ padding: '4px 8px', fontSize: 12, fontWeight: 600 }}>{fmt(p.amount)}</td>
                          <td style={{ padding: '4px 8px', fontSize: 12, color: p.funding_source === 'borrowed' ? '#b45309' : '#15803d' }}>{p.funding_source}</td>
                          <td style={{ padding: '4px 8px', fontSize: 12 }}>{p.account_label || '—'}</td>
                          <td style={{ padding: '4px 8px', fontSize: 12 }}>{p.ref || '—'}</td>
                          <td style={{ padding: '4px 8px' }}><button style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#dc2626', fontSize: 12 }} onClick={() => delPayment(p.id)}>✕</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : <div style={{ fontSize: 12, color: 'var(--text3)' }}>No payments recorded yet.</div>}
              </div>
            )}
          </div>
        ))}
        {dists.length === 0 && <div style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No distributors yet — add one above.</div>}
      </div>
    </div>
  )
}
