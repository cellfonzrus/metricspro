'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import Link from 'next/link'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MarketStorePicker, type StoreOpt } from '../_lib/MarketStorePicker'
import { WorkflowNext } from '@/components/WorkflowNext'

// ── BANK DEPOSIT — the banking STEP, its own screen (owner directive 2026-10-10) ────────────────
//
// Owner, verbatim: *"Bank deposit should be a separate module for both cash and epay in one link on
// top and bottom but recorded separately as right now it is confusing and not traceable by
// employees easily add another step in the workflow after cash pick up epay pick then add bank
// deposit and the cash recon"*.
//
// WHAT WAS CONFUSING. Banking the money had no screen. It was a one-line "Record a deposit:" strip
// wedged into the header of the Cash Deposit RECONCILIATION report — a report whose job is to
// check, after the fact, whether what was collected matches what was banked. So the person doing
// the banking had to open a reconciliation to do it, pick the right bucket out of one undivided
// category dropdown, and then read their own entry back out of a reconciliation table mixed in with
// every other store-day. Cash and bill-payment cash are two different piles of money banked by two
// different flows, and the form treated them as one list.
//
// WHAT THIS IS. The same two piles, in two sections, recorded separately and read back separately,
// with the section links at the top AND the bottom of the page so somebody deep in one section can
// reach the other without scrolling back.
//
// IT DERIVES NOTHING AND STORES NOTHING NEW. Every figure here comes from `GET /closing/
// deposit-recon` — the SAME endpoint, the same expected/deposited/variance math, the same keyset —
// and every deposit is written through `POST /closing/bank-deposit`, the one writer every other
// deposit-recording surface in this module already uses. A second derivation of "how much should
// have been banked" is exactly the drift the house rules forbid; this screen is a different VIEW of
// one answer, not a second answer.
//
// WHICH SECTION A DEPOSIT BELONGS TO IS DECLARED, NOT GUESSED. Cash vs bill-payment is the deposit
// category's own `basis` (mig 509 — the two lazy-seeded presets are "Store Cash Deposit" and "Bill
// Payment Cash Deposit"), resolved by `closing/deposit_recon.section_for_basis` on the server and
// served as `section` on every category and every recon block. This screen spells no basis word and
// no processor name: the section labels come from the served `sections` catalog, which names the
// bill-payment section with the tenant's OWN processor term (RULE TWO — the stage_catalog
// precedent). A category whose basis declares neither sorts to "Other Deposits" and is counted in
// neither pile, because guessing would silently mis-bank money.
function monthAgo(): string {
  const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - 1)
  return d.toISOString().slice(0, 10)
}

type SecRow = {
  store_code: string; store_address: string; close_date: string;
  category_id: string | null; category_name: string; section: string;
  cash_collected: number; expected_deposit: number; total_deposited: number;
  variance: number; status: string; remaining_short: number; deposits: any[];
}

type Section = { key: string; label: string }

