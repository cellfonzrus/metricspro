'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, apiUpload, fmt, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { ExportButtons, ExportPayload, ExportColumn } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import type { StoreOpt } from '@/lib/market-store-cascade'

// RULE FIVE (§3d) retrofit, owner request 2026-10-03: "add date range and market with standard filters
// for distributor invoices". The shared <StandardFilterBar> is ADOPTED — period as a DATE RANGE plus the
// market -> store cascade — and the page's own month/status pickers are APPENDED as module facets (the
// core set is never substituted). The page's old single "All stores" select is REPLACED by the standard
// store multi-select rather than kept beside it: its options were this feed's own raw `location` strings,
// which is the same store offered once per spelling (§13e), and the new picker reaches every one of them
// (measured: all 29 distributor spellings on the house org are selectable through it).
//
// DEVIATION, stated out loud: a distributor invoice is raised against the DEALER account and names no
// salesperson, so there is no rep dimension and the rep control is hidden.
//
// Filtering is SERVER-side, not over the loaded rows: the tiles, the fees-by-type panel and the
// fees-by-store table are computed by /vip/summary, so a client-side narrowing would leave them
// reporting the unfiltered totals above a filtered table. Market cannot be filtered client-side at all —
// `vip_invoices` has no market column and `location` is the distributor's own spelling of a store
// address, which only the org's store vocabulary can resolve (see vip_invoice_filter.py).


type Totals = {
  invoices: number; sub_total: number; grand_total: number; fees_total: number
  shipping: number; discount: number; other_cost: number; other_deductions: number; tax: number
}
type StoreRow = {
  location: string; invoices: number; sub_total: number; grand_total: number
  shipping: number; discount: number; other_cost: number; other_deductions: number; tax: number
}
// `unresolved` — invoices a store/market selection could not bind to the org's store vocabulary. The
// server REPORTS them rather than folding them into the totals, so a store missing from the mapping
// reads as a row to add, never as money that is not there.
type Unresolved = { invoices: number; grand_total: number; locations: string[] }
type Summary = { totals: Totals; fees_by_type: Record<string, number>; by_store: StoreRow[]; unresolved?: Unresolved }
type Invoice = {
  vip_id: number; invoice_number: string; order_number: string | null; location: string
  status: string; created_on: string | null; due_date: string | null
  sub_total: number; shipping: number; discount: number; other_cost: number
  other_deductions: number; tax: number; grand_total: number; period: string | null
}

