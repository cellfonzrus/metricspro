'use client'
// HR · Payroll Expenses — mod-people, migration 404 (PARKED, MONEY-TOUCHING pending Gate 1/2).
//
// Two employer-burden buckets, configured here and rolled into ONE ADDITIVE "Payroll Expenses" line
// on the Store Expenses page (source_key='payroll_expenses'):
//   1. Payroll TAX — auto-computed (FICA Social Security + Medicare + FUTA + SUTA), only the RATES
//      are editable here.
//   2. Payroll Expense ITEMS — fully operator-customizable (Unemployment Insurance / Workers Comp
//      are seeded defaults; add/edit/remove any additional item).
// Backend: GET/PUT /storeops/payroll-tax-config, GET/POST/PATCH/DELETE
// /storeops/payroll-expense-items(/{id}), GET /storeops/payroll-expenses/{period},
// POST /storeops/payroll-expenses/run/{period}. See backend/app/modules/storeops/payroll_expenses.py
// + docs/handoffs/people.md for the full contract.
import { useState, useEffect, useCallback } from 'react'
import { api, fmt, parseLocalDate } from '@/lib/client'
import { apiCached, CONFIG } from '@/lib/cache'
import { currentPeriodFromSettingsResponse } from '@/lib/pay-period'
import EntityPicker, { EntityOption } from '@/components/EntityPicker'
import { ReportExportBar, ExportColumn } from '@/components/ReportExportBar'

interface TaxConfig {
  enabled: boolean
  fica_ss_rate: number; fica_ss_wage_base: number
  medicare_rate: number
  futa_rate: number; futa_wage_base: number
  suta_rate: number; suta_wage_base: number
}
interface Item {
  id: string; key: string; name: string
  calc_method: 'pct_wages' | 'per_100_wages' | 'per_employee' | 'fixed'
  rate_or_amount: number; wage_cap: number | null
  scope: 'store' | 'company'; enabled: boolean; sort_order: number
}
interface ItemDetail { key: string; label: string; calc_method: string; scope: string; company_amount: number }
interface StoreRow {
  store: string; wages: number; fica_ss: number; medicare: number; futa: number; suta: number
  tax_total: number; items: Record<string, number>; items_total: number; total: number
}

const CALC_METHOD_OPTIONS: EntityOption[] = [
  { id: 'pct_wages', label: '% of wages' },
  { id: 'per_100_wages', label: '$ per $100 of wages' },
  { id: 'per_employee', label: '$ per employee (headcount)' },
  { id: 'fixed', label: 'Flat $ amount' },
]
const SCOPE_OPTIONS: EntityOption[] = [
  { id: 'store', label: 'Per store' },
  { id: 'company', label: 'Company-wide (allocated to stores by wage share)' },
]

const lbl: React.CSSProperties = { fontSize: 12, fontWeight: 600, color: 'var(--text2)', marginBottom: 4, display: 'block' }
const inp: React.CSSProperties = { width: '100%', padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: 12.5, borderTop: '1px solid var(--border)', whiteSpace: 'nowrap' }

const blankNewItem = { key: '', name: '', calc_method: 'pct_wages' as Item['calc_method'], rate_or_amount: '', wage_cap: '', scope: 'store' as Item['scope'] }

// Current month 'YYYY-MM', local-safe (was a hardcoded '2026-07' that would silently go stale
// every month for every tenant, Boost included).
function currentMonth() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

