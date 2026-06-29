'use client'
import { useState, useEffect } from 'react'
import { api, fmt } from '@/lib/client'

// Multi-month payout schedules (migration 057). A schedule spreads one activation's commission over
// N months (flat or %MRC); months 2..N pay only if the bill was paid + residual received that month.
// "Preview" runs the engine READ-ONLY against raw_mi — it does NOT change live payouts (wiring into
// the calc is a deliberate later step once a schedule is validated here).

type Line = { month_index: number; payout_kind: string; flat_amount: any; mrc_pct: any; mrc_basis: string; requires_paid: boolean }
type Sched = { id?: string; carrier_id?: string | null; activation_type: string; num_months: number; gate_signal: string; bypass_tier: boolean; is_active: boolean; lines?: Line[] }

const sel: React.CSSProperties = { padding: '6px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const GATES = [
  { v: 'paid_residual', l: 'Bill paid + residual received (recommended)' },
  { v: 'active_status', l: 'Subscriber Active that month' },
  { v: 'nonzero_residual', l: 'Non-zero residual that month' },
]
const blankLine = (i: number): Line => ({ month_index: i, payout_kind: i === 1 ? 'flat' : 'pct_mrc', flat_amount: '', mrc_pct: '', mrc_basis: 'commissionable_mrc', requires_paid: i > 1 })
const blankSched = (): Sched => ({ carrier_id: '', activation_type: '*', num_months: 3, gate_signal: 'paid_residual', bypass_tier: true, is_active: true, lines: [blankLine(1), blankLine(2), blankLine(3)] })