export default function BankDepositPage() {
  const [dateFrom, setDateFrom] = useState(monthAgo())
  const [dateTo, setDateTo] = useState(() => localToday())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [stores, setStores] = useState<any[]>([])
  const [fMarkets, setFMarkets] = useState<string[]>([])
  const [fStores, setFStores] = useState<string[]>([])

  useEffect(() => {
    apiCached('/api/v1/closing/stores', LOOKUP)
      .then((s: any) => setStores(Array.isArray(s) ? s : (s?.stores || []))).catch(() => {})
  }, [])

  const storesForCascade: StoreOpt[] = useMemo(
    () => stores.filter((s: any) => s.store_code)
      .map((s: any) => ({ id: s.store_code, label: s.store_address || s.store_code, market: s.market || null })),
    [stores])

  const load = useCallback(() => {
    setLoading(true); setErr('')
    const qs = new URLSearchParams({ date_from: dateFrom, date_to: dateTo })
    if (fStores.length) qs.set('stores', fStores.join(','))
    api(`/api/v1/closing/deposit-recon?${qs.toString()}`)
      .then(setData)
      .catch((e: any) => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [dateFrom, dateTo, fStores])
  useEffect(() => { load() }, [load])

  // The market filter is folded here rather than sent, because `/closing/deposit-recon` takes the
  // store list the cascade picker expands a market into (the designed mechanism — see
  // lib/market-store-cascade.ts) and the recon report has no market parameter of its own. Same
  // fold the reconciliation page beside this one already does, over the same rows.
  const marketByCode = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code && s.market) m[s.store_code] = s.market
    return m
  }, [stores])
  const fMarketsFold = useMemo(() => new Set(fMarkets.map(m => m.trim().toLowerCase())), [fMarkets])

  // The section vocabulary is the SERVER'S (deposit_recon.section_catalog) — this screen never
  // writes a section key or a processor name of its own.
  const sections: Section[] = data?.sections || []
  const cats: any[] = data?.categories || []

  const rowsBySection = useMemo(() => {
    const out: Record<string, SecRow[]> = {}
    for (const day of data?.days || []) {
      if (fMarketsFold.size && !fMarketsFold.has((marketByCode[day.store_code] || '').trim().toLowerCase())) continue
      const push = (r: SecRow) => { (out[r.section] ||= []).push(r) }
      for (const c of day.categories || []) {
        push({
          store_code: day.store_code, store_address: day.store_address, close_date: day.close_date,
          category_id: c.category_id, category_name: c.category_name, section: c.section,
          cash_collected: c.cash_collected, expected_deposit: c.expected_deposit,
          total_deposited: c.total_deposited, variance: c.variance, status: c.status,
          remaining_short: c.remaining_short, deposits: c.deposits || [],
        })
      }
      if (day.uncategorized) {
        const u = day.uncategorized
        push({
          store_code: day.store_code, store_address: day.store_address, close_date: day.close_date,
          category_id: null, category_name: u.category_name || 'Uncategorized', section: u.section,
          cash_collected: 0, expected_deposit: 0, total_deposited: u.total_deposited,
          variance: u.total_deposited, status: 'uncategorized', remaining_short: 0,
          deposits: u.deposits || [],
        })
      }
    }
    return out
  }, [data, fMarketsFold, marketByCode])

  // A section with no categories AND no rows is not shown — an empty "Other Deposits" heading on
  // every tenant that never added a manual bucket is noise, not information.
  const liveSections = useMemo(
    () => sections.filter(s => (rowsBySection[s.key] || []).length || cats.some((c: any) => c.section === s.key && c.is_active !== false)),
    [sections, rowsBySection, cats])

  const jumpBar = (where: 'top' | 'bottom') => (
    <nav aria-label={`Bank deposit sections (${where})`} className="card"
      style={{ padding: '9px 14px', display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap', marginBottom: where === 'top' ? 12 : 0, marginTop: where === 'bottom' ? 16 : 0 }}>
      <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '.08em', color: 'var(--text3)' }}>
        Sections
      </span>
      {liveSections.map(s => (
        <a key={s.key} href={`#section-${s.key}`} style={{ fontSize: 13, color: 'var(--accent)', textDecoration: 'none' }}>
          {s.label} ↓
        </a>
      ))}
      {where === 'bottom' && (
        <a href="#top" style={{ fontSize: 13, color: 'var(--text3)', textDecoration: 'none', marginLeft: 'auto' }}>↑ Back to top</a>
      )}
    </nav>
  )

  return (
    <div id="top">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 6 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏦 Bank Deposit</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 820 }}>
            Bank the cash the district managers collected, and record each deposit with its slip. Store
            cash and bill-payment cash are <b>recorded separately</b>, in their own sections below — the
            money comes from two different pickups and is banked separately, so it is tracked separately.
            Once banked, <Link href="/closing/deposit-recon" style={{ color: 'var(--accent)' }}>Cash Deposit
            Reconciliation</Link> checks it against what was collected.
          </p>
        </div>
        <Link href="/closing" className="btn btn-secondary" style={{ fontSize: 13 }}>← Dashboard</Link>
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
        <Link href="/closing/deposit-categories" style={{ fontSize: 12, color: 'var(--text3)', marginLeft: 'auto' }}>
          Manage deposit categories →
        </Link>
      </div>

      {jumpBar('top')}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {err}</div>
      ) : liveSections.length === 0 ? (
        <div className="card" style={{ padding: 16, fontSize: 13, color: 'var(--text2)' }}>
          No deposit categories are configured yet, so there is nothing to record a deposit against.{' '}
          <Link href="/closing/deposit-categories" style={{ color: 'var(--accent)' }}>Set them up here</Link>.
        </div>
      ) : (
        liveSections.map(sec => (
          <DepositSection key={sec.key} section={sec}
            categories={cats.filter((c: any) => c.section === sec.key && c.is_active !== false)}
            rows={rowsBySection[sec.key] || []}
            stores={stores} dateFrom={dateFrom} dateTo={dateTo} onSaved={load} />
        ))
      )}

      {jumpBar('bottom')}
      <WorkflowNext here="/closing/bank-deposit" />
    </div>
  )
}

