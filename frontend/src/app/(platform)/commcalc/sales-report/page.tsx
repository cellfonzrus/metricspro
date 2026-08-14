'use client'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { ExportColumn } from '@/lib/export'
import ReportShell from '@/components/ReportShell'
import ReportExportBar from '@/components/ReportExportBar'
import { MultiSelect } from '@/lib/multiselect'
import EntityPicker from '@/components/EntityPicker'
import { optionsFromRows } from '@/lib/standard-filters'
import { WhereAreMyRowsButton } from '../_lib/UploadTracePanel'

// Targeted super-admin org-resolution mitigation (see NEEDS CORE): the sales-report reads carry NO org_id
// in the URL, so for a super-admin (whom the tenant middleware does NOT rewrite) the backend defaults to
// the HOUSE org and the tenant's data looks empty. Appending the active tenant fixes THIS page until the
// universal client.ts fix lands. Harmless for normal users (the middleware overrides org_id to their own
// membership regardless) and a no-op when no tenant is selected.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

// The sales actually done across all stores, from the imported Sales Transaction Details
// (raw_sales, falling back to the daily email feed). One row per store + rep + day; ReportShell
// adds the rep/store/date/month filters, add-your-own filter, group-by, export and send.
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }

function thisMonth() { return new Date().toISOString().slice(0, 7) }

