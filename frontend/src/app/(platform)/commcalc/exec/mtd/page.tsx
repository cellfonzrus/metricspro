'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, getActiveOrg, localToday } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { MultiSelect } from '@/lib/multiselect'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Super-admin org-resolution mitigation (same as the Sales Report page): reads carry the active tenant
// so a super-admin (whom the tenant middleware does NOT rewrite) reads the selected tenant, not the house
// org. No-op for normal users (the middleware overrides org_id to their membership) and when no tenant is
// selected. RULE FIVE: this page's filter bar mirrors the Sales Report's — same MultiSelect UX.
const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }

// Executive MTD summary — replicates b2bsoft's "Month To Date Location / Employee Sales Report".
// Reads the org-corrected sales source (feed for the open month, raw_sales for a closed one) so it works
// for luxelink AND the house org with NO monthly upload. DISPLAY-ONLY.

const th: React.CSSProperties = { textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

const pct = (n: number) => `${((n || 0) * 100).toFixed(1)}%`
const n2 = (n: number) => Number(n || 0).toFixed(2)
const int = (n: number) => String(Math.round(n || 0))

export default function ExecMtdPage() {
  const { period } = usePeriod()
  const [data, setData] = useState<any>(null)
  const [tab, setTab] = useState<'location' | 'employee'>('location')
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [showCfg, setShowCfg] = useState(false)
  // RULE FIVE standardized filters — store(s) / market(s) / rep(s) multi-select, applied SERVER-SIDE.
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])

  const load = useCallback(() => {
    if (!period) return
    setLoading(true); setErr(null)
    const qs = new URLSearchParams()
    // Send the BROWSER's local date, exactly like the Daily/Accessory Targets pages do. Without it the
    // MTD cut + trending divisor were resolved on the server's UTC clock, so after ~8pm ET the two
    // surfaces projected off a different "complete days elapsed" and could not agree.
    qs.set('today', localToday())
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    selReps.forEach((s) => qs.append('reps', s))
    const q = qs.toString()
    api(`/api/v1/commcalc/exec-mtd/${encodeURIComponent(period)}?${q}${orgParam()}`)
      .then(setData).catch((e) => setErr(String(e?.message || e))).finally(() => setLoading(false))
  }, [period, selStores, selMarkets, selReps])
  useEffect(() => { load() }, [load])

  // Options are computed by the backend from the UNFILTERED union (pick-don't-type over real data).
  const opt = data?.filters || {}
  const storeOpts: string[] = opt.stores || []
  const marketOpts: string[] = opt.markets || []
  const repOpts: string[] = opt.reps || []
  const hasFilter = selStores.length > 0 || selMarkets.length > 0 || selReps.length > 0
  const clearFilters = () => { setSelStores([]); setSelMarkets([]); setSelReps([]) }
  const src = data?.source || {}

  const active = tab === 'location' ? data?.by_location : data?.by_employee
  const labelKey = tab === 'location' ? 'store' : 'employee'
  const labelHdr = tab === 'location' ? 'Store' : 'Employee'
  const rows: any[] = active?.rows || []
  const total = active?.total || {}
  const tr = data?.trending || {}

  // 16-column layout, in the exact order of the owner's spreadsheet, THEN two appended reconciliation
  // columns (the spreadsheet's own order is preserved). Conv. exported as the raw ratio (as the file
  // stores it); money columns flagged so Excel/PDF format + subtotal correctly.
  // WHY the two extra columns (2026-07-30): "Acc. Sales" here is the PURE accessory$ — the same number
  // the Sales Report shows and the same one this b2bsoft report has always meant. The Accessory Targets
  // page measures achieved/target on accessory$ + the device SET-UP FEE (owner directive 2026-07-17:
  // the set-up fee is a separate PAY item, never folded into accessory$, but it DOES count toward the
  // accessory TARGET). Both come from the one shared classifier; showing the bridge (Set-up Fee) and
  // the target basis (Acc.+Set-up) is what lets the two pages be reconciled to the cent.
  const cols = (lk: string): ExportColumn[] => [
    { header: lk === 'store' ? 'Store' : 'Employee', field: lk, role: lk === 'store' ? 'store' : 'rep', get: (r) => r[lk] },
    { header: 'Total Activation', field: 'total_activation', type: 'number', get: (r) => r.total_activation },
    { header: 'Activation', field: 'activation', type: 'number', get: (r) => r.activation },
    { header: 'Port', field: 'port', type: 'number', get: (r) => r.port },
    { header: 'BYOD', field: 'byod', type: 'number', get: (r) => r.byod },
    { header: 'Upgrade', field: 'upgrade', type: 'number', get: (r) => r.upgrade },
    { header: 'Total Phones', field: 'total_phones', type: 'number', get: (r) => r.total_phones },
    { header: 'Trending Box', field: 'trending_box', type: 'number', get: (r) => r.trending_box },
    { header: 'Bill Payment Qty', field: 'bill_payment_qty', type: 'number', get: (r) => r.bill_payment_qty },
    { header: '$', field: 'amount', money: true, get: (r) => r.amount },
    { header: 'Conv.', field: 'conv', type: 'number', get: (r) => r.conv },
    { header: 'Acc. Sales', field: 'acc_sales', money: true, get: (r) => r.acc_sales },
    { header: 'APB', field: 'apb', type: 'number', get: (r) => r.apb },
    { header: 'Trending Acc. Sales', field: 'trending_acc_sales', money: true, get: (r) => r.trending_acc_sales },
    { header: 'Activation Fee', field: 'activation_fee', money: true, get: (r) => r.activation_fee },
    { header: 'Total Protect', field: 'total_protect', type: 'number', get: (r) => r.total_protect },
    { header: 'Set-up Fee', field: 'setup_fee', money: true, get: (r) => r.setup_fee },
    { header: 'Acc.+Set-up (target basis)', field: 'acc_plus_setup', money: true, get: (r) => r.acc_plus_setup },
  ]
  // Export BOTH tabs (like the file's two sheets), each with its own totals row appended.
  const withTotal = (rs: any[], tot: any, lk: string) => [...rs, { ...tot, [lk]: 'TOTAL' }]
  const exportSheets = [
    { name: 'By location', columns: cols('store'), rows: withTotal(data?.by_location?.rows || [], data?.by_location?.total || {}, 'store') },
    { name: 'By employee', columns: cols('employee'), rows: withTotal(data?.by_employee?.rows || [], data?.by_employee?.total || {}, 'employee') },
  ]

  const HEADERS = ['Total Activation', 'Activation', 'Port', 'BYOD', 'Upgrade', 'Total Phones', 'Trending Box',
    'Bill Payment Qty', '$', 'Conv.', 'Acc. Sales', 'APB', 'Trending Acc. Sales', 'Activation Fee', 'Total Protect',
    'Set-up Fee', 'Acc.+Set-up']
  // Tooltips only on the two appended reconciliation columns (the b2bsoft 15 are unchanged).
  const HEADER_TIPS: Record<string, string> = {
    'Acc. Sales': 'Accessory sales revenue ONLY — the device set-up fee is excluded (it is a separate pay item). Same number as the Sales Report.',
    'Set-up Fee': 'Device set-up fee sold. A separate pay item, so it is NOT in Acc. Sales — but it DOES count toward the accessory target.',
    'Acc.+Set-up': 'Accessory sales + device set-up fee = the basis the Accessory Targets page measures achieved vs target on. THIS is the number to compare with that page.',
  }

  const cellVals = (r: any) => [
    int(r.total_activation), int(r.activation), int(r.port), int(r.byod), int(r.upgrade), int(r.total_phones),
    int(r.trending_box), int(r.bill_payment_qty), fmt(r.amount), pct(r.conv), fmt(r.acc_sales), n2(r.apb),
    fmt(r.trending_acc_sales), fmt(r.activation_fee), int(r.total_protect),
    fmt(r.setup_fee), fmt(r.acc_plus_setup),
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 21, fontWeight: 700, margin: 0 }}>📈 Executive MTD — {period}</h1>
          <Link href="/commcalc/exec" style={{ fontSize: 12 }}>← Owner Overview</Link>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0' }}>
          Month-to-date location &amp; employee sales — activations by type, phones, bill payments, accessory
          sales, and trending projections. {tr.factor ? `Trending = MTD × ${tr.days_in_month} ÷ ${tr.elapsed_days} complete days.` : ''}
        </p>
      </div>

      {/* RULE FIVE standardized filter bar — store(s) / market(s) / rep(s), pick-don't-type over the org's
          real data, applied server-side so tables, trending AND exports all reflect the same selection. */}
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        {storeOpts.length > 0 && <MultiSelect allLabel="All stores" width={150} value={selStores} options={storeOpts} onChange={setSelStores} searchable />}
        {marketOpts.length > 0 && <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={marketOpts} onChange={setSelMarkets} />}
        {repOpts.length > 0 && <MultiSelect allLabel="All employees" width={150} value={selReps} options={repOpts} onChange={setSelReps} searchable />}
        {hasFilter && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={clearFilters}>Clear filters</button>}
      </div>

      {/* Source-coverage transparency (owner's debug-first mandate): which source led the union and how
          many stores it surfaced — so a partial-feed month that hid stores is self-evident here. */}
      {(src.stores_shown != null) && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
          <span style={{ background: 'var(--surface2)', borderRadius: 8, padding: '4px 10px' }}>
            Reading <b>{src.primary === 'daily_sales_feed' ? 'daily email feed' : 'monthly raw_sales'}</b>
            {' '}· <b>{src.stores_shown}</b> store{src.stores_shown === 1 ? '' : 's'} shown
            {' '}(feed {src.feed_rows ?? 0} · raw_sales {src.raw_rows ?? 0} rows)
            {src.stores_from_other > 0 && <> · <b>{src.stores_from_other}</b> store{src.stores_from_other === 1 ? '' : 's'} pulled from {src.other === 'raw_sales' ? 'raw_sales' : 'the feed'} that the primary didn’t carry</>}
            {src.filled_cells > 0 && <> · {src.filled_cells} filled + {src.richer_cells || 0} richer store-day cell(s)</>}
          </span>
        </div>
      )}

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
          {(['location', 'employee'] as const).map((tb) => (
            <button key={tb} onClick={() => setTab(tb)}
              style={{ padding: '6px 14px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
                background: tab === tb ? 'var(--accent)' : 'transparent', color: tab === tb ? '#fff' : 'var(--text2)' }}>
              {tb === 'location' ? 'By location' : 'By employee'}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <button onClick={() => setShowCfg((s) => !s)} style={{ fontSize: 12, background: 'none', border: '1px solid var(--border)', borderRadius: 6, padding: '5px 10px', cursor: 'pointer', color: 'var(--text2)' }}>
            ⚙︎ Metric definitions
          </button>
          <ReportExportBar title={`Executive MTD ${period}`} filename={`exec_mtd_${String(period).replace(/\s+/g, '_')}`} sheets={exportSheets} />
        </div>
      </div>

      {showCfg && <MetricConfigPanel onClose={() => setShowCfg(false)} onSaved={load} />}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
      ) : err ? (
        <div className="card" style={{ padding: 16, borderLeft: '3px solid #b42318', color: '#b42318', fontSize: 13 }}>Could not load MTD summary: {err}</div>
      ) : rows.length === 0 ? (
        <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
          {hasFilter
            ? <>No sales match the selected filter for {period}. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={clearFilters}>Clear filters</span>.</>
            : <>No sales for {period}. (If the daily feed is ingesting but this is empty, run the sales promotion —
              POST /commcalc/sales/promote-due — or check the tenant&apos;s sales mailbox.)</>}
        </div>
      ) : (
        <div className="card table-wrapper" style={{ padding: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr style={{ background: 'var(--surface2)' }}>
              <th style={thL}>{labelHdr}</th>
              {HEADERS.map((h) => <th key={h} style={th} title={HEADER_TIPS[h]}>{h}</th>)}
            </tr></thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i}>
                  <td style={{ ...tdL, fontWeight: 600 }}>{r[labelKey] || '—'}</td>
                  {cellVals(r).map((v, j) => <td key={j} style={td}>{v}</td>)}
                </tr>
              ))}
              <tr style={{ background: 'var(--surface2)', fontWeight: 700 }}>
                <td style={{ ...tdL, fontWeight: 800 }}>TOTAL</td>
                {cellVals(total).map((v, j) => <td key={j} style={{ ...td, fontWeight: 700 }}>{v}</td>)}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {!loading && !err && rows.length > 0 && (
        <p style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12, maxWidth: 900 }}>
          <b>Two accessory numbers, on purpose.</b> <b>Acc. Sales</b> is accessory sales revenue only —
          the device <b>set-up fee</b> is a separate pay item and is never folded into it (owner
          directive 2026‑07‑17), which is why it has its own column. The{' '}
          <a href="/commcalc/targets/accessories">Accessory Targets</a> page measures achieved‑vs‑target
          on <b>Acc.+Set‑up</b> — so compare that column, not Acc. Sales, when the two pages look
          different. Both bases come from the SAME shared classifier and the same sales rows as the
          Sales Report; <b>APB</b> and <b>Trending Acc. Sales</b> stay on the pure Acc. Sales basis.
        </p>
      )}
    </div>
  )
}

