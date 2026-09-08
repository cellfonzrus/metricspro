'use client'
import { useState, useEffect, Fragment } from 'react'
import { api, fmt, ORG_ID } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { ReportExportBar } from '@/components/ReportExportBar'
import { MultiSelect } from '@/lib/multiselect'
import SalesTaxRateLink from '@/components/SalesTaxRateLink'

// Tax Collected — per-store drill-down with a date-range + store multi-select + market multi-select.
// Tax is sourced from the UNIFIED sales set (raw_sales ∪ daily_sales_feed deduped by trans_id) on the
// backend, so a feed-only tenant still gets a report. Store/market filters are RULE THREE pickers over the
// values that actually appear in the data (markets come from store_mapping). RULE FOUR exports honor the
// active filters (Excel/PDF via ExportButtons).

type Day = { date: string; tax: number; revenue: number; taxable_revenue: number
             untaxed_revenue: number; effective_rate: number }
type Bucket = { sales: number; taxable_revenue: number; untaxed_revenue: number; tax: number }
type Tender = Record<'cash' | 'card' | 'financing' | 'other' | 'mixed', Bucket>
type Store = { store: string; market: string; tax: number; revenue: number; taxable_revenue: number
               untaxed_revenue: number; effective_rate: number; tender: Tender; days: Day[] }
const TENDERS: { key: keyof Tender; label: string }[] = [
  { key: 'cash', label: 'Cash' }, { key: 'card', label: 'Credit / debit card' },
  { key: 'financing', label: 'Financing' }, { key: 'other', label: 'Other' },
  { key: 'mixed', label: 'Mixed tender' },
]