// Contract-type map is keyed by a CANONICAL lowercased contract-type so a re-cased POS label
// ("PREPAID NEW" vs a saved "Prepaid New") still resolves (Gate-1 f3). Normalizing on load + save also
// dedupes any pre-existing case-variant keys deterministically: entries are folded in sorted-key order, so
// the lexicographically-greatest original-case key's bucket wins (stable regardless of object order).
function normalizeCtMap(raw: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const k of Object.keys(raw || {}).sort()) {
    const lk = String(k).trim().toLowerCase()
    if (lk && raw[k]) out[lk] = raw[k]
  }
  return out
}
// Case-insensitive membership + toggle for the bill-payment picker (backend matches case-insensitively;
// keep the UI consistent + never store case-variant duplicates).
const hasCI = (arr: string[], v: string) => arr.some(x => x.toLowerCase() === v.toLowerCase())
const toggleCI = (arr: string[], v: string) => hasCI(arr, v) ? arr.filter(x => x.toLowerCase() !== v.toLowerCase()) : [...arr, v]

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
  // d=accessory depts · c=accessory categories · p=accessory product-keywords · a=ACIMA tenders ·
  // box=device-unit "box" departments (mig 218) · setup=device set-up-fee keywords (mig 217) ·
  // billpay=bill-payment product/item values for the conversion metric (mig 214).
  const [accSel, setAccSel] = useState<{ d: string[]; c: string[]; p: string[]; a: string[]; box: string[]; setup: string[]; billpay: string[] }>({ d: [], c: [], p: [], a: [], box: [], setup: [], billpay: [] })
  const [accMsg, setAccMsg] = useState('')
  const [kwInput, setKwInput] = useState('')
  const [setupInput, setSetupInput] = useState('')
  const [ctMap, setCtMap] = useState<Record<string, string>>({})   // contract_type -> activation bucket (mig 213); keyed lowercased (f3)
  const [accCanEdit, setAccCanEdit] = useState(true)               // caller may edit Classification settings ('classification' perm)
  const [boxBuckets, setBoxBuckets] = useState<string[]>([])       // box_count_buckets (mig 231); UI toggles only 'byod', other members preserved
  const [catOn, setCatOn] = useState(false)                        // catalog-driven accessory classification (mig 231)
  const [gpOn, setGpOn] = useState(false)                          // GP-report adoption of these rules (mig 250)
  const [catCats, setCatCats] = useState<string[]>([])             // which catalog categories = accessory
  const [catOpts, setCatOpts] = useState<string[]>([])             // distinct catalog categories (pick-don't-type)
  const [selMarkets, setSelMarkets] = useState<string[]>([])   // multi-select market filter
  const [selStores, setSelStores] = useState<string[]>([])     // multi-select store filter
  const [selReps, setSelReps] = useState<string[]>([])         // RULE FIVE rep(s) multi (pick-don't-type)
  const [unmOpen, setUnmOpen] = useState(false)                // "see the unmatched transactions" viewer
  const [unm, setUnm] = useState<any>(null)                    // the unmatched blank-ct activation candidates
  const [unmBusy, setUnmBusy] = useState(false)

  // SEE THE UNMATCHED TRANSACTIONS (2026-08-14): the banner counts them; this lists the ACTUAL ones behind
  // it (department / category / product) so the owner knows exactly what blank-CT activation rule to write.
  // Read-only; reuses the SAME classifier the report banner uses (single source of truth).
  function openUnmatched() {
    setUnmOpen(true); setUnm(null); setUnmBusy(true)
    api(`/api/v1/commcalc/sales-report/classification-unmatched?period=${encodeURIComponent(period)}${orgParam()}`)
      .then(setUnm).catch(e => setUnm({ error: String(e?.message || e), by_line: [], transactions: [] }))
      .finally(() => setUnmBusy(false))
  }

  function openDiag() {
    setDiag({}); setDiagBusy(true)
    api(`/api/v1/commcalc/sales-diagnostics?period=${encodeURIComponent(period)}${orgParam()}`)
      .then(setDiag).catch(e => setDiag({ error: String(e?.message || e) }))
      .finally(() => setDiagBusy(false))
  }
  function openAccCfg() {
    setAccOpen(true); setAccFields(null); setAccMsg(''); setKwInput(''); setSetupInput('')
    api(`/api/v1/commcalc/sales-fields?period=${encodeURIComponent(period)}${orgParam()}`).then((f: any) => {
      setAccFields(f)
      setAccSel({ d: f.accessory_departments || [], c: f.accessory_categories || [], p: f.accessory_product_keywords || [],
        a: f.acima_tenders || [], box: f.box_departments || [], setup: f.setup_fee_keywords || [], billpay: f.billpay_products || [] })
      setCtMap(normalizeCtMap(f.contract_type_map || {}))
      setAccCanEdit(f.can_edit !== false)
      setBoxBuckets(Array.isArray(f.box_count_buckets) ? f.box_count_buckets : [])
      setCatOn(!!f.catalog_classify_enabled)
      setGpOn(!!f.apply_to_gp)
      setCatCats(f.catalog_accessory_categories || [])
      setCatOpts(f.catalog_categories || [])
    }).catch(e => setAccMsg('❌ ' + (e?.message || e)))
  }
  async function saveAccCfg() {
    setAccMsg('Saving…')
    // fold any half-typed keyword in either box into its list before saving
    const extra = kwInput.split(',').map(s => s.trim()).filter(Boolean)
    const kws = Array.from(new Set([...accSel.p, ...extra]))
    const setupExtra = setupInput.split(',').map(s => s.trim()).filter(Boolean)
    const setupKws = Array.from(new Set([...accSel.setup, ...setupExtra]))
    try {
      await api('/api/v1/commcalc/accessory-config', { method: 'PUT', body: JSON.stringify({
        departments: accSel.d, categories: accSel.c, product_keywords: kws, acima_tenders: accSel.a,
        box_departments: accSel.box, setup_fee_keywords: setupKws, contract_type_map: ctMap,
        billpay_products: accSel.billpay,
        box_count_buckets: boxBuckets,
        catalog_classify_enabled: catOn, catalog_accessory_categories: catCats,
        apply_to_gp: gpOn }) })
      setAccMsg('✅ Saved.'); setAccOpen(false); load()
    } catch (e: any) { setAccMsg('❌ ' + (e?.message || e)) }
  }
  const toggle = (arr: string[], v: string) => arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v]

  function openDrill(r: any) {
    setDrill(r); setDetail(null); setOpenTxn({}); setDrillBusy(true)
    const qs = new URLSearchParams({ period, store: r.store || '', salesperson: r.salesperson || '', date: r.trans_date || '' })
    api(`/api/v1/commcalc/sales-report/detail?${qs.toString()}${orgParam()}`)
      .then(setDetail).catch(e => setDetail({ transactions: [], error: String(e?.message || e) }))
      .finally(() => setDrillBusy(false))
  }

  const load = useCallback(() => {
    setLoading(true)
    api(`/api/v1/commcalc/sales-report?period=${encodeURIComponent(period)}${orgParam()}`)
      .then(setData).catch(e => setData({ rows: [], totals: {}, error: String(e?.message || e) }))
      .finally(() => setLoading(false))
  }, [period])
  useEffect(() => { load() }, [load])

  const rows: any[] = data?.rows || []
  const marketOpts: string[] = data?.markets || []
  const storeOpts: string[] = data?.stores || []
  // RULE FIVE rep(s) picker — options straight from the already-org-scoped rows (pick-don't-type §3b).
  const repOpts = useMemo(() => optionsFromRows(rows, { rep: (r: any) => r.salesperson }).reps, [rows])
  // Apply the multi-select market/store/rep filters in-memory (AND-composed); ReportShell's own By-rep
  // column dropdown still works, now narrowed to this filtered set.
  const fRows = rows.filter(r =>
    (selMarkets.length === 0 || selMarkets.includes(r.market)) &&
    (selStores.length === 0 || selStores.includes(r.store)) &&
    (selReps.length === 0 || selReps.includes(r.salesperson)))
  const filtered = selMarkets.length > 0 || selStores.length > 0 || selReps.length > 0
  // Tiles reflect the current filter (fall back to the backend period totals when nothing is filtered).
  const sum = (k: string) => fRows.reduce((s, r) => s + (Number(r[k]) || 0), 0)
  const t = filtered
    ? { revenue: sum('revenue'), gp: sum('gp'), accessory_rev: sum('accessory_rev'),
        txns: sum('txns'), activations: sum('activations'), byod: sum('byod'), upgrades: sum('upgrades'),
        swaps: sum('swaps') }
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
    { header: 'Swaps', get: r => r.swaps, align: 'right' },
    { header: 'Accessory $', get: r => r.accessory_rev, money: true },
    { header: 'Revenue $', get: r => r.revenue, money: true },
    { header: 'GP $', get: r => r.gp, money: true },
  ]

  // Drill-down export (RULE FOUR): one row per transaction LINE — what the modal shows. `Device` = the
  // phone sold on that transaction (repeated per line for a flat export), `Product` = the item on the line
  // (the phone itself on a device line; the accessory/feature on the others), plus SKU where present.
  const detailCols: ExportColumn[] = [
    { header: 'Trans ID', get: r => r.trans_id },
    { header: 'Date', get: r => r.trans_date, type: 'date' },
    { header: 'Customer', get: r => r.customer || '' },
    { header: 'Device', get: r => r.device || '' },
    { header: 'Department', get: r => r.department || '' },
    { header: 'Category', get: r => r.category || '' },
    { header: 'Contract', get: r => r.contract_type || '' },
    { header: 'Product', get: r => r.product || '' },
    { header: 'SKU', get: r => r.sku || '' },
    { header: 'MDN', get: r => r.mdn || '' },
    { header: 'Serial', get: r => r.serial || '' },
    { header: 'Price', get: r => r.ext_price, money: true },
    { header: 'GP', get: r => r.gp, money: true },
  ]
  const detailRows: any[] = (detail?.transactions || []).flatMap((t: any) =>
    (t.lines || []).map((l: any) => ({
      trans_id: t.trans_id, trans_date: t.trans_date, customer: t.customer, device: t.device,
      department: l.department, category: l.category, contract_type: l.contract_type,
      product: l.product, sku: l.sku, mdn: l.mdn, serial: l.serial, ext_price: l.ext_price, gp: l.gp,
    })))

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
        {repOpts.length > 0 && <EntityPicker multi options={repOpts} value={selReps} onChange={setSelReps} placeholder="Reps…" width={180} ariaLabel="Filter by rep" />}
        {filtered && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => { setSelMarkets([]); setSelStores([]); setSelReps([]) }}>Clear filters</button>}
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openDiag}>🔍 Data diagnostics</button>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={openAccCfg}>⚙️ Classification settings</button>
        <WhereAreMyRowsButton period={period} />
        {data?.source === 'daily_sales_feed' && <span style={{ fontSize: 11, color: '#b45309' }}>source: daily email feed (raw_sales not promoted yet — enable ‘auto’ on Connectors)</span>}
      </div>

      {/* PROMINENT error banner (was a tiny inline span that read as "no data"). A read failure now says so. */}
      {data?.error && (
        <div className="card" style={{ padding: '12px 16px', marginBottom: 14, background: '#fee2e2', color: '#991b1b', fontSize: 13 }}>
          <b>❌ Sales Report could not read this month.</b> {data.error}
          <div style={{ marginTop: 6 }}><WhereAreMyRowsButton period={period} /></div>
        </div>
      )}

      {/* TRANSPARENCY LINE (owner's debug-first mandate): exactly which source(s) this read used, how many
          rows each side holds, and — critically — which ORG it read from (a super-admin viewing a tenant
          whose reads default to the HOUSE org sees the mismatch here instead of a silent blank page). */}
      {data?.source_meta && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ background: 'var(--surface2)', borderRadius: 8, padding: '4px 10px' }}>
            Reading <b>{data.source_meta.primary === 'daily_sales_feed' ? 'daily email feed' : 'monthly raw_sales'}</b>
            {' '}({data.shown_rows ?? 0} rows shown · feed {data.feed_rows ?? 0} · raw_sales {data.raw_rows ?? 0})
            {(data.filled_days || []).length > 0 && <> · pulled <b>{(data.filled_days || []).length}</b> extra day(s) from raw_sales that the feed didn’t have</>}
            {(data.source_meta.completeness_rows ?? 0) > 0 && <> · recovered <b>{data.source_meta.completeness_rows}</b> sale line(s) present in raw_sales that the feed missed on a shared store-day</>}
          </span>
          {data.org_id && <span style={{ color: 'var(--text3)' }}>org <code style={{ fontSize: 11 }}>{String(data.org_id).slice(0, 8)}…</code></span>}
        </div>
      )}

      {/* Activation-classification VISIBILITY (mig 213/224): a tenant whose activations read 0 sees exactly
          WHY (blank / unrecognized contract types) and where to map them — never a silent 0 again. */}
      {data?.classification_gaps?.note && (
        <div style={{ fontSize: 12.5, background: 'var(--warn-bg, #fff6e5)', color: 'var(--warn-fg, #7a5200)',
                      border: '1px solid var(--warn-border, #f0d28a)', borderRadius: 8, padding: '8px 12px', marginBottom: 14 }}>
          ⚠️ {data.classification_gaps.note}
          {(data.classification_gaps.rescued_by_rules ?? 0) > 0 &&
            <> · <b>{data.classification_gaps.rescued_by_rules}</b> blank-contract-type transaction(s) already classified by your activation rules.</>}
          {/* Never a silent suppression: say how many blank-contract-type transactions were left OUT of the
              count because they could not have been activations (bill-payment / accessory-only receipts). */}
          {(data.classification_gaps.blank_ct_non_activation ?? 0) > 0 &&
            <> · <b>{data.classification_gaps.blank_ct_non_activation}</b> further blank-contract-type transaction(s) are
              bill-payment or accessory-only and are correctly <i>not</i> counted as activations.</>}
          {/* SEE THE UNMATCHED TRANSACTIONS: the count alone leaves the owner guessing what rule to write —
              this lists the actual unmatched transactions with the fields an activation rule matches on. */}
          {(data.classification_gaps.blank_ct_unrecovered ?? 0) > 0 && (
            <div style={{ marginTop: 6 }}>
              <button className="btn btn-secondary" style={{ fontSize: 12, padding: '2px 10px' }} onClick={openUnmatched}>
                🔎 See the {data.classification_gaps.blank_ct_unrecovered} unmatched transaction(s)
              </button>
            </div>
          )}
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 18 }}>
        <Tile label="Revenue" value={fmt(t.revenue || 0)} />
        <Tile label="Gross Profit" value={fmt(t.gp || 0)} />
        <Tile label="Accessory $" value={fmt(t.accessory_rev || 0)} />
        <Tile label="Transactions" value={String(t.txns || 0)} />
        <Tile label="Activations" value={String(t.activations || 0)} />
        <Tile label="BYOD" value={String(t.byod || 0)} />
        <Tile label="Upgrades" value={String(t.upgrades || 0)} />
        <Tile label="Swaps" value={String(t.swaps || 0)} />
      </div>

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : fRows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 60, color: 'var(--text3)' }}>
          {filtered
            ? <>No sales match the selected market/store/rep filter for {period}. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={() => { setSelMarkets([]); setSelStores([]); setSelReps([]) }}>Clear filters</span>.</>
            : <>No sales for {period}. Sales come from the imported Sales Transaction Details — check the month, or that the daily feed / monthly upload has loaded on the Imports pages.</>}
        </div>
      ) : (
        <ReportShell
          title={`Sales Report — ${period}`}
          subtitle={`${filtered ? `${fRows.length} filtered rows` : 'All stores'} · from Sales Transaction Details`}
          filename={`sales-report-${period.replace(/\s+/g, '-')}`}
          columns={cols}
          rows={fRows}
          totals
          stickyHeader
          defaultGroupBy="Store"
          collapsibleGroups
          defaultCollapsed
          groupPersistKey="sales-report:groupBy"
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
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {/* RULE FOUR: the drill-down line detail (incl. the phone sold + SKU) exports — what you see is what exports. */}
                {detailRows.length > 0 && (
                  <ReportExportBar
                    title={`Sales detail — ${drill.store} · ${drill.salesperson}`}
                    subtitle={`${drill.trans_date} · ${detail?.txn_count ?? 0} transaction${(detail?.txn_count ?? 0) === 1 ? '' : 's'}`}
                    filename={`sales-detail-${(drill.store || '').replace(/\s+/g, '-')}-${drill.trans_date || period}`}
                    columns={detailCols}
                    rows={detailRows}
                  />
                )}
                <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setDrill(null)}>✕</button>
              </div>
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
                        {/* Which phone was sold — visible without expanding (owner request 2026-07-17). */}
                        {t.device && <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text)' }}>📱 {t.device}</span>}
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
                                  <td style={{ padding: '5px 8px', fontWeight: l.is_device ? 600 : 400 }}>{l.is_device ? '📱 ' : ''}{l.product || '—'}{l.sku ? <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · {l.sku}</span> : ''}</td>
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

      {/* SEE THE UNMATCHED TRANSACTIONS — the actual blank-contract-type activation candidates behind the
          banner count, so the owner can read off exactly which department/category/product to write a
          blank-CT activation rule for. Read-only; the SAME classifier the banner uses. */}
      {unmOpen && (
        <div onClick={() => setUnmOpen(false)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <div onClick={e => e.stopPropagation()} className="card" style={{ background: 'var(--surface)', borderRadius: 12, padding: 20, width: 'min(860px,97vw)', maxHeight: '88vh', overflowY: 'auto' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
              <div style={{ fontSize: 16, fontWeight: 700 }}>🔎 Unmatched activation candidates</div>
              <button className="btn btn-secondary" style={{ padding: '2px 10px' }} onClick={() => setUnmOpen(false)}>✕</button>
            </div>
            <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 12px' }}>
              These are the blank-contract-type transactions that <b>could</b> be activations (they have a real sale line, not just a bill payment or accessory) but no <b>Contract type → activation bucket</b> map entry and no <b>blank-contract-type activation rule</b> matched — so they currently count as <b>0</b> activations. Find the <b>Department / Category / Product</b> pattern that identifies a real activation below, then add a blank-contract-type activation rule for it (e.g. <i>Department contains</i> <code>BrandedHandset</code> <i>and</i> <code>Rtr</code> → <b>Activation</b>). Read-only; nothing here changes a number until you add the rule.
            </p>
            {!unm ? (
              <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>{unmBusy ? 'Loading…' : ' '}</div>
            ) : unm.error ? (
              <div style={{ background: '#fef2f2', border: '1px solid #fecaca', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#991b1b' }}>❌ {unm.error}</div>
            ) : (unm.by_line || []).length === 0 ? (
              <div style={{ padding: 20, textAlign: 'center', color: 'var(--text3)', fontSize: 13 }}>No unmatched activation candidates for this period — nothing to map. ✅</div>
            ) : (
              <>
                <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 8 }}>
                  <b>{unm.counts?.blank_ct_unrecovered ?? 0}</b> unmatched transaction(s) · grouped by the fields a rule matches on (most frequent first). The counts here reconcile with the report banner.
                </div>
                <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 8 }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                    <thead>
                      <tr style={{ background: 'var(--surface2)', textAlign: 'left' }}>
                        {['Store', 'Department', 'Category', 'Product', 'Txns', 'Lines'].map(h => (
                          <th key={h} style={{ padding: '6px 10px', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(unm.by_line || []).map((r: any, i: number) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--border)', background: i % 2 ? 'var(--surface2)' : undefined }}>
                          <td style={{ padding: '5px 10px', whiteSpace: 'nowrap' }}>{r.store || '—'}</td>
                          <td style={{ padding: '5px 10px' }}><code style={{ fontSize: 11 }}>{r.department || '(blank)'}</code></td>
                          <td style={{ padding: '5px 10px' }}><code style={{ fontSize: 11 }}>{r.category || '(blank)'}</code></td>
                          <td style={{ padding: '5px 10px' }}>{r.product_desc || '(blank)'}</td>
                          <td style={{ padding: '5px 10px', textAlign: 'right' }}>{r.transactions}</td>
                          <td style={{ padding: '5px 10px', textAlign: 'right', color: 'var(--text3)' }}>{r.lines}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {(unm.transactions || []).length > 0 && (
                  <details style={{ marginTop: 12 }}>
                    <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>Show individual transactions ({(unm.transactions || []).length})</summary>
                    <div style={{ marginTop: 8, maxHeight: 260, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {(unm.transactions || []).map((t: any) => (
                        <div key={t.trans_id} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '6px 10px' }}>
                          <div style={{ fontSize: 12, fontWeight: 600 }}>{t.store || '—'} · {t.trans_date || ''} · <span style={{ color: 'var(--text3)', fontWeight: 400 }}>txn {t.trans_id}</span></div>
                          <div style={{ marginTop: 3, display: 'flex', flexDirection: 'column', gap: 2 }}>
                            {(t.lines || []).map((l: any, j: number) => (
                              <div key={j} style={{ fontSize: 11.5, color: 'var(--text2)' }}>
                                <code style={{ fontSize: 11 }}>{l.department || '(blank)'}</code> / <code style={{ fontSize: 11 }}>{l.category || '(blank)'}</code> — {l.product_desc || '(blank)'}
                              </div>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
                <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 12, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
                  Note: tender type is not shown — the Sales Report reads a projection without it, and activation rules match on Department / Category / Product / Transaction type (the columns above).
                </div>
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
                {!accCanEdit && (
                  <div style={{ background: '#fef9c3', border: '1px solid #fde047', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#92400e', marginBottom: 10 }}>
                    🔒 Read-only. Editing Classification settings requires the <b>Classification settings</b> permission — ask an administrator to grant it (Roles &rarr; settings permissions).
                  </div>
                )}
                <div style={{ pointerEvents: accCanEdit ? 'auto' : 'none', opacity: accCanEdit ? 1 : 0.6 }}>
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
                {/* BOX (device-unit) departments — drives the box count on Productivity/Ranking/Review,
                    Daily-Targets conversion, and the Sales-Report box count. Tick the POS Departments that
                    are a device "box". Multi-carrier orgs (e.g. Total Wireless IN the house org) must tick
                    the NON-Boost device departments too, or those device sales don't count as boxes. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Box (device-unit) departments <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(which Department values count as a device &ldquo;box&rdquo; — for productivity boxes/hr, stack ranking, review &amp; conversion. Default = the Boost XP departments; a multi-carrier org must ALSO tick its Total/other device departments.)</span></div>
                  <div style={{ maxHeight: 200, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                    {(accFields.departments || []).length === 0 ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>no departments in this period</div> : (accFields.departments || []).map((v: string) => (
                      <label key={v} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, padding: '2px 0' }}>
                        <input type="checkbox" checked={accSel.box.includes(v)}
                          onChange={() => setAccSel(s => ({ ...s, box: toggle(s.box, v) }))} />
                        {v}
                      </label>
                    ))}
                  </div>
                </div>
                {/* BILL-PAYMENT items — which product/item values count as a bill payment (walk-in recharge)
                    for the Daily-Targets CONVERSION metric (boxes ÷ bill-payments). Pick from the OBSERVED
                    product descriptions in the sales data (RULE THREE — matched EXACTLY, not typed). Leave
                    empty to fall back to the built-in Boost defaults (Boost RTR / Xfinity Prepaid Refill).
                    DISPLAY only — drives conversion, never a payout. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Bill-payment items <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(tick the product/item values that = a bill payment / walk-in recharge — drives the Daily-Targets conversion rate, boxes &divide; bill-payments. Leave empty to use the built-in Boost defaults. Display only, no pay change.)</span></div>
                  <div style={{ fontSize: 11, marginBottom: 4, color: accSel.billpay.length === 0 ? 'var(--accent)' : 'var(--text3)' }}>
                    {accSel.billpay.length === 0
                      ? <>Currently using <b>Boost defaults</b> (product name contains &ldquo;Boost RTR&rdquo; or &ldquo;Xfinity Prepaid Refill&rdquo;).</>
                      : <><b>{accSel.billpay.length}</b> item(s) active — only these count as bill payments.</>}
                  </div>
                  <div style={{ maxHeight: 200, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                    {(() => {
                      const opts = Array.from(new Set([...(accFields.billpay_product_options || []), ...accSel.billpay]))
                      return opts.length === 0
                        ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>no products in this period</div>
                        : opts.map((v: string) => (
                          <label key={v} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, padding: '2px 0' }}>
                            <input type="checkbox" checked={hasCI(accSel.billpay, v)}
                              onChange={() => setAccSel(s => ({ ...s, billpay: toggleCI(s.billpay, v) }))} />
                            {v}
                          </label>
                        ))
                    })()}
                  </div>
                </div>
                {/* CONTRACT TYPE -> activation bucket — map the tenant's OBSERVED Contract Type values to
                    the activation buckets so a Total/non-Boost POS whose labels differ from the built-in
                    keyword set still counts activations/upgrades/BYOD on the Sales Report, Exec MTD & Daily
                    Targets. 'Auto' = built-in classifier; 'Not an activation' excludes a label. DISPLAY only. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Contract type &rarr; activation bucket <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(map each Contract Type value your POS uses to an activation bucket &mdash; for tenants whose labels differ from the built-in Activation / Port-In / Upgrade / BYOD set. &ldquo;Auto&rdquo; uses the built-in classifier; &ldquo;Not an activation&rdquo; excludes a label. Drives the Sales Report, Executive MTD &amp; Daily-Targets activation / upgrade counts &mdash; display only, no pay change.)</span></div>
                  <div style={{ maxHeight: 240, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                    {(() => {
                      // Case-insensitive keys (f3): each row's select reads/writes ctMap[<lowercased label>].
                      // Rows = the period's observed contract types PLUS any ORPHANED mapped keys (mapped
                      // labels no longer present in the observed values) so they stay visible + un-mappable.
                      const obs: string[] = accFields.contract_types || []
                      const obsLower = new Set(obs.map(v => v.trim().toLowerCase()))
                      const orphans = Object.keys(ctMap).filter(k => !obsLower.has(k))
                      const rows = [
                        ...obs.map(v => ({ label: v, key: v.trim().toLowerCase(), orphan: false })),
                        ...orphans.map(k => ({ label: k, key: k, orphan: true })),
                      ]
                      if (rows.length === 0) return <div style={{ fontSize: 12, color: 'var(--text3)' }}>no contract types in this period</div>
                      return rows.map(r => (
                        <div key={r.key} style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between', fontSize: 13, padding: '3px 0' }}>
                          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.label}>
                            {r.label}{r.orphan && <span style={{ color: 'var(--text3)', fontSize: 11 }}> (mapped · not in this period)</span>}
                          </span>
                          <select style={{ ...sel, width: 190, flex: 'none' }} value={ctMap[r.key] || ''}
                            onChange={e => setCtMap(m => { const n = { ...m }; if (e.target.value) n[r.key] = e.target.value; else delete n[r.key]; return n })}>
                            <option value="">Auto (built-in)</option>
                            <option value="premium">Activation</option>
                            <option value="upgrade">Upgrade</option>
                            <option value="byod">BYOD</option>
                            <option value="none">Not an activation</option>
                          </select>
                        </div>
                      ))
                    })()}
                  </div>
                </div>
                {/* Device SET-UP FEE keywords — product-desc substrings counted toward the accessory TARGET
                    (reported separately). Default 'Device Setup Charge'. */}
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>Device set-up fee — product name contains… <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(these lines count toward the accessory TARGET and are reported separately; default <code>Device Setup Charge</code>)</span></div>
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
                    {accSel.setup.map(k => (
                      <span key={k} style={{ display: 'inline-flex', gap: 4, alignItems: 'center', background: 'var(--surface2)', borderRadius: 12, padding: '2px 8px', fontSize: 12 }}>
                        {k}<span style={{ cursor: 'pointer', color: '#dc2626', fontWeight: 700 }} onClick={() => setAccSel(s => ({ ...s, setup: s.setup.filter(x => x !== k) }))}>✕</span>
                      </span>
                    ))}
                    {accSel.setup.length === 0 && <span style={{ fontSize: 12, color: 'var(--text3)' }}>none</span>}
                  </div>
                  <input style={{ ...sel, width: '100%' }} placeholder="e.g. Device Setup Charge, Set Up Fee (comma-separated) — Enter to add"
                    value={setupInput} onChange={e => setSetupInput(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') { const xs = setupInput.split(',').map(s => s.trim()).filter(Boolean); setAccSel(s => ({ ...s, setup: Array.from(new Set([...s.setup, ...xs])) })); setSetupInput('') } }} />
                  {(accFields.products || []).length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Products seen on non-phone lines (click to add):</div>
                      <div style={{ maxHeight: 90, overflowY: 'auto', display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                        {(accFields.products || []).slice(0, 40).map((p: string) => (
                          <span key={p} style={{ cursor: 'pointer', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 10, padding: '2px 7px', fontSize: 11 }}
                            onClick={() => setAccSel(s => ({ ...s, setup: s.setup.includes(p) ? s.setup : [...s.setup, p] }))}>{p}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                {/* BYOD → total boxes (mig 231). Owner 2026-07-24: the customer-phone / BYOD activation must
                    count toward "total boxes sold". Adds each BYOD transaction to the box count across the
                    Sales-Report box count, Daily-Targets conversion + attainment, and Productivity/Review. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, fontWeight: 700 }}>
                    <input type="checkbox" checked={boxBuckets.includes('byod')}
                      onChange={() => setBoxBuckets(b => b.includes('byod') ? b.filter(x => x !== 'byod') : [...b, 'byod'])} />
                    Count BYOD / customer-phone toward total boxes sold
                  </label>
                  <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
                    A BYOD activation (customer brings their own phone) has no device box, so it isn&apos;t counted as a &ldquo;box&rdquo; by default. Tick this to count each BYOD activation as one box — it flows to the box count everywhere (Daily Targets, Productivity, stack ranking). Requires BYOD to be classified (Contract-type &rarr; activation bucket, or blank-CT activation rules). <b>Money-adjacent</b> only if a plan pays on box targets — re-run Calculate to apply.
                  </div>
                </div>
                {/* GP-report adoption (mig 250): the GP page's Acc GP / Phone Sales buckets classify through
                    THESE rules instead of the department-only Boost defaults. Off = legacy, byte-identical. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, fontWeight: 700 }}>
                    <input type="checkbox" checked={gpOn} onChange={e => setGpOn(e.target.checked)} />
                    Use these rules for the Gross-Profit report buckets
                  </label>
                  <div style={{ fontSize: 11, color: 'var(--text3)', margin: '4px 0 8px' }}>
                    When on, the <a href="/commcalc/gp" style={{ color: 'var(--accent)' }}>GP report</a> counts a line as <b>Acc GP</b> using the accessory rules above (department, category, keyword, catalog) and as <b>Phone Sales</b> using the box departments — instead of the built-in Boost department labels. Turn this on when your POS departments don&apos;t match the Boost names (e.g. a Total feed where the same department holds both phones and accessories). Display-only: rep pay never reads it.
                  </div>
                </div>
                {/* CATALOG-driven accessory classification (migs 230/231). A product-catalog upload's category
                    labels a product as an accessory even when the sales line's own department/category are
                    blank. Additive — never removes a legacy accessory. */}
                <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--border)' }}>
                  <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13, fontWeight: 700 }}>
                    <input type="checkbox" checked={catOn} onChange={e => setCatOn(e.target.checked)} />
                    Use the product catalog to classify accessories
                  </label>
                  <div style={{ fontSize: 11, color: 'var(--text3)', margin: '4px 0 8px' }}>
                    When on, a sale line whose product matches a catalog row carrying an accessory category counts as accessory sales (in addition to the department/category/keyword rules above). Upload the catalog under <a href="/commcalc/upload" style={{ color: 'var(--accent)' }}>Data Imports → Product Catalog</a>; recategorize items at <a href="/commcalc/catalog" style={{ color: 'var(--accent)' }}>Catalog Categories</a>. <b>Money-adjacent</b>: widens accessory revenue/target and (via an Incentive Plan rule keyed on <code>accessory</code>) accessory pay — re-run Calculate to apply.
                  </div>
                  {catOn && (
                    <div>
                      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>Catalog categories that count as accessory <span style={{ fontWeight: 400, color: 'var(--text3)' }}>(default: <code>Accessories</code>)</span></div>
                      <div style={{ maxHeight: 160, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 8, padding: 8 }}>
                        {(Array.from(new Set([...(catOpts || []), ...catCats]))).length === 0
                          ? <div style={{ fontSize: 12, color: 'var(--text3)' }}>no catalog uploaded yet — upload one first</div>
                          : Array.from(new Set([...(catOpts || []), ...catCats])).sort().map((v: string) => (
                            <label key={v} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13, padding: '2px 0' }}>
                              <input type="checkbox" checked={catCats.includes(v)}
                                onChange={() => setCatCats(cs => cs.includes(v) ? cs.filter(x => x !== v) : [...cs, v])} />
                              {v}
                            </label>
                          ))}
                      </div>
                    </div>
                  )}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 12, gap: 8 }}>
                  <span style={{ fontSize: 12, color: accMsg.startsWith('❌') ? '#dc2626' : 'var(--text3)' }}>{accMsg}</span>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <button className="btn btn-secondary" style={{ fontSize: 13 }} disabled={!accCanEdit} onClick={() => { setAccSel({ d: [], c: [], p: [], a: [], box: [], setup: [], billpay: [] }); setKwInput(''); setSetupInput('') }}>Clear all</button>
                    <button className="btn btn-primary" style={{ fontSize: 13 }} disabled={!accCanEdit} onClick={saveAccCfg}>Save</button>
                  </div>
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
