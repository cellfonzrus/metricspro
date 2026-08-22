'use client'
import { useState, useEffect, useCallback, useMemo, Fragment } from 'react'
import { api, fmt, localToday } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'

type Payment = { id: string; amount: number; paid_date: string; note: string | null }
type Loan = {
  id: string; borrower_store: string; lender_store: string; market: string | null
  amount: number; borrowed_date: string; note: string | null
  repaid: number; outstanding: number; settled: boolean; payments: Payment[]
}
type Pair = { borrower_store: string; lender_store: string; market: string | null; borrowed: number; repaid: number; outstanding: number; loans: number }
type NetRow = { store: string; owes: number; owed: number; net: number }

const sel: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '7px 10px', borderBottom: '1px solid var(--border)', fontSize: 13, whiteSpace: 'nowrap' }

export default function BorrowedMoneyPage() {
  const [stores, setStores] = useState<{ store: string; market: string | null }[]>([])
  // 2026-08-04 owner report ("added Cellular Services as a store but it does not appear in the
  // borrowed lending store list"): `stores` above is ledger-derived (asset_ledger has no rows for a
  // brand-new store — exactly the case for a store BORROWING money to buy its first inventory), so
  // it can never contain a just-created store. `registryStores` is the tenant's full store roster
  // (commcalc.store_mapping, via GET /filter-options' additive `registry_stores`) and is unioned
  // into the borrower/lender pickers ONLY (see `pickerStores` below) — the report filter dropdown
  // (`fStore`) is left ledger-derived on purpose, same as every other asset report's store filter.
  const [registryStores, setRegistryStores] = useState<{ store: string; market: string | null }[]>([])
  const [markets, setMarkets] = useState<string[]>([])
  const [loans, setLoans] = useState<Loan[]>([])
  const [totals, setTotals] = useState({ total_borrowed: 0, total_repaid: 0, total_outstanding: 0, count: 0 })
  const [pairs, setPairs] = useState<Pair[]>([])
  const [byStore, setByStore] = useState<NetRow[]>([])
  const [loading, setLoading] = useState(true)
  const [msg, setMsg] = useState('')

  // filters
  const [fStore, setFStore] = useState('')
  const [fMarket, setFMarket] = useState('')
  const [fStatus, setFStatus] = useState('')

  // new borrowing form
  const [borrowed, setBorrowed] = useState(true) // false = same-company (no debt)
  const [nb, setNb] = useState({ borrower_store: '', lender_store: '', amount: '', borrowed_date: localToday(), note: '' })
  const [expanded, setExpanded] = useState<string | null>(null)
  const [pay, setPay] = useState<{ amount: string; paid_date: string; note: string }>({ amount: '', paid_date: localToday(), note: '' })

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = `store=${encodeURIComponent(fStore)}&market=${encodeURIComponent(fMarket)}`
      const [opt, list, sum] = await Promise.all([
        apiCached('/api/v1/asset/filter-options', LOOKUP).catch(() => ({ stores: [], markets: [] })),
        api(`/api/v1/asset/borrowings?${qs}&status=${fStatus}`),
        api(`/api/v1/asset/borrowings/summary?${qs}`),
      ])
      setStores(opt.stores || []); setMarkets(opt.markets || []); setRegistryStores(opt.registry_stores || [])
      setLoans(list.borrowings || [])
      setTotals({ total_borrowed: list.total_borrowed || 0, total_repaid: list.total_repaid || 0, total_outstanding: list.total_outstanding || 0, count: list.count || 0 })
      setPairs(sum.pairs || []); setByStore(sum.by_store || [])
    } catch (e: any) { setMsg('Load failed: ' + (e?.message || e)) }
    setLoading(false)
  }, [fStore, fMarket, fStatus])
  useEffect(() => { load() }, [load])

  // Borrower/lender pickers: union `stores` (has asset rows) with `registryStores` (the FULL store
  // registry) so a brand-new store with no financing history yet still appears (RULE THREE: pick,
  // don't type — a new store must be reachable without free-typing its name). Dedup case-
  // insensitively; a store present in both keeps the ledger-derived market (real data wins over the
  // registry default). `registryOnly` powers a separate optgroup so it's clear which stores have no
  // asset history yet.
  const pickerGroups = useMemo(() => {
    const seen = new Set(stores.map(s => s.store.trim().toLowerCase()))
    const registryOnly = registryStores.filter(s => s.store && !seen.has(s.store.trim().toLowerCase()))
    const all = [...stores, ...registryOnly].sort((a, b) => a.store.localeCompare(b.store))
    return { withAssets: stores, registryOnly, byName: new Map(all.map(s => [s.store, s])) }
  }, [stores, registryStores])

  async function addBorrowing() {
    if (!borrowed) { setMsg('Same-company funds create no debt — nothing to record.'); return }
    if (!nb.borrower_store || !nb.lender_store) { setMsg('Pick both the borrower and the lender store.'); return }
    if (nb.borrower_store === nb.lender_store) { setMsg('Borrower and lender must be different stores.'); return }
    if (!(Number(nb.amount) > 0)) { setMsg('Enter an amount greater than 0.'); return }
    setMsg('')
    try {
      // A registry-only store (no asset_ledger rows) has no ledger market — resolves from the
      // registry when store_mapping has one, else null (the create endpoint already tolerates a
      // null market).
      const market = pickerGroups.byName.get(nb.borrower_store)?.market || null
      await api('/api/v1/asset/borrowings', { method: 'POST', body: JSON.stringify({ ...nb, amount: Number(nb.amount), market }) })
      setMsg(`Logged: ${nb.borrower_store} borrowed ${fmt(Number(nb.amount))} from ${nb.lender_store}`)
      setNb({ borrower_store: '', lender_store: '', amount: '', borrowed_date: localToday(), note: '' })
      await load()
    } catch (e: any) { setMsg('Save failed: ' + (e?.message || e)) }
  }

  async function recordPayment(loan: Loan) {
    if (!(Number(pay.amount) > 0)) { setMsg('Enter a payment amount greater than 0.'); return }
    setMsg('')
    try {
      await api(`/api/v1/asset/borrowings/${loan.id}/payment`, { method: 'POST', body: JSON.stringify({ amount: Number(pay.amount), paid_date: pay.paid_date, note: pay.note }) })
      setPay({ amount: '', paid_date: localToday(), note: '' })
      await load()
    } catch (e: any) { setMsg('Payment failed: ' + (e?.message || e)) }
  }
  async function delLoan(loan: Loan) {
    if (!confirm(`Delete this borrowing (${loan.borrower_store} ← ${loan.lender_store}, ${fmt(loan.amount)})? Its payments are removed too.`)) return
    try { await api(`/api/v1/asset/borrowings/${loan.id}`, { method: 'DELETE' }); await load() }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }
  async function delPayment(p: Payment) {
    if (!confirm(`Remove payment ${fmt(p.amount)} on ${p.paid_date}?`)) return
    try { await api(`/api/v1/asset/borrowing-payment/${p.id}`, { method: 'DELETE' }); await load() }
    catch (e: any) { setMsg('Delete failed: ' + (e?.message || e)) }
  }

  const buildPayload = (): ExportPayload => ({
    title: 'Inter-store Borrowed Money',
    subtitle: [fMarket && `Market: ${fMarket}`, fStore && `Store: ${fStore}`, fStatus && `Status: ${fStatus}`].filter(Boolean).join(' · ') || 'All stores',
    filename: 'borrowed-money',
    sheets: [
      { name: 'Who owes whom', columns: [
        { header: 'Borrower (owes)', get: r => r.borrower_store },
        { header: 'Lender (owed)', get: r => r.lender_store },
        { header: 'Market', get: r => r.market || '' },
        { header: 'Borrowed', get: r => r.borrowed, money: true, align: 'right' },
        { header: 'Repaid', get: r => r.repaid, money: true, align: 'right' },
        { header: 'Outstanding', get: r => r.outstanding, money: true, align: 'right' },
        { header: 'Loans', get: r => r.loans, align: 'right' },
      ], rows: pairs },
      { name: 'Net by store', columns: [
        { header: 'Store', get: r => r.store },
        { header: 'Owes others', get: r => r.owes, money: true, align: 'right' },
        { header: 'Owed by others', get: r => r.owed, money: true, align: 'right' },
        { header: 'Net (owed − owes)', get: r => r.net, money: true, align: 'right' },
      ], rows: byStore },
      { name: 'All borrowings', columns: [
        { header: 'Date', get: r => r.borrowed_date },
        { header: 'Borrower', get: r => r.borrower_store },
        { header: 'Lender', get: r => r.lender_store },
        { header: 'Market', get: r => r.market || '' },
        { header: 'Amount', get: r => r.amount, money: true, align: 'right' },
        { header: 'Repaid', get: r => r.repaid, money: true, align: 'right' },
        { header: 'Outstanding', get: r => r.outstanding, money: true, align: 'right' },
        { header: 'Status', get: r => r.settled ? 'Settled' : 'Open' },
        { header: 'Note', get: r => r.note || '' },
      ], rows: loans },
    ],
  })

  const card = (label: string, val: string, color?: string) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 150 }}>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{val}</div>
    </div>
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💸 Inter-store Borrowed Money</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Track money one store borrowed from another to fund asset purchases — who owes how much to which store.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <a className="btn" href="/commcalc/asset" style={{ textDecoration: 'none' }}>← Asset</a>
          <><ExportButtons payload={buildPayload} /><SendReportButton exportPayload={buildPayload} compact /></>
        </div>
      </div>

      {/* Record a borrowing */}
      <div className="card" style={{ padding: 14, marginBottom: 14 }}>
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>➕ Record a borrowing</div>
        <div style={{ display: 'flex', gap: 14, marginBottom: 10, flexWrap: 'wrap' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
            <input type="radio" checked={borrowed} onChange={() => setBorrowed(true)} /> Borrowed from another store
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: 'var(--text2)' }}>
            <input type="radio" checked={!borrowed} onChange={() => setBorrowed(false)} /> Same company (no debt)
          </label>
        </div>
        {borrowed ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <select style={{ ...sel, width: 200 }} value={nb.borrower_store} onChange={e => setNb({ ...nb, borrower_store: e.target.value })}>
              <option value="">Borrower store (owes) *</option>
              <optgroup label="Stores with assets">
                {pickerGroups.withAssets.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
              </optgroup>
              {pickerGroups.registryOnly.length > 0 && (
                <optgroup label="Other registered stores (no assets yet)">
                  {pickerGroups.registryOnly.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
                </optgroup>
              )}
            </select>
            <span style={{ fontSize: 13, color: 'var(--text3)' }}>borrowed from</span>
            <select style={{ ...sel, width: 200 }} value={nb.lender_store} onChange={e => setNb({ ...nb, lender_store: e.target.value })}>
              <option value="">Lender store (owed) *</option>
              <optgroup label="Stores with assets">
                {pickerGroups.withAssets.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
              </optgroup>
              {pickerGroups.registryOnly.length > 0 && (
                <optgroup label="Other registered stores (no assets yet)">
                  {pickerGroups.registryOnly.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
                </optgroup>
              )}
            </select>
            <input style={{ ...sel, width: 120 }} type="number" placeholder="Amount *" value={nb.amount} onChange={e => setNb({ ...nb, amount: e.target.value })} />
            <input style={{ ...sel, width: 150 }} type="date" value={nb.borrowed_date} onChange={e => setNb({ ...nb, borrowed_date: e.target.value })} />
            <input style={{ ...sel, width: 200 }} placeholder="Note (e.g. asset batch)" value={nb.note} onChange={e => setNb({ ...nb, note: e.target.value })} />
            <button className="btn btn-primary" onClick={addBorrowing}>➕ Add</button>
          </div>
        ) : (
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>Same-company funds create no inter-store debt, so there's nothing to record here.</div>
        )}
        {msg && <div style={{ fontSize: 13, marginTop: 8 }}>{msg}</div>}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Filter:</span>
        <select style={sel} value={fMarket} onChange={e => setFMarket(e.target.value)}>
          <option value="">All markets</option>
          {markets.map(m => <option key={m} value={m}>{m}</option>)}
        </select>
        <select style={sel} value={fStore} onChange={e => setFStore(e.target.value)}>
          <option value="">All stores</option>
          {stores.map(s => <option key={s.store} value={s.store}>{s.store}</option>)}
        </select>
        <select style={sel} value={fStatus} onChange={e => setFStatus(e.target.value)}>
          <option value="">Open + settled</option>
          <option value="open">Open only</option>
          <option value="settled">Settled only</option>
        </select>
        {(fStore || fMarket || fStatus) && <button className="btn" onClick={() => { setFStore(''); setFMarket(''); setFStatus('') }}>Clear</button>}
      </div>

      {/* Summary cards */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        {card('Outstanding', fmt(totals.total_outstanding), totals.total_outstanding > 0 ? '#dc2626' : '#059669')}
        {card('Total borrowed', fmt(totals.total_borrowed))}
        {card('Total repaid', fmt(totals.total_repaid), '#059669')}
        {card('Borrowings', String(totals.count))}
      </div>

      {loading ? <div style={{ padding: 40, color: 'var(--text3)' }}>Loading…</div> : (
        <>
          {/* Who owes whom */}
          <div className="card" style={{ padding: 0, marginBottom: 16 }}>
            <div style={{ padding: '10px 14px', fontSize: 13, fontWeight: 700, borderBottom: '1px solid var(--border)' }}>🔁 Who owes whom (outstanding)</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 700 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Borrower (owes)', 'Lender (owed)', 'Market', 'Borrowed', 'Repaid', 'Outstanding', 'Loans'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {pairs.filter(p => p.outstanding > 0.005).map((p, i) => (
                    <tr key={i}>
                      <td style={{ ...td, fontWeight: 600 }}>{p.borrower_store}</td>
                      <td style={td}>{p.lender_store}</td>
                      <td style={td}>{p.market || '—'}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmt(p.borrowed)}</td>
                      <td style={{ ...td, textAlign: 'right', color: '#059669' }}>{fmt(p.repaid)}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 700, color: '#dc2626' }}>{fmt(p.outstanding)}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{p.loans}</td>
                    </tr>
                  ))}
                  {pairs.filter(p => p.outstanding > 0.005).length === 0 && <tr><td colSpan={7} style={{ ...td, textAlign: 'center', color: 'var(--text3)' }}>Nothing outstanding 🎉</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          {/* Net by store */}
          <div className="card" style={{ padding: 0, marginBottom: 16 }}>
            <div style={{ padding: '10px 14px', fontSize: 13, fontWeight: 700, borderBottom: '1px solid var(--border)' }}>⚖️ Net position by store</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Store', 'Owes others', 'Owed by others', 'Net'].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {byStore.map((r, i) => (
                    <tr key={i}>
                      <td style={{ ...td, fontWeight: 600 }}>{r.store}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmt(r.owes)}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{fmt(r.owed)}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 700, color: r.net >= 0 ? '#059669' : '#dc2626' }}>{r.net >= 0 ? '+' : ''}{fmt(r.net)}</td>
                    </tr>
                  ))}
                  {byStore.length === 0 && <tr><td colSpan={4} style={{ ...td, textAlign: 'center', color: 'var(--text3)' }}>No borrowings yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>

          {/* All borrowings (with payments) */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '10px 14px', fontSize: 13, fontWeight: 700, borderBottom: '1px solid var(--border)' }}>📒 Borrowings ({loans.length})</div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  {['Date', 'Borrower', 'Lender', 'Amount', 'Repaid', 'Outstanding', 'Status', ''].map(h => <th key={h} style={th}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {loans.map(l => (
                    <Fragment key={l.id}>
                      <tr>
                        <td style={td}>{l.borrowed_date}</td>
                        <td style={{ ...td, fontWeight: 600 }}>{l.borrower_store}</td>
                        <td style={td}>{l.lender_store}</td>
                        <td style={{ ...td, textAlign: 'right' }}>{fmt(l.amount)}</td>
                        <td style={{ ...td, textAlign: 'right', color: '#059669' }}>{fmt(l.repaid)}</td>
                        <td style={{ ...td, textAlign: 'right', fontWeight: 700, color: l.outstanding > 0.005 ? '#dc2626' : '#059669' }}>{fmt(l.outstanding)}</td>
                        <td style={td}>{l.settled ? <span className="badge badge-green">Settled</span> : <span className="badge badge-blue">Open</span>}</td>
                        <td style={{ ...td, whiteSpace: 'nowrap' }}>
                          <button className="btn" style={{ fontSize: 12, padding: '3px 8px' }} onClick={() => setExpanded(expanded === l.id ? null : l.id)}>{expanded === l.id ? '▾' : '▸'} Payments</button>{' '}
                          <button className="btn" style={{ fontSize: 12, padding: '3px 8px', color: '#dc2626' }} onClick={() => delLoan(l)}>🗑</button>
                        </td>
                      </tr>
                      {expanded === l.id && (
                        <tr>
                          <td colSpan={8} style={{ padding: '10px 14px', background: '#f8fafc', borderBottom: '1px solid var(--border)' }}>
                            {l.note && <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>📝 {l.note}</div>}
                            {l.payments.length > 0 ? (
                              <table style={{ borderCollapse: 'collapse', marginBottom: 8 }}>
                                <tbody>
                                  {l.payments.map(p => (
                                    <tr key={p.id}>
                                      <td style={{ padding: '3px 12px 3px 0', fontSize: 13 }}>{p.paid_date}</td>
                                      <td style={{ padding: '3px 12px', fontSize: 13, fontWeight: 600, color: '#059669' }}>{fmt(p.amount)}</td>
                                      <td style={{ padding: '3px 12px', fontSize: 12, color: 'var(--text2)' }}>{p.note || ''}</td>
                                      <td style={{ padding: '3px 0' }}><button className="btn" style={{ fontSize: 11, padding: '2px 6px', color: '#dc2626' }} onClick={() => delPayment(p)}>remove</button></td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            ) : <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>No payments yet.</div>}
                            {!l.settled && (
                              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                                <span style={{ fontSize: 12, fontWeight: 600 }}>Record payback:</span>
                                <input style={{ ...sel, width: 110 }} type="number" placeholder="Amount" value={pay.amount} onChange={e => setPay({ ...pay, amount: e.target.value })} />
                                <input style={{ ...sel, width: 150 }} type="date" value={pay.paid_date} onChange={e => setPay({ ...pay, paid_date: e.target.value })} />
                                <input style={{ ...sel, width: 160 }} placeholder="Note" value={pay.note} onChange={e => setPay({ ...pay, note: e.target.value })} />
                                <button className="btn btn-primary" style={{ fontSize: 12, padding: '4px 12px' }} onClick={() => recordPayment(l)}>💰 Pay</button>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                  {loans.length === 0 && <tr><td colSpan={8} style={{ ...td, textAlign: 'center', color: 'var(--text3)' }}>No borrowings recorded yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
