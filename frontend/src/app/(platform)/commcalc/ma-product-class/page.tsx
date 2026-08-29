'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { ReportShell } from '@/components/ReportShell'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { useActiveCarrier } from '@/lib/auth-context'

// MA Daily Tx — Product Name Classification.
//
// The `product_name` column on commcalc.raw_ma_daily_tx mixes MANY payment types in ONE column: a
// commission installment, a spiff installment, a residual, a customer PLAN PURCHASE, a DEVICE SALE, a
// dealer FEE, a credit memo. This page classifies it per EXACT product name, per tenant.
//
// READ-ONLY WITH RESPECT TO MONEY. Nothing here changes a payout, a ledger row, carrier income or the
// P&L. The "Impact preview" tab shows what the classification WOULD reclassify — wiring a class into a
// money number is a separate, owner-approved change.
//
// EXACT MATCH ONLY. Names are matched byte-for-byte after trimming whitespace — no contains/prefix
// rules. 'TW EDGE SPF Month 1' is the Total Wireless EDGE FINANCING TENDER, not a Motorola Edge
// handset; 'Total ALL ACCESS Plan $65' and 'Total ALL ACCESS Plan $65 New Activation Commission'
// differ only by suffix. A keyword rule would collapse those. Backed by commcalc.ma_product_class_map
// (migration 254); falls back to the built-in proposals (read-only) until 254 is run.

type ClassRow = { class_key: string; label: string; description: string; is_reserved: boolean }
type Item = {
  id?: string | null; product_name: string; product_class: string; status: string; note: string
  matched: boolean; saved?: boolean; not_in_data?: boolean
  lines: number; total: number; min: number | null; max: number | null
  sign: string; negatives: number; positives: number; zeros: number
  months: string[]; month_count: number; raw_variants: string[]
  first_seen: string | null; last_seen: string | null
}
type SourceDef = { source_table: string; name_column: string; amount_column: string; money_columns: string[]; label: string }
type Facets = { periods: string[]; stores: string[]; reps: string[]; money_columns: string[] }
type Bucket = { lines: number; total: number }
type Mode = { by_month: Record<string, Record<string, Bucket>>; by_class: Record<string, Bucket>; line_count: number; total: number; unmapped_lines: number; unmapped_total: number }
type Preview = {
  preview: { months: { key: string; label: string }[]; confirmed: Mode; proposed: Mode; classes_present: string[]; delta: { lines_newly_classified: number; dollars_newly_classified: number } }
  unmapped: { names: number; lines: number; total: number; detail: { product_name: string; lines: number; total: number; sign: string }[] }
  class_labels: Record<string, string>
  note: string; ready: boolean; migration: string | null
}

