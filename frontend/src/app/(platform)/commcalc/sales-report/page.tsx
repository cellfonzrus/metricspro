'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import { MultiSelect } from '@/lib/multiselect'

// The sales actually done across all stores, from the imported Sales Transaction Details
// (raw_sales, falling back to the daily email feed). One row per store + rep + day; ReportShell
// adds the rep/store/date/month filters, add-your-own filter, group-by, export and send.
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function SalesReportPage() {
  const [period, setPeriod] = useState(thisMonth())
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [drill, setDrill] = useState<any>(null)          // the clicked (store, rep, day) cell
  const [detail, setDetail] = useState<any>(null)        // its transactions
  const [drillBusy, setDrillBusy] = useState(false)
  const [openTxn, setOpenTxn] = useState<Record<string, boolean>>({})
  const [diag, setDiag] = useState<any>(null)            // data diagnostics for this period
  const [diagBusy, setDiagBusy] = useState(false)
  const [accOpen, setAccOpen] = useState(false)          // accessory-settings modal
  const [accFields, setAccFields] = useState<any>(null)  // distinct departments/categories + current config
  const [accSel, setAccSel] = useState<{ d: string[]; c: string[]; p: string[]; a: string[] }>({ d: [], c: [], p: [], a: [] })
  const [accMsg, setAccMsg] = useState('')
  const [kwInput, setKwInput] = useState('')
  const [selMarkets, setSelMarkets] = useState<string[]>([])   // multi-select market filter
  const [selStores, setSelStores] = useState<string[]>([])     // multi-select store filter

  function openDiag() {
    setDiag({}); setDiagBusy(true)
    api(`/api/v1/commcalc/sales-diagnostics?period=${encodeURIComponent(period)}`)
      .then(setDiag).catch(e => setDiag({ error: String(e?.message || e) }))
      .finally(() => setDiagBusy(false))
  }
  function openAccCfg() {
    setAccOpen(true); setAccFields(null); setAccMsg(''); setKwInput('')
    api(`/api/v1/commcalc/sales-fields?period=${encodeURIComponent(period)}`).then((f: any) => {
      setAccFields(f)
      setAccSel({ d: f.accessory_departments || [], c: f.accessory_categories || [], p: f.accessory_product_keywords || [], a: f.acima_tenders || [] })
    }).catch(e => setAccMsg('❌ ' + (e?.message || e)))
  }
  async function saveAccCfg() {
    setAccMsg('Saving…')
    // fold any half-typed keyword in the box into the list before saving
    const extra = kwInput.split(',').map(s => s.trim()).filter(Boolean)
    const kws = Array.from(new Set([...accSel.p, ...extra]))
    try {
      await api('/api/v1/commcalc/accessory-config', { method: 'PUT', body: JSON.stringify({ departments: accSel.d, categories: accSel.c, product_keywords: kws, acima_tenders: accSel.a }) })
      setAccMsg('✅ Saved.'); setAccOpen(false); load()
    } catch (e: any) { setAccMsg('❌ ' + (e?.message || e)) }
  }
  const toggle = (arr: string[], v: string) => arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]

  function openDrill(r: any) {
    setDrill(r); setDetail(null); setOpenTxn({}); setDrillBusy(true)
    const qs = new URLSearchParams({ period, store: r.store || '', salesperson: r.salesperson || '', date: r.trans_date || '' })
    api(`/api/v1/commcalc/sales-report/detail?${qs.toString()}`)
      .then(setDetail).catch(e => setDetail({ transactions: [], error: String(e?.message || e) }))
      .finally(() => setDrillBusy(false))
  }

  const load = useCallback(() => {
    setLoading(true)
    api(`/api/v1/commcalc/sales-report?period=${encodeURIComponent(period)}`)
      .then(setData).catch(e => setData({ rows: [], totals: {}, error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const marketOpts: string[] = data?.markets || []
  const storeOpts: string[] = data?.stores || []
  // Apply the multi-select market/store filters in-memory; ReportShell handles the rest.
  const fRows = rows.filter(r =>
    (selMarkets.length === 0 || selMarkets.includes(r.market)) &&
    (selStores.length === 0 || selStores.includes(r.store)))
  const filtered = selMarkets.length > 0 || selStores.length > 0
  // Tiles reflect the current filter (fall back to the backend period totals when nothing is filtered).
  const sum = (k: string) => fRows.reduce((s, r) => s + (Number(r[k]) || 0), 0)
  const t = filtered
    ? { revenue: sum('revenue'), gp: sum('gp'), accessory_rev: sum('accessory_rev'),
        txns: sum('txns'), activations: sum('activations'), byod: sum('byod'), upgrades: sum('upgrades') }
    : (data?.totals || {})
  // Distinct months available across both sales tables (for the picker).
  const months = Array.from(new Set((data?.periods || []).map((p: string) => {
    const s = String(p)
    if (/^\d{4}-\d{2}/.test(s)) return s.slice(0, 7)
    const d = new Date(s + ' 1'); return isNaN(d.getTime()) ? null : `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
  }).filter(Boolean))).sort().reverse() as string[]

  const cols: ExportColumn[] = [
    { header: 'Store', get: r => r.store, role: 'store' },
    { header: 'Market', get: r => r.market || '—' },
    { header: 'Rep', get: r => r.salesperson, role: 'rep' },
    { header: 'Date', get: r => r.trans_date, type: 'date' },
    { header: 'Txns', get: r => r.txns, align: 'right' },
    { header: 'Activations', get: r => r.activations, align: 'right' },
    { header: 'BYOD', get: r => r.byod, align: 'right' },
    { header: 'Upgrades', get: r => r.upgrades, align: 'right' },
    { header: 'Accessory $', get: r => r.accessory_rev, money: true },
    { header: 'Revenue $', get: r => r.revenue, money: true },
    { header: 'GP $', get: r => r.gp, money: true },
  ]

  const Tile = ({ label, value }: { label: string; value: string }) => (
    <div className="card" style={{ padding: '12px 16px', minWidth: 120 }}>
      <div style={{ fontSize: 11, color: 'var(--text3)', textTransform: 'uppercase', fontWeight: 600 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 700, marginTop: 2 }}>{value}</div>
    </div>
  )

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Sales Report</h1>
        <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
          Sales done across all stores, from the imported Sales Transaction Details. Filter by rep, store, date or
          month, add your own filter, group by any column, then export or send to a rep.
        </p>
      </div>

      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 14, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 12, color: 'var(--text2)' }}>Month{' '}
          {months.length > 0
            ? <select style={sel} value={period.length === 7 ? period : ''} onChange={e => setPeriod(e.target.value)}>
                {months.map(m => <option key={m} value={m}>{m}</option>)}
                {!months.includes(period) && <option value={period}>{period}</option>}
              </select>
            : <input type="month" style={sel} value={period.length === 7 ? period : thisMonth()} onChange={e => setPeriod(e.target.value)} />}
        </label>
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={150} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {storeOpts.length > 0 && <MultiSelect allLabel="All stores" width={150} value={selStores} options={storeOpts} onChange={setSelStores} searchable />}
        {filtered && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelMarkets([]); setSelStores([]) }}>Clear filters</button>}
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openDiag}>🔍 Data diagnostics</button>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openAccCfg}>⚙️ Classification settings</button>
        {data?.source === 'daily_sales_feed' && <span style={{ fontSize: 11, color: '#b45309' }}>source: daily email feed (raw_sales not promoted yet — enable ‘auto’ on Connectors)</span>}
        {data?.error && <span style={{ fontSize: 12, color: '#dc2626' }}>❌ {data.error}</span>}
      </div>

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Revenue" value={fmt(t.revenue || 0)} />
        <Tile label="Gross Profit" value={fmt(t.gp || 0)} />
        <Tile label="Accessory $" value={fmt(t.accessory_rev || 0)} />
        <Tile label="Transactions" value={String(t.txns || 0)} />
        <Tile label="Activations" value={String(t.activations || 0)} />
        <Tile label="BYOD" value={String(t.byod || 0)} />
        <Tile label="Upgrades" value={String(t.upgrades || 0)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : fRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {filtered
            ? <>No sales match the selected market/store filter for {period}. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={() => { setSelMarkets([]); setSelStores([]) }}>Clear filters</span>.</>
            : <>No sales for {period}. Sales come from the imported Sales Transaction Details — check the month, or that the daily feed / monthly upload has loaded on the Imports pages.</>}
        </div>
      ) : (
        <ReportShell
          title={`Sales Report — ${period}`}
          subtitle={`${filtered ? `${fRows.length} filtered rows` : 'All stores'} · from Sales Transaction Details`}
          filename={`sales-report-${period.replace(/\s+/g, '-')}`}
          columns={cols}
          rows={fRows}
          onRowClick={openDrill}
        />
      )}

      {!loading && fRows.length > 0 && <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 6 }}>💡 Click any row to see the individual transactions behind it.</div>}

      {/* Transaction drill-down */}
      {drill && (
        <div onClick={() => setDrill(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(820px,97vw)', maxHeight: '88vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 8 }}>
              <div>
                <div style={{ fontSize: 16, fontWeight: 700 }}>{drill.store} · {drill.salesperson}</div>
                <div style={{ fontSize: 12, color: 'var(--text3)' }}>{drill.trans_date} · {detail?.txn_count ?? drill.txns} transaction{(detail?.txn_count ?? drill.txns) === 1 ? '' : 's'}</div>
              </div>
              <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDrill(null)}>✕</button>
            </div>
            {drillBusy ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading transactions…</div>
            ) : detail?.error ? (
              <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>❌ {detail.error}</div>
            ) : (detail?.transactions || []).length === 0 ? (
              <div style={{ padding: 20, color: 'var(--text3)', fontSize: 13 }}>No transaction detail found for this cell.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {detail.transactions.map((t: any) => {
                  const open = !!openTxn[t.trans_id]
                  return (
                    <div key={t.trans_id} className="card" style={{ padding: 0, border: '1px solid var(--border)', borderRadius: 8 }}>
                      <div onClick={() => setOpenTxn(o => ({ ...o, [t.trans_id]: !o[t.trans_id] }))}
                        style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '9px 12px', cursor: 'pointer', flexWrap: 'wrap' }}>
                        <span style={{ color: 'var(--text3)', width: 12 }}>{open ? '▾' : '▸'}</span>
                        <span style={{ fontFamily: 'monospace', fontSize: 12 }}>#{t.trans_id}</span>
                        {t.customer && <span style={{ fontSize: 12, color: 'var(--text2)' }}>{t.customer}</span>}
                        <span style={{ fontSize: 11, color: 'var(--text3)' }}>{t.line_count} line{t.line_count === 1 ? '' : 's'}</span>
                        <div style={{ flex: 1 }} />
                        <span style={{ fontSize: 12, color: 'var(--text3)' }}>GP {fmt(t.gp)}</span>
                        <span style={{ fontSize: 14, fontWeight: 700 }}>{fmt(t.total)}</span>
                      </div>
                      {open && (
                        <div style={{ borderTop: '1px solid var(--border)', overflowX: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead><tr style={{ background: 'var(--surface2)' }}>
                              {['Department', 'Category', 'Contract', 'Product', 'MDN', 'Serial', 'Price', 'GP'].map(h =>
                                <th key={h} style={{ textAlign: h === 'Price' || h === 'GP' ? 'right' : 'left', padding: '5px 8px', fontSize: 10, fontWeight: 600, color: 'var(--text2)' }}>{h}</th>)}
                            </tr></thead>
                            <tbody>
                              {t.lines.map((l: any, i: number) => (
                                <tr key={i} style={{ borderTop: '1px solid var(--border)' }}>
                                  <td style={{ padding: '5px 8px' }}>{l.department || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.category || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.contract_type || '—'}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.product || '—'}{l.sku ? <span style={{ color: 'var(--text3)' }}> · {l.sku}</span> : ''}</td>
                                  <td style={{ padding: '5px 8px' }}>{l.mdn || '—'}</td>
                                  <td style={{ padding: '5px 8px', fontFamily: 'monospace', fontSize: 11 }}>{l.serial || '—'}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(l.ext_price)}</td>
                                  <td style={{ padding: '5px 8px', textAlign: 'right' }}>{fmt(l.gp)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Data diagnostics — what the sales tables actually hold for this month */}
      {diag && (
        <div onClick={() => setDiag(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(880px,97vw)', maxHeight: '88vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <div style={{ fontSize: 16, fontWeight: 700 }}>🔍 Data diagnostics · {period}</div>
              <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDiag(null)}>✕</button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 12px' }}>
              What the sales tables actually hold for this month — so a wrong tile can be traced to an unrecognized Contract Type or a missing month. Screenshot this to me if numbers still look off.
            </p>
            {diagBusy ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--text3)' }}>Loading…</div>
            ) : diag.error ? (
              <div style={{ padding: 20, color: '#dc2626', fontSize: 13 }}>❌ {diag.error}</div>
            ) : (
              <>
                <div style={{ fontSize: 13, marginBottom: 12, display: 'flex', gap: 16, flexWrap: 'wrap' }}>
                  <span>Computed totals: <b>{diag.computed_actuals_totals?.activations ?? 0}</b> act · <b>{diag.computed_actuals_totals?.byod ?? 0}</b> byod · <b>{diag.computed_actuals_totals?.upgrades ?? 0}</b> upg · <b>{fmt(diag.computed_actuals_totals?.accessory_gp || 0)}</b> acc GP</span>
                  <span style={{ color: 'var(--text3)' }}>open month: {String(diag.open_month)}</span>
                </div>
                {['daily_sales_feed', 'raw_sales'].map(tbl => {
                  const d = diag[tbl] || {}
                  const dist = (obj: any) => Object.entries(obj || {}).sort((a: any, b: any) => b[1] - a[1])
                  return (
                    <div key={tbl} style={{ marginBottom: 16 }}>
                      <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 4 }}>{tbl} <span style={{ fontWeight: 400, color: 'var(--text3)' }}>· {d.rows ?? 0} rows{d.periods ? ` · periods: ${Object.keys(d.periods).join(', ') || '—'}` : ''}</span></div>
                      {d.error ? <div style={{ fontSize: 12, color: '#dc2626' }}>{d.error}</div> : (
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                          {[['Contract Types', d.contract_types], ['Departments', d.departments], ['Categories', d.categories], ['Products (non-phone lines = accessories live here)', d.products_on_nonphone_lines]].map(([lbl, obj]: any) => (
                            <div key={lbl}>
                              <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text2)', marginBottom: 2 }}>{lbl}</div>
                              <div style={{ maxHeight: 200, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 6, fontSize: 12 }}>
                                {dist(obj).length === 0 ? <div style={{ padding: 8, color: 'var(--text3)' }}>—</div> : dist(obj).map(([k, v]: any) => (
                                  <div key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 8px', borderTop: '1px solid var(--border)' }}>
                                    <span>{k}</span><span style={{ color: 'var(--text3)' }}>{v}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </>
            )}
          </div>
        </div>
      )}

      {/* Accessory settings — configure which departments/categories count as accessory sales */}
      {accOpen && (
        <div onClick={() => setAccOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(720px,97vw)', maxHeight: '88vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <div style={{ fontSize: 16, fontWeight: 700 }}>⚙️ Classification settings</div>
              <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setAccOpen(false)}>✕</button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 12px' }}>
              Works with <b>any POS</b> — these lists are the actual <b>Department / product-type</b> and <b>Category</b> values found in your uploaded sales data. Tick which ones are accessory sales (a line counts if its department OR category is ticked). This drives the Accessory$ here, the Action-Plan accessory target, and — after a recalc — commission accessory pay. If the values below look wrong/empty because your POS uses different column names, map your file&apos;s columns to ours first in <a href="/commcalc/column-mapping" style={{ color: 'var(--accent)' }}>Column Mapping</a>. Leave everything unticked to fall back to the default department <code>Ondigo</code>.
            </p>
            {!accFields ? (
              <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>{accMsg || 'Loading…'}</div>
            ) : (
              <>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
                  {([['Department / product-type', 'd', accFields.departments], ['Category', 'c', accFields.categories]] as const).map(([lbl, keyName, list]: any) => (
                    <div key={lbl}>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{lbl} <span style={{ fontWeight: 400, color: 'var(--text3)' }}>({(list || []).length})</span></div>
                      <div style={{ maxHeight: 320, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                        {(list || []).length === 0 ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>none in this period</div> : (list || []).map((v: string) => (
                          <label key={v} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, padding: '2px 0' }}>
                            <input type="checkbox" checked={(accSel as any)[keyName].includes(v)}
                              onChange={() => setAccSel(s => ({ ...s, [keyName]: toggle((s as any)[keyName], v) }))} />
                            {v}
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
                {/* Product-keyword matching — for feeds (like the B2B daily feed) with NO Department/Category */}
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Product name contains… <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(use when Department/Category are blank — a non-phone line is an accessory if its product description contains any of these)</span></div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                    {accSel.p.map(k => (
                      <span key={k} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', background: 'var(--surface2)', borderRadius: 12, padding: '2px 8px', fontSize: 12 }}>
                        {k}<span style={{ cursor: 'pointer', color: '#dc2626', fontWeight: 700 }} onClick={() => setAccSel(s => ({ ...s, p: s.p.filter(x => x !== k) }))}>✕</span>
                      </span>
                    ))}
                    {accSel.p.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>none</span>}
                  </div>
                  <input style={{ ...sel, width: '100%' }} placeholder="e.g. case, screen, protector, charger, cable (comma-separated) — Enter to add"
                    value={kwInput} onChange={e => setKwInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { const xs = kwInput.split(',').map(s => s.trim()).filter(Boolean); setAccSel(s => ({ ...s, p: Array.from(new Set([...s.p, ...xs])) })); setKwInput('') } }} />
                  {(accFields.products || []).length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Products seen on non-phone lines (click to add as a keyword):</div>
                      <div style={{ maxHeight: 120, overflowY: 'auto', display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                        {(accFields.products || []).slice(0, 40).map((p: string) => (
                          <span key={p} style={{ cursor: 'pointer', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '2px 7px', fontSize: 11 }}
                            onClick={() => setAccSel(s => ({ ...s, p: s.p.includes(p) ? s.p : [...s.p, p] }))}>{p}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                {/* ACIMA lease tender — which Tender Type = an ACIMA/financing lease (spiff = # txns × rate) */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>ACIMA lease tender <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(tick the Tender Type(s) that = an ACIMA/financing lease — the spiff pays per such transaction; leave empty for the old &lsquo;acima&rsquo; default)</span></div>
                  <div style={{ maxHeight: 150, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                    {(accFields.tenders || []).length === 0 ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>no tender types in this period</div> : (accFields.tenders || []).map((t: string) => (
                      <label key={t} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, padding: '2px 0' }}>
                        <input type="checkbox" checked={accSel.a.includes(t)}
                          onChange={() => setAccSel(s => ({ ...s, a: s.a.includes(t) ? s.a.filter(x => x !== t) : [...s.a, t] }))} />
                        {t}
                      </label>
                    ))}
                  </div>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 8 }}>
                  <span style={{ fontSize: 12, color: accMsg.startsWith('❌') ? '#dc2626' : 'var(--text3)' }}>{accMsg}</span>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" style={{ fontSize: 13 }} onClick={() => { setAccSel({ d: [], c: [], p: [], a: [] }); setKwInput('') }}>Clear all</button>
                    <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={saveAccCfg}>Save</button>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