export default function PayoutSchedulesPage() {
  const [carriers, setCarriers] = useState<any[]>([])
  const [scheds, setScheds] = useState<Sched[]>([])
  const [ready, setReady] = useState(true)
  const [draft, setDraft] = useState<Sched>(blankSched())
  const [msg, setMsg] = useState('')
  const [period, setPeriod] = useState('')
  const [preview, setPreview] = useState<any>(null)
  const [busy, setBusy] = useState(false)

  async function load() {
    try {
      setCarriers(await api('/api/v1/commcalc/carriers').catch(() => []))
      const r = await api('/api/v1/commcalc/payout-schedule')
      setScheds(r.schedules || []); setReady(r.ready !== false)
      if (r.ready === false) setMsg(r.note || 'Run migration 057 to enable.')
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
  }
  useEffect(() => { load() }, [])

  function setNum(n: number) {
    const lines = Array.from({ length: n }, (_, i) => draft.lines?.[i] || blankLine(i + 1))
    setDraft({ ...draft, num_months: n, lines })
  }
  const setLine = (i: number, patch: Partial<Line>) =>
    setDraft({ ...draft, lines: (draft.lines || []).map((l, idx) => idx === i ? { ...l, ...patch } : l) })

  async function save() {
    setBusy(true); setMsg('')
    try {
      await api('/api/v1/commcalc/payout-schedule', { method: 'POST', body: JSON.stringify({
        ...draft, carrier_id: draft.carrier_id || null,
        lines: (draft.lines || []).map(l => ({ ...l, flat_amount: Number(l.flat_amount) || 0, mrc_pct: Number(l.mrc_pct) || 0 })),
      }) })
      setMsg('✅ Schedule saved.'); setDraft(blankSched()); load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  async function edit(s: Sched) { setDraft({ ...s, carrier_id: s.carrier_id || '', lines: s.lines?.length ? s.lines : Array.from({ length: s.num_months }, (_, i) => blankLine(i + 1)) }) }
  async function del(id?: string) {
    if (!id || !confirm('Delete this schedule?')) return
    try { await api(`/api/v1/commcalc/payout-schedule/${id}`, { method: 'DELETE' }); load() } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }
  async function runPreview() {
    if (!period.trim()) { setMsg('Enter a pay period (e.g. June 2026).'); return }
    setBusy(true); setMsg(''); setPreview(null)
    try { setPreview(await api(`/api/v1/commcalc/payout-schedule/preview?period=${encodeURIComponent(period.trim())}`)) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) } finally { setBusy(false) }
  }
  const carrierName = (id?: string | null) => carriers.find(c => c.id === id)?.name || (id ? 'carrier' : 'Any carrier')

  return (
    <div style={{ maxWidth: 980 }}>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📆 Multi-Month Payout Schedules</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Spread a rep&apos;s commission for one activation over up to 3 months (flat or % of that month&apos;s MRC).
          Months 2–3 pay only if the bill was <strong>paid + residual received</strong> that month. No schedule =
          single-month payout (unchanged). <strong>Preview</strong> is read-only — it does not change live payouts.
        </p>
      </div>
      {!ready && <div className="card" style={{ padding: 14, marginBottom: 14, background: '#fffbeb', border: '1px solid #fde68a', fontSize: 13 }}>⚠️ {msg || 'Run migration 057_multi_month_payout.sql in Supabase to enable this feature.'}</div>}

      {/* editor */}
      <div className="card" style={{ padding: 16, marginBottom: 16 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 12 }}>{draft.id ? '✏️ Edit schedule' : '➕ New schedule'}</div>
        <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 12 }}>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Carrier<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.carrier_id || ''} onChange={e => setDraft({ ...draft, carrier_id: e.target.value })}>
              <option value="">Any carrier</option>
              {carriers.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Applies to<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.activation_type} onChange={e => setDraft({ ...draft, activation_type: e.target.value })}>
              <option value="*">All activations</option><option value="premium">Premium</option><option value="byod">BYOD</option><option value="upgrade">Upgrade</option>
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>Months<br />
            <select style={{ ...sel, marginTop: 4 }} value={draft.num_months} onChange={e => setNum(parseInt(e.target.value))}>
              {Array.from({ length: 12 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)' }}>“Paid” signal (months 2–3)<br />
            <select style={{ ...sel, marginTop: 4, width: 280 }} value={draft.gate_signal} onChange={e => setDraft({ ...draft, gate_signal: e.target.value })}>
              {GATES.map(g => <option key={g.v} value={g.v}>{g.l}</option>)}
            </select>
          </label>
          <label style={{ fontSize: 13, display: 'flex', alignItems: 'center', gap: 6, paddingBottom: 6 }}>
            <input type="checkbox" checked={draft.bypass_tier} onChange={e => setDraft({ ...draft, bypass_tier: e.target.checked })} /> Don&apos;t re-apply KPI tier
          </label>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', maxWidth: 720 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>{['Month', 'Type', 'Amount', 'MRC basis', 'Requires paid'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
          <tbody>
            {(draft.lines || []).map((l, i) => (
              <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                <td style={{ padding: '6px 8px', fontWeight: 600 }}>Month {l.month_index}</td>
                <td style={{ padding: '6px 8px' }}>
                  <select style={sel} value={l.payout_kind} onChange={e => setLine(i, { payout_kind: e.target.value })}>
                    <option value="flat">Flat $</option><option value="pct_mrc">% of MRC</option>
                  </select>
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.payout_kind === 'pct_mrc'
                    ? <input style={{ ...sel, width: 90 }} type="number" step="0.01" placeholder="0.05 = 5%" value={l.mrc_pct} onChange={e => setLine(i, { mrc_pct: e.target.value })} />
                    : <input style={{ ...sel, width: 90 }} type="number" step="0.01" placeholder="$" value={l.flat_amount} onChange={e => setLine(i, { flat_amount: e.target.value })} />}
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.payout_kind === 'pct_mrc'
                    ? <select style={sel} value={l.mrc_basis} onChange={e => setLine(i, { mrc_basis: e.target.value })}><option value="commissionable_mrc">Commissionable MRC</option><option value="base_mrc">Base MRC</option></select>
                    : <span style={{ fontSize: 12, color: 'var(--text3)' }}>—</span>}
                </td>
                <td style={{ padding: '6px 8px' }}>
                  {l.month_index === 1 ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>always</span>
                    : <input type="checkbox" checked={l.requires_paid} onChange={e => setLine(i, { requires_paid: e.target.checked })} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
          <button className="btn btn-primary" disabled={busy} onClick={save}>💾 Save schedule</button>
          {draft.id && <button className="btn btn-secondary" onClick={() => setDraft(blankSched())}>Cancel edit</button>}
          {msg && ready && <span style={{ fontSize: 13 }}>{msg}</span>}
        </div>
      </div>

      {/* existing schedules */}
      {scheds.length > 0 && (
        <div className="card" style={{ padding: 0, marginBottom: 16 }}>
          <div style={{ padding: '10px 14px', fontWeight: 700, fontSize: 13, borderBottom: '1px solid var(--border)' }}>Configured schedules ({scheds.length})</div>
          {scheds.map(s => (
            <div key={s.id} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px', borderTop: '1px solid var(--border)', fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>{carrierName(s.carrier_id)}</span>
              <span style={{ color: 'var(--text3)' }}>· {s.activation_type === '*' ? 'all activations' : s.activation_type} · {s.num_months} mo · {(s.lines || []).map(l => l.payout_kind === 'pct_mrc' ? `${Math.round((l.mrc_pct || 0) * 100)}%MRC` : `$${l.flat_amount}`).join(' → ')}</span>
              <span style={{ flex: 1 }} />
              {!s.is_active && <span style={{ fontSize: 11, color: '#b45309' }}>inactive</span>}
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px' }} onClick={() => edit(s)}>Edit</button>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '3px 9px', color: '#dc2626' }} onClick={() => del(s.id)}>Delete</button>
            </div>
          ))}
        </div>
      )}

      {/* preview */}
      <div className="card" style={{ padding: 16 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🔎 Preview (read-only)</div>
          <input style={{ ...sel, width: 150 }} placeholder="Pay period e.g. June 2026" value={period} onChange={e => setPeriod(e.target.value)} />
          <button className="btn btn-primary" disabled={busy} onClick={runPreview}>{busy ? '…' : 'Run preview'}</button>
          {preview?.totals && <span style={{ fontSize: 13, color: 'var(--text2)' }}>{fmt(preview.totals.amount)} · {preview.totals.reps} reps · {preview.totals.paid} paid / {preview.totals.withheld} withheld / {preview.totals.pending} pending</span>}
        </div>
        {preview?.note && <div style={{ fontSize: 13, color: 'var(--text3)' }}>{preview.note}</div>}
        {preview?.ledger?.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>{['Subscriber', 'Rep', 'Store', 'Month', 'MRC', 'Amount', 'Status'].map(h => <th key={h} style={{ textAlign: 'left', padding: '6px 8px', fontSize: 11, color: 'var(--text2)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {preview.ledger.slice(0, 200).map((d: any, i: number) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.subscriber_id}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.rep || '—'}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.store || '—'}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{d.month_index}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12 }}>{fmt(d.mrc_at_pay)}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12, fontWeight: 600 }}>{fmt(d.amount)}</td>
                    <td style={{ padding: '5px 8px', fontSize: 12, color: d.status === 'paid' ? '#15803d' : d.status === 'withheld_unpaid' ? '#b91c1c' : '#b45309' }}>{d.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {preview.ledger.length > 200 && <div style={{ fontSize: 12, color: 'var(--text3)', padding: 8 }}>Showing first 200 of {preview.ledger.length}.</div>}
          </div>
        )}
      </div>
    </div>
  )
}