const UNMAPPED = 'unmapped'
const money = (n: any) => (Number(n) || 0).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
const sel: React.CSSProperties = { padding: '5px 8px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)' }
const th: React.CSSProperties = { textAlign: 'left', padding: '7px 8px', fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '6px 8px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }

function Badge({ kind }: { kind: 'unmapped' | 'proposed' | 'confirmed' }) {
  const s: Record<string, React.CSSProperties> = {
    unmapped: { background: '#fef2f2', color: '#b91c1c', border: '1px solid #fecaca' },
    proposed: { background: '#fff7ed', color: '#9a3412', border: '1px solid #fdba74' },
    confirmed: { background: '#f0fdf4', color: '#15803d', border: '1px solid #bbf7d0' },
  }
  const label = kind === 'unmapped' ? 'UNMAPPED' : kind === 'proposed' ? 'PROPOSED — not confirmed' : 'CONFIRMED'
  return <span style={{ ...s[kind], borderRadius: 999, padding: '1px 8px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{label}</span>
}

export default function MaProductClassPage() {
  // Active-carrier lens: the exact-match example is neutralized (no carrier/brand names) for a
  // dual-carrier tenant. Single-carrier tenants keep the original wording.
  const { multi } = useActiveCarrier()
  const [tab, setTab] = useState<'names' | 'preview'>('names')
  const [classes, setClasses] = useState<ClassRow[]>([])
  const [assignable, setAssignable] = useState<string[]>([])
  const [items, setItems] = useState<Item[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [dollars, setDollars] = useState<Record<string, number>>({})
  const [source, setSource] = useState<SourceDef | null>(null)
  const [facets, setFacets] = useState<Facets>({ periods: [], stores: [], reps: [], money_columns: [] })
  const [pv, setPv] = useState<Preview | null>(null)
  const [ready, setReady] = useState(true)
  const [builtin, setBuiltin] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [truncated, setTruncated] = useState(false)

  // ── the standardized filter bar (RULE FIVE): period · store · rep, all pick-don't-type from the
  // org's REAL data. `market` is not carried on raw_ma_daily_tx, so it is not offered here.
  const [period, setPeriod] = useState('')
  const [store, setStore] = useState('')
  const [rep, setRep] = useState('')
  const [amountCol, setAmountCol] = useState('')
  const [status, setStatus] = useState('')
  const [klass, setKlass] = useState('')
  const [search, setSearch] = useState('')

  const qs = useCallback((extra = '') => {
    const p = new URLSearchParams()
    if (period) p.set('period', period)
    if (store) p.set('store', store)
    if (rep) p.set('rep', rep)
    if (amountCol) p.set('amount_column', amountCol)
    const s = p.toString()
    return (s ? '?' + s : '') + (extra ? (s ? '&' : '?') + extra : '')
  }, [period, store, rep, amountCol])

  const flash = (m: string) => { setMsg(m); setTimeout(() => setMsg(''), 4500) }

  const loadNames = useCallback(async () => {
    setBusy(true)
    try {
      const d = await api('/api/v1/commcalc/ma-product-class' + qs())
      setItems(d?.items || []); setCounts(d?.counts || {}); setDollars(d?.dollars || {})
      setClasses(d?.classes || []); setAssignable(d?.assignable || [])
      setSource(d?.source || null); setReady(d?.ready !== false); setBuiltin(!!d?.using_builtin_proposals)
      setTruncated(!!d?.read?.truncated)
    } catch (e: any) { flash(e?.message || 'Load failed') }
    setBusy(false)
  }, [qs])

  const loadPreview = useCallback(async () => {
    setBusy(true)
    try { setPv(await api('/api/v1/commcalc/ma-product-class/preview' + qs())) }
    catch (e: any) { flash(e?.message || 'Preview failed') }
    setBusy(false)
  }, [qs])

  useEffect(() => {
    api('/api/v1/commcalc/ma-product-class/facets')
      .then(d => setFacets({ periods: d?.periods || [], stores: d?.stores || [], reps: d?.reps || [], money_columns: d?.money_columns || [] }))
      .catch(() => { /* facets are a convenience — never block the page */ })
  }, [])
  useEffect(() => { loadNames() }, [loadNames])
  useEffect(() => { if (tab === 'preview') loadPreview() }, [tab, loadPreview])

  async function assign(it: Item, cls: string) {
    if (!cls) {
      if (!it.id) { flash('Nothing saved for this name yet.'); return }
      try { await api('/api/v1/commcalc/ma-product-class/' + it.id, { method: 'DELETE' }); flash(`"${it.product_name}" unmapped`); loadNames(); if (tab === 'preview') loadPreview() }
      catch (e: any) { flash(e?.message || 'Delete failed') }
      return
    }
    try {
      await api('/api/v1/commcalc/ma-product-class', {
        method: 'POST',
        body: JSON.stringify({ product_name: it.product_name, product_class: cls, status: 'proposed', note: it.note || null }),
      })
      flash(`"${it.product_name}" → ${cls} (proposed — confirm it to make it count)`)
      loadNames(); if (tab === 'preview') loadPreview()
    } catch (e: any) { flash(e?.message || 'Save failed — is migration 254 applied?') }
  }

  async function confirm(items: Item[]) {
    if (!items.length) { flash('Nothing to confirm.'); return }
    try {
      // `items` carries the class shown on screen, so confirming a proposal that was never saved
      // CREATES it instead of silently matching nothing (owner report 2026-08-11: "Subsidy is not
      // going away"). product_names stays for backward compatibility with any older caller.
      const d = await api('/api/v1/commcalc/ma-product-class/confirm', {
        method: 'POST',
        body: JSON.stringify({
          product_names: items.map(i => i.product_name),
          items: items.map(i => ({ product_name: i.product_name, product_class: i.product_class })),
        }),
      })
      const made = d?.created_count ?? 0
      const missed = (d?.not_found || []).length
      flash(`Confirmed ${d?.confirmed_count ?? 0} name(s)${made ? ` (${made} newly saved)` : ''}`
        + (missed ? ` · ${missed} could not be confirmed — assign a class first: ${(d.not_found || []).slice(0, 3).join(', ')}` : ''))
      loadNames(); if (tab === 'preview') loadPreview()
    } catch (e: any) { flash(e?.message || 'Confirm failed — is migration 254 applied?') }
  }

  async function seed() {
    try {
      const d = await api('/api/v1/commcalc/ma-product-class/seed-proposals', { method: 'POST', body: JSON.stringify({}) })
      flash(`Seeded ${d?.inserted ?? 0} proposal(s) for this tenant.`); loadNames()
    } catch (e: any) { flash(e?.message || 'Seed failed — is migration 254 applied?') }
  }

  // client-side narrowing (the server already applied period/store/rep)
  const shown = useMemo(() => items.filter(i => {
    if (status === 'unmapped' && i.product_class !== UNMAPPED) return false
    if (status === 'proposed' && !(i.product_class !== UNMAPPED && i.status === 'proposed')) return false
    if (status === 'confirmed' && !(i.product_class !== UNMAPPED && i.status === 'confirmed')) return false
    if (klass && i.product_class !== klass) return false
    if (search && !i.product_name.toLowerCase().includes(search.toLowerCase())) return false
    return true
  }), [items, status, klass, search])

  // Send the WHOLE item, not just the name: a proposal the user can see but that has no saved row yet
  // needs its class to travel with the confirm, otherwise the server has nothing to create.
  const proposedShown = useMemo(() => shown.filter(i => i.product_class !== UNMAPPED && i.status === 'proposed'), [shown])

  const nameCols: ExportColumn[] = [
    { header: 'Product name', field: 'product_name', get: r => r.product_name },
    { header: 'Class', field: 'product_class', get: r => r.product_class },
    { header: 'Status', field: 'status', get: r => (r.product_class === UNMAPPED ? 'unmapped' : r.status) },
    { header: 'Lines', field: 'lines', type: 'number', align: 'right', get: r => r.lines },
    { header: 'Total (signed)', field: 'total', money: true, align: 'right', get: r => r.total },
    { header: 'Sign', field: 'sign', get: r => r.sign },
    { header: 'Months', field: 'month_count', type: 'number', align: 'right', get: r => r.month_count },
    { header: 'First seen', field: 'first_seen', type: 'date', get: r => r.first_seen || '' },
    { header: 'Last seen', field: 'last_seen', type: 'date', get: r => r.last_seen || '' },
    { header: 'Note', field: 'note', get: r => r.note || '' },
  ]
  const namePayload = (): ExportPayload => ({
    title: 'MA Daily Tx — Product Name Classification',
    subtitle: [period && `Period ${period}`, store && `Store ${store}`, rep && `Rep ${rep}`, `amount = ${source?.amount_column || 'retail_cost'}`].filter(Boolean).join(' · '),
    filename: 'ma-product-class',
    sheets: [{ name: 'Product names', columns: nameCols, rows: shown }],
  })

  // ── the impact preview, flattened for ReportShell (per class per month, both readings) ──
  const pvRows = useMemo(() => {
    if (!pv) return []
    const out: any[] = []
    const p = pv.preview
    for (const m of p.months) {
      for (const c of p.classes_present) {
        const conf = p.confirmed.by_month[m.key]?.[c]
        const prop = p.proposed.by_month[m.key]?.[c]
        if (!conf && !prop) continue
        out.push({
          month: m.label, month_key: m.key, cls: c, label: pv.class_labels[c] || c,
          conf_lines: conf?.lines || 0, conf_total: conf?.total || 0,
          prop_lines: prop?.lines || 0, prop_total: prop?.total || 0,
          d_lines: (prop?.lines || 0) - (conf?.lines || 0),
          d_total: Number(((prop?.total || 0) - (conf?.total || 0)).toFixed(2)),
        })
      }
    }
    return out.sort((a, b) => (a.month_key < b.month_key ? 1 : a.month_key > b.month_key ? -1 : (a.cls < b.cls ? -1 : 1)))
  }, [pv])

  const pvCols: ExportColumn[] = [
    { header: 'Month', field: 'month', role: 'month', get: r => r.month },
    { header: 'Class', field: 'label', get: r => r.label },
    { header: 'Confirmed — lines', field: 'conf_lines', type: 'number', align: 'right', get: r => r.conf_lines },
    { header: 'Confirmed — total', field: 'conf_total', money: true, align: 'right', get: r => r.conf_total },
    { header: 'With proposals — lines', field: 'prop_lines', type: 'number', align: 'right', get: r => r.prop_lines },
    { header: 'With proposals — total', field: 'prop_total', money: true, align: 'right', get: r => r.prop_total },
    { header: 'Δ lines', field: 'd_lines', type: 'number', align: 'right', get: r => r.d_lines },
    { header: 'Δ total', field: 'd_total', money: true, align: 'right', get: r => r.d_total },
  ]

  const filterBar = (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 12 }}>
      <select style={sel} value={period} onChange={e => setPeriod(e.target.value)}>
        <option value="">All periods</option>
        {facets.periods.map(p => <option key={p} value={p}>{p}</option>)}
      </select>
      <select style={sel} value={store} onChange={e => setStore(e.target.value)}>
        <option value="">All stores (accounts)</option>
        {facets.stores.map(s => <option key={s} value={s}>{s}</option>)}
      </select>
      <select style={sel} value={rep} onChange={e => setRep(e.target.value)}>
        <option value="">All reps</option>
        {facets.reps.map(r => <option key={r} value={r}>{r}</option>)}
      </select>
      <select style={sel} value={amountCol} onChange={e => setAmountCol(e.target.value)} title="Which signed money column the totals sum">
        <option value="">Amount: {source?.amount_column || 'retail_cost'} (default)</option>
        {(facets.money_columns.length ? facets.money_columns : source?.money_columns || []).map(c => <option key={c} value={c}>Amount: {c}</option>)}
      </select>
      {(period || store || rep || amountCol) && (
        <button onClick={() => { setPeriod(''); setStore(''); setRep(''); setAmountCol('') }}
          style={{ ...sel, cursor: 'pointer' }}>Clear filters</button>
      )}
    </div>
  )

  return (
    <div style={{ padding: 24, maxWidth: 1400 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, marginBottom: 4 }}>🏷️ MA Daily Tx — Product Name Classification</h1>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13, marginBottom: 10, maxWidth: 900, lineHeight: 1.5 }}>
        One <code>product_name</code> column on the MA Daily Tx file carries commission installments, spiffs,
        residuals, customer <b>plan purchases</b>, <b>device sales</b>, dealer fees and credit memos side by
        side. Classify each name here so the file can be read as what it actually is.{' '}
        <b>Matching is exact</b> (whitespace trimmed, nothing else){multi
          ? <> — a financing-tender line is not a device with a similar name, and two plan lines can differ only by suffix.</>
          : <> — “TW EDGE SPF Month 1” is the EDGE
             financing tender, not a Motorola Edge phone, and “Total ALL ACCESS Plan $65” differs from
             “Total ALL ACCESS Plan $65 New Activation Commission” only by suffix.</>}
      </p>

      <div style={{ background: '#eff6ff', border: '1px solid #bfdbfe', color: '#1e40af', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 12, maxWidth: 900 }}>
        <b>Nothing on this page changes anyone’s pay.</b> The classification is configuration and the
        “Impact preview” tab is read-only — it shows what this WOULD reclassify. Feeding a class into a
        commission, the Commission Ledger, carrier income or the P&amp;L is a separate change that needs
        your explicit go-ahead.
      </div>

      {!ready && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          Run migration <code>254_commission_ma_product_class.sql</code> to save, confirm and edit
          classifications. Until then the page shows the built-in proposals read-only — nothing else breaks.
        </div>
      )}
      {ready && builtin && (
        <div style={{ background: '#fff7ed', border: '1px solid #fdba74', color: '#9a3412', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12, display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <span>No saved classifications for this tenant yet — the built-in proposals are shown.</span>
          <button onClick={seed} style={{ ...sel, cursor: 'pointer', fontWeight: 600 }}>Save them as proposals for this tenant</button>
        </div>
      )}
      {truncated && (
        <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#b91c1c', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>
          The source read hit its row cap — the counts below cover only part of the file. Narrow by period.
        </div>
      )}
      {msg && <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, padding: '8px 12px', fontSize: 13, marginBottom: 12 }}>{msg}</div>}

      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        {(['names', 'preview'] as const).map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            padding: '6px 14px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
            border: '1px solid var(--border)',
            background: tab === t ? 'var(--accent,#2563eb)' : 'var(--surface)',
            color: tab === t ? '#fff' : 'var(--text)',
          }}>{t === 'names' ? '🏷️ Product names' : '📊 Impact preview'}</button>
        ))}
      </div>

      {filterBar}

      {tab === 'names' && (
        <>
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
            {([['unmapped', 'Unmapped', '#b91c1c'], ['proposed', 'Proposed', '#9a3412'], ['confirmed', 'Confirmed', '#15803d']] as const).map(([k, lab, col]) => (
              <div key={k} className="card" style={{ padding: '8px 14px', border: '1px solid var(--border)', borderRadius: 10, minWidth: 150 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)' }}>{lab}</div>
                <div style={{ fontSize: 19, fontWeight: 700, color: col }}>{counts[k] ?? 0} <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--text3)' }}>names</span></div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(dollars[k])}</div>
              </div>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginBottom: 10 }}>
            <select style={sel} value={status} onChange={e => setStatus(e.target.value)}>
              <option value="">All statuses</option>
              <option value="unmapped">Unmapped only</option>
              <option value="proposed">Proposed only</option>
              <option value="confirmed">Confirmed only</option>
            </select>
            <select style={sel} value={klass} onChange={e => setKlass(e.target.value)}>
              <option value="">All classes</option>
              {classes.map(c => <option key={c.class_key} value={c.class_key}>{c.label}</option>)}
            </select>
            <input style={{ ...sel, minWidth: 220 }} placeholder="Filter by name…" value={search} onChange={e => setSearch(e.target.value)} />
            <button disabled={!proposedShown.length} onClick={() => confirm(proposedShown)}
              style={{ ...sel, cursor: proposedShown.length ? 'pointer' : 'not-allowed', fontWeight: 600, opacity: proposedShown.length ? 1 : .5 }}>
              ✓ Confirm all {proposedShown.length} shown proposals
            </button>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
              <ExportButtons payload={namePayload} compact />
              <SendReportButton exportPayload={namePayload} title="MA Daily Tx — Product Name Classification" compact />
            </div>
          </div>

          <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr>
                <th style={th}>Product name</th>
                <th style={{ ...th, textAlign: 'right' }}>Lines</th>
                <th style={{ ...th, textAlign: 'right' }}>Total (signed)</th>
                <th style={th}>Sign</th>
                <th style={th}>Class</th>
                <th style={th}>Status</th>
                <th style={th}></th>
              </tr></thead>
              <tbody>
                {!shown.length && <tr><td style={td} colSpan={7}>{busy ? 'Loading…' : 'No product names match these filters.'}</td></tr>}
                {shown.map(it => (
                  <tr key={it.product_name}>
                    <td style={{ ...td, maxWidth: 460 }}>
                      <code style={{ fontSize: 12.5 }}>{it.product_name}</code>
                      {it.not_in_data && <span style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>(mapped, not in the filtered data)</span>}
                      {!!it.raw_variants.length && (
                        <div style={{ fontSize: 11, color: '#9a3412', marginTop: 2 }}>
                          stored with surrounding whitespace in the file: {it.raw_variants.map(v => <code key={v}>&quot;{v}&quot;</code>)} — matched after trim
                        </div>
                      )}
                      {it.note && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2, maxWidth: 440 }}>{it.note}</div>}
                    </td>
                    <td style={{ ...td, textAlign: 'right' }}>{it.lines.toLocaleString()}</td>
                    <td style={{ ...td, textAlign: 'right', color: it.total < 0 ? '#15803d' : 'inherit' }}>{money(it.total)}</td>
                    <td style={td}><span style={{ fontSize: 11.5, color: 'var(--text3)' }}>{it.sign}</span></td>
                    <td style={td}>
                      <select style={sel} value={it.product_class === UNMAPPED ? '' : it.product_class}
                        onChange={e => assign(it, e.target.value)}>
                        <option value="">— unmapped —</option>
                        {classes.filter(c => !c.is_reserved).map(c => (
                          <option key={c.class_key} value={c.class_key}
                            disabled={!assignable.includes(c.class_key)}>{c.label}</option>
                        ))}
                      </select>
                    </td>
                    <td style={td}>
                      <Badge kind={it.product_class === UNMAPPED ? 'unmapped' : (it.status === 'confirmed' ? 'confirmed' : 'proposed')} />
                    </td>
                    <td style={td}>
                      {it.product_class !== UNMAPPED && it.status !== 'confirmed' && (
                        <button onClick={() => confirm([it])} style={{ ...sel, cursor: 'pointer', fontWeight: 600 }}>✓ Confirm</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'preview' && (
        <>
          {pv && (
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
              <div className="card" style={{ padding: '8px 14px', border: '1px solid var(--border)', borderRadius: 10, minWidth: 190 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)' }}>Unmapped today (confirmed only)</div>
                <div style={{ fontSize: 19, fontWeight: 700, color: '#b91c1c' }}>{pv.preview.confirmed.unmapped_lines.toLocaleString()} lines</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(pv.preview.confirmed.unmapped_total)}</div>
              </div>
              <div className="card" style={{ padding: '8px 14px', border: '1px solid var(--border)', borderRadius: 10, minWidth: 190 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)' }}>Still unmapped if you confirm all</div>
                <div style={{ fontSize: 19, fontWeight: 700, color: '#9a3412' }}>{pv.preview.proposed.unmapped_lines.toLocaleString()} lines</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(pv.preview.proposed.unmapped_total)}</div>
              </div>
              <div className="card" style={{ padding: '8px 14px', border: '1px solid var(--border)', borderRadius: 10, minWidth: 190 }}>
                <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: .4, color: 'var(--text3)' }}>Confirming would classify</div>
                <div style={{ fontSize: 19, fontWeight: 700, color: '#15803d' }}>{pv.preview.delta.lines_newly_classified.toLocaleString()} lines</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{money(pv.preview.delta.dollars_newly_classified)}</div>
              </div>
            </div>
          )}
          {pv?.note && <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10, maxWidth: 900, lineHeight: 1.5 }}>{pv.note}</div>}
          {!!pv?.unmapped?.names && (
            <div style={{ background: '#fef2f2', border: '1px solid #fecaca', color: '#b91c1c', borderRadius: 8, padding: '8px 12px', fontSize: 12.5, marginBottom: 12, maxWidth: 1000 }}>
              <b>{pv.unmapped.names} product name(s) have no class at all</b> — {pv.unmapped.lines.toLocaleString()} lines,
              {' '}{money(pv.unmapped.total)}. They are counted as “unmapped”, never folded into a money class:{' '}
              {pv.unmapped.detail.slice(0, 8).map(d => <code key={d.product_name} style={{ marginRight: 6 }}>{d.product_name}</code>)}
              {pv.unmapped.detail.length > 8 && <span>+{pv.unmapped.detail.length - 8} more</span>}
            </div>
          )}
          <ReportShell
            title="Impact preview — per class, per month"
            subtitle={`Read-only. “Confirmed” counts only what you have confirmed; “With proposals” counts confirmed + proposed. Amounts are raw signed sums of ${source?.amount_column || 'retail_cost'}.`}
            filename="ma-product-class-preview"
            columns={pvCols}
            rows={pvRows}
            defaultGroupBy="month"
            compact
          />
        </>
      )}
    </div>
  )
}