// ── Admin-editable metric definitions (SAP-configurable; no hard-coded classifier) ──────────────────
function MetricConfigPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [cfg, setCfg] = useState<any>(null)
  const [saving, setSaving] = useState<string | null>(null)
  useEffect(() => { api('/api/v1/commcalc/exec-metric-config').then(setCfg).catch(console.error) }, [])
  const BUCKET_KEYS: Record<string, string[]> = {
    activation: ['byod', 'upgrade', 'port'],
    phones: ['category'],
    bill_payment: ['department', 'category'],
    accessory: ['category'],
    activation_fee: ['product_desc_contains'],
    protect: ['product_desc_contains', 'exclude_product_desc_contains', 'exclude_department', 'exclude_category'],
  }
  const setTok = (bucket: string, key: string, csv: string) => {
    setCfg((c: any) => {
      const nc = { ...c, config: { ...c.config, [bucket]: { ...c.config[bucket], rules: { ...c.config[bucket].rules, [key]: csv.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean) } } } }
      return nc
    })
  }
  const save = (bucket: string) => {
    setSaving(bucket)
    api('/api/v1/commcalc/exec-metric-config', { method: 'PUT', body: JSON.stringify({ bucket, rules: cfg.config[bucket].rules, basis: cfg.config[bucket].basis }) })
      .then(() => onSaved()).catch(console.error).finally(() => setSaving(null))
  }
  if (!cfg) return <div className="card" style={{ padding: 16, marginBottom: 12 }}>Loading definitions…</div>
  return (
    <div className="card" style={{ padding: 16, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Metric definitions (config — comma-separated tokens, case-insensitive)</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text3)' }}>✕</button>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        These map raw sales lines to the MTD buckets. Tokens match the stored <code>department</code> /
        <code>category</code> (a b2bsoft export stores either the Category or the System Category column) or
        a substring of <code>product_desc</code>. Editing here does NOT touch commission pay.
      </p>
      <p style={{ fontSize: 12, color: 'var(--text2)', margin: '0 0 10px', padding: '7px 9px', background: 'var(--surface2)', borderRadius: 6 }}>
        ℹ️ <b>Activation type</b> (Activation/BYOD/Upgrade counts) and <b>Accessory $</b> now flow from the
        shared Sales Report classifier + the Sales Report’s <b>Classification settings</b> — counted by
        <b> distinct transaction</b> — so Executive MTD, the Sales Report and Daily Targets always agree.
        The tokens below still drive the <b>Port</b> split and the per-line columns
        (<b>Total Phones / Bill Payment / Activation Fee / Total Protect</b>); the <code>activation</code>
        byod/upgrade and <code>accessory</code> tokens no longer change the headline counts.
      </p>
      {(cfg.buckets as string[]).map((b) => (
        <div key={b} style={{ borderTop: '1px solid var(--border)', padding: '8px 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{b} <span style={{ color: 'var(--text3)', fontWeight: 400 }}>({cfg.config[b].basis})</span></div>
            <button onClick={() => save(b)} disabled={saving === b} style={{ fontSize: 12, padding: '3px 10px', cursor: 'pointer' }}>{saving === b ? 'Saving…' : 'Save'}</button>
          </div>
          {(BUCKET_KEYS[b] || Object.keys(cfg.config[b].rules)).map((k) => (
            <label key={k} style={{ display: 'grid', gridTemplateColumns: '190px 1fr', gap: 8, alignItems: 'center', margin: '4px 0' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{k}</span>
              <input value={(cfg.config[b].rules[k] || []).join(', ')} onChange={(e) => setTok(b, k, e.target.value)}
                style={{ fontSize: 12, padding: '4px 8px', border: '1px solid var(--border)', borderRadius: 6 }} />
            </label>
          ))}
        </div>
      ))}
    </div>
  )
}
