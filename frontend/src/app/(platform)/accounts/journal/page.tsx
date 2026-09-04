'use client'
import { useState, useEffect, useMemo } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { usePeriod } from '@/lib/period-context'
import ReportExportBar from '@/components/ReportExportBar'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { EntityOption } from '@/components/EntityPicker'

const inp: React.CSSProperties = { padding: '6px 8px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const PL_TYPES = ['revenue', 'cogs', 'opex', 'other']
const BS_TYPES = ['asset', 'liability', 'equity']
// common manual lines the cash-basis chart needs (the AUTO lines come from the data)
// Owner directive 2026-09-04: cash at bank must be enterable PER STORE, PER COMPANY, or as ONE
// TENANT TOTAL — "everything else is based per store, if there is cash per store then we can get a
// close to reality figure". The three grains are simply the three ways a row can be addressed
// (store picked / company picked / neither), so the quick-adds seed the row already addressed.
const CASH_LINE = 'Cash / bank'
const SUGGESTED = [
  { statement: 'balance_sheet', account_type: 'asset', account_line: 'Cash / bank' },
  { statement: 'balance_sheet', account_type: 'asset', account_line: 'Fixtures / equipment' },
  { statement: 'balance_sheet', account_type: 'equity', account_line: 'Owner capital / contributions' },
  { statement: 'balance_sheet', account_type: 'equity', account_line: 'Opening retained earnings' },
  { statement: 'pl', account_type: 'other', account_line: 'Income taxes' },
  { statement: 'pl', account_type: 'other', account_line: 'Interest expense' },
]
type Row = { statement: string; account_type: string; account_line: string; amount: number; company_id?: string | null; store_address?: string; memo?: string }

export default function JournalPage() {
  const { period } = usePeriod()
  const [rows, setRows] = useState<Row[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  // RULE FIVE (§3d) standard store/market filter. Period = section switcher (usePeriod), rep n/a on a
  // journal entry → only stores + markets shown (deviations documented). The filter narrows the DISPLAY
  // + EXPORT only; `rows` stays the full editable/save source of truth so filtering never drops entries
  // (Save preserves every row, including those hidden). A row with no store (or a store outside the
  // selection / market) is an unattributed entry → hidden while a store/market filter is active.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [fopts, setFopts] = useState<{ stores?: any[]; markets?: string[] }>({})
  const filterActive = filt.stores.length > 0 || filt.markets.length > 0
  // RULE THREE (pick-don't-type) — the defect that stranded the owner's $560k of entries: company
  // and store are now PICKERS (companies from /account/companies, stores from the canonical
  // filter-options roster), never free text. The server echo below confirms what each save did.
  const [companies, setCompanies] = useState<any[]>([])
  const [rejected, setRejected] = useState<any[]>([])
  const [resolved, setResolved] = useState<any[]>([])

  useEffect(() => {
    apiCached(`/api/v1/core/filter-options?org_id=${ORG_ID}`, LOOKUP).then((d: any) => setFopts(d || {})).catch(() => setFopts({}))
    api(`/api/v1/account/companies?org_id=${ORG_ID}`).then((d: any) => setCompanies(d.companies || [])).catch(() => setCompanies([]))
  }, [])
  const storeMarket = useMemo(() => {
    const m: Record<string, string> = {}
    ;(fopts.stores || []).forEach((s: any) => { if (s.store && s.market) m[s.store] = s.market })
    return m
  }, [fopts])
  const storeOpts: EntityOption[] = useMemo(() =>
    (fopts.stores || []).map((s: any) => ({ id: s.store, label: s.store, sublabel: s.market || undefined })), [fopts])
  const marketOpts: string[] = useMemo(() => fopts.markets || [], [fopts])
  // per-row store/market match (client-side; market resolved from the store's roster market)
  const matchRow = (r: Row) => {
    const sa = (r.store_address || '').trim()
    if (filt.stores.length && !filt.stores.includes(sa)) return false
    if (filt.markets.length) { const mk = storeMarket[sa]; if (!mk || !filt.markets.includes(mk)) return false }
    return true
  }
  const namedRows = rows.filter(r => r.account_line.trim())
  const hiddenCount = filterActive ? namedRows.filter(r => !matchRow(r)).length : 0

  function load() {
    setLoading(true)
    api(`/api/v1/account/journal/${encodeURIComponent(period)}?org_id=${ORG_ID}`).then((d: any) => {
      setRows((d.entries || []).map((e: any) => ({
        statement: e.statement, account_type: e.account_type, account_line: e.account_line,
        amount: parseFloat(e.amount) || 0, company_id: e.company_id || '', store_address: e.store_address || '', memo: e.memo || '',
      })))
    }).catch(console.error).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [period])

  // The grain a row is entered at — the same classification the statements apply server-side
  // (balance_sheet.entry_grain): a picked STORE is the finest grain and wins even when a company is
  // also picked; a company alone is company grain; neither is one tenant-wide total.
  const grainOf = (r: Row) => (r.store_address || '').trim() ? 'store' : (r.company_id ? 'company' : 'tenant')
  const GRAIN_LABEL: Record<string, string> = { store: 'per store', company: 'per company', tenant: 'tenant total' }
  // Mixed grains on the SAME account line are legal but must never be added twice: the statements
  // book a coarser row NET of the finer rows inside it. Flag it here so the number is never a surprise.
  const mixedLines = useMemo(() => {
    const byLine: Record<string, Set<string>> = {}
    rows.filter(r => r.account_line.trim() && r.amount).forEach(r => {
      const k = r.account_line.trim().toLowerCase()
      ;(byLine[k] = byLine[k] || new Set()).add(grainOf(r))
    })
    return Object.entries(byLine).filter(([, g]) => g.size > 1).map(([k]) => k)
  }, [rows])

  const set = (i: number, patch: Partial<Row>) => setRows(r => r.map((x, j) => j === i ? { ...x, ...patch } : x))
  const addRow = (seed?: Partial<Row>) => setRows(r => [...r, { statement: 'balance_sheet', account_type: 'asset', account_line: '', amount: 0, store_address: '', memo: '', ...seed }])
  const del = (i: number) => setRows(r => r.filter((_, j) => j !== i))

  async function save() {
    setSaving(true); setMsg(''); setRejected([]); setResolved([])
    try {
      const r = await api(`/api/v1/account/journal/${encodeURIComponent(period)}?org_id=${ORG_ID}`, {
        method: 'PUT', body: JSON.stringify({ rows: rows.filter(x => x.account_line.trim() && x.amount) }),
      })
      // The server's echo (PR #179): an entered amount that cannot be accepted is REPORTED with its
      // reason — surface it loudly so nothing is ever silently dropped again; `resolved` confirms
      // which company each entry attributed to (picker or typed designation).
      setRejected(r.rejected || []); setResolved(r.resolved || [])
      setMsg(`Saved ${r.saved} entries.${(r.rejected || []).length ? ` ${r.rejected.length} REJECTED — see below.` : ''} Re-compute statements on the dashboard to apply.`); load()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
    setSaving(false)
  }

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📒 Manual Journal Entries</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>{period} · cash/bank, fixtures, owner capital, payroll, taxes — the lines the engine can't derive from the data.</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {msg && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{msg}</span>}
          {/* RULE FOUR (§3c) + FIVE (§3d): export the manual journal for this period, honoring the
              active store/market filter (what you see is what exports). */}
          {namedRows.length > 0 && <ReportExportBar
            title={`Manual Journal Entries — ${period}`} subtitle={`${period} · manual entries${filterActive ? ' · filtered' : ''}`}
            filename={`journal-${period.replace(/\s+/g, '-')}`}
            columns={[
              { header: 'Statement', get: (r: any) => r.statement === 'pl' ? 'P&L' : 'Balance Sheet' },
              { header: 'Type', get: (r: any) => r.account_type },
              { header: 'Account line', get: (r: any) => r.account_line },
              { header: 'Amount', get: (r: any) => r.amount, money: true },
              { header: 'Store', get: (r: any) => r.store_address || '' },
              { header: 'Level', get: (r: any) => GRAIN_LABEL[grainOf(r)] },
              { header: 'Memo', get: (r: any) => r.memo || '' },
            ]}
            rows={namedRows.filter(r => !filterActive || matchRow(r))} />}
          <button className="btn btn-primary" onClick={save} disabled={saving}>{saving ? '…' : '💾 Save entries'}</button>
        </div>
      </div>

      {/* RULE FIVE (§3d) standard filter bar — stores + markets. Period = section switcher; rep n/a. */}
      <StandardFilterBar value={filt} onChange={setFilt} show={{ period: false, reps: false }}
        periodMode="none" storeOptions={storeOpts} marketOptions={marketOpts} />
      {filterActive && (
        <div style={{ fontSize: 12, color: 'var(--text2)', margin: '-4px 0 12px' }}>
          Filter active — showing entries for the selected store(s){filt.markets.length ? '/market(s)' : ''}.
          {hiddenCount > 0 && <> <strong>{hiddenCount}</strong> unattributed / other-store entr{hiddenCount === 1 ? 'y is' : 'ies are'} hidden.</>}
          {' '}Saving still preserves every entry (clear the filter to edit hidden rows).
        </div>
      )}

      <div className="card" style={{ padding: 12, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>Quick add:</span>
        {SUGGESTED.map(s => (
          <button key={s.account_line} className="btn" style={{ fontSize: 12 }} onClick={() => addRow(s)}>＋ {s.account_line}</button>
        ))}
        <button className="btn" style={{ fontSize: 12 }} onClick={() => addRow()}>＋ Blank row</button>
      </div>

      {/* The three cash grains, seeded already-addressed (owner directive 2026-09-04). */}
      <div className="card" style={{ padding: 12, marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 4 }}>💵 Cash at bank — enter it at whichever level you actually know</div>
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
          Per store gives the closest-to-reality balance sheet, because every other figure is already
          per store. Per company or one total for the whole tenant work too. If you use more than one
          level on the same line, the bigger figure is treated as the <strong>total</strong> and the smaller
          ones as where part of it sits — so nothing is ever counted twice.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn" style={{ fontSize: 12 }} onClick={() => addRow({ statement: 'balance_sheet', account_type: 'asset', account_line: CASH_LINE, store_address: storeOpts[0]?.id || '' })}>＋ Cash per store</button>
          <button className="btn" style={{ fontSize: 12 }} onClick={() => addRow({ statement: 'balance_sheet', account_type: 'asset', account_line: CASH_LINE, company_id: companies[0]?.id || '' })}>＋ Cash per company</button>
          <button className="btn" style={{ fontSize: 12 }} onClick={() => addRow({ statement: 'balance_sheet', account_type: 'asset', account_line: CASH_LINE })}>＋ Cash — one tenant total</button>
        </div>
        {mixedLines.length > 0 && (
          <div style={{ fontSize: 12.5, marginTop: 8, color: 'var(--text2)' }}>
            ⓘ More than one level is in use on: <strong>{mixedLines.join(', ')}</strong>. The rollups net the
            bigger figure by the smaller ones — check the totals on the Balance Sheet after recomputing.
          </div>
        )}
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : (
        <div className="card" style={{ padding: 0, overflow: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 880 }}>
            <thead>
              <tr style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Statement</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Type</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Account line</th>
                <th style={{ textAlign: 'right', padding: '8px 12px' }}>Amount</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Company (optional)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Store (optional)</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Level</th>
                <th style={{ textAlign: 'left', padding: '8px 12px' }}>Memo</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                // Hide named entries that don't match the active store/market filter (unattributed
                // rows included) — blank scaffold rows stay visible so adding is still possible. `i`
                // stays the true index into `rows`, so edit/delete/save operate on the real entry.
                if (filterActive && r.account_line.trim() && !matchRow(r)) return null
                const types = r.statement === 'pl' ? PL_TYPES : BS_TYPES
                return (
                  <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                    <td style={{ padding: '5px 12px' }}>
                      <select style={inp} value={r.statement} onChange={e => set(i, { statement: e.target.value, account_type: (e.target.value === 'pl' ? PL_TYPES : BS_TYPES)[0] })}>
                        <option value="pl">P&amp;L</option><option value="balance_sheet">Balance Sheet</option>
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}>
                      <select style={inp} value={r.account_type} onChange={e => set(i, { account_type: e.target.value })}>
                        {types.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}><input style={{ ...inp, width: 200 }} value={r.account_line} placeholder="e.g. Cash / bank" onChange={e => set(i, { account_line: e.target.value })} /></td>
                    <td style={{ padding: '5px 12px' }}><input type="number" step="0.01" style={{ ...inp, width: 120, textAlign: 'right' }} value={r.amount || ''} onChange={e => set(i, { amount: parseFloat(e.target.value) || 0 })} /></td>
                    {/* RULE THREE: pick-don't-type. Company picker (the fix for the stranded
                        $250k/$100k/$210k rows typed as company names into the store field). */}
                    <td style={{ padding: '5px 12px' }}>
                      <select style={{ ...inp, width: 150 }} value={r.company_id || ''} onChange={e => set(i, { company_id: e.target.value || null })}>
                        <option value="">(consolidated)</option>
                        {companies.map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}>
                      <select style={{ ...inp, width: 170 }} value={r.store_address || ''} onChange={e => set(i, { store_address: e.target.value })}>
                        <option value="">(all stores)</option>
                        {/* a legacy typed value not on the roster still renders + stays selectable */}
                        {r.store_address && !storeOpts.find(o => o.id === r.store_address) && <option value={r.store_address}>{r.store_address} (typed)</option>}
                        {storeOpts.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </select>
                    </td>
                    <td style={{ padding: '5px 12px' }}>
                      <span style={{ fontSize: 11.5, padding: '3px 8px', borderRadius: 999, whiteSpace: 'nowrap',
                                     border: '1px solid var(--border)', color: 'var(--text2)' }}>{GRAIN_LABEL[grainOf(r)]}</span>
                    </td>
                    <td style={{ padding: '5px 12px' }}><input style={{ ...inp, width: 160 }} value={r.memo} onChange={e => set(i, { memo: e.target.value })} /></td>
                    <td style={{ padding: '5px 12px' }}><button className="btn" style={{ fontSize: 12 }} onClick={() => del(i)}>✕</button></td>
                  </tr>
                )
              })}
              {rows.length === 0 && <tr><td colSpan={9} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>No manual entries yet. Use Quick add above.</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {/* Server echo (PR #179): nothing is ever silently dropped — rejected rows show WHY, resolved
          rows confirm which company each entry attributed to. */}
      {rejected.length > 0 && (
        <div className="card" style={{ padding: 12, marginTop: 14, background: '#fef2f2', border: '1px solid #fecaca' }}>
          <div style={{ fontWeight: 700, fontSize: 13, color: '#991b1b', marginBottom: 6 }}>⚠ {rejected.length} entr{rejected.length === 1 ? 'y was' : 'ies were'} NOT saved</div>
          {rejected.map((r: any, i: number) => (
            <div key={i} style={{ fontSize: 12.5, color: '#991b1b' }}>· <b>{r.account_line}</b> — {r.reason}</div>
          ))}
        </div>
      )}
      {resolved.length > 0 && (
        <div className="card" style={{ padding: 12, marginTop: 14 }}>
          <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 6 }}>✓ Saved — company attribution</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 4 }}>
            {resolved.map((r: any, i: number) => (
              <div key={i} style={{ fontSize: 12.5, color: 'var(--text2)' }}>
                · {r.account_line} ({fmt(r.amount)}) → <b>{r.company || 'Consolidated (all companies)'}</b>
              </div>
            ))}
          </div>
        </div>
      )}

      <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
        Match an account line label to a statement line to fill it (e.g. “Cash / bank”, “Wages / hourly payroll”). Other labels appear as their own line in the chosen section. To balance the Balance Sheet, enter Cash and any Owner capital / Opening retained earnings.
      </p>
    </div>
  )
}
