'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'
import { CheckboxDropdown } from '../_lib/CheckboxDropdown'
import { cascadeStores, marketsFromStores, type StoreOpt } from '../_lib/market-store-cascade'

// DM Envelope Payout execution page (mig 506/507, EEP). Flow: pick a store + date -> see what's due
// (commission accrual balance, clock-in salary balance, approved-unpaid expense lines, each gated by
// the org's cadence config) -> for each item, "Pay from envelope" asks the backend for the FEWEST
// envelopes that cover exactly that item's amount (GET /closing/envelope-plan?required_amount=<item>)
// -> DM confirms cash taken/left per envelope -> the withdrawal is recorded AND the underlying payout
// is recorded (commission ledger / salary ledger / expense marked paid) in one call per envelope
// (POST /closing/envelope-withdrawal). The residual "deposit this" figure is read from Cash Position
// (already netted against everything taken).
const sel: React.CSSProperties = { padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const card: React.CSSProperties = { padding: 14, marginBottom: 12 }

type Plan = { required: number; open_envelopes: number; picks: any[]; total_taken: number; shortfall: number } | null

export default function EnvelopePayoutPage() {
  const [stores, setStores] = useState<any[]>([])
  const [storeCode, setStoreCode] = useState<string | null>(null)
  const [asOf, setAsOf] = useState(() => localToday())
  const [due, setDue] = useState<any>(null)
  const [cashOnHand, setCashOnHand] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [planFor, setPlanFor] = useState<string | null>(null)   // item key currently showing a plan
  const [plan, setPlan] = useState<Plan>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => { apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {}) }, [])
  // OWNER DIRECTIVE 2026-08-04 (market->store cascade + checkbox picker): this is a single-STORE
  // ACTION page (payout-due/envelope-plan only ever operate on ONE store_code at a time), so the store
  // control stays a single-select EntityPicker rather than the checkbox multi-select used on report
  // pages — but a market picker still narrows the store search space first, per the owner's "market
  // and then selectable store" ask, and clears the picked store if it falls outside a newly-picked
  // market (never leaves a stale, now-hidden store silently selected).
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])
  const marketOpts = useMemo(() => marketsFromStores(storesForCascade), [storesForCascade])
  const cascadedStores = useMemo(() => cascadeStores(storesForCascade, fMarkets), [storesForCascade, fMarkets])
  const storeOptions: EntityOption[] = cascadedStores.map(s => ({ id: s.id, label: s.label, sublabel: fMarkets.length ? undefined : (s.market || undefined) }))
  useEffect(() => {
    if (storeCode && fMarkets.length && !cascadedStores.some(s => s.id === storeCode)) setStoreCode(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fMarkets])

  const loadDue = useCallback(() => {
    if (!storeCode) { setDue(null); return }
    setLoading(true); setMsg('')
    api(`/api/v1/closing/payout-due?store_code=${encodeURIComponent(storeCode)}&as_of=${asOf}`)
      .then(setDue).catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setLoading(false))
    api(`/api/v1/closing/cash-position?date=${asOf}&stores=${encodeURIComponent(storeCode)}`)
      .then((d: any) => setCashOnHand(d?.rows?.[0]?.cash_on_hand ?? null)).catch(() => setCashOnHand(null))
  }, [storeCode, asOf])
  useEffect(() => { loadDue() }, [loadDue])

  async function showPlan(key: string, amount: number) {
    if (!storeCode || amount <= 0) return
    setPlanFor(key); setPlan(null); setMsg('')
    try {
      const r: any = await api(`/api/v1/closing/envelope-plan?store_code=${encodeURIComponent(storeCode)}&as_of=${asOf}&required_amount=${amount}`)
      setPlan(r)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
  }

  async function confirmPay(item: { kind: 'commission' | 'salary' | 'expense'; employee_id?: string; employee_name?: string; expense_id?: string; amount: number }) {
    if (!plan || !plan.picks.length) return
    setBusy(true); setMsg('')
    try {
      for (const p of plan.picks) {
        const purpose = item.kind === 'commission' ? 'commission_payout' : item.kind === 'salary' ? 'salary_payout' : 'expense'
        await api('/api/v1/closing/envelope-withdrawal', { method: 'POST', body: JSON.stringify({
          store_code: p.store_code, close_date: p.close_date, closing_row_id: p.closing_row_id,
          amount: p.take, purpose, expense_id: item.expense_id, employee_id: item.employee_id,
          employee_name: item.employee_name, remaining_after: Math.round((p.available - p.take) * 100) / 100,
          notes: `Paid ${item.employee_name || 'expense'} ${fmt(item.amount)} via ${plan.picks.length} envelope(s)`,
        }) })
      }
      setMsg('✅ Recorded.')
      setPlanFor(null); setPlan(null)
      loadDue()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  function Item({ itemKey, label, amount, kind, employee_id, employee_name, expense_id }: {
    itemKey: string; label: string; amount: number; kind: 'commission' | 'salary' | 'expense'
    employee_id?: string; employee_name?: string; expense_id?: string
  }) {
    const showing = planFor === itemKey
    return (
      <div style={{ padding: '8px 0', borderTop: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13 }}>{label}</span>
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <b style={{ fontSize: 13 }}>{fmt(amount)}</b>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={busy}
              onClick={() => showPlan(itemKey, amount)}>💵 Pay from envelope</button>
          </span>
        </div>
        {showing && (
          <div style={{ marginTop: 8, padding: 10, borderRadius: 8, background: 'var(--surface2)' }}>
            {plan === null ? <span style={{ fontSize: 12 }}>Loading plan…</span> : (
              <>
                {plan.shortfall > 0 && (
                  <div style={{ fontSize: 12, color: '#b42318', marginBottom: 6 }}>
                    ⚠️ Only {fmt(plan.total_taken)} available across {plan.open_envelopes} open envelope(s) — short {fmt(plan.shortfall)}.
                  </div>
                )}
                <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
                  <thead><tr style={{ color: 'var(--text3)' }}>
                    <th style={{ textAlign: 'left', padding: 4 }}>Envelope (date · rep)</th>
                    <th style={{ textAlign: 'right', padding: 4 }}>Available</th>
                    <th style={{ textAlign: 'right', padding: 4 }}>Take</th>
                    <th style={{ textAlign: 'right', padding: 4 }}>Left after</th>
                  </tr></thead>
                  <tbody>
                    {plan.picks.map((p: any) => (
                      <tr key={p.closing_row_id}>
                        <td style={{ padding: 4 }}>{p.close_date} · {p.employee_name || '—'}</td>
                        <td style={{ padding: 4, textAlign: 'right' }}>{fmt(p.available)}</td>
                        <td style={{ padding: 4, textAlign: 'right' }}>{fmt(p.take)}</td>
                        <td style={{ padding: 4, textAlign: 'right' }}>{fmt(p.available - p.take)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <div style={{ marginTop: 8, display: 'flex', gap: 8 }}>
                  <button className="btn btn-primary" style={{ fontSize: 12 }} disabled={busy || !plan.picks.length}
                    onClick={() => confirmPay({ kind, employee_id, employee_name, expense_id, amount })}>
                    ✅ Confirm — cash taken, mark paid
                  </button>
                  <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setPlanFor(null); setPlan(null) }}>Cancel</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>✉️ Envelope Payout</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 760 }}>
            Pay commission, salary, and approved expenses in cash from the envelope. The system picks
            the fewest envelopes needed for each payment; you confirm what's taken and what's left.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/closing/envelope-config" className="btn btn-secondary" style={{ fontSize: 13 }}>⚙️ Config</Link>
          <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
        </div>
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        {marketOpts.length > 0 && (
          <CheckboxDropdown options={marketOpts} value={fMarkets} onChange={setFMarkets} placeholder="Market…" width={160} ariaLabel="Narrow store list by market" />
        )}
        <EntityPicker options={storeOptions} value={storeCode} onChange={setStoreCode} placeholder="Store…" width={240} />
        <input type="date" style={sel} value={asOf} onChange={e => setAsOf(e.target.value)} />
        {msg && <span style={{ fontSize: 13 }}>{msg}</span>}
      </div>

      {!storeCode ? (
        <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>Pick a store to see what's due.</div>
      ) : loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px,1fr))', gap: 12, marginBottom: 14 }}>
            <Stat label="Commission due" value={fmt(due?.commission_due)} />
            <Stat label="Salary due" value={fmt(due?.salary_due)} />
            <Stat label="Expenses due" value={fmt(due?.expenses_due)} />
            <Stat label="Total cash required" value={fmt(due?.total_cash_required)} bold />
            <Stat label="Cash on hand (this store)" value={cashOnHand == null ? '—' : fmt(cashOnHand)} color={cashOnHand != null && due?.total_cash_required > cashOnHand ? '#b42318' : undefined} />
          </div>
          {(due?.notes || []).map((n: string, i: number) => (
            <div key={i} style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 6 }}>ℹ️ {n}</div>
          ))}

          <div className="card" style={card}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>Commission</div>
            {(due?.commission_employees || []).length === 0
              ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>Nothing due.</div>
              : (due.commission_employees || []).map((e: any) => (
                  <Item key={e.employee_key} itemKey={`c-${e.employee_key}`} label={e.name || e.employee_key}
                    amount={e.amount} kind="commission" employee_id={e.employee_key} employee_name={e.name} />
                ))}
          </div>

          <div className="card" style={card}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>Salary</div>
            {(due?.salary_employees || []).length === 0
              ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>Nothing due.</div>
              : (due.salary_employees || []).map((e: any) => (
                  <Item key={e.employee_id} itemKey={`s-${e.employee_id}`} label={e.name || e.employee_id}
                    amount={e.amount} kind="salary" employee_id={e.employee_id} employee_name={e.name} />
                ))}
          </div>

          <div className="card" style={card}>
            <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 4 }}>Approved expenses (unpaid)</div>
            {(due?.expense_lines || []).length === 0
              ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>Nothing due.</div>
              : (due.expense_lines || []).map((e: any) => (
                  <Item key={e.id} itemKey={`e-${e.id}`} label={`${e.category_name}${e.employee_name ? ' — ' + e.employee_name : ''} — ${e.description || ''}`}
                    amount={e.amount} kind="expense" expense_id={e.id} employee_name={e.employee_name} />
                ))}
          </div>

          <div className="card" style={{ padding: 14, background: 'var(--surface2)' }}>
            <div style={{ fontSize: 13 }}>
              💰 <b>Deposit the rest:</b> {cashOnHand == null ? '—' : fmt(cashOnHand)}
              <span style={{ color: 'var(--text3)' }}> — updates automatically as payouts are confirmed above.</span>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function Stat({ label, value, color, bold }: { label: string; value: string; color?: string; bold?: boolean }) {
  return (
    <div className="card" style={{ padding: 12 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: bold ? 800 : 700, color: color || 'var(--text1)' }}>{value}</div>
    </div>
  )
}