export default function TaxCollectedPage() {
  const { period, setPeriod, periods } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  // The dates the QUERY uses, debounced away from the ones the inputs show. A native <input type="date">
  // fires onChange on every segment you touch (month, then day, then year), so binding the fetch straight
  // to `start`/`end` meant a round trip per keystroke — see the note on `firstLoad` below for why that was
  // not merely wasteful (owner bug report 2026-09-07: "every time you pick the date it is refreshing itself").
  const [qStart, setQStart] = useState('')
  const [qEnd, setQEnd] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  // FIRST load blanks the page for a spinner; every later refetch keeps the previous rows on screen. The
  // filter bar is rendered OUTSIDE the loading branch (below) for the same reason: it is a control, not
  // content, and unmounting the date input the user is currently typing into is what made this unusable.
  const [firstLoad, setFirstLoad] = useState(true)

  // 600ms, and only ever a COMPLETE date. A half-typed value is not a range the user meant, and
  // refetching on it is what made the page feel like it was fighting the person entering the range.
  // The no-op guard stops the very first tick (both already '') from firing a duplicate fetch.
  useEffect(() => {
    const ok = (v: string) => v === '' || v.length === 10
    if (!ok(start) || !ok(end)) return
    if (start === qStart && end === qEnd) return
    const t = setTimeout(() => { setQStart(start); setQEnd(end) }, 600)
    return () => clearTimeout(t)
  }, [start, end, qStart, qEnd])

  useEffect(() => {
    setLoading(true)
    const qs = `org_id=${ORG_ID}&period=${encodeURIComponent(period)}`
      + (qStart ? `&start=${qStart}` : '') + (qEnd ? `&end=${qEnd}` : '')
    api(`/api/v1/commcalc/tax-collected?${qs}`)
      .then(setData).catch(e => setData({ error: e?.message || String(e) }))
      .finally(() => { setLoading(false); setFirstLoad(false) })
  }, [period, qStart, qEnd])

  const allStores: Store[] = data?.stores || []
  const storeOpts = allStores.map(s => ({ value: s.store, label: s.store }))
  const marketOpts = (data?.markets || []).map((m: string) => ({ value: m, label: m }))
  const rows = allStores.filter(s =>
    (!selStores.length || selStores.includes(s.store)) &&
    (!selMarkets.length || selMarkets.includes(s.market || '')))
  const tax = rows.reduce((a, r) => a + (r.tax || 0), 0)
  const revenue = rows.reduce((a, r) => a + (r.revenue || 0), 0)
  // The rate is tax over the sales that WERE TAXED, not over every sale. Dividing by all sales gave
  // "2.23%" on the house org's August — no jurisdiction charges that — because 73% of the base was bill
  // payments and device set-up fees, both taxed $0.00 by construction (owner report 2026-09-07).
  const taxable = rows.reduce((a, r) => a + (r.taxable_revenue || 0), 0)
  // Non-taxable comes from the backend per row now (owner 2026-09-07: "segregate the sales from
  // taxable and non taxable sales") rather than being inferred by subtraction here.
  const untaxed = rows.reduce((a, r) => a + (r.untaxed_revenue ?? ((r.revenue || 0) - (r.taxable_revenue || 0))), 0)
  const effRate = taxable ? (100 * tax / taxable) : 0
  const tender = TENDERS.map(t => ({
    ...t,
    sales: rows.reduce((a, r) => a + (r.tender?.[t.key]?.sales || 0), 0),
    taxable: rows.reduce((a, r) => a + (r.tender?.[t.key]?.taxable_revenue || 0), 0),
    untaxed: rows.reduce((a, r) => a + (r.tender?.[t.key]?.untaxed_revenue || 0), 0),
    tax: rows.reduce((a, r) => a + (r.tender?.[t.key]?.tax || 0), 0),
  })).filter(t => t.sales || t.tax)
  // The window the SERVER actually read (it spans whole months for a cross-month range), not what the
  // inputs happen to show mid-edit.
  const rangeLabel = data?.window || (start || end ? `${start || '…'} → ${end || '…'}` : period)

  // RULE FOUR §3c: export the CURRENTLY-VISIBLE (filtered) rows to Excel/PDF/Print + Send (email/WhatsApp).
  const dayRows = rows.flatMap(s => s.days.map(d => ({ store: s.store, market: s.market, ...d })))
  const exportSheets = [
    { name: 'By store', rows, columns: [
      { header: 'Store', get: (r: any) => r.store },
      { header: 'Market', get: (r: any) => r.market || '' },
      { header: 'Tax collected', get: (r: any) => r.tax, money: true },
      { header: 'Total sales (pre-tax)', get: (r: any) => r.revenue, money: true },
      { header: 'Taxable sales', get: (r: any) => r.taxable_revenue, money: true },
      { header: 'Non-taxable sales', get: (r: any) => r.untaxed_revenue, money: true },
      { header: 'Effective rate %', get: (r: any) => r.effective_rate },
      { header: 'Cash sales', get: (r: any) => r.tender?.cash?.sales ?? 0, money: true },
      { header: 'Card sales', get: (r: any) => r.tender?.card?.sales ?? 0, money: true },
      { header: 'Financing sales', get: (r: any) => r.tender?.financing?.sales ?? 0, money: true },
    ] },
    { name: 'By store & day', rows: dayRows, columns: [
      { header: 'Store', get: (r: any) => r.store },
      { header: 'Market', get: (r: any) => r.market || '' },
      { header: 'Date', get: (r: any) => r.date },
      { header: 'Tax collected', get: (r: any) => r.tax, money: true },
      { header: 'Total sales (pre-tax)', get: (r: any) => r.revenue, money: true },
      { header: 'Taxable sales', get: (r: any) => r.taxable_revenue, money: true },
      { header: 'Non-taxable sales', get: (r: any) => r.untaxed_revenue, money: true },
      { header: 'Effective rate %', get: (r: any) => r.effective_rate },
      { header: 'Cash sales', get: (r: any) => r.tender?.cash?.sales ?? 0, money: true },
      { header: 'Card sales', get: (r: any) => r.tender?.card?.sales ?? 0, money: true },
      { header: 'Financing sales', get: (r: any) => r.tender?.financing?.sales ?? 0, money: true },
    ] },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Tax Collected</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0', maxWidth: 720 }}>
            Retail sales tax collected per store, from the unified sales transactions. Merchandise (ext
            price) is pre-tax; this is the tax on top the customer paid. Drill a store to its daily detail.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {/* The rate on this page is OBSERVED (tax / taxable sales). When it is wrong the fix is a
              CONFIGURED rate, which lives in POS Settings -> Sales Tax — and there was no way to get
              there from here (owner report 2026-09-07). */}
          <SalesTaxRateLink />
          <select className="select" value={period} onChange={e => setPeriod(e.target.value)}>
            {periods.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          {allStores.length > 0 && (
            <ReportExportBar title="Tax Collected"
              subtitle={`${period}${(start || end) ? ` · ${rangeLabel}` : ''}`}
              filename={`tax-collected_${period.replace(/\s+/g, '-')}`} sheets={exportSheets} />
          )}
        </div>
      </div>

      {/* FILTER BAR — deliberately OUTSIDE the loading branch (owner bug report 2026-09-07: "every time
          you pick the date it is refreshing itself"). It used to live inside `{loading ? spinner : (...)}`,
          so changing a date set loading=true, which UNMOUNTED the very date input being typed into and
          remounted it when the fetch returned — the calendar popup closed and focus was lost on every
          segment. A control must not be a casualty of loading the content it controls. */}
      <div className="card" style={{ padding: '10px 14px', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 12 }}>
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 5, alignItems: 'center' }}>
          From <input type="date" className="select" value={start} onChange={e => setStart(e.target.value)} style={{ padding: '4px 6px' }} />
        </label>
        <label style={{ fontSize: 12, color: 'var(--text2)', display: 'flex', gap: 5, alignItems: 'center' }}>
          To <input type="date" className="select" value={end} onChange={e => setEnd(e.target.value)} style={{ padding: '4px 6px' }} />
        </label>
        {(start || end) && <button className="btn" style={{ fontSize: 12, padding: '4px 8px' }} onClick={() => { setStart(''); setEnd('') }}>Clear dates</button>}
        <MultiSelect allLabel="All stores" width={170} value={selStores} searchable options={storeOpts} onChange={setSelStores} />
        <MultiSelect allLabel="All markets" width={160} value={selMarkets} searchable options={marketOpts} onChange={setSelMarkets} />
        <span style={{ fontSize: 12, color: 'var(--text3)' }}>{rows.length} store(s)</span>
        {loading && !firstLoad && <span style={{ fontSize: 12, color: 'var(--text3)' }}>updating…</span>}
      </div>

      {firstLoad && loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : data?.error ? (
        <div className="card" style={{ padding: 16, color: '#b91c1c' }}>Error: {data.error}</div>
      ) : (
        <>
          {data?.note && <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 13, color: '#92400e', background: '#fffbeb', borderLeft: '3px solid #f59e0b' }}>⚠️ {data.note}</div>}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 12, marginBottom: 16 }}>
            <Stat label="Total tax collected" value={fmt(tax)} color="var(--accent)" />
            <Stat label="Total sales (all)" value={fmt(revenue)} />
            <Stat label="Taxable sales" value={fmt(taxable)} color="#16a34a" />
            <Stat label="Non-taxable sales" value={fmt(untaxed)} color="var(--text2)" />
            <Stat label="Effective tax rate" value={`${effRate.toFixed(2)}%`} color="#16a34a" />
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', margin: '-6px 0 16px' }}>
            Rate is tax ÷ <strong>taxable</strong> sales. Taxable + non-taxable = total sales; a line counts
            as taxable when it actually carried tax, so bill payments, set-up fees and the like fall on the
            non-taxable side and never dilute the rate.
          </div>

          {tender.length > 0 && (
            <div className="card" style={{ padding: 0, overflow: 'auto', marginBottom: 16 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 660 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 12px' }}>How it was paid</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Sales</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Taxable</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Non-taxable</th>
                  <th style={{ textAlign: 'right', padding: '8px 12px' }}>Tax collected</th>
                </tr></thead>
                <tbody>
                  {tender.map(t => (
                    <tr key={t.key} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '8px 12px' }}>
                        {t.label}
                        {t.key === 'mixed' && <span style={{ fontSize: 11, color: 'var(--text3)' }}>
                          {' '}— one line, several tenders, no split recorded
                        </span>}
                      </td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(t.sales)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmt(t.taxable)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--text2)' }}>{fmt(t.untaxed)}</td>
                      <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600 }}>{fmt(t.tax)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {rows.length === 0 ? (
            <div className="card" style={{ padding: 24, textAlign: 'center', color: 'var(--text3)' }}>No tax data for {rangeLabel}.</div>
          ) : (
            <div className="card" style={{ padding: 0, overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead><tr style={{ background: 'var(--surface2)', fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Store</th>
                  <th style={{ textAlign: 'left', padding: '8px 14px' }}>Market</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Tax collected</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Total sales</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Taxable</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Non-taxable</th>
                  <th style={{ textAlign: 'right', padding: '8px 14px' }}>Effective rate</th>
                </tr></thead>
                <tbody>
                  {rows.map((r, i) => {
                    const isOpen = !!open[r.store]
                    return (
                      <Fragment key={r.store}>
                        <tr onClick={() => setOpen(o => ({ ...o, [r.store]: !o[r.store] }))}
                            style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined, cursor: 'pointer' }}>
                          <td style={{ padding: '9px 14px', fontSize: 13, fontWeight: 500 }}>
                            <span style={{ color: 'var(--text3)', marginRight: 6 }}>{isOpen ? '▾' : '▸'}</span>{r.store}
                          </td>
                          <td style={{ padding: '9px 14px', fontSize: 13, color: 'var(--text2)' }}>{r.market || '—'}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, fontWeight: 700 }}>{fmt(r.tax)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13 }}>{fmt(r.revenue)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, color: '#16a34a' }}>{fmt(r.taxable_revenue)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{fmt(r.untaxed_revenue)}</td>
                          <td style={{ padding: '9px 14px', textAlign: 'right', fontSize: 13, color: 'var(--text2)' }}>{r.effective_rate}%</td>
                        </tr>
                        {isOpen && r.days.map(d => (
                          <tr key={r.store + d.date} style={{ borderTop: '1px solid var(--border)', background: 'var(--surface)' }}>
                            <td style={{ padding: '6px 14px 6px 34px', fontSize: 12, color: 'var(--text2)' }}>{d.date}</td>
                            <td />
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12 }}>{fmt(d.tax)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(d.revenue)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(d.taxable_revenue)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{fmt(d.untaxed_revenue)}</td>
                            <td style={{ padding: '6px 14px', textAlign: 'right', fontSize: 12, color: 'var(--text3)' }}>{d.effective_rate}%</td>
                          </tr>
                        ))}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
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