// ── One section: its own recording form, its own tiles, its own table and its own export ────────
// Recorded separately means exactly that — the form here offers only this section's categories, so
// bill-payment cash cannot be banked into the store-cash bucket by picking the wrong line out of
// one long dropdown, which is how the two got mixed up on the old combined form.
function DepositSection({ section, categories, rows, stores, dateFrom, dateTo, onSaved }: {
  section: Section; categories: any[]; rows: SecRow[]; stores: any[];
  dateFrom: string; dateTo: string; onSaved: () => void
}) {
  const [dep, setDep] = useState(() => ({
    close_date: localToday(), store_code: '', category_id: '', amount: '', employee_name: '', note: '',
  }))
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  // Default to this section's only category when there is exactly one — the common case, and one
  // fewer thing to get wrong.
  useEffect(() => {
    if (!dep.category_id && categories.length === 1 && categories[0]?.id) {
      setDep(d => ({ ...d, category_id: categories[0].id }))
    }
  }, [categories, dep.category_id])

  const deposited = useMemo(() => rows.reduce((a, r) => a + (r.total_deposited || 0), 0), [rows])
  const expected = useMemo(() => rows.reduce((a, r) => a + (r.expected_deposit || 0), 0), [rows])
  const shortRows = useMemo(() => rows.filter(r => r.status === 'short').length, [rows])

  async function save() {
    if (!dep.store_code || !dep.category_id || !dep.amount) {
      setMsg('❌ Pick a store and a category, and enter an amount.'); return
    }
    setBusy(true); setMsg('')
    try {
      const r: any = await api('/api/v1/closing/bank-deposit', {
        method: 'POST', body: JSON.stringify({ ...dep, amount: Number(dep.amount) || 0 }),
      })
      // The short-deposit flow is the reconciliation's — this screen records the banking and says
      // plainly when it does not cover what was collected, rather than opening a second modal that
      // would be a copy of one that already exists.
      setMsg(r?.recon?.is_short
        ? `⚠ Recorded — still short ${fmt(r.recon.remaining_short)} against expected ${fmt(r.recon.expected_deposit)}.`
        : '✅ Deposit recorded.')
      setDep(d => ({ ...d, amount: '', employee_name: '' }))
      onSaved()
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    setBusy(false)
  }

  const columns: ExportColumn[] = useMemo(() => [
    { header: 'Date', field: 'close_date', type: 'date', role: 'date', get: (r: SecRow) => r.close_date },
    { header: 'Store', field: 'store_address', role: 'store', get: (r: SecRow) => r.store_address },
    { header: 'Category', field: 'category_name', get: (r: SecRow) => r.category_name },
    { header: 'Collected', field: 'cash_collected', money: true, get: (r: SecRow) => r.cash_collected },
    { header: 'Expected deposit', field: 'expected_deposit', money: true, get: (r: SecRow) => r.expected_deposit },
    { header: 'Deposited', field: 'total_deposited', money: true, get: (r: SecRow) => r.total_deposited },
    { header: 'Variance', field: 'variance', money: true, get: (r: SecRow) => r.variance },
    { header: 'Status', field: 'status', get: (r: SecRow) => (r.status || '').toUpperCase() },
  ], [])

  const inp = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
  const cell: React.CSSProperties = { padding: '7px 10px', borderTop: '1px solid var(--border)', fontSize: 12.5 }

  return (
    <section id={`section-${section.key}`} style={{ marginBottom: 22, scrollMarginTop: 16 }}>
      <h2 style={{ fontSize: 16, fontWeight: 700, margin: '0 0 8px' }}>{section.label}</h2>

      <div className="card" style={{ padding: 14, marginBottom: 10, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: 13 }}>Record a {section.label.toLowerCase()} deposit:</strong>
        <input type="date" style={inp} value={dep.close_date} onChange={e => setDep(d => ({ ...d, close_date: e.target.value }))} />
        <select style={inp} value={dep.store_code} onChange={e => setDep(d => ({ ...d, store_code: e.target.value }))}>
          <option value="">Store…</option>
          {stores.map((s: any, i: number) => <option key={i} value={s.store_code}>{s.store_address || s.store_code}</option>)}
        </select>
        <select style={inp} value={dep.category_id} onChange={e => setDep(d => ({ ...d, category_id: e.target.value }))}>
          <option value="">Category…</option>
          {categories.map((c: any) => <option key={c.id || c.name} value={c.id || ''}>{c.name}</option>)}
        </select>
        <input style={{ ...inp, width: 110 }} inputMode="decimal" placeholder="Amount $"
          value={dep.amount} onChange={e => setDep(d => ({ ...d, amount: e.target.value }))} />
        <input style={{ ...inp, width: 140 }} placeholder="Deposited by"
          value={dep.employee_name} onChange={e => setDep(d => ({ ...d, employee_name: e.target.value }))} />
        <button className="btn" disabled={busy} onClick={save} style={{ background: 'var(--accent)', color: '#fff' }}>
          {busy ? 'Saving…' : 'Save'}
        </button>
        {msg && <span style={{ fontSize: 12, color: msg.startsWith('❌') ? '#b91c1c' : msg.startsWith('⚠') ? '#b45309' : 'var(--text2)' }}>{msg}</span>}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14, marginBottom: 10 }}>
        <Stat label="Expected" value={fmt(expected)} />
        <Stat label="Deposited" value={fmt(deposited)} />
        <Stat label="Short store-days" value={String(shortRows)} color={shortRows ? '#dc2626' : '#059669'} />
      </div>

      <ReportShell title={`Bank Deposit — ${section.label}`} subtitle={`${dateFrom} → ${dateTo}`}
        filename={`bank-deposit-${section.key}_${dateFrom}_${dateTo}`} columns={columns} rows={rows} />

      {rows.length === 0 ? (
        <div className="card" style={{ padding: 14, marginTop: 10, fontSize: 12.5, color: 'var(--text3)' }}>
          No deposits recorded in this section for this range.
        </div>
      ) : (
        <div className="card" style={{ padding: 0, marginTop: 10, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              {['Date', 'Store', 'Category', 'Expected', 'Deposited', 'Variance', 'Status'].map((h, i) =>
                <th key={i} style={{ textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const k = `${r.close_date}|${r.store_code}|${r.category_id || 'u'}`
                return (
                  <tr key={k}>
                    <td style={{ ...cell, whiteSpace: 'nowrap', color: 'var(--text3)' }}>{r.close_date}</td>
                    <td style={cell}>{r.store_address || r.store_code}</td>
                    <td style={cell}>
                      <span style={{ cursor: r.deposits.length ? 'pointer' : undefined }}
                        onClick={() => r.deposits.length && setExpanded(e => ({ ...e, [k]: !e[k] }))}>
                        {r.deposits.length ? (expanded[k] ? '▾ ' : '▸ ') : ''}{r.category_name}
                        {r.deposits.length ? <span style={{ color: 'var(--text3)' }}> ({r.deposits.length})</span> : null}
                      </span>
                      {expanded[k] && r.deposits.map((d: any, j: number) => (
                        <div key={j} style={{ fontSize: 11.5, padding: '3px 0 0 16px', color: 'var(--text2)' }}>
                          {fmt(d.amount)}{d.is_supplemental ? ' (supplemental)' : ''} by {d.employee_name || d.recorded_by || '—'}
                          {d.created_at ? ` at ${d.created_at}` : ''}
                          {d.short_reason ? ` — reason: ${d.short_reason}` : ''}
                        </div>
                      ))}
                    </td>
                    <td style={cell}>{fmt(r.expected_deposit)}</td>
                    <td style={{ ...cell, fontWeight: 600 }}>{fmt(r.total_deposited)}</td>
                    <td style={{ ...cell, color: r.variance < 0 ? '#dc2626' : r.variance > 0 ? '#b45309' : 'var(--text2)' }}>{fmt(r.variance)}</td>
                    <td style={cell}>{(r.status || '').toUpperCase()}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return <div className="card" style={{ padding: '12px 14px' }}>
    <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '.05em' }}>{label}</div>
    <div style={{ fontSize: 19, fontWeight: 700, marginTop: 4, color: color || 'var(--text1)' }}>{value}</div>
  </div>
}
