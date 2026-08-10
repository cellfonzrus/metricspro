'use client'
// POS module — Sales Tax.
//
// 2026-08-10: THE BACKEND FOR THIS SECTION SHIPPED ON 2026-08-09 WITH NO CALLER. Four endpoints were
// built that afternoon in answer to the owner's own words — "give the store which are already
// configured as a drop down menu to assign them the respective sales tax, if not then it should
// present a spreadsheet to enter" and "option for new tax code should also have market in pos
// settings to assign the same tax code to that market and option to select multiple stores from the
// drop down menu" — and `grep` across `frontend/src` found ZERO references to any of them:
//     GET  /pos/tax-codes/store-grid   every configured store + the rate it actually charges
//     GET  /pos/tax-codes/markets      the market list, with store counts
//     POST /pos/tax-codes/bulk         many stores, or a whole market, in one save
//     GET  /pos/tax-codes/resolve      the ONE precedence used by the register
// From the owner's seat nothing had changed: still one store at a time, no market, no multi-select.
// This file is that UI.
//
// WHY THE COVERAGE BANNER IS THE FIRST THING ON THE SCREEN. Measured live on Luxelink 2026-08-10:
// 20 active stores, ONE tax code (Lefferts 8.875%), so 19 stores resolved `scope: 'none'` — a taxable
// sale at any of them charges $0, which is not recoverable once the customer has left. The POS setup
// wizard nevertheless reported sales tax COMPLETE, because its predicate counts tax_code ROWS and one
// row satisfies `min: 1`. A number that is silently zero is the worst failure mode this section has,
// so it is stated at the top, in red, before anything else.
//
// SCOPE IS RENDERED HONESTLY. Migration 741 added `market`, making a tax code's scope one of
// store > market > org-wide. The old table printed the Store column from `store_code` alone, so a
// market-scoped row — store_code NULL — read "All stores". That mislabels the reach of a tax RATE,
// so scope is now derived from both columns and shown as its own column.
//
// PICK-DON'T-TYPE (RULE THREE): market and store are chosen from the org's real, org-scoped lists.
// Nothing here is a free-text reference to an entity that already exists.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { resolvePosConfig } from '@/lib/pos-config'
import type { PosSettingRow } from '@/lib/pos-config'
import { friendlyError, storeLabel } from '@/components/pos/PosConfigSection'
import type { PosStore } from '@/components/pos/PosConfigSection'

interface TaxCode {
  id: string; name: string; rate: number
  store_code: string | null; market?: string | null
  is_active: boolean; created_at?: string
}
/** One row of GET /pos/tax-codes/store-grid. */
interface GridStore {
  store_code: string; address: string | null; market: string | null
  rate: number | null                 // a rate set ON this store
  effective_rate: number | null       // what it would actually charge
  effective_scope: 'store' | 'market' | 'org' | 'none'
  tax_code_id: string | null; name: string | null; inherits_org_rate: boolean
}
interface MarketRow { market: string; stores: number; rate: number | null }

type ScopeKind = 'org' | 'market' | 'stores'
interface TaxCodeForm {
  id: string | null; name: string; rate: string
  scope: ScopeKind; market: string; storeCodes: string[]
  is_active: boolean
}
type TaxRule = 'pre_discount' | 'post_discount'

const emptyTaxForm: TaxCodeForm = {
  id: null, name: '', rate: '', scope: 'stores', market: '', storeCodes: [], is_active: true,
}

const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const label: React.CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 14px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 14px', fontSize: 13, borderBottom: '1px solid var(--border)' }
const errorBox: React.CSSProperties = { margin: '12px 16px', border: '1px solid #dc2626', color: '#dc2626', borderRadius: 8, padding: '10px 14px', fontSize: 12 }

/** A tax code's reach, from BOTH scope columns. store_code wins; then market; else org-wide. */
export function scopeOf(tc: { store_code?: string | null; market?: string | null }):
  { kind: ScopeKind; value: string } {
  if ((tc.store_code || '').trim()) return { kind: 'stores', value: (tc.store_code as string).trim() }
  if ((tc.market || '').trim()) return { kind: 'market', value: (tc.market as string).trim() }
  return { kind: 'org', value: '' }
}

const pct = (n: number | null | undefined) =>
  n === null || n === undefined ? '—' : `${Number(n).toFixed(3).replace(/\.?0+$/, '')}%`

