'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import { api, fmt, getActiveOrg } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { MultiSelect } from '@/lib/multiselect'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'

// Productivity · Stack Ranking · Performance Review — mod-commission, NON-money (display/analytics).
// Feature 1: output per hour worked (StoreOps time-clock) vs the store's own baseline. Feature 2: weighted
// stack ranking. Feature 3: performance-review scorecard. ONE unified per-org item registry (⚙️ Config)
// drives both the ranker and the review. RULE FIVE filter bar + RULE FOUR exports throughout.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const orgQ = () => { const o = getActiveOrg(); return o ? `?org_id=${encodeURIComponent(o)}` : '' }

const th: React.CSSProperties = { textAlign: 'right', padding: '7px 8px', fontSize: 11, fontWeight: 600, color: 'var(--text2)', whiteSpace: 'nowrap' }
const thL: React.CSSProperties = { ...th, textAlign: 'left' }
const td: React.CSSProperties = { textAlign: 'right', padding: '6px 8px', borderTop: '1px solid var(--border)', fontSize: 12.5, whiteSpace: 'nowrap' }
const tdL: React.CSSProperties = { ...td, textAlign: 'left' }

const n2 = (n: any) => (n == null ? '—' : Number(n).toFixed(2))
const n3 = (n: any) => (n == null ? '—' : Number(n).toFixed(3))
const pct = (n: any) => (n == null ? '—' : `${Number(n).toFixed(1)}%`)
const idx = (n: any) => (n == null ? '—' : `${Number(n).toFixed(2)}×`)

type Tab = 'productivity' | 'ranking' | 'review' | 'config'

export default function ProductivityPage() {
  const { period } = usePeriod()
  const [tab, setTab] = useState<Tab>('productivity')
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState<string | null>(null)
  const [selStores, setSelStores] = useState<string[]>([])
  const [selMarkets, setSelMarkets] = useState<string[]>([])
  const [selReps, setSelReps] = useState<string[]>([])

  const endpoint = tab === 'ranking' ? 'productivity/rankings' : tab === 'review' ? 'productivity/review' : 'productivity'

  const load = useCallback(() => {
    if (!period || tab === 'config') return
    setLoading(true); setErr(null)
    const qs = new URLSearchParams()
    selStores.forEach((s) => qs.append('stores', s))
    selMarkets.forEach((s) => qs.append('markets', s))
    selReps.forEach((s) => qs.append('reps', s))
    api(`/api/v1/commcalc/${endpoint}/${encodeURIComponent(period)}?${qs.toString()}${orgParam()}`)
      .then(setData).catch((e) => setErr(String(e?.message || e))).finally(() => setLoading(false))
  }, [period, tab, endpoint, selStores, selMarkets, selReps])
  useEffect(() => { load() }, [load])

  const opt = data?.filters || {}
  const hasFilter = selStores.length + selMarkets.length + selReps.length > 0
  const clearFilters = () => { setSelStores([]); setSelMarkets([]); setSelReps([]) }

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 21, fontWeight: 700, margin: 0 }}>🏅 Productivity &amp; Reviews — {period}</h1>
          <Link href="/commcalc/kpi" style={{ fontSize: 12 }}>← KPI Metrics</Link>
        </div>
        <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 0' }}>
          Output per hour worked vs each store&apos;s baseline · weighted stack ranking · performance review.
          Display-only — nothing here changes commission pay.
        </p>
      </div>

      <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', marginBottom: 12 }}>
        {([['productivity', 'Productivity'], ['ranking', 'Stack Ranking'], ['review', 'Performance Review'], ['config', '⚙️ Config']] as [Tab, string][]).map(([k, lbl]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ padding: '6px 14px', fontSize: 13, fontWeight: 600, border: 'none', cursor: 'pointer',
              background: tab === k ? 'var(--accent)' : 'transparent', color: tab === k ? '#fff' : 'var(--text2)' }}>
            {lbl}
          </button>
        ))}
      </div>

      {tab === 'config' ? (
        <ConfigPanel />
      ) : (
        <>
          {/* RULE FIVE standardized filter bar — pick-don't-type over the org's real data, applied server-side. */}
          <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
            {(opt.stores || []).length > 0 && <MultiSelect allLabel="All stores" width={150} value={selStores} options={opt.stores} onChange={setSelStores} searchable />}
            {(opt.markets || []).length > 0 && <MultiSelect allLabel="All markets" width={140} value={selMarkets} options={opt.markets} onChange={setSelMarkets} />}
            {(opt.reps || []).length > 0 && <MultiSelect allLabel="All employees" width={150} value={selReps} options={opt.reps} onChange={setSelReps} searchable />}
            {hasFilter && <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={clearFilters}>Clear filters</button>}
          </div>

          {loading ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: 60 }}><div className="spinner" /></div>
          ) : err ? (
            <div className="card" style={{ padding: 16, borderLeft: '3px solid #b42318', color: '#b42318', fontSize: 13 }}>Could not load: {err}</div>
          ) : tab === 'productivity' ? (
            <ProductivityView data={data} period={period} hasFilter={hasFilter} clearFilters={clearFilters} />
          ) : tab === 'ranking' ? (
            <RankingView data={data} period={period} />
          ) : (
            <ReviewView data={data} period={period} />
          )}
        </>
      )}
    </div>
  )
}