export default function PayrollExpensesPage() {
  const [month, setMonth] = useState(() => currentMonth())

  // Phase W2 period coherence (owner directive 2026-09-01): the DEFAULT month is derived from the
  // tenant's CURRENT pay period — the calendar month containing the pay period's START date — via
  // the shared resolver (@/lib/pay-period over GET /core/tenant-settings preview[0]).
  //
  // DOCUMENTED SEAM: this page's whole backend contract is keyed on a calendar-MONTH path param
  // ({period} in GET/POST /storeops/payroll-expenses[/run]/{month}), not an arbitrary start/end
  // range — wage-base caps are tracked cumulatively per calendar year, so the month is a real
  // backend concept, not a UI convenience. This phase deliberately does NOT rewrite that contract;
  // it only aligns the DEFAULT month with the shared pay period. Making the run period-native
  // (biweekly-range payroll-expense runs) is a separate backend phase.
  useEffect(() => {
    let cancelled = false
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      if (cancelled) return
      const cur = currentPeriodFromSettingsResponse(r)
      const m = cur?.period?.start?.slice(0, 7)
      // Applied only while the month is still the untouched initial default, so a month the user
      // picked before this (cached) fetch resolved is never clobbered.
      if (m && /^\d{4}-\d{2}$/.test(m)) setMonth(prev => (prev === currentMonth() ? m : prev))
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])
  const [taxCfg, setTaxCfg] = useState<TaxConfig | null>(null)
  const [taxSaving, setTaxSaving] = useState(false)
  const [taxMsg, setTaxMsg] = useState('')

  const [items, setItems] = useState<Item[]>([])
  const [newItem, setNewItem] = useState<any>(blankNewItem)
  const [itemsMsg, setItemsMsg] = useState('')
  const [itemBusy, setItemBusy] = useState<string>('')

  const [view, setView] = useState<{ stores: StoreRow[]; items: ItemDetail[]; last_run_at: string | null } | null>(null)
  const [loadingView, setLoadingView] = useState(false)
  const [running, setRunning] = useState(false)
  const [runMsg, setRunMsg] = useState('')

  const loadTaxConfig = useCallback(() => {
    api('/api/v1/storeops/payroll-tax-config').then(r => setTaxCfg(r.row || r.effective))
      .catch(() => setTaxCfg(null))
  }, [])
  const loadItems = useCallback(() => {
    api('/api/v1/storeops/payroll-expense-items').then(r => setItems(r.items || [])).catch(() => setItems([]))
  }, [])
  const loadView = useCallback(() => {
    setLoadingView(true)
    api(`/api/v1/storeops/payroll-expenses/${month}`).then(setView).catch(() => setView(null))
      .finally(() => setLoadingView(false))
  }, [month])

  useEffect(() => { loadTaxConfig(); loadItems() }, [loadTaxConfig, loadItems])
  useEffect(() => { loadView(); setRunMsg('') }, [loadView])

  async function saveTaxConfig() {
    if (!taxCfg) return
    setTaxSaving(true); setTaxMsg('')
    try {
      await api('/api/v1/storeops/payroll-tax-config', {
        method: 'PUT',
        body: JSON.stringify({
          enabled: taxCfg.enabled,
          fica_ss_rate: Number(taxCfg.fica_ss_rate), fica_ss_wage_base: Number(taxCfg.fica_ss_wage_base),
          medicare_rate: Number(taxCfg.medicare_rate),
          futa_rate: Number(taxCfg.futa_rate), futa_wage_base: Number(taxCfg.futa_wage_base),
          suta_rate: Number(taxCfg.suta_rate), suta_wage_base: Number(taxCfg.suta_wage_base),
        }),
      })
      setTaxMsg('✅ Saved.')
      loadView()
    } catch (e: any) { setTaxMsg('❌ ' + (e?.message || 'Save failed')) }
    finally { setTaxSaving(false) }
  }

  async function addItem() {
    if (!newItem.key.trim() || !newItem.name.trim()) { setItemsMsg('❌ Key and name are required.'); return }
    setItemBusy('new'); setItemsMsg('')
    try {
      await api('/api/v1/storeops/payroll-expense-items', {
        method: 'POST',
        body: JSON.stringify({
          key: newItem.key.trim(), name: newItem.name.trim(), calc_method: newItem.calc_method,
          rate_or_amount: Number(newItem.rate_or_amount || 0),
          wage_cap: newItem.wage_cap === '' ? null : Number(newItem.wage_cap),
          scope: newItem.scope, enabled: true,
        }),
      })
      setNewItem(blankNewItem)
      setItemsMsg('✅ Item added.')
      loadItems(); loadView()
    } catch (e: any) { setItemsMsg('❌ ' + (e?.message || 'Add failed')) }
    finally { setItemBusy('') }
  }

  function patchLocal(id: string, patch: Partial<Item>) {
    setItems(list => list.map(i => i.id === id ? { ...i, ...patch } : i))
  }
  async function saveItem(it: Item) {
    setItemBusy(it.id); setItemsMsg('')
    try {
      await api(`/api/v1/storeops/payroll-expense-items/${it.id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: it.name, calc_method: it.calc_method, rate_or_amount: Number(it.rate_or_amount),
          wage_cap: it.wage_cap === null || (it.wage_cap as any) === '' ? null : Number(it.wage_cap),
          scope: it.scope, enabled: it.enabled,
        }),
      })
      setItemsMsg(`✅ Saved ${it.name}.`)
      loadView()
    } catch (e: any) { setItemsMsg('❌ ' + (e?.message || 'Save failed')) }
    finally { setItemBusy('') }
  }
  async function removeItem(it: Item) {
    if (!confirm(`Remove "${it.name}"? Past runs already booked keep their history — this only stops future runs.`)) return
    setItemBusy(it.id); setItemsMsg('')
    try {
      await api(`/api/v1/storeops/payroll-expense-items/${it.id}`, { method: 'DELETE' })
      setItems(list => list.filter(i => i.id !== it.id))
      setItemsMsg(`🗑️ Removed ${it.name}.`)
      loadView()
    } catch (e: any) { setItemsMsg('❌ ' + (e?.message || 'Remove failed')) }
    finally { setItemBusy('') }
  }

  async function runNow() {
    setRunning(true); setRunMsg('')
    try {
      const r = await api(`/api/v1/storeops/payroll-expenses/run/${month}`, { method: 'POST' })
      const push = r.push || {}
      setRunMsg(push.pushed
        ? `✅ Ran — pushed to Store Expenses as "Payroll Expenses" (${r.tax_ledger_rows_written} tax rows, ${r.expense_ledger_rows_written} expense rows).`
        : `✅ Ran — ledgers saved (${r.tax_ledger_rows_written} tax rows, ${r.expense_ledger_rows_written} expense rows). Expense push not applied yet: ${push.note || 'unknown reason'}`)
      loadView()
    } catch (e: any) { setRunMsg('❌ ' + (e?.message || 'Run failed')) }
    finally { setRunning(false) }
  }

  const monthName = month ? parseLocalDate(month + '-01').toLocaleDateString('en-US', { month: 'long', year: 'numeric' }) : month

  const itemCols: ExportColumn[] = (view?.items || []).map(it => ({
    header: it.label, field: it.key, money: true, get: (r: StoreRow) => r.items[it.key] || 0,
  }))
  const exportCols: ExportColumn[] = [
    { header: 'Store', field: 'store', role: 'store', get: (r: StoreRow) => r.store },
    { header: 'Wages', field: 'wages', money: true, get: (r: StoreRow) => r.wages },
    { header: 'FICA SS', field: 'fica_ss', money: true, get: (r: StoreRow) => r.fica_ss },
    { header: 'Medicare', field: 'medicare', money: true, get: (r: StoreRow) => r.medicare },
    { header: 'FUTA', field: 'futa', money: true, get: (r: StoreRow) => r.futa },
    { header: 'SUTA', field: 'suta', money: true, get: (r: StoreRow) => r.suta },
    { header: 'Tax Total', field: 'tax_total', money: true, get: (r: StoreRow) => r.tax_total },
    ...itemCols,
    { header: 'Items Total', field: 'items_total', money: true, get: (r: StoreRow) => r.items_total },
    { header: 'Payroll Expenses (total)', field: 'total', money: true, get: (r: StoreRow) => r.total },
  ]

  const grandTotal = (view?.stores || []).reduce((s, r) => s + (r.total || 0), 0)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💼 Payroll Expenses</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Employer payroll tax + customizable burden items (Unemployment Insurance, Workers Comp, …) —
            rolled into ONE additive &quot;Payroll Expenses&quot; line on the Store Expenses page each run.
          </p>
        </div>
        <input className="input" type="month" value={month} onChange={e => setMonth(e.target.value)} style={{ width: 160 }} />
      </div>

      {/* ── Payroll tax config ────────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>🧾 Payroll Tax Rates</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 0 }}>
          Employer-side FICA Social Security &amp; Medicare, FUTA, and SUTA/state unemployment. Wage-base
          caps (Social Security / FUTA / SUTA) are tracked CUMULATIVELY across the calendar year per
          employee — SUTA/FUTA defaults are generic placeholders; set your real state rate and wage base.
        </p>
        {taxCfg && (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 10 }}>
              <label style={{ fontSize: 12 }}>
                <span style={lbl}>Enabled</span>
                <input type="checkbox" checked={!!taxCfg.enabled} onChange={e => setTaxCfg({ ...taxCfg, enabled: e.target.checked })} />
              </label>
              <label><span style={lbl}>FICA Social Security rate</span>
                <input style={inp} type="number" step="0.0001" value={taxCfg.fica_ss_rate}
                  onChange={e => setTaxCfg({ ...taxCfg, fica_ss_rate: e.target.value as any })} /></label>
              <label><span style={lbl}>SS wage base ($/yr)</span>
                <input style={inp} type="number" value={taxCfg.fica_ss_wage_base}
                  onChange={e => setTaxCfg({ ...taxCfg, fica_ss_wage_base: e.target.value as any })} /></label>
              <label><span style={lbl}>Medicare rate (no cap)</span>
                <input style={inp} type="number" step="0.0001" value={taxCfg.medicare_rate}
                  onChange={e => setTaxCfg({ ...taxCfg, medicare_rate: e.target.value as any })} /></label>
              <label><span style={lbl}>FUTA rate</span>
                <input style={inp} type="number" step="0.0001" value={taxCfg.futa_rate}
                  onChange={e => setTaxCfg({ ...taxCfg, futa_rate: e.target.value as any })} /></label>
              <label><span style={lbl}>FUTA wage base ($/yr)</span>
                <input style={inp} type="number" value={taxCfg.futa_wage_base}
                  onChange={e => setTaxCfg({ ...taxCfg, futa_wage_base: e.target.value as any })} /></label>
              <label><span style={lbl}>SUTA rate (your state)</span>
                <input style={inp} type="number" step="0.0001" value={taxCfg.suta_rate}
                  onChange={e => setTaxCfg({ ...taxCfg, suta_rate: e.target.value as any })} /></label>
              <label><span style={lbl}>SUTA wage base ($/yr, your state)</span>
                <input style={inp} type="number" value={taxCfg.suta_wage_base}
                  onChange={e => setTaxCfg({ ...taxCfg, suta_wage_base: e.target.value as any })} /></label>
            </div>
            <button className="btn btn-secondary" disabled={taxSaving} onClick={saveTaxConfig}>
              {taxSaving ? 'Saving…' : 'Save Tax Rates'}
            </button>
            {taxMsg && <span style={{ fontSize: 12, marginLeft: 10 }}>{taxMsg}</span>}
          </>
        )}
      </div>

      {/* ── Payroll expense items ─────────────────────────────────────────────────────────── */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div style={{ fontWeight: 700, fontSize: 14, marginBottom: 10 }}>🧩 Payroll Expense Items</div>
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 0 }}>
          Unemployment Insurance and Workers Comp are seeded at $0/0% — set your real carrier rate. Add any
          other custom employer-burden item below.
        </p>
        <div className="card" style={{ padding: 0, overflowX: 'auto', marginBottom: 12 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 780 }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Name', 'Calc method', 'Rate / Amount', 'Wage cap', 'Scope', 'Enabled', ''].map(h => <th key={h} style={th}>{h}</th>)}
            </tr></thead>
            <tbody>
              {items.map(it => (
                <tr key={it.id}>
                  <td style={td}><input style={{ ...inp, width: 160 }} value={it.name} onChange={e => patchLocal(it.id, { name: e.target.value })} /></td>
                  <td style={td}><div style={{ width: 190 }}>
                    <EntityPicker options={CALC_METHOD_OPTIONS} value={it.calc_method} clearable={false}
                      onChange={v => patchLocal(it.id, { calc_method: (v || 'fixed') as Item['calc_method'] })} width="100%" />
                  </div></td>
                  <td style={td}><input style={{ ...inp, width: 90 }} type="number" step="0.0001" value={it.rate_or_amount}
                    onChange={e => patchLocal(it.id, { rate_or_amount: e.target.value as any })} /></td>
                  <td style={td}><input style={{ ...inp, width: 90 }} type="number" placeholder="none" value={it.wage_cap ?? ''}
                    onChange={e => patchLocal(it.id, { wage_cap: (e.target.value === '' ? null : e.target.value) as any })} /></td>
                  <td style={td}><div style={{ width: 150 }}>
                    <EntityPicker options={SCOPE_OPTIONS} value={it.scope} clearable={false}
                      onChange={v => patchLocal(it.id, { scope: (v || 'store') as Item['scope'] })} width="100%" />
                  </div></td>
                  <td style={td}><input type="checkbox" checked={it.enabled} onChange={e => patchLocal(it.id, { enabled: e.target.checked })} /></td>
                  <td style={td}>
                    <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 8px', marginRight: 6 }}
                      disabled={itemBusy === it.id} onClick={() => saveItem(it)}>{itemBusy === it.id ? '…' : '💾 Save'}</button>
                    <button className="btn" style={{ fontSize: 11, padding: '3px 8px' }}
                      disabled={itemBusy === it.id} onClick={() => removeItem(it)}>🗑️</button>
                  </td>
                </tr>
              ))}
              {items.length === 0 && <tr><td style={td} colSpan={7}><span style={{ color: 'var(--text3)' }}>No items yet — Unemployment Insurance / Workers Comp appear once migration 404 has run.</span></td></tr>}

              {/* add-new row */}
              <tr>
                <td style={td}><input style={{ ...inp, width: 160 }} placeholder="Name" value={newItem.name}
                  onChange={e => setNewItem({ ...newItem, name: e.target.value, key: newItem.key || e.target.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_') })} /></td>
                <td style={td}><div style={{ width: 190 }}>
                  <EntityPicker options={CALC_METHOD_OPTIONS} value={newItem.calc_method} clearable={false}
                    onChange={v => setNewItem({ ...newItem, calc_method: v || 'pct_wages' })} width="100%" />
                </div></td>
                <td style={td}><input style={{ ...inp, width: 90 }} type="number" step="0.0001" placeholder="rate" value={newItem.rate_or_amount}
                  onChange={e => setNewItem({ ...newItem, rate_or_amount: e.target.value })} /></td>
                <td style={td}><input style={{ ...inp, width: 90 }} type="number" placeholder="none" value={newItem.wage_cap}
                  onChange={e => setNewItem({ ...newItem, wage_cap: e.target.value })} /></td>
                <td style={td}><div style={{ width: 150 }}>
                  <EntityPicker options={SCOPE_OPTIONS} value={newItem.scope} clearable={false}
                    onChange={v => setNewItem({ ...newItem, scope: v || 'store' })} width="100%" />
                </div></td>
                <td style={td}>—</td>
                <td style={td}>
                  <button className="btn btn-primary" style={{ fontSize: 11, padding: '3px 8px' }}
                    disabled={itemBusy === 'new'} onClick={addItem}>{itemBusy === 'new' ? '…' : '➕ Add'}</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        {itemsMsg && <div style={{ fontSize: 12 }}>{itemsMsg}</div>}
      </div>

      {/* ── Computed breakdown + run ──────────────────────────────────────────────────────── */}
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>📊 {monthName} — computed breakdown</div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {view && view.stores.length > 0 && (
              <ReportExportBar title="Payroll Expenses" subtitle={monthName} filename={`payroll-expenses-${month}`}
                columns={exportCols} rows={view.stores} compact />
            )}
            <button className="btn btn-primary" disabled={running} onClick={runNow}
              title="Computes this period's payroll tax + expense items, saves the ledgers, and pushes the rolled-up 'Payroll Expenses' line to Store Expenses">
              {running ? 'Running…' : `▶ Run for ${month}`}
            </button>
          </div>
        </div>
        {runMsg && <div style={{ fontSize: 12, marginBottom: 10 }}>{runMsg}</div>}
        {view?.last_run_at && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>Last run: {new Date(view.last_run_at).toLocaleString()}</div>}

        {loadingView ? <div style={{ padding: 30, color: 'var(--text3)' }}>Loading…</div> : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
              <thead><tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Store</th><th style={th}>Wages</th>
                <th style={th}>FICA SS</th><th style={th}>Medicare</th><th style={th}>FUTA</th><th style={th}>SUTA</th>
                <th style={th}>Tax Total</th>
                {(view?.items || []).map(it => <th key={it.key} style={th}>{it.label}</th>)}
                <th style={th}>Items Total</th>
                <th style={{ ...th, fontWeight: 800 }}>Payroll Expenses (total)</th>
              </tr></thead>
              <tbody>
                {(view?.stores || []).map(r => (
                  <tr key={r.store}>
                    <td style={{ ...td, fontWeight: 600 }}>{r.store}</td>
                    <td style={td}>{fmt(r.wages)}</td>
                    <td style={td}>{fmt(r.fica_ss)}</td>
                    <td style={td}>{fmt(r.medicare)}</td>
                    <td style={td}>{fmt(r.futa)}</td>
                    <td style={td}>{fmt(r.suta)}</td>
                    <td style={{ ...td, fontWeight: 600 }}>{fmt(r.tax_total)}</td>
                    {(view?.items || []).map(it => <td key={it.key} style={td}>{fmt(r.items[it.key] || 0)}</td>)}
                    <td style={{ ...td, fontWeight: 600 }}>{fmt(r.items_total)}</td>
                    <td style={{ ...td, fontWeight: 800 }}>{fmt(r.total)}</td>
                  </tr>
                ))}
                {(!view?.stores || view.stores.length === 0) && (
                  <tr><td colSpan={20} style={{ ...td, textAlign: 'center', color: 'var(--text3)' }}>No worked hours for {monthName} yet.</td></tr>
                )}
              </tbody>
              {view && view.stores.length > 0 && (
                <tfoot><tr style={{ borderTop: '2px solid var(--border)' }}>
                  <td style={{ ...td, fontWeight: 800 }}>Total</td>
                  <td colSpan={6 + (view.items?.length || 0) + 1} />
                  <td style={{ ...td, fontWeight: 800 }}>{fmt(grandTotal)}</td>
                </tr></tfoot>
              )}
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