interface Props {
  stores: PosStore[]
  /** All pos_settings rows (owned by the page) — used to read the effective org tax rule. */
  rows: PosSettingRow[]
  /** Called after the tax rule is saved so the page re-fetches settings rows (keeps the config engine coherent). */
  onSettingsChanged: () => Promise<void>
}

export default function TaxCodesSection({ stores, rows, onSettingsChanged }: Props) {
  const [taxCodes, setTaxCodes] = useState<TaxCode[]>([])
  const [grid, setGrid] = useState<GridStore[]>([])
  const [gridMode, setGridMode] = useState<'stores' | 'blank'>('blank')
  const [markets, setMarkets] = useState<MarketRow[]>([])
  const [loading, setLoading] = useState(true)
  const [taxForm, setTaxForm] = useState<TaxCodeForm | null>(null)
  const [taxError, setTaxError] = useState('')
  const [taxSaving, setTaxSaving] = useState(false)

  // The store spreadsheet: store_code -> the rate typed into that row (string while editing).
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [gridOpen, setGridOpen] = useState(false)
  const [gridSaving, setGridSaving] = useState(false)
  const [gridMsg, setGridMsg] = useState('')

  // Tax rule (org-scope pos setting) — optimistic while the PUT + reload are in flight.
  const [pendingRule, setPendingRule] = useState<TaxRule | null>(null)
  const [ruleSaving, setRuleSaving] = useState(false)
  const [ruleError, setRuleError] = useState('')
  const [ruleSavedAt, setRuleSavedAt] = useState('')

  const savedRule = String(resolvePosConfig(rows, null).values.tax_applied_on) as TaxRule
  const taxRule: TaxRule = pendingRule ?? savedRule

  const loadAll = useCallback(async () => {
    // Each read is independent: the coverage banner must still appear when the market list 404s on an
    // older backend, and the code list must still render when the grid endpoint is unavailable.
    try {
      const r = await api('/api/v1/pos/tax-codes')
      setTaxCodes((r.tax_codes || []) as TaxCode[])
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not load tax codes'))
    }
    try {
      const g = await api('/api/v1/pos/tax-codes/store-grid')
      setGrid((g.stores || []) as GridStore[])
      setGridMode(g.mode === 'stores' ? 'stores' : 'blank')
    } catch { setGrid([]) }
    try {
      const m = await api('/api/v1/pos/tax-codes/markets')
      setMarkets((m.markets || []) as MarketRow[])
    } catch { setMarkets([]) }
  }, [])

  useEffect(() => { loadAll().then(() => setLoading(false)) }, [loadAll])

  // ── Coverage: the stores that would charge nothing ────────────────────────────────────────────
  const uncovered = useMemo(() => grid.filter(s => s.effective_scope === 'none'), [grid])

  async function saveTaxRule(rule: TaxRule) {
    if (ruleSaving || rule === taxRule) return
    setPendingRule(rule); setRuleSaving(true); setRuleError(''); setRuleSavedAt('')
    try {
      await api('/api/v1/pos/settings', {
        method: 'PUT',
        body: JSON.stringify({ key: 'tax_applied_on', value: rule, store_code: null }),
      })
      await onSettingsChanged()
      setRuleSavedAt(new Date().toLocaleTimeString())
    } catch (err) {
      setRuleError(friendlyError(err, 'Could not save tax rule'))
    } finally { setPendingRule(null); setRuleSaving(false) }
  }

  // ── Save the form ─────────────────────────────────────────────────────────────────────────────
  // Creating uses /tax-codes/bulk for every scope EXCEPT a plain org-wide row, because bulk is the
  // only endpoint that understands market + several stores, and it UPDATES a store that already has
  // a rate instead of creating a second one (two rates for one store is the outcome that silently
  // mis-charges tax). Editing one existing row still PATCHes that row by id.
  async function saveTaxCode() {
    if (!taxForm) return
    const name = taxForm.name.trim()
    const rate = Number(taxForm.rate)
    if (!name) { setTaxError('Give the rate a name (e.g. "NYC" or "Illinois 10.25").'); return }
    if (taxForm.rate.trim() === '' || !Number.isFinite(rate)) { setTaxError('Rate must be a number (percent, e.g. 8.875).'); return }
    if (rate < 0 || rate > 30) { setTaxError('Rate must be between 0 and 30 percent.'); return }
    if (taxForm.scope === 'market' && !taxForm.market) { setTaxError('Pick a market, or change the scope.'); return }
    if (taxForm.scope === 'stores' && taxForm.storeCodes.length === 0) { setTaxError('Pick at least one store, or change the scope.'); return }

    setTaxSaving(true); setTaxError('')
    try {
      if (taxForm.id) {
        // Editing an existing row: send BOTH scope columns so a row can move between rungs. Migration
        // 741's CHECK forbids naming a store AND a market at once, so exactly one is ever non-null.
        const sc = taxForm.scope
        await api(`/api/v1/pos/tax-codes/${taxForm.id}`, {
          method: 'PATCH',
          body: JSON.stringify({
            name, rate, is_active: taxForm.is_active,
            store_code: sc === 'stores' ? (taxForm.storeCodes[0] || null) : null,
            market: sc === 'market' ? taxForm.market : null,
          }),
        })
      } else if (taxForm.scope === 'market') {
        await api('/api/v1/pos/tax-codes/bulk', {
          method: 'POST', body: JSON.stringify({ market: taxForm.market, rate, name }),
        })
      } else if (taxForm.scope === 'stores') {
        await api('/api/v1/pos/tax-codes/bulk', {
          method: 'POST', body: JSON.stringify({ store_codes: taxForm.storeCodes, rate, name }),
        })
      } else {
        await api('/api/v1/pos/tax-codes', {
          method: 'POST', body: JSON.stringify({ name, rate, store_code: null }),
        })
      }
      setTaxForm(null)
      await loadAll()
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not save tax code'))
    } finally { setTaxSaving(false) }
  }

  async function toggleTaxCode(tc: TaxCode) {
    const s = scopeOf(tc)
    const reach = s.kind === 'org' ? 'every store that has no rate of its own'
      : s.kind === 'market' ? `every store in ${s.value}` : s.value
    if (tc.is_active && !confirm(`Deactivate "${tc.name}" (${pct(tc.rate)})?\n\nRegisters at ${reach} will fall back to the next rate down — and charge $0 if there isn't one.`)) return
    setTaxError('')
    try {
      await api(`/api/v1/pos/tax-codes/${tc.id}`, { method: 'PATCH', body: JSON.stringify({ is_active: !tc.is_active }) })
      await loadAll()
    } catch (err) {
      setTaxError(friendlyError(err, 'Could not update tax code'))
    }
  }

  // ── The store spreadsheet ─────────────────────────────────────────────────────────────────────
  async function saveGrid() {
    const entries = Object.entries(draft)
      .filter(([, v]) => String(v).trim() !== '')
      .map(([store_code, v]) => ({ store_code, rate: Number(v) }))
    if (entries.length === 0) { setGridMsg('Nothing typed yet — enter a rate against at least one store.'); return }
    const bad = entries.find(e => !Number.isFinite(e.rate) || e.rate < 0 || e.rate > 30)
    if (bad) { setGridMsg(`"${bad.store_code}" has an invalid rate — it must be a number between 0 and 30.`); return }
    setGridSaving(true); setGridMsg('')
    try {
      const r = await api('/api/v1/pos/tax-codes/bulk', { method: 'POST', body: JSON.stringify({ entries }) })
      setDraft({})
      await loadAll()
      const skipped = (r.skipped || []).length
      setGridMsg(`Saved — ${r.created || 0} new, ${r.updated || 0} updated${skipped ? `, ${skipped} skipped` : ''}.`)
    } catch (err) {
      setGridMsg(friendlyError(err, 'Could not save the rates'))
    } finally { setGridSaving(false) }
  }

  function fillUncovered() {
    const v = window.prompt(`Set the same rate for all ${uncovered.length} stores with no rate.\nEnter the percent (e.g. 8.875):`)
    if (v === null) return
    const n = Number(v)
    if (!Number.isFinite(n) || n < 0 || n > 30) { setGridMsg('That is not a percent between 0 and 30.'); return }
    setGridOpen(true)
    setDraft(d => { const next = { ...d }; uncovered.forEach(s => { next[s.store_code] = String(n) }); return next })
    setGridMsg(`Typed ${n}% against ${uncovered.length} stores — press "Save rates" to apply.`)
  }

  const storeName = (code: string | null) => {
    if (!code) return ''
    const s = stores.find(x => x.store_code === code)
    return s ? storeLabel(s) : code
  }
  // The store multi-select is driven by the GRID (which is org-scoped and carries market), falling
  // back to the page's store list on an older backend.
  const pickableStores: { code: string; label: string; market: string | null }[] = useMemo(() => (
    grid.length
      ? grid.map(g => ({ code: g.store_code, label: g.address ? `${g.store_code} — ${g.address}` : g.store_code, market: g.market }))
      : stores.map(s => ({ code: s.store_code, label: storeLabel(s), market: s.market ?? null }))
  ), [grid, stores])

  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, marginBottom: 16, overflow: 'hidden' }}>
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 700 }}>💵 Sales Tax</div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
            A rate can cover one store, a whole market, or the company — the register uses the most specific one that applies
          </div>
        </div>
        {!taxForm && (
          <div style={{ display: 'flex', gap: 8 }}>
            {gridMode === 'stores' && (
              <button className="btn btn-secondary" onClick={() => { setGridMsg(''); setGridOpen(o => !o) }}>
                {gridOpen ? 'Hide store list' : '📋 Set rates store by store'}
              </button>
            )}
            <button className="btn btn-primary" onClick={() => { setTaxError(''); setTaxForm({ ...emptyTaxForm }) }}>+ Add Tax Code</button>
          </div>
        )}
      </div>

      {/* ── COVERAGE: stated before anything else ─────────────────────────────────────────────── */}
      {!loading && uncovered.length > 0 && (
        <div style={{ margin: '12px 16px', border: '1px solid #dc2626', background: '#fef2f2', borderRadius: 8, padding: '11px 14px', fontSize: 13, color: '#991b1b' }}>
          <b>{uncovered.length} of {grid.length} store{grid.length === 1 ? '' : 's'} has no sales-tax rate.</b>{' '}
          A taxable sale there charges <b>$0 tax</b>, and that is not recoverable once the customer has
          left. Stores affected: {uncovered.slice(0, 6).map(s => s.store_code).join(', ')}
          {uncovered.length > 6 ? ` and ${uncovered.length - 6} more` : ''}.
          <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setGridMsg(''); setGridOpen(true) }}>
              Show the store list
            </button>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={fillUncovered}>
              Set one rate for all {uncovered.length}
            </button>
          </div>
        </div>
      )}
      {!loading && grid.length > 0 && uncovered.length === 0 && (
        <div style={{ margin: '12px 16px', border: '1px solid #86efac', background: '#f0fdf4', borderRadius: 8, padding: '9px 14px', fontSize: 12.5, color: '#166534' }}>
          ✅ All {grid.length} stores have a rate that applies.
        </div>
      )}

      {/* ── Org rule ──────────────────────────────────────────────────────────────────────────── */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 8, textTransform: 'uppercase' }}>
          Discounted sales — what gets taxed
          {ruleSaving && <span style={{ color: '#f39c12', fontWeight: 400, marginLeft: 10, textTransform: 'none' }}>saving…</span>}
          {!ruleSaving && ruleSavedAt && <span style={{ color: '#16a34a', fontWeight: 400, marginLeft: 10, textTransform: 'none' }}>saved {ruleSavedAt}</span>}
        </div>
        {([
          { value: 'pre_discount' as TaxRule, label: 'Tax on price BEFORE discount', hint: 'Tax is computed on the original price, then the discount is applied.' },
          { value: 'post_discount' as TaxRule, label: 'Tax on price AFTER discount', hint: 'Tax is computed on the discounted price (most states). Default.' },
        ]).map(opt => (
          <label key={opt.value} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '6px 0', cursor: ruleSaving ? 'wait' : 'pointer' }}>
            <input type="radio" name="pos_tax_rule" checked={taxRule === opt.value} disabled={ruleSaving}
              onChange={() => saveTaxRule(opt.value)} style={{ marginTop: 2 }} />
            <span>
              <span style={{ fontSize: 13, fontWeight: taxRule === opt.value ? 700 : 400 }}>{opt.label}</span>
              <span style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>{opt.hint}</span>
            </span>
          </label>
        ))}
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
          This rule is state-dependent — check the regulations for the states you operate in. Saved as the org-wide
          “Tax is applied on” setting (stores can override it in POS Configuration → Tax Calculation Rules).
        </div>
        {ruleError && <div style={{ ...errorBox, margin: '10px 0 0' }}>{ruleError}</div>}
      </div>

      {/* ── Tax code form ─────────────────────────────────────────────────────────────────────── */}
      {taxForm && (
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 10 }}>{taxForm.id ? 'Edit Tax Code' : 'New Tax Code'}</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div>
              <label style={label}>Name</label>
              <input value={taxForm.name} onChange={e => setTaxForm({ ...taxForm, name: e.target.value })} placeholder="e.g. NY State + NYC" style={{ ...input, width: 200 }} />
            </div>
            <div>
              <label style={label}>Rate (%)</label>
              <input value={taxForm.rate} onChange={e => setTaxForm({ ...taxForm, rate: e.target.value })} placeholder="8.875" inputMode="decimal" style={{ ...input, width: 100 }} />
            </div>
            <div>
              <label style={label}>Applies to</label>
              <select value={taxForm.scope} onChange={e => setTaxForm({ ...taxForm, scope: e.target.value as ScopeKind })} style={{ ...input, width: 190 }}>
                <option value="stores">Specific store(s)</option>
                <option value="market">A whole market</option>
                <option value="org">Every store (company default)</option>
              </select>
            </div>

            {taxForm.scope === 'market' && (
              <div>
                <label style={label}>Market</label>
                <select value={taxForm.market} onChange={e => setTaxForm({ ...taxForm, market: e.target.value })} style={{ ...input, width: 240 }}>
                  <option value="">Pick a market…</option>
                  {markets.map(m => (
                    <option key={m.market} value={m.market}>
                      {m.market} — {m.stores} store{m.stores === 1 ? '' : 's'}{m.rate !== null ? ` (currently ${pct(m.rate)})` : ''}
                    </option>
                  ))}
                </select>
              </div>
            )}

            {taxForm.scope === 'stores' && (
              <div style={{ minWidth: 300 }}>
                <label style={label}>
                  Stores — tick as many as share this rate
                  {taxForm.id && <span style={{ color: '#b45309' }}> (editing one row: only the first tick is kept)</span>}
                </label>
                <div style={{ display: 'flex', gap: 8, marginBottom: 5 }}>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                    onClick={() => setTaxForm({ ...taxForm, storeCodes: pickableStores.map(s => s.code) })}>Select all</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                    onClick={() => setTaxForm({ ...taxForm, storeCodes: [] })}>Clear</button>
                  {uncovered.length > 0 && (
                    <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                      onClick={() => setTaxForm({ ...taxForm, storeCodes: uncovered.map(s => s.store_code) })}>
                      Only the {uncovered.length} with no rate
                    </button>
                  )}
                </div>
                <div style={{ maxHeight: 168, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 7, padding: '6px 10px', background: 'var(--surface)' }}>
                  {pickableStores.length === 0 && <div style={{ fontSize: 12, color: 'var(--text3)' }}>No stores are set up yet.</div>}
                  {pickableStores.map(s => (
                    <label key={s.code} style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, padding: '2px 0', cursor: 'pointer' }}>
                      <input type="checkbox" checked={taxForm.storeCodes.includes(s.code)}
                        onChange={ev => setTaxForm({
                          ...taxForm,
                          storeCodes: ev.target.checked
                            ? [...taxForm.storeCodes, s.code]
                            : taxForm.storeCodes.filter(c => c !== s.code),
                        })} />
                      <span>{s.label}{s.market ? <span style={{ color: 'var(--text3)' }}> · {s.market}</span> : null}</span>
                    </label>
                  ))}
                </div>
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 4 }}>
                  {taxForm.storeCodes.length} selected. A store that already has a rate is updated, never duplicated.
                </div>
              </div>
            )}

            {taxForm.id && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, paddingBottom: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={taxForm.is_active} onChange={e => setTaxForm({ ...taxForm, is_active: e.target.checked })} />
                Active
              </label>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" onClick={saveTaxCode} disabled={taxSaving}>{taxSaving ? 'Saving…' : 'Save'}</button>
              <button className="btn btn-secondary" onClick={() => { setTaxForm(null); setTaxError('') }}>Cancel</button>
            </div>
          </div>
          {taxForm.scope === 'org' && (
            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
              A company default is only used by stores that have no rate of their own and are not in a
              market that has one. It is the safety net, not the answer for a company operating in
              several tax jurisdictions.
            </div>
          )}
        </div>
      )}

      {taxError && <div style={errorBox}>{taxError}</div>}

      {/* ── The store spreadsheet (the owner's "give me the stores as a list") ────────────────── */}
      {gridOpen && (
        <div style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
          <div style={{ padding: '12px 16px 4px', fontSize: 12, color: 'var(--text2)' }}>
            Every store you have set up. <b>Rate</b> is what is set on the store itself; <b>Charges</b> is
            what the register actually uses once markets and the company default are taken into account.
            Type into any row and press Save.
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 420 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: 'var(--surface)' }}>
                  <th style={th}>Store</th><th style={th}>Market</th><th style={th}>Rate on this store</th>
                  <th style={th}>Charges</th><th style={th}>From</th><th style={th}>Set rate</th>
                </tr>
              </thead>
              <tbody>
                {grid.map(s => (
                  <tr key={s.store_code} style={{ background: s.effective_scope === 'none' ? '#fef2f2' : undefined }}>
                    <td style={{ ...td, fontWeight: 600 }}>{s.store_code}
                      {s.address && <div style={{ fontWeight: 400, fontSize: 11.5, color: 'var(--text3)' }}>{s.address}</div>}
                    </td>
                    <td style={td}>{s.market || '—'}</td>
                    <td style={td}>{pct(s.rate)}</td>
                    <td style={{ ...td, fontWeight: 700, color: s.effective_scope === 'none' ? '#dc2626' : '#16a34a' }}>
                      {s.effective_scope === 'none' ? '$0 — no rate' : pct(s.effective_rate)}
                    </td>
                    <td style={{ ...td, fontSize: 12, color: 'var(--text2)' }}>
                      {s.effective_scope === 'store' ? 'this store'
                        : s.effective_scope === 'market' ? `market (${s.market})`
                          : s.effective_scope === 'org' ? 'company default' : '—'}
                    </td>
                    <td style={td}>
                      <input value={draft[s.store_code] ?? ''} inputMode="decimal" placeholder={s.rate !== null ? String(s.rate) : '—'}
                        aria-label={`Sales tax rate for ${s.store_code}`}
                        onChange={e => setDraft(d => ({ ...d, [s.store_code]: e.target.value }))}
                        style={{ ...input, width: 90 }} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-primary" onClick={saveGrid} disabled={gridSaving}>
              {gridSaving ? 'Saving…' : `Save rates (${Object.values(draft).filter(v => String(v).trim() !== '').length})`}
            </button>
            <button className="btn btn-secondary" onClick={() => { setDraft({}); setGridMsg('') }}>Clear typed</button>
            {gridMsg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{gridMsg}</span>}
          </div>
        </div>
      )}

      {/* ── Tax codes table ───────────────────────────────────────────────────────────────────── */}
      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 30 }}><div className="spinner" /></div>
      ) : taxCodes.length === 0 ? (
        <div style={{ padding: '24px 16px', textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No tax codes yet — add one so registers can charge sales tax.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ background: 'var(--surface2)' }}>
                <th style={th}>Name</th><th style={th}>Rate</th><th style={th}>Applies to</th><th style={th}>Status</th><th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {taxCodes.map(tc => {
                const s = scopeOf(tc)
                return (
                  <tr key={tc.id} style={{ opacity: tc.is_active ? 1 : 0.5 }}>
                    <td style={{ ...td, fontWeight: 600 }}>{tc.name}</td>
                    <td style={{ ...td, color: '#16a34a', fontWeight: 700 }}>{pct(tc.rate)}</td>
                    <td style={td}>
                      {s.kind === 'org' && <span style={{ color: '#f39c12' }}>Every store (company default)</span>}
                      {s.kind === 'market' && <span>Market · <b>{s.value}</b></span>}
                      {s.kind === 'stores' && <span>{storeName(s.value) || s.value}</span>}
                    </td>
                    <td style={td}><span style={{ color: tc.is_active ? '#16a34a' : '#dc2626', fontWeight: 600 }}>{tc.is_active ? 'Active' : 'Inactive'}</span></td>
                    <td style={td}>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px' }}
                          onClick={() => {
                            setTaxError('')
                            setTaxForm({
                              id: tc.id, name: tc.name, rate: String(tc.rate),
                              scope: s.kind, market: s.kind === 'market' ? s.value : '',
                              storeCodes: s.kind === 'stores' ? [s.value] : [],
                              is_active: tc.is_active,
                            })
                          }}>Edit</button>
                        <button className="btn btn-secondary" style={{ fontSize: 11, padding: '2px 8px', color: tc.is_active ? '#dc2626' : '#16a34a' }}
                          onClick={() => toggleTaxCode(tc)}>{tc.is_active ? 'Deactivate' : 'Reactivate'}</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', fontSize: 12, color: 'var(--text2)' }}>
        💡 The register picks the most specific rate that applies: the store&apos;s own rate, then its
        market&apos;s, then the company default. If none of the three exists it charges <b>no tax at all</b> —
        which is why the red banner above matters more than anything else on this screen.
      </div>
    </div>
  )
}
