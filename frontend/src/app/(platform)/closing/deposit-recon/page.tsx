'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'

// Cash Deposit Reconciliation (OWNER DIRECTIVE 2026-08-05) — cross-checks cash COLLECTED (Daily
// Closing + POS X-Report) against cash DEPOSITED (commcalc.bank_deposit), per tenant-defined category,
// net of tenant-configurable adjustments (excluded by default — see /closing/cash-config). RULE FOUR/
// FIVE: ReportShell exports + the standard filter set (date range + market/store via the shared
// cascade picker). Also hosts the "record a deposit" mini-form (reused by the short-deposit modal for
// a supplemental deposit) — the same POST /closing/bank-deposit endpoint every other deposit-recording
// surface in this module uses.
function monthAgo(): string {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1)
  return d.toISOString().slice(0, 10)
}

type DepRow = {
  store_code: string; store_address: string; close_date: string;
  closing_cash_total: number; xreport_cash: number | null; xreport_available: boolean;
  category_id: string | null; category_name: string; basis: string | null;
  cash_collected: number; adjustments_applied: number; expected_deposit: number;
  total_deposited: number; variance: number; status: string; remaining_short: number;
  deposits: any[];
}

export default function DepositReconPage() {
  const [dateFrom, setDateFrom] = useState(monthAgo())
  const [dateTo, setDateTo] = useState(() => localToday())
  const [includeExpenses, setIncludeExpenses] = useState(false)
  const [includeBillPayments, setIncludeBillPayments] = useState(false)
  const [includeOther, setIncludeOther] = useState(false)
  const [categoryId, setCategoryId] = useState('')
  const [statusOnly, setStatusOnly] = useState('')   // '' | 'short' | 'over'
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [cats, setCats] = useState<any[]>([])
  const [stores, setStores] = useState<any[]>([])
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])

  useEffect(() => {
    api('/api/v1/closing/deposit-categories').then((d: any) => setCats(d?.categories || [])).catch(() => {})
    apiCached('/api/v1/closing/stores', LOOKUP).then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {})
  }, [])

  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter((s: any) => s.store_code).map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = new URLSearchParams({
      date_from: dateFrom, date_to: dateTo,
      include_expenses: String(includeExpenses), include_bill_payments: String(includeBillPayments),
      include_other_adj: String(includeOther),
    })
    if (categoryId) qs.set('category_id', categoryId)
    if (fStores.length) qs.set('stores', fStores.join(','))
    api(`/api/v1/closing/deposit-recon?${qs.toString()}`)
      .then(setData)
      .catch((e: any) => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [dateFrom, dateTo, includeExpenses, includeBillPayments, includeOther, categoryId, fStores])
  useEffect(() => { load() }, [load])

  const marketByCode = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code && s.market) m[s.store_code] = s.market
    return m
  }, [stores])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])

  const days: any[] = data?.days || []
  const flatRows: DepRow[] = useMemo(() => {
    const out: DepRow[] = []
    for (const day of days) {
      if (fMarketsFold.size && !fMarketsFold.has((marketByCode[day.store_code] || '').trim().toLowerCase())) continue
      for (const c of day.categories || []) {
        out.push({ store_code: day.store_code, store_address: day.store_address, close_date: day.close_date,
          closing_cash_total: day.closing_cash_total, xreport_cash: day.xreport_cash, xreport_available: day.xreport_available,
          category_id: c.category_id, category_name: c.category_name, basis: c.basis,
          cash_collected: c.cash_collected, adjustments_applied: c.adjustments_applied,
          expected_deposit: c.expected_deposit, total_deposited: c.total_deposited,
          variance: c.variance, status: c.status, remaining_short: c.remaining_short, deposits: c.deposits })
      }
      if (day.uncategorized) {
        const u = day.uncategorized
        out.push({ store_code: day.store_code, store_address: day.store_address, close_date: day.close_date,
          closing_cash_total: day.closing_cash_total, xreport_cash: day.xreport_cash, xreport_available: day.xreport_available,
          category_id: null, category_name: 'Uncategorized', basis: null,
          cash_collected: 0, adjustments_applied: 0, expected_deposit: 0, total_deposited: u.total_deposited,
          variance: u.total_deposited, status: 'uncategorized', remaining_short: 0, deposits: u.deposits })
      }
      // OWNER 2026-09-02 ("cash deposit capture should be shown as a separate line item under cash
      // deposit recon"): the pickup-flow deposit CAPTURE (slip photo + OCR amount recorded at the
      // pickup) as its OWN line item on the day — evidence beside the bank_deposit numbers, never
      // summed into expected/deposited (one number, one source).
      if (day.pickup_deposit) {
        const p = day.pickup_deposit
        out.push({ store_code: day.store_code, store_address: day.store_address, close_date: day.close_date,
          closing_cash_total: day.closing_cash_total, xreport_cash: day.xreport_cash, xreport_available: day.xreport_available,
          category_id: null,
          category_name: `Deposit capture (pickup slips)${p.missing_slip ? ' — ⚠ slip missing' : ''}`, basis: null,
          cash_collected: 0, adjustments_applied: 0, expected_deposit: 0, total_deposited: p.amount,
          variance: 0, status: p.missing_slip ? 'missing_slip' : 'capture', remaining_short: 0,
          deposits: (p.deposits || []).map((d: any) => ({
            amount: d.amount, employee_name: d.employee_name, created_at: d.deposited_at,
            capture: true, kind: d.kind, has_slip: d.has_slip, slip_url: d.deposit_slip_url, flagged: d.flagged,
          })) })
      }
    }
    return out
  }, [days, fMarketsFold, marketByCode])

  const filtered = useMemo(() => statusOnly ? flatRows.filter(r => r.status === statusOnly) : flatRows, [flatRows, statusOnly])

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: DepRow) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: DepRow) => r.store_address },
    { header: 'Category', field: 'category_name', get: (r: DepRow) => r.category_name },
    { header: 'Closing cash (declared)', field: 'closing_cash_total', money: true, get: (r: DepRow) => r.closing_cash_total },
    { header: 'X-Report cash', field: 'xreport_cash', money: true, get: (r: DepRow) => r.xreport_available ? r.xreport_cash : 'pending' as any },
    { header: 'Cash collected (basis)', field: 'cash_collected', money: true, get: (r: DepRow) => r.cash_collected },
    { header: 'Adjustments applied', field: 'adjustments_applied', money: true, get: (r: DepRow) => r.adjustments_applied },
    { header: 'Expected deposit', field: 'expected_deposit', money: true, get: (r: DepRow) => r.expected_deposit },
    { header: 'Deposited', field: 'total_deposited', money: true, get: (r: DepRow) => r.total_deposited },
    { header: 'Variance', field: 'variance', money: true, get: (r: DepRow) => r.variance },
    { header: 'Status', field: 'status', get: (r: DepRow) => r.status.toUpperCase() },
    { header: 'Remaining short', field: 'remaining_short', money: true, get: (r: DepRow) => r.remaining_short },
  ], [])

  // ── Record a deposit (+ short-deposit modal) ──────────────────────────────────────────────────────
  const [dep, setDep] = useState({ close_date: localToday(), store_code: '', category_id: '', amount: '', employee_name: '', note: '' })
  const [depMsg, setDepMsg] = useState('')
  const [depBusy, setDepBusy] = useState(false)
  const [shortModal, setShortModal] = useState<any>(null)   // {recon, depositId, store_code, close_date, category_id}
  const [shortReason, setShortReason] = useState('')
  const [supplementalAmt, setSupplementalAmt] = useState('')

  async function saveDeposit(extra?: any) {
    const payload = { ...dep, amount: Number(extra?.amount ?? dep.amount) || 0, ...extra }
    if (!payload.store_code || !payload.category_id || !payload.amount) {
      setDepMsg('❌ Pick a store, a category, and enter an amount.'); return
    }
    setDepBusy(true); setDepMsg('')
    try {
      const r: any = await api('/api/v1/closing/bank-deposit', { method: 'POST', body: JSON.stringify(payload) })
      setDepMsg('✅ Deposit recorded.')
      if (r?.recon?.is_short) {
        setShortModal({ recon: r.recon, depositId: r.row?.id, store_code: payload.store_code,
          close_date: payload.close_date, category_id: payload.category_id })
        setShortReason(''); setSupplementalAmt('')
      } else {
        setDep(d => ({ ...d, amount: '', employee_name: '' }))
      }
      load()
    } catch (e: any) { setDepMsg('❌ ' + (e?.message || e)) }
    setDepBusy(false)
  }

  async function saveShortReason(willDepositMore: boolean) {
    if (!shortModal?.depositId) return
    try {
      await api(`/api/v1/closing/bank-deposit/${shortModal.depositId}`, {
        method: 'PUT', body: JSON.stringify({ short_reason: shortReason, will_deposit_more: willDepositMore }),
      })
    } catch { /* best-effort */ }
    if (!willDepositMore) { setShortModal(null); setDep(d => ({ ...d, amount: '', employee_name: '' })); load() }
  }

  async function saveSupplemental() {
    if (!shortModal || !supplementalAmt) return
    setDepBusy(true)
    try {
      const r: any = await api('/api/v1/closing/bank-deposit', {
        method: 'POST', body: JSON.stringify({
          close_date: shortModal.close_date, store_code: shortModal.store_code, category_id: shortModal.category_id,
          amount: Number(supplementalAmt) || 0, employee_name: dep.employee_name, parent_deposit_id: shortModal.depositId,
          will_deposit_more: false,
        }),
      })
      setShortModal(null); setSupplementalAmt(''); setDep(d => ({ ...d, amount: '', employee_name: '' })); load()
    } catch (e: any) { setDepMsg('❌ ' + (e?.message || e)) }
    setDepBusy(false)
  }

  const inp = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const shortCount = flatRows.filter(r => r.status === 'short').length
  const overCount = flatRows.filter(r => r.status === 'over').length

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💵 Cash Deposit Reconciliation</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 780 }}>
            For every day with a recorded deposit: cash collected (Daily Closing + POS X-Report) vs cash
            deposited, per category, minus any adjustments you choose to include below (excluded by
            default). <Link href="/closing/deposit-categories" style={{ color: 'var(--accent)' }}>Manage categories</Link>.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
      </div>

      {/* Record a deposit */}
      <div className="card" style={{ padding: 14, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 13 }}>Record a deposit:</strong>
        <input type="date" style={inp} value={dep.close_date} onChange={e => setDep(d => ({ ...d, close_date: e.target.value }))} />
        <select style={inp} value={dep.store_code} onChange={e => setDep(d => ({ ...d, store_code: e.target.value }))}>
          <option value="">Store…</option>
          {stores.map((s, i) => <option key={i} value={s.store_code}>{s.store_address || s.store_code}</option>)}
        </select>
        <select style={inp} value={dep.category_id} onChange={e => setDep(d => ({ ...d, category_id: e.target.value }))}>
          <option value="">Category…</option>
          {cats.map((c: any) => <option key={c.id || c.name} value={c.id || ''}>{c.name}</option>)}
        </select>
        <input style={{ ...inp, width: 110 }} inputMode="decimal" placeholder="Amount $" value={dep.amount} onChange={e => setDep(d => ({ ...d, amount: e.target.value }))} />
        <input style={{ ...inp, width: 130 }} placeholder="Deposited by" value={dep.employee_name} onChange={e => setDep(d => ({ ...d, employee_name: e.target.value }))} />
        <button className="btn" disabled={depBusy} onClick={() => saveDeposit()} style={{ background: 'var(--accent)', color: '#fff' }}>{depBusy ? 'Saving…' : 'Save'}</button>
        {depMsg && <span style={{ fontSize: 12, color: depMsg.startsWith('❌') ? '#b91c1c' : 'var(--text2)' }}>{depMsg}</span>}
      </div>

      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, display: 'flex', gap: 4, alignItems: 'center' }}>
          <input type="date" className="select" value={dateFrom} onChange={e => setDateFrom(e.target.value)} />
          →<input type="date" className="select" value={dateTo} onChange={e => setDateTo(e.target.value)} />
        </span>
        <MarketStorePicker
          stores={storesForCascade}
          selectedMarkets={fMarkets} onMarketsChange={setFMarkets}
          selectedStores={fStores} onStoresChange={setFStores}
        />
        <select className="select" value={categoryId} onChange={e => setCategoryId(e.target.value)}>
          <option value="">All categories</option>
          {cats.map((c: any) => <option key={c.id || c.name} value={c.id || ''}>{c.name}</option>)}
        </select>
        <select className="select" value={statusOnly} onChange={e => setStatusOnly(e.target.value)}>
          <option value="">All statuses</option>
          <option value="short">Short only</option>
          <option value="over">Over only</option>
        </select>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
          <input type="checkbox" checked={includeExpenses} onChange={e => setIncludeExpenses(e.target.checked)} /> Include expenses
        </label>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
          <input type="checkbox" checked={includeBillPayments} onChange={e => setIncludeBillPayments(e.target.checked)} /> Include bill payments
        </label>
        <label style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 5 }}>
          <input type="checkbox" checked={includeOther} onChange={e => setIncludeOther(e.target.checked)} /> Include other adjustments
        </label>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 16 }}>
        <Stat label="Rows" value={String(filtered.length)} />
        <Stat label="Short" value={String(shortCount)} color={shortCount ? '#dc2626' : '#059669'} />
        <Stat label="Over" value={String(overCount)} color={overCount ? '#b45309' : '#059669'} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : (
        <>
          <ReportShell title="Cash Deposit Reconciliation" subtitle={`${dateFrom} → ${dateTo}`}
            filename={`cash-deposit-recon_${dateFrom}_${dateTo}`} columns={columns} rows={filtered} />
          <div className="card" style={{ padding: 0, marginTop: 12, overflow: 'auto' }}>
            {filtered.map((r, i) => (
              <div key={i} style={{ borderTop: i ? '1px solid var(--border)' : undefined, padding: '8px 12px' }}>
                <div style={{ cursor: 'pointer', fontSize: 12, color: 'var(--text3)' }}
                  onClick={() => setExpanded(e => ({ ...e, [i]: !e[i] }))}>
                  {expanded[i] ? '▾' : '▸'} {r.deposits.length} deposit(s) — {r.store_address} · {r.close_date} · {r.category_name}
                </div>
                {expanded[i] && r.deposits.map((d: any, j: number) => (
                  <div key={j} style={{ fontSize: 12, padding: '4px 0 4px 18px', color: 'var(--text2)' }}>
                    {fmt(d.amount)} {d.is_supplemental ? '(supplemental) ' : ''}{d.capture ? `(${d.kind === 'billpay' ? 'bill-pay ' : ''}pickup capture) ` : ''}
                    by {d.employee_name || d.recorded_by || '—'} at {d.created_at || '—'}
                    {d.short_reason ? ` — reason: ${d.short_reason}` : ''}
                    {d.capture && (d.has_slip
                      ? <> · <a href={d.slip_url} target="_blank" rel="noreferrer" style={{ color: 'var(--accent)' }}>slip</a></>
                      : <span style={{ color: '#dc2626', fontWeight: 600 }}> · ⚠ no deposit slip on file</span>)}
                    {d.flagged ? <span style={{ color: '#b45309' }}> · amount flagged</span> : null}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </>
      )}

      <AccountabilityBoard dateFrom={dateFrom} dateTo={dateTo} />

      {shortModal && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50 }}>
          <div className="card" style={{ padding: 20, maxWidth: 420, width: '90%' }}>
            <h3 style={{ margin: 0, fontSize: 16 }}>⚠️ Cash deposit is short by {fmt(shortModal.recon.remaining_short)}</h3>
            <p style={{ fontSize: 13, color: 'var(--text2)' }}>
              Expected {fmt(shortModal.recon.expected_deposit)}, deposited {fmt(shortModal.recon.total_deposited_today)} so far.
            </p>
            <textarea style={{ width: '100%', minHeight: 70, padding: 8, borderRadius: 6, border: '1px solid var(--border)', fontSize: 13 }}
              placeholder="Reason for the short deposit…" value={shortReason} onChange={e => setShortReason(e.target.value)} />
            <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <input style={{ ...inp, width: 110 }} inputMode="decimal" placeholder="More $ now?" value={supplementalAmt}
                onChange={e => setSupplementalAmt(e.target.value)} />
              <button className="btn" disabled={depBusy || !supplementalAmt} onClick={saveSupplemental}
                style={{ background: 'var(--accent)', color: '#fff' }}>I'll deposit more now</button>
              <button className="btn btn-secondary" onClick={async () => { await saveShortReason(true); }}>Save reason — will deposit more later</button>
              <button className="btn btn-secondary" onClick={async () => { await saveShortReason(false); }}>Save reason — that's final</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Deposit Accountability board (owner directive 2026-09-02, mig 943) ────────────────────────
// "if the cash has been handed over to the management then a check box should be there for all
// the dates of which the cash has been handed over to the management - then the management
// should be able to confirm that the cash has been received by them in the system as a check
// box and making the color green for the days the cash has been accounted for whether deposit
// or handed over, it should be a similar workflow as did for the approval."
// The approval-workflow mirror (payroll approvals precedent): pending → confirmed with actor +
// timestamp. GREEN day ⇔ every picked-up envelope is accounted (deposited WITH slip, or handed
// AND management-confirmed). The confirm checkbox is management-only (server-gated fail-closed,
// same gate as Cash Recon (Management)); everyone in span sees the board read-only. Marking a
// day "handed over" records the existing handed_to_mgmt disposition on each still-undisposed
// picked-up envelope (the same POST /pickup/deposit machinery the pickup pages use — no second
// write path); it is one-way here, like any disposition (undo stays a deliberate act).
function AccountabilityBoard({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) {
  const { user } = useAuth()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [busyKey, setBusyKey] = useState('')
  const [msg, setMsg] = useState('')

  const load = useCallback(() => {
    if (!dateFrom || !dateTo) return
    setLoading(true)
    api(`/api/v1/closing/deposit-accountability?start=${dateFrom}&end=${dateTo}`)
      .then(setData).catch(() => setData(null)).finally(() => setLoading(false))
  }, [dateFrom, dateTo])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const s = data?.summary || {}
  const rowKey = (r: any) => `${r.day}|${r.store_code}`

  async function confirmDay(r: any, checked: boolean) {
    setBusyKey(rowKey(r)); setMsg('')
    try {
      const res: any = await api('/api/v1/closing/deposit-mgmt-confirm', { method: 'POST', body: JSON.stringify({
        store_code: r.store_code, close_date: r.day, confirmed: checked, confirmed_by: user?.full_name || 'management',
      }) })
      setMsg(res.note ? `ℹ️ ${res.note}` : checked ? `✅ Receipt confirmed for ${r.store_name || r.store_code} · ${r.day}.` : `↩️ Confirmation revoked for ${r.store_name || r.store_code} · ${r.day}.`)
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusyKey('') }
  }

  async function markHanded(r: any) {
    const undisposed = (r.envelopes || []).filter((e: any) => e.state === 'undisposed')
    if (!undisposed.length) return
    setBusyKey(rowKey(r)); setMsg('')
    try {
      for (const e of undisposed) {
        await api(e.kind === 'billpay' ? '/api/v1/closing/billpay-pickup/deposit' : '/api/v1/closing/pickup/deposit', {
          method: 'POST', body: JSON.stringify({
            store_code: r.store_code, close_date: r.day, employee_name: e.employee_name,
            disposition: 'handed_to_mgmt', handed_to: user?.full_name || undefined,
          }),
        })
      }
      setMsg(`🤝 ${undisposed.length} envelope(s) marked handed to management for ${r.store_name || r.store_code} · ${r.day} — awaiting management confirmation.`)
      load()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusyKey('') }
  }

  function dayStatus(r: any) {
    if (r.green) return <span style={{ color: '#059669', fontWeight: 700 }}>✅ accounted</span>
    const bits: React.ReactNode[] = []
    if (r.missing_slip_rows) bits.push(<span key="s" style={{ color: '#dc2626', fontWeight: 600 }}>⚠ {r.missing_slip_rows} deposit(s) missing the slip</span>)
    if (r.unconfirmed_rows) bits.push(<span key="c" style={{ color: '#b45309', fontWeight: 600 }}>🤝 awaiting mgmt confirmation</span>)
    if (r.undisposed_rows) bits.push(<span key="u" style={{ color: 'var(--text2)' }}>{r.undisposed_rows} pickup(s) not yet deposited/handed</span>)
    if (!bits.length) bits.push(<span key="n" style={{ color: 'var(--text3)' }}>no picked-up envelopes</span>)
    return <span style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap' }}>{bits}</span>
  }

  // Actual-vs-declared at pickup time (owner 2026-09-04, mig 949): a SHORT pickup is visible on
  // the day — display + flag only, it never gates green (the money posture is the owner-gated
  // pickup_actual_relieves_cash knob, not this board).
  function pickupVarianceChip(r: any) {
    if (!r.pickup_short_rows && !r.pickup_over_rows) return null
    return (
      <span title={`DM-recorded actual vs declared at pickup: net ${fmt(r.pickup_variance_total)}`}>
        {r.pickup_short_rows ? <span style={{ color: '#dc2626', fontWeight: 700 }}> · ⚠ {r.pickup_short_rows} short pickup{r.pickup_short_rows === 1 ? '' : 's'} ({fmt(r.pickup_variance_total)})</span> : null}
        {!r.pickup_short_rows && r.pickup_over_rows ? <span style={{ color: '#b45309', fontWeight: 600 }}> · +{fmt(r.pickup_variance_total)} over</span> : null}
      </span>
    )
  }

  const bcell: React.CSSProperties = { padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 12.5, verticalAlign: 'middle' }
  return (
    <div className="card" style={{ padding: 14, marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>🟩 Deposit Accountability <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>{dateFrom} → {dateTo}</span></div>
          <div style={{ fontSize: 12, color: 'var(--text2)', maxWidth: 780 }}>
            Every picked-up envelope must end accounted: <b>deposited with the bank deposit slip</b>, or <b>handed to
            management and confirmed received in the system</b>. Green days are fully accounted; management confirms
            with the checkbox (market manager and above — same workflow as approvals).
          </div>
        </div>
        {data && (
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>
            <b style={{ color: '#059669' }}>{s.green_days || 0}</b>/{s.store_days || 0} days green
            {s.missing_slip_days ? <span style={{ color: '#dc2626' }}> · {s.missing_slip_days} missing slips</span> : null}
            {s.awaiting_confirm_days ? <span style={{ color: '#b45309' }}> · {s.awaiting_confirm_days} awaiting confirm</span> : null}
            {s.short_pickup_days ? <span style={{ color: '#dc2626' }}> · {s.short_pickup_days} short-pickup day{s.short_pickup_days === 1 ? '' : 's'}</span> : null}
          </div>
        )}
      </div>
      {msg && <div style={{ fontSize: 12, marginBottom: 8 }}>{msg}</div>}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : rows.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--text3)', padding: '10px 0' }}>No cash/bill-pay pickups recorded in this range.</div>
      ) : (
        <div className="table-wrapper" style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Day', 'Store', 'Picked up', 'Deposited', 'Handed over', 'Mgmt confirmed', 'Status'].map((h, i) =>
                <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r: any) => {
                const k = rowKey(r); const busy = busyKey === k
                const undisposed = (r.envelopes || []).filter((e: any) => e.state === 'undisposed')
                return (
                  <tr key={k} style={{ background: r.green ? 'rgba(5,150,105,0.10)' : undefined }}>
                    <td style={{ ...bcell, color: 'var(--text3)', whiteSpace: 'nowrap' }}>{r.green ? '🟢 ' : ''}{r.day}</td>
                    <td style={bcell}>{r.store_name || r.store_code}{r.market ? <span style={{ color: 'var(--text3)' }}> · {r.market}</span> : null}</td>
                    <td style={{ ...bcell, fontWeight: 600 }}>{fmt(r.picked_total)} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>({r.picked_envelopes})</span>{pickupVarianceChip(r)}</td>
                    <td style={bcell}>
                      {r.deposited_rows
                        ? <span>{fmt(r.deposited_total)}{r.missing_slip_rows
                            ? <span style={{ color: '#dc2626', fontWeight: 600 }}> · ⚠ {r.missing_slip_rows} no slip</span>
                            : <span style={{ color: '#166534' }}> · slips ✓</span>}
                            {r.flagged_rows ? <span style={{ color: '#b45309' }}> · {r.flagged_rows} flagged</span> : null}</span>
                        : <span style={{ color: 'var(--text3)' }}>—</span>}
                    </td>
                    <td style={bcell}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}
                             title={r.handed ? 'Cash was handed to management (recorded disposition — undo is a deliberate act on the pickup page)'
                               : undisposed.length ? 'Mark the remaining picked-up envelope(s) of this day as handed to management'
                               : 'No picked-up envelopes without a disposition'}>
                        <input type="checkbox" checked={!!r.handed} disabled={busy || r.handed || !undisposed.length}
                               onChange={() => markHanded(r)} />
                        {r.handed ? <span>{fmt(r.handed_total)}</span> : <span style={{ color: 'var(--text3)' }}>{undisposed.length ? 'mark handed' : '—'}</span>}
                      </label>
                    </td>
                    <td style={bcell}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}
                             title={!r.handed_rows ? 'Nothing handed to management on this day'
                               : data?.can_confirm ? 'Confirm (or revoke) that management received this cash — recorded with your name and time'
                               : 'Market manager and above confirm receipt (same gate as Cash Recon Management)'}>
                        <input type="checkbox" checked={!!r.mgmt_confirmed}
                               disabled={busy || !r.handed_rows || !data?.can_confirm}
                               onChange={ev => confirmDay(r, ev.target.checked)} />
                        {r.mgmt_confirmed
                          ? <span style={{ color: '#166534' }}>by {r.mgmt_confirmed_by || 'management'}{r.mgmt_confirmed_at ? ` · ${new Date(r.mgmt_confirmed_at).toLocaleString()}` : ''}</span>
                          : <span style={{ color: r.handed_rows ? '#b45309' : 'var(--text3)' }}>{r.handed_rows ? 'pending' : '—'}</span>}
                      </label>
                    </td>
                    <td style={bcell}>{dayStatus(r)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '14px 16px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 20, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