// ── FEATURE 1 — productivity vs the store baseline (grouped by store, rep rows) ──────────────────────
function ProductivityView({ data, period, hasFilter, clearFilters }: any) {
  const stores: any[] = data?.stores || []
  if (stores.length === 0) {
    return <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>
      {hasFilter ? <>No sales/hours match the filter. <span style={{ color: 'var(--accent)', cursor: 'pointer' }} onClick={clearFilters}>Clear filters</span>.</>
        : <>No sales for {period}. Hours come from StoreOps time-clock punches; sales from the shared aggregation.</>}
    </div>
  }
  // Export: one flat sheet — store baseline rows + rep rows (what you see).
  const flat: any[] = []
  for (const s of stores) {
    flat.push({ scope: 'STORE', store: s.store_label, rep: '', boxes: s.store_boxes, acc: s.store_acc, hours: s.store_hours, bhr: s.store_boxes_per_hr, ahr: s.store_acc_per_hr, bidx: '', aidx: '' })
    for (const r of s.reps) flat.push({ scope: 'rep', store: s.store_label, rep: r.rep, boxes: r.boxes, acc: r.acc_sales, hours: r.hours, bhr: r.boxes_per_hr, ahr: r.acc_per_hr, bidx: r.boxes_index, aidx: r.acc_index })
  }
  const cols: ExportColumn[] = [
    { header: 'Store', field: 'store', role: 'store', get: (r) => r.store },
    { header: 'Employee', field: 'rep', role: 'rep', get: (r) => r.rep },
    { header: 'Boxes', field: 'boxes', type: 'number', get: (r) => r.boxes },
    { header: 'Acc $', field: 'acc', money: true, get: (r) => r.acc },
    { header: 'Hours', field: 'hours', type: 'number', get: (r) => r.hours },
    { header: 'Boxes/hr', field: 'bhr', type: 'number', get: (r) => r.bhr },
    { header: 'Acc $/hr', field: 'ahr', type: 'number', get: (r) => r.ahr },
    { header: 'Boxes idx', field: 'bidx', type: 'number', get: (r) => r.bidx },
    { header: 'Acc idx', field: 'aidx', type: 'number', get: (r) => r.aidx },
  ]
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
        <ReportExportBar title={`Productivity ${period}`} filename={`productivity_${String(period).replace(/\s+/g, '_')}`}
          columns={cols} rows={flat} />
      </div>
      {stores.map((s) => (
        <div key={s.store_code} className="card table-wrapper" style={{ padding: 0, marginBottom: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '8px 12px', background: 'var(--surface2)', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontWeight: 700, fontSize: 14 }}>{s.store_label} {s.market && <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 12 }}>· {s.market}</span>}</div>
            <div style={{ fontSize: 12, color: 'var(--text2)' }}>
              Store baseline: <b>{n3(s.store_boxes_per_hr)}</b> boxes/hr · <b>{fmt(s.store_acc_per_hr)}</b>/hr
              <span style={{ color: 'var(--text3)' }}> ({n2(s.store_boxes)} boxes · {fmt(s.store_acc)} · {n2(s.store_hours)} hrs)</span>
            </div>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={thL}>Employee</th><th style={th}>Boxes</th><th style={th}>Acc $</th><th style={th}>Hours</th>
              <th style={th}>Boxes/hr</th><th style={th}>Acc $/hr</th><th style={th}>vs store (boxes)</th><th style={th}>vs store (acc)</th>
            </tr></thead>
            <tbody>
              {s.reps.map((r: any, i: number) => (
                <tr key={i}>
                  <td style={{ ...tdL, fontWeight: 600 }}>{r.rep}{r.no_hours && <span title="No time-clock punches this period" style={{ marginLeft: 6, fontSize: 10, color: 'var(--amber)' }}>⚠ no hours</span>}</td>
                  <td style={td}>{n2(r.boxes)}</td>
                  <td style={td}>{fmt(r.acc_sales)}</td>
                  <td style={td}>{n2(r.hours)}</td>
                  <td style={td}>{n3(r.boxes_per_hr)}</td>
                  <td style={td}>{r.acc_per_hr == null ? '—' : fmt(r.acc_per_hr)}</td>
                  <td style={{ ...td, color: r.boxes_index == null ? 'var(--text3)' : r.boxes_index >= 1 ? 'var(--green)' : 'var(--red)' }}>{idx(r.boxes_index)}</td>
                  <td style={{ ...td, color: r.acc_index == null ? 'var(--text3)' : r.acc_index >= 1 ? 'var(--green)' : 'var(--red)' }}>{idx(r.acc_index)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  )
}

// ── FEATURE 2 — weighted stack ranking (explainable per-metric attainment) ──────────────────────────
function RankingView({ data, period }: any) {
  const rows: any[] = data?.rows || []
  const items: any[] = data?.items || []
  if (rows.length === 0) return <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No ranked employees for {period}. Enable ranking metrics under ⚙️ Config.</div>
  const flat = rows.map((r) => {
    const o: any = { rank: r.rank, rep: r.rep, score: r.score }
    for (const b of r.breakdown) o[b.item_key] = b.attainment
    return o
  })
  const cols: ExportColumn[] = [
    { header: 'Rank', field: 'rank', type: 'number', get: (r) => r.rank },
    { header: 'Employee', field: 'rep', role: 'rep', get: (r) => r.rep },
    { header: 'Score', field: 'score', type: 'number', get: (r) => r.score },
    ...items.map((it) => ({ header: `${it.label} %`, field: it.item_key, type: 'number' as const, get: (r: any) => r[it.item_key] })),
  ]
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>Weighted attainment (Σ attainment×weight ÷ Σ weight). Metrics with no absolute standard rank <b>relative</b> to the field. Per-metric % shown so a rep can see why.</div>
        <ReportExportBar title={`Stack Ranking ${period}`} filename={`stack_ranking_${String(period).replace(/\s+/g, '_')}`} columns={cols} rows={flat} />
      </div>
      <div className="card table-wrapper" style={{ padding: 0 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={th}>#</th><th style={thL}>Employee</th><th style={th}>Score</th>
            {items.map((it) => <th key={it.item_key} style={th} title={`weight ${it.weight}`}>{it.label}</th>)}
          </tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td style={{ ...td, fontWeight: 700 }}>{r.rank}</td>
                <td style={{ ...tdL, fontWeight: 600 }}>{r.rep}{r.market && <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 11 }}> · {r.market}</span>}</td>
                <td style={{ ...td, fontWeight: 700 }}>{r.score == null ? '—' : r.score.toFixed(1)}</td>
                {items.map((it) => {
                  const b = r.breakdown.find((x: any) => x.item_key === it.item_key)
                  return <td key={it.item_key} style={{ ...td, color: b?.na ? 'var(--text3)' : b?.met === false ? 'var(--red)' : 'var(--text)' }}
                    title={b ? `value ${b.value ?? '—'} / std ${b.standard ?? 'relative'}` : ''}>{b ? pct(b.attainment) : '—'}</td>
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

// ── FEATURE 3 — performance review scorecards (per employee, vs definable standards) ────────────────
function ReviewView({ data, period }: any) {
  const rows: any[] = data?.rows || []
  const items: any[] = data?.items || []
  if (rows.length === 0) return <div className="card" style={{ textAlign: 'center', padding: 50, color: 'var(--text3)' }}>No review scorecards for {period}. Enable review items under ⚙️ Config.</div>
  // One export sheet per employee (a printable per-employee review sheet — one per page in PDF) + a summary.
  const itemCols: ExportColumn[] = [
    { header: 'Item', field: 'label', get: (r) => r.label },
    { header: 'Value', field: 'value', type: 'number', get: (r) => r.value },
    { header: 'Standard', field: 'standard', type: 'number', get: (r) => r.standard },
    { header: 'Attainment %', field: 'attainment', type: 'number', get: (r) => r.attainment },
    { header: 'Weight', field: 'weight', type: 'number', get: (r) => r.weight },
    { header: 'Weighted', field: 'weighted', type: 'number', get: (r) => r.weighted },
  ]
  const summaryCols: ExportColumn[] = [
    { header: 'Employee', field: 'rep', role: 'rep', get: (r) => r.rep },
    { header: 'Review score', field: 'review_score', type: 'number', get: (r) => r.review_score },
  ]
  const sheets = [
    { name: 'Summary', columns: summaryCols, rows },
    ...rows.map((r) => ({ name: (r.rep || 'rep').slice(0, 26), columns: itemCols, rows: r.items })),
  ]
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>Each item measured against its definable standard (⚙️ Config). A missing-source item shows <b>n/a</b> and is excluded from the total.</div>
        <ReportExportBar title={`Performance Review ${period}`} filename={`performance_review_${String(period).replace(/\s+/g, '_')}`} sheets={sheets} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))', gap: 12 }}>
        {rows.map((r, i) => (
          <div key={i} className="card" style={{ padding: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 12px', background: 'var(--surface2)' }}>
              <div style={{ fontWeight: 700, fontSize: 14 }}>{r.rep}{r.market && <span style={{ color: 'var(--text3)', fontWeight: 400, fontSize: 11 }}> · {r.market}</span>}</div>
              <div style={{ fontWeight: 800, fontSize: 16, color: r.review_score == null ? 'var(--text3)' : r.review_score >= 100 ? 'var(--green)' : r.review_score >= 70 ? 'var(--amber)' : 'var(--red)' }}>{r.review_score == null ? '—' : r.review_score.toFixed(1)}</div>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={thL}>Item</th><th style={th}>Value</th><th style={th}>Std</th><th style={th}>Attain</th><th style={th}>Wt</th></tr></thead>
              <tbody>
                {r.items.map((b: any, j: number) => (
                  <tr key={j}>
                    <td style={tdL}>{b.label}</td>
                    <td style={{ ...td, color: b.na ? 'var(--text3)' : 'var(--text)' }}>{b.na ? 'n/a' : n2(b.value)}</td>
                    <td style={td}>{b.standard == null ? '—' : n2(b.standard)}</td>
                    <td style={{ ...td, color: b.na ? 'var(--text3)' : b.met === false ? 'var(--red)' : 'var(--green)' }}>{b.na ? '—' : pct(b.attainment)}</td>
                    <td style={td}>{n2(b.weight)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </>
  )
}

// ── ⚙️ CONFIG — the unified item registry (drives BOTH the ranker and the review) ───────────────────
function ConfigPanel() {
  const [cfg, setCfg] = useState<any>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const reload = useCallback(() => {
    api(`/api/v1/commcalc/productivity/config${orgQ()}`).then(setCfg).catch((e) => setMsg(String(e?.message || e)))
  }, [])
  useEffect(() => { reload() }, [reload])

  const save = (item: any) => {
    setBusy(item.item_key); setMsg(null)
    api(`/api/v1/commcalc/productivity/config${orgQ()}`, { method: 'PUT', body: JSON.stringify(item) })
      .then((r) => { if (r?.ok === false) setMsg(r.hint || r.error || 'save failed'); reload() })
      .catch((e) => setMsg(String(e?.message || e))).finally(() => setBusy(null))
  }
  const del = (item_key: string) => {
    if (!confirm(`Remove "${item_key}"?`)) return
    setBusy(item_key)
    api(`/api/v1/commcalc/productivity/config/${encodeURIComponent(item_key)}${orgQ()}`, { method: 'DELETE' })
      .then(reload).catch((e) => setMsg(String(e?.message || e))).finally(() => setBusy(null))
  }
  const reset = () => { if (!confirm('Reset all items to system defaults?')) return; api(`/api/v1/commcalc/productivity/config/reset${orgQ()}`, { method: 'POST' }).then(reload) }
  const addItem = () => {
    const src = cfg.sources[0]
    const key = `custom_${Date.now().toString(36)}`
    save({ item_key: key, label: 'New item', source_key: src.source_key, standard: src.default_standard ?? null,
      standard_type: src.value_type, weight: 1, count_in_stack_ranker: true, count_in_review: false, enabled: true })
  }

  if (!cfg) return <div className="card" style={{ padding: 16 }}>{msg ? <span style={{ color: '#b42318' }}>{msg}</span> : 'Loading…'}</div>
  const patch = (ik: string, field: string, val: any) =>
    setCfg((c: any) => ({ ...c, items: c.items.map((it: any) => it.item_key === ik ? { ...it, [field]: val } : it) }))

  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>Metric registry — powers the Stack Ranker and the Performance Review</div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={addItem}>+ Add item</button>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={reset}>Reset to defaults</button>
        </div>
      </div>
      <p style={{ fontSize: 12, color: 'var(--text3)', margin: '0 0 10px' }}>
        Every item picks a <b>source</b> from the system catalog (pick-don&apos;t-type — no free-form formulas), a
        <b> standard</b> to measure against, and a <b>weight</b>. Toggle whether it counts in the ranker, the review,
        or both. Editing here does <b>not</b> change commission pay.
      </p>
      {msg && <div style={{ fontSize: 12, color: '#b42318', marginBottom: 8 }}>{msg}</div>}
      <div className="table-wrapper" style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 900 }}>
          <thead><tr style={{ background: 'var(--surface2)' }}>
            <th style={thL}>On</th><th style={thL}>Label</th><th style={thL}>Source</th><th style={th}>Standard</th>
            <th style={thL}>Type</th><th style={th}>Weight</th><th style={th}>Rank</th><th style={th}>Review</th><th style={th}></th>
          </tr></thead>
          <tbody>
            {cfg.items.map((it: any) => (
              <tr key={it.item_key}>
                <td style={tdL}><input type="checkbox" checked={!!it.enabled} onChange={(e) => patch(it.item_key, 'enabled', e.target.checked)} /></td>
                <td style={tdL}><input value={it.label || ''} onChange={(e) => patch(it.item_key, 'label', e.target.value)} style={inp(150)} /></td>
                <td style={tdL}>
                  <select value={it.source_key || ''} onChange={(e) => patch(it.item_key, 'source_key', e.target.value)} style={inp(170)}>
                    {cfg.sources.map((s: any) => <option key={s.source_key} value={s.source_key}>{s.label}</option>)}
                  </select>
                </td>
                <td style={td}><input type="number" value={it.standard ?? ''} onChange={(e) => patch(it.item_key, 'standard', e.target.value === '' ? null : Number(e.target.value))} placeholder="relative" style={{ ...inp(80), textAlign: 'right' }} /></td>
                <td style={tdL}>
                  <select value={it.standard_type || 'number'} onChange={(e) => patch(it.item_key, 'standard_type', e.target.value)} style={inp(90)}>
                    {(cfg.value_types || ['number', 'dollar', 'percent', 'score']).map((v: string) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </td>
                <td style={td}><input type="number" value={it.weight ?? 1} onChange={(e) => patch(it.item_key, 'weight', Number(e.target.value))} style={{ ...inp(60), textAlign: 'right' }} /></td>
                <td style={td}><input type="checkbox" checked={!!it.count_in_stack_ranker} onChange={(e) => patch(it.item_key, 'count_in_stack_ranker', e.target.checked)} /></td>
                <td style={td}><input type="checkbox" checked={!!it.count_in_review} onChange={(e) => patch(it.item_key, 'count_in_review', e.target.checked)} /></td>
                <td style={td}>
                  <button className="btn" style={{ fontSize: 11, padding: '3px 8px' }} disabled={busy === it.item_key} onClick={() => save(it)}>{busy === it.item_key ? '…' : 'Save'}</button>
                  <button className="btn btn-secondary" style={{ fontSize: 11, padding: '3px 6px', marginLeft: 4 }} onClick={() => del(it.item_key)}>✕</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 12, padding: '9px 11px', background: 'var(--surface2)', borderRadius: 8, fontSize: 12, color: 'var(--text2)' }}>
        <b>Commission tie-in (inert):</b> this module exposes each rep&apos;s <code>performance_score</code> and
        per-item <code>perf:&lt;item&gt;</code> attainment as KPI inputs the payout engine can reference. It is
        <b> inert</b> — no payout changes unless a Commission Plan explicitly references one of these keys and the
        owner re-runs the calc.
      </div>
    </div>
  )
}

const inp = (w: number): React.CSSProperties => ({ fontSize: 12, padding: '4px 7px', border: '1px solid var(--border)', borderRadius: 6, width: w })