const FEE_LABELS: Record<string, string> = {
  shipping: 'Shipping', discount: 'Discount', other_cost: 'Other Cost',
  other_deductions: 'Other Deductions', tax: 'Tax',
}
const d10 = (s: string | null) => (s ? String(s).slice(0, 10) : '—')
const selStyle = { padding: '6px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

type InvLine = { name: string | null; note: string | null; sku: string | null; price: number | null; quantity: number | null; total: number | null }
type InvDevice = { serial: string | null; product_name: string | null; imei: string | null; sim: string | null }
type InvoiceDetail = { invoice: any; lines: InvLine[]; devices: InvDevice[] }

// Click-to-sort over any row list. Numeric-aware; nulls sort last.
function useSort<T>(rows: T[], initialKey: string, initialDir: 'asc' | 'desc' = 'desc') {
  const [sortKey, setSortKey] = useState(initialKey)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(initialDir)
  const sorted = [...rows].sort((a, b) => {
    const av = (a as any)[sortKey], bv = (b as any)[sortKey]
    if (av == null && bv == null) return 0
    if (av == null) return 1
    if (bv == null) return -1
    const cmp = (typeof av === 'number' && typeof bv === 'number')
      ? av - bv : String(av).localeCompare(String(bv), undefined, { numeric: true })
    return sortDir === 'asc' ? cmp : -cmp
  })
  function toggle(key: string) {
    if (key === sortKey) setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    else { setSortKey(key); setSortDir('asc') }
  }
  return { sorted, sortKey, sortDir, toggle }
}

function SortTh({ label, k, sort, align }: { label: string; k: string; sort: ReturnType<typeof useSort>; align?: 'left' | 'right' }) {
  const active = sort.sortKey === k
  return (
    <th onClick={() => sort.toggle(k)}
      style={{ cursor: 'pointer', userSelect: 'none', textAlign: align || 'left', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: active ? 'var(--accent)' : 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap', position: 'sticky', top: 0, background: 'var(--surface2)' }}>
      {label}{active ? (sort.sortDir === 'asc' ? ' ▲' : ' ▼') : ' ↕'}
    </th>
  )
}

function InvoiceDetailModal({ vipId, onClose }: { vipId: number; onClose: () => void }) {
  const [detail, setDetail] = useState<InvoiceDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [q, setQ] = useState('')

  useEffect(() => {
    let alive = true
    setLoading(true)
    api(`/api/v1/commcalc/vip/invoice/${vipId}?org_id=${ORG_ID}`)
      .then((d: any) => { if (alive) { setDetail(d); setLoading(false) } })
      .catch(e => { console.error(e); if (alive) setLoading(false) })
    return () => { alive = false }
  }, [vipId])

  const ql = q.trim().toLowerCase()
  const lines = (detail?.lines || []).filter(l => !ql || [l.name, l.sku, l.note].some(v => String(v || '').toLowerCase().includes(ql)))
  const devices = (detail?.devices || []).filter(dv => !ql || [dv.serial, dv.imei, dv.product_name, dv.sim].some(v => String(v || '').toLowerCase().includes(ql)))
  const lineSort = useSort(lines, 'total')
  const devSort = useSort(devices, 'serial', 'asc')
  const inv = detail?.invoice

  function buildPayload(): ExportPayload {
    return {
      title: `Distributor Invoice ${inv?.invoice_number || vipId}`,
      subtitle: [inv?.location, d10(inv?.created_on || null), inv?.status].filter(Boolean).join(' · '),
      filename: `vip-invoice-${inv?.invoice_number || vipId}`,
      sheets: [
        { name: 'Line Items', rows: lineSort.sorted, columns: [
          { header: 'Name', get: r => r.name }, { header: 'SKU', get: r => r.sku },
          { header: 'Note', get: r => r.note }, { header: 'Price', get: r => r.price, money: true },
          { header: 'Qty', get: r => r.quantity, align: 'right' }, { header: 'Total', get: r => r.total, money: true },
        ]},
        { name: 'Devices', rows: devSort.sorted, columns: [
          { header: 'Serial', get: r => r.serial }, { header: 'Product', get: r => r.product_name },
          { header: 'IMEI', get: r => r.imei }, { header: 'SIM', get: r => r.sim },
        ]},
      ],
    }
  }

  const money = (n: any) => fmt(Number(n) || 0)
  const th = { textAlign: 'left' as const, padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase' as const, whiteSpace: 'nowrap' as const }
  const td = { padding: '7px 12px', fontSize: 12, borderTop: '1px solid var(--border)' }

  return (
    <div onClick={onClose}
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '5vh 16px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} className="card"
        style={{ width: 'min(960px, 100%)', maxHeight: '90vh', overflowY: 'auto', padding: 0 }}>
        <div style={{ position: 'sticky', top: 0, background: 'var(--surface)', borderBottom: '1px solid var(--border)', padding: '16px 20px', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, zIndex: 1 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700 }}>🧾 Invoice {inv?.invoice_number || `#${vipId}`}</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>
              {inv ? `${inv.location || '—'} · ${d10(inv.created_on)} · ${inv.status || '—'}` : 'Loading…'}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {detail && <ExportButtons payload={buildPayload} />}
            <button className="btn" style={{ fontSize: 13, padding: '5px 12px' }} onClick={onClose}>✕ Close</button>
          </div>
        </div>

        {loading ? (
          <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Loading invoice…</div>
        ) : !detail ? (
          <div style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>Could not load invoice.</div>
        ) : (
          <div style={{ padding: 20 }}>
            {/* Totals breakdown */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 18 }}>
              {[['Subtotal', inv.sub_total], ['Shipping', inv.shipping], ['Discount', inv.discount], ['Other Cost', inv.other_cost], ['Other Ded.', inv.other_deductions], ['Tax', inv.tax], ['Grand Total', inv.grand_total]].map(([lab, v]) => (
                <div key={lab as string} style={{ padding: '10px 12px', background: 'var(--surface2)', borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{lab as string}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, marginTop: 3, color: lab === 'Grand Total' ? '#059669' : 'var(--text1)' }}>{money(v)}</div>
                </div>
              ))}
            </div>

            <div style={{ marginBottom: 14 }}>
              <input value={q} onChange={e => setQ(e.target.value)} placeholder="Filter lines & devices…"
                style={{ ...selStyle, width: 260 }} />
            </div>

            {/* Line items */}
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
              Line Items ({lineSort.sorted.length})
            </div>
            <div style={{ overflowX: 'auto', marginBottom: 22 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 640 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  <SortTh label="Name" k="name" sort={lineSort} />
                  <SortTh label="SKU" k="sku" sort={lineSort} />
                  <SortTh label="Note" k="note" sort={lineSort} />
                  <SortTh label="Price" k="price" sort={lineSort} align="right" />
                  <SortTh label="Qty" k="quantity" sort={lineSort} align="right" />
                  <SortTh label="Total" k="total" sort={lineSort} align="right" />
                </tr></thead>
                <tbody>
                  {lineSort.sorted.length === 0 ? (
                    <tr><td colSpan={6} style={{ ...td, textAlign: 'center', color: 'var(--text3)', padding: 18 }}>No line items.</td></tr>
                  ) : lineSort.sorted.map((l, i) => (
                    <tr key={i}>
                      <td style={td}>{l.name || '—'}</td>
                      <td style={{ ...td, fontFamily: 'monospace' }}>{l.sku || '—'}</td>
                      <td style={{ ...td, color: 'var(--text2)' }}>{l.note || '—'}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{money(l.price)}</td>
                      <td style={{ ...td, textAlign: 'right' }}>{l.quantity ?? '—'}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{money(l.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Devices */}
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
              Devices ({devSort.sorted.length})
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
                <thead><tr style={{ background: 'var(--surface2)' }}>
                  <SortTh label="Serial / ESN" k="serial" sort={devSort} />
                  <SortTh label="Product" k="product_name" sort={devSort} />
                  <SortTh label="IMEI" k="imei" sort={devSort} />
                  <SortTh label="SIM" k="sim" sort={devSort} />
                </tr></thead>
                <tbody>
                  {devSort.sorted.length === 0 ? (
                    <tr><td colSpan={4} style={{ ...td, textAlign: 'center', color: 'var(--text3)', padding: 18 }}>No devices on this invoice.</td></tr>
                  ) : devSort.sorted.map((dv, i) => (
                    <tr key={i}>
                      <td style={{ ...td, fontFamily: 'monospace' }}>{dv.serial || '—'}</td>
                      <td style={td}>{dv.product_name || '—'}</td>
                      <td style={{ ...td, fontFamily: 'monospace' }}>{dv.imei || '—'}</td>
                      <td style={{ ...td, fontFamily: 'monospace' }}>{dv.sim || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function VipInvoicesPage() {
  // The standard core set. `period`/`periodTo` are the date RANGE (inclusive YYYY-MM-DD days over the
  // invoice's created_on); `stores`/`markets` are the cascade's multi-selects.
  const [filt, setFilt] = useState<StandardFilterValue>(emptyStandardFilter())
  const [period, setPeriod] = useState('')          // module facet: the distributor's own month period
  const [status, setStatus] = useState('')          // module facet
  const [periods, setPeriods] = useState<string[]>([])
  const [statuses, setStatuses] = useState<string[]>([])
  // Store roster WITH each store's market, from /vip/filter-options — which reads the org's one store
  // option home (core.scope.org_store_options) plus any distributor spelling the matcher cannot bind,
  // so every spelling in the data is selectable and no store is offered twice.
  const [roster, setRoster] = useState<StoreOpt[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [loading, setLoading] = useState(true)
  const [importing, setImporting] = useState(false)
  const [importMsg, setImportMsg] = useState('')
  const [detailId, setDetailId] = useState<number | null>(null)  // open invoice preview
  const [invQ, setInvQ] = useState('')                            // free-text invoice search

  const loadOptions = useCallback(() => {
    apiCached(`/api/v1/commcalc/vip/filter-options?org_id=${ORG_ID}`, LOOKUP)
      .then((d: any) => {
        setPeriods(d.periods || []); setStatuses(d.statuses || [])
        setRoster((d.stores || []).map((x: any) => (
          typeof x === 'string' ? { id: x, label: x, market: null }
                                : { id: x.store, label: x.store, market: x.market || null })))
      })
      .catch(console.error)
  }, [])

  useEffect(() => { loadOptions() }, [loadOptions])

  // ONE query string for both calls, so the table and the tiles above it can never be answering
  // different questions. Stores/markets are PIPE-separated — a store address may contain a comma.
  const filterParams = useCallback(() => {
    const qs = new URLSearchParams({ org_id: ORG_ID })
    if (period) qs.set('period', period)
    if (status) qs.set('status', status)
    if (filt.period) qs.set('date_from', filt.period)
    if (filt.periodTo) qs.set('date_to', filt.periodTo)
    if (filt.stores.length) qs.set('stores', filt.stores.join('|'))
    if (filt.markets.length) qs.set('markets', filt.markets.join('|'))
    return qs
  }, [period, status, filt])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = filterParams().toString()
      const [s, inv] = await Promise.all([
        api(`/api/v1/commcalc/vip/summary?${qs}`),
        api(`/api/v1/commcalc/vip/invoices?${qs}`),
      ])
      setSummary(s); setInvoices(inv)
    } catch (e) { console.error(e) }
    setLoading(false)
  }, [filterParams])

  useEffect(() => { load() }, [load])

  async function handleImport(file: File) {
    setImporting(true); setImportMsg('')
    const form = new FormData(); form.append('file', file)
    try {
      // apiUpload (not a bare fetch) so the bearer token rides along with the multipart body
      const data = await apiUpload(`/api/v1/commcalc/vip/upload?org_id=${ORG_ID}`, form)
      setImportMsg(`✅ ${data.invoices.toLocaleString()} invoices · ${data.lines.toLocaleString()} lines · ${data.devices.toLocaleString()} devices`)
      loadOptions()
      load()
    } catch (e: any) {
      setImportMsg(`❌ ${e.message}`)
    }
    setImporting(false)
  }

  function buildPayload(): ExportPayload {
    const invCols: ExportColumn[] = [
      { header: 'Invoice #', get: r => r.invoice_number },
      { header: 'Date', get: r => d10(r.created_on) },
      { header: 'Store', get: r => r.location },
      { header: 'Status', get: r => r.status },
      { header: 'Subtotal', get: r => r.sub_total, money: true },
      { header: 'Shipping', get: r => r.shipping, money: true },
      { header: 'Discount', get: r => r.discount, money: true },
      { header: 'Other Cost', get: r => r.other_cost, money: true },
      { header: 'Other Deductions', get: r => r.other_deductions, money: true },
      { header: 'Tax', get: r => r.tax, money: true },
      { header: 'Grand Total', get: r => r.grand_total, money: true },
    ]
    const storeCols: ExportColumn[] = [
      { header: 'Store', get: r => r.location },
      { header: 'Invoices', get: r => r.invoices, align: 'right' },
      { header: 'Shipping', get: r => r.shipping, money: true },
      { header: 'Discount', get: r => r.discount, money: true },
      { header: 'Other Cost', get: r => r.other_cost, money: true },
      { header: 'Other Deductions', get: r => r.other_deductions, money: true },
      { header: 'Tax', get: r => r.tax, money: true },
      { header: 'Grand Total', get: r => r.grand_total, money: true },
    ]
    // What-you-see-is-what-exports: the subtitle names every filter that shaped these rows, the core
    // set included, so an exported workbook can never look like the whole feed when it is a slice.
    const dateLabel = filt.period && filt.periodTo ? `${filt.period} → ${filt.periodTo}`
      : filt.period ? `from ${filt.period}` : filt.periodTo ? `through ${filt.periodTo}` : null
    const filterLabel = [
      dateLabel, period || null, status || null,
      filt.markets.length ? `Markets: ${filt.markets.join(', ')}` : null,
      filt.stores.length ? `Stores: ${filt.stores.join(', ')}` : null,
    ].filter(Boolean).join(' · ') || 'All invoices'
    const slug = (v: string) => v.replace(/[^a-z0-9]+/gi, '-').toLowerCase()
    const nameBit = filt.stores.length === 1 ? '-' + slug(filt.stores[0])
      : filt.markets.length === 1 ? '-' + slug(filt.markets[0]) : ''
    return {
      title: 'Distributor Invoices',
      subtitle: filterLabel,
      filename: `vip-invoices${nameBit}`,
      sheets: [
        { name: 'Invoices', rows: invoices, columns: invCols },
        { name: 'Fees by Store', rows: summary?.by_store || [], columns: storeCols },
      ],
    }
  }

  const t = summary?.totals
  const tiles = t ? [
    { label: 'Invoices', val: t.invoices.toLocaleString(), color: 'var(--accent)' },
    { label: 'Subtotal', val: fmt(t.sub_total), color: '#0369a1' },
    { label: 'Fees (all types)', val: fmt(t.fees_total), color: '#d97706' },
    { label: 'Grand Total', val: fmt(t.grand_total), color: '#059669' },
  ] : []

  // Invoice list: add a per-row fees total, free-text search, and click-to-sort.
  const invRows = invoices.map(r => ({ ...r, _fees: (r.shipping || 0) + (r.discount || 0) + (r.other_cost || 0) + (r.other_deductions || 0) + (r.tax || 0) }))
  const invQl = invQ.trim().toLowerCase()
  const invFiltered = invQl
    ? invRows.filter(r => [r.invoice_number, r.order_number, r.location, r.status, r.period].some(v => String(v || '').toLowerCase().includes(invQl)))
    : invRows
  const invSort = useSort(invFiltered, 'created_on')

  return (
    <div>
      <div style={{ marginBottom: 20, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🧾 Distributor Invoices</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Scraped from the Distributor dealer portal. Fees-by-type uses the invoice money buckets.
          </p>
        </div>
        {summary && <ExportButtons payload={buildPayload} />}
        {summary && <SendReportButton reportKey="vip_invoices" filters={{
          ...(period ? { period } : {}), ...(status ? { status } : {}),
          ...(filt.period ? { date_from: filt.period } : {}), ...(filt.periodTo ? { date_to: filt.periodTo } : {}),
          ...(filt.stores.length ? { stores: filt.stores.join('|') } : {}),
          ...(filt.markets.length ? { markets: filt.markets.join('|') } : {}),
        }} />}
        <a className="btn" href="/commcalc/vip/paygo" style={{ textDecoration: 'none' }}>📦 Asset Lending (PayGo)</a>
        <a className="btn" href="/commcalc/vip/sweep" style={{ textDecoration: 'none' }}>⚙️ Auto-sweep</a>
      </div>

      {/* Import workbook */}
      <div className="card" style={{ marginBottom: 20, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600 }}>Import Distributor workbook</span>
        <span style={{ color: 'var(--text3)', fontSize: 12, flex: 1 }}>
          The distributor&apos;s invoices workbook (Invoices / Lines / Devices sheets). Full replace.
        </span>
        {importing ? (
          <span style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text2)', fontSize: 13 }}>
            <span className="spinner" /> Importing…
          </span>
        ) : (
          <label style={{ cursor: 'pointer' }}>
            <span className="btn btn-secondary" style={{ display: 'inline-flex' }}>📂 Choose workbook</span>
            <input type="file" accept=".xlsx,.xls" style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) handleImport(f) }} />
          </label>
        )}
        {importMsg && <div style={{ fontSize: 12, color: importMsg.startsWith('✅') ? '#16a34a' : '#dc2626', width: '100%' }}>{importMsg}</div>}
      </div>

      {/* Filters — the shared core set (date range + market -> store cascade), with this module's own
          period and status pickers appended. Reps are hidden: an invoice names no salesperson. */}
      <div className="card" style={{ padding: 14, marginBottom: 20 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt}
          periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: false }}
          cascadeStores={roster}
          storeLabel="Stores…" marketLabel="Markets…"
          right={<>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              Period
              <select style={selStyle} value={period} onChange={e => setPeriod(e.target.value)}>
                <option value="">All periods</option>
                {periods.map(p => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              Status
              <select style={selStyle} value={status} onChange={e => setStatus(e.target.value)}>
                <option value="">All statuses</option>
                {statuses.map(s => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>
          </>}
        />
      </div>

      {/* A store/market selection that could not bind some of this feed's own store spellings SAYS SO.
          Those invoices are excluded (never guessed into a market), and the fix is a store_aliases row
          in the org vocabulary — which every report reads, so one row fixes all of them at once. */}
      {summary && (summary.unresolved?.invoices || 0) > 0 && (
        <div className="card" style={{ marginBottom: 20, borderLeft: '3px solid #d97706', fontSize: 13 }}>
          <strong>{summary.unresolved!.invoices.toLocaleString()} invoices</strong> ({fmt(summary.unresolved!.grand_total)})
          are excluded because this filter could not match the distributor&apos;s spelling of their store:{' '}
          {summary.unresolved!.locations.join(', ')}. They are reachable by picking that spelling in the
          store filter; mapping it to the store (a store alias) makes it follow the market filter too.
        </div>
      )}

      {loading ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>Loading…</div>
      ) : !summary || summary.totals.invoices === 0 ? (
        <div style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          No Distributor invoices. Import the workbook above to load data.
        </div>
      ) : (
        <>
          {/* Summary tiles */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 16, marginBottom: 20 }}>
            {tiles.map(c => (
              <div key={c.label} className="card" style={{ padding: '18px 22px', borderTop: `3px solid ${c.color}` }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c.label}</div>
                <div style={{ fontSize: 24, fontWeight: 700, color: c.color, marginTop: 6 }}>{c.val}</div>
              </div>
            ))}
          </div>

          {/* Fees by type */}
          <div className="card" style={{ marginBottom: 20 }}>
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 12 }}>Fees by Type</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 12 }}>
              {Object.keys(FEE_LABELS).map(k => (
                <div key={k} style={{ padding: '12px 14px', background: 'var(--surface2)', borderRadius: 8 }}>
                  <div style={{ fontSize: 11, color: 'var(--text2)', textTransform: 'uppercase' }}>{FEE_LABELS[k]}</div>
                  <div style={{ fontSize: 18, fontWeight: 700, marginTop: 4 }}>{fmt(summary.fees_by_type[k] || 0)}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Per-store fees */}
          <div className="card" style={{ padding: 0, marginBottom: 20 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14 }}>
              Fees & Totals by Store ({summary.by_store.length})
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 820 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Store', 'Inv', 'Shipping', 'Discount', 'Other Cost', 'Other Ded.', 'Tax', 'Grand Total'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '8px 12px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {summary.by_store.map((s, i) => (
                    <tr key={s.location} style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{s.location}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{s.invoices}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.shipping)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.discount)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.other_cost)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.other_deductions)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(s.tax)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 600 }}>{fmt(s.grand_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Invoice list */}
          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontWeight: 600, fontSize: 14, display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <span>Invoices ({invSort.sorted.length.toLocaleString()}{invQl && invSort.sorted.length !== invoices.length ? ` of ${invoices.length.toLocaleString()}` : ''}) <span style={{ fontWeight: 400, color: 'var(--text3)', fontSize: 12 }}>— click a row to preview</span></span>
              <input value={invQ} onChange={e => setInvQ(e.target.value)} placeholder="Search invoice # / store / status…" style={{ ...selStyle, width: 280, fontWeight: 400 }} />
            </div>
            <div style={{ overflowX: 'auto', maxHeight: 600, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    <SortTh label="Invoice #" k="invoice_number" sort={invSort} />
                    <SortTh label="Date" k="created_on" sort={invSort} />
                    <SortTh label="Store" k="location" sort={invSort} />
                    <SortTh label="Status" k="status" sort={invSort} />
                    <SortTh label="Subtotal" k="sub_total" sort={invSort} align="right" />
                    <SortTh label="Fees" k="_fees" sort={invSort} align="right" />
                    <SortTh label="Grand Total" k="grand_total" sort={invSort} align="right" />
                  </tr>
                </thead>
                <tbody>
                  {invSort.sorted.length === 0 ? (
                    <tr><td colSpan={7} style={{ padding: 24, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No invoices match.</td></tr>
                  ) : invSort.sorted.map((r, i) => (
                    <tr key={r.vip_id} onClick={() => setDetailId(r.vip_id)}
                      style={{ borderTop: '1px solid var(--border)', background: i % 2 === 0 ? 'transparent' : 'var(--surface2)', cursor: 'pointer' }}>
                      <td style={{ padding: '8px 12px', fontSize: 12, fontFamily: 'monospace', color: 'var(--accent)', fontWeight: 600 }}>{r.invoice_number || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, whiteSpace: 'nowrap' }}>{d10(r.created_on)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12 }}>{r.location || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, color: 'var(--text2)' }}>{r.status || '—'}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right' }}>{fmt(r.sub_total)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', color: r._fees ? '#d97706' : 'var(--text3)' }}>{fmt(r._fees)}</td>
                      <td style={{ padding: '8px 12px', fontSize: 12, textAlign: 'right', fontWeight: 600 }}>{fmt(r.grand_total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {detailId != null && <InvoiceDetailModal vipId={detailId} onClose={() => setDetailId(null)} />}
    </div>
  )
}
