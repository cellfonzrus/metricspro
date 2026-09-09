'use client'
// SALES FROM EVENTS — the three reports (owner directive 2026-09-09).
//
//   1. Total sales    — every sale rung on the org's event register(s), with all available fields.
//   2. Retention      — do the lines activated at the event STAY? THREE states, never two.
//   3. ROI            — commission received against what the day cost.
//
// THE HONESTY THIS PAGE RENDERS, RATHER THAN HIDES
//   • The attribution caption is a VISIBLE caption, never a tooltip (the §23 rule this module already
//     follows on the event-actuals screen). It says these are sales rung on the event TILL, which is
//     not the same as sales the event caused.
//   • Retention's third state — a line that could not be matched into the subscriber feed — is shown
//     with its reason and is NEVER coloured or counted as a loss. A percentage with no matched line
//     renders "—", never "0%".
//   • An ROI whose cost is unknown renders "Not computed" plus what is needed. Never "$0.00".
//   • Every table cell that declares white text declares its own background
//     (harness_table_header_contrast).
//
// RULE FIVE §3d: the shared <StandardFilterBar> supplies period (as a date RANGE — an event is a day,
// not a month) and store. Market and rep are omitted: the register rows carry a store and a
// salesperson but the report's grain is the (store, date) event key, and a rep filter here would
// invite reading a rep's share of an event as their contribution to it.
import { useCallback, useEffect, useMemo, useState } from 'react'
import StandardFilterBar from '@/components/StandardFilterBar'
import { api } from '@/lib/client'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import { panel, btn, btnPrimary, input, label as lbl, th, cell, fmtMoney } from '@/lib/marketing'

type Tab = 'sales' | 'retention' | 'roi'

const TABS: { key: Tab; label: string; blurb: string }[] = [
  { key: 'sales', label: '1 · Total sales', blurb: 'Everything rung on the event register.' },
  { key: 'retention', label: '2 · Retention', blurb: 'Do the lines activated at the event stay?' },
  { key: 'roi', label: '3 · ROI', blurb: 'Commission received against what the day cost.' },
]

const nf = (n: any) => (n === null || n === undefined ? '—' : Number(n).toLocaleString())
const pct = (n: any) => (n === null || n === undefined ? '—' : `${Number(n).toFixed(1)}%`)
const money = (n: any) => (n === null || n === undefined ? '—' : fmtMoney(n))

/** The attribution caption. Visible, never collapsible, never a tooltip — quoting it out of context
 *  still says the right thing, which is the point. */
function Attribution({ a }: { a: any }) {
  if (!a) return null
  return (
    <div style={{ ...panel, borderLeft: '3px solid #f39c12', marginBottom: 12, fontSize: 12,
                  color: 'var(--text2)', lineHeight: 1.55 }}>
      <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{a.headline}</div>
      <div>{a.detail}</div>
      <div style={{ marginTop: 6 }}>{a.register_note}</div>
      <div style={{ marginTop: 6 }}>{a.activation_note}</div>
      <div style={{ marginTop: 6 }}>
        Event register(s): <b>{(a.registers || []).join(', ') || 'none configured'}</b>
        {' · '}Counted as an activation: <b>{(a.activation_classes_counted || []).join(', ')}</b>
      </div>
      {a.source_note && <div style={{ marginTop: 6, color: '#b45309' }}>{a.source_note}</div>}
    </div>
  )
}

function Tile({ label, value, sub, color }: { label: string; value: any; sub?: string; color?: string }) {
  return (
    <div style={{ ...panel, minWidth: 140, flex: '1 1 140px' }}>
      <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4, color: color || 'var(--text)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

function Empty({ reason }: { reason?: string | null }) {
  if (!reason) return null
  return (
    <div style={{ ...panel, marginTop: 12, fontSize: 13, color: 'var(--text2)' }}>{reason}</div>
  )
}

export default function SalesFromEventsPage() {
  const [tab, setTab] = useState<Tab>('sales')
  const [filters, setFilters] = useState<StandardFilterValue>(() => emptyStandardFilter(''))
  const [data, setData] = useState<Record<Tab, any>>({ sales: null, retention: null, roi: null })
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState('')

  const qs = useMemo(() => {
    const p = new URLSearchParams()
    if (filters.period) p.set('date_from', filters.period)
    if (filters.periodTo) p.set('date_to', filters.periodTo)
    if (filters.stores.length) p.set('store', filters.stores.join(','))
    return p.toString() ? `?${p.toString()}` : ''
  }, [filters])

  const path = useMemo(() => ({
    sales: '/api/v1/marketing/event-sales',
    retention: '/api/v1/marketing/event-sales/subscriber-retention',
    roi: '/api/v1/marketing/event-sales/roi',
  }), [])

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const r = await api(`${path[tab]}${qs}`)
      setData(d => ({ ...d, [tab]: r }))
    } catch (e: any) {
      setErr(e?.message || 'Could not load the report.')
    } finally { setLoading(false) }
  }, [tab, qs, path])

  useEffect(() => { load() }, [load])

  const cur = data[tab]
  // Store options come from the rows the page already loaded — org-scoped by construction.
  const storeOptions = useMemo(() => {
    const s = new Set<string>()
    for (const t of ['sales', 'retention', 'roi'] as Tab[]) {
      const d: any = data[t]
      for (const k of (d?.summary?.event_keys || d?.by_store || d?.days || [])) {
        if (k.store) s.add(k.store)
      }
    }
    return Array.from(s).sort()
  }, [data])

  return (
    <div style={{ padding: 18, maxWidth: 1500, margin: '0 auto' }}>
      <h1 style={{ fontSize: 21, fontWeight: 700, marginBottom: 2 }}>Sales from Events</h1>
      <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14 }}>
        Sales rung on the event register, the retention of the lines they activated, and what the day
        returned against what it cost.
      </div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 12, flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            style={{ ...(tab === t.key ? btnPrimary : btn), textAlign: 'left' }}>
            <div style={{ fontWeight: 700 }}>{t.label}</div>
            <div style={{ fontSize: 11, opacity: 0.85 }}>{t.blurb}</div>
          </button>
        ))}
      </div>

      {/* RULE FIVE §3d — the shared bar. Range mode: an event is a DAY. */}
      <StandardFilterBar
        value={filters} onChange={setFilters} periodMode="range"
        show={{ period: true, stores: true, markets: false, reps: false }}
        storeOptions={storeOptions}
        storeLabel="Store"
        right={<button style={btn} onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>}
      />

      {err && <div style={{ ...panel, marginTop: 12, borderLeft: '3px solid #dc2626', fontSize: 13 }}>{err}</div>}
      {cur && <div style={{ marginTop: 12 }}><Attribution a={cur.attribution} /></div>}
      {cur && <Empty reason={cur.empty_reason} />}

      {tab === 'sales' && cur && <SalesTab d={cur} />}
      {tab === 'retention' && cur && <RetentionTab d={cur} />}
      {tab === 'roi' && cur && <RoiTab d={cur} onSaved={load} />}
    </div>
  )
}


// ══════════════════════════════════════════════════════════════════════════════════════════════
// REPORT 1
// ══════════════════════════════════════════════════════════════════════════════════════════════
function SalesTab({ d }: { d: any }) {
  const s = d.summary || {}
  const [showAll, setShowAll] = useState(false)
  const fields: string[] = s.fields || []
  const rows: any[] = d.rows || []
  const shown = showAll ? rows : rows.slice(0, 200)
  return (
    <>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 12 }}>
        <Tile label="Lines" value={nf(s.rows)} sub={`${nf(s.uncountable_rows)} void/return, listed not counted`} />
        <Tile label="Transactions" value={nf(s.transactions)} sub="distinct trans_id" />
        <Tile label="Lines activated" value={nf((s.by_class?.premium || 0) + (s.by_class?.byod || 0))}
              sub={`plus ${nf(s.by_class?.upgrade)} upgrade(s), reported beside`} />
        <Tile label="Mobile numbers" value={nf(s.distinct_mdns)} />
        <Tile label="Sales $" value={money(s.ext_price)} />
        <Tile label="GP $" value={money(s.gp)} />
      </div>

      <div style={{ ...panel, marginBottom: 12, overflowX: 'auto' }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Per event — one row per (store, date)</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            {['Date', 'Store', 'Store code', 'Txns', 'Lines activated', 'Numbers', 'Sales $', 'GP $', 'Rows']
              .map(h => <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(s.event_keys || []).map((k: any) => (
              <tr key={`${k.store}|${k.trans_date}`}>
                <td style={cell}>{k.trans_date}</td>
                <td style={cell}>{k.store}</td>
                <td style={cell}>{k.store_code || '—'}</td>
                <td style={cell}>{nf(k.transactions)}</td>
                <td style={cell}>{nf(k.lines)}</td>
                <td style={cell}>{nf(k.distinct_mdns)}</td>
                <td style={cell}>{money(k.ext_price)}</td>
                <td style={cell}>{money(k.gp)}</td>
                <td style={cell}>{nf(k.rows)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ ...panel, overflowX: 'auto' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <div style={{ fontWeight: 700 }}>Every line, every field</div>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>
            showing {nf(shown.length)} of {nf(rows.length)}
            {rows.length > shown.length && (
              <button style={{ ...btn, marginLeft: 8 }} onClick={() => setShowAll(true)}>Show all</button>
            )}
          </div>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead><tr>
            <th style={th}>Class</th>
            {fields.map(f => <th key={f} style={th}>{f}</th>)}
          </tr></thead>
          <tbody>
            {shown.map((r: any, i: number) => (
              <tr key={i} style={r.countable ? undefined : { opacity: 0.55 }}>
                <td style={cell}>
                  {r.line_class_label || '—'}
                  {!r.countable && <span style={{ fontSize: 10, color: '#b91c1c' }}> · not counted</span>}
                </td>
                {fields.map(f => (
                  <td key={f} style={{ ...cell, whiteSpace: 'nowrap' }}>
                    {r[f] === null || r[f] === undefined || r[f] === '' ? '' : String(r[f])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}


// ══════════════════════════════════════════════════════════════════════════════════════════════
// REPORT 2
// ══════════════════════════════════════════════════════════════════════════════════════════════
function RetentionTab({ d }: { d: any }) {
  const windows: any[] = d.windows || []
  const reasons: Record<string, string> = d.unmatched_reasons || {}
  return (
    <>
      <div style={{ ...panel, borderLeft: '3px solid #2563eb', marginBottom: 12, fontSize: 12,
                    color: 'var(--text2)', lineHeight: 1.55 }}>
        <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>{d.basis?.headline}</div>
        <div>{d.basis?.detail}</div>
        <div style={{ marginTop: 6 }}>{d.basis?.grain_note}</div>
        <div style={{ marginTop: 6 }}>Not to be confused with {d.basis?.not_to_be_confused_with}</div>
        {d.latest_loaded_period &&
          <div style={{ marginTop: 6 }}>“Still active now” reads the latest loaded subscriber feed:{' '}
            <b>{d.latest_loaded_period}</b>.</div>}
      </div>

      <div style={{ ...panel, marginBottom: 12, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>
            {['Window', 'Lines', 'Active', 'Not active', 'Could not match', 'Retention', 'Why unmatched']
              .map(h => <th key={h} style={th}>{h}</th>)}
          </tr></thead>
          <tbody>
            {windows.map(w => (
              <tr key={w.window}>
                <td style={cell}><b>{w.label}</b></td>
                <td style={cell}>{nf(w.lines)}</td>
                <td style={{ ...cell, color: '#16a34a', fontWeight: 600 }}>{nf(w.active)}</td>
                <td style={{ ...cell, color: '#dc2626' }}>{nf(w.churned)}</td>
                {/* GREY, never red: "we could not look it up" must not look like a loss. */}
                <td style={{ ...cell, color: 'var(--text2)' }}>{nf(w.unmatched)}</td>
                <td style={{ ...cell, fontWeight: 700 }}>
                  {pct(w.retention_pct)}
                  <div style={{ fontSize: 10, fontWeight: 400, color: 'var(--text2)' }}>
                    {w.matched ? `of ${nf(w.matched)} matched` : 'no matched line — not a 0%'}
                  </div>
                </td>
                <td style={{ ...cell, fontSize: 11 }}>
                  {Object.entries(w.by_reason || {}).map(([k, v]) => (
                    <div key={k} title={reasons[k]}>{nf(v)} · {k.replace(/_/g, ' ')}</div>
                  ))}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ ...panel, marginBottom: 12, fontSize: 12, color: 'var(--text2)' }}>
        <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 6 }}>
          What “could not match” means — and why it is not churn
        </div>
        {Object.entries(reasons).map(([k, v]) => (
          <div key={k} style={{ marginBottom: 4 }}><b>{k.replace(/_/g, ' ')}</b> — {v}</div>
        ))}
      </div>

      <div style={{ ...panel, marginBottom: 12, overflowX: 'auto' }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Still active now — per store</div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>{['Store', 'Lines', 'Active', 'Not active', 'Could not match', 'Retention']
            .map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {(d.by_store || []).map((b: any) => (
              <tr key={b.store}>
                <td style={cell}>{b.store}</td>
                <td style={cell}>{nf(b.lines)}</td>
                <td style={{ ...cell, color: '#16a34a' }}>{nf(b.active)}</td>
                <td style={{ ...cell, color: '#dc2626' }}>{nf(b.churned)}</td>
                <td style={{ ...cell, color: 'var(--text2)' }}>{nf(b.unmatched)}</td>
                <td style={{ ...cell, fontWeight: 600 }}>{pct(b.retention_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ ...panel, overflowX: 'auto' }}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>Subscriber feed coverage</div>
        <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>
          A month that was never loaded cannot answer a retention window. It is reported here rather
          than being absorbed into the numbers as loss.
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead><tr>{['Month', 'Loaded', 'Rows matched'].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {(d.feed_coverage || []).map((f: any) => (
              <tr key={f.period}>
                <td style={cell}>{f.label || f.period}</td>
                <td style={{ ...cell, color: f.loaded ? '#16a34a' : '#b45309' }}>
                  {f.loaded ? 'loaded' : 'not loaded'}
                </td>
                <td style={cell}>{nf(f.matched_rows)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}


// ══════════════════════════════════════════════════════════════════════════════════════════════
// REPORT 3
// ══════════════════════════════════════════════════════════════════════════════════════════════
function RoiTab({ d, onSaved }: { d: any; onSaved: () => void }) {
  return (
    <>
      <div style={{ ...panel, borderLeft: '3px solid #2563eb', marginBottom: 12, fontSize: 12,
                    color: 'var(--text2)', lineHeight: 1.55 }}>
        <div style={{ fontWeight: 700, color: 'var(--text)', marginBottom: 4 }}>
          How the commission figure is arrived at
        </div>
        <div>{d.commission_bases?.[d.commission_basis]}</div>
        <div style={{ marginTop: 6 }}>
          An ROI is only shown when every cost is known. Where one is not, the report says which and
          asks for it — it is never filled in as $0.00.
        </div>
      </div>
      {(d.days || []).map((row: any) => (
        <RoiDay key={`${row.store}|${row.trans_date}`} row={row} onSaved={onSaved} />
      ))}
      {(d.notes || []).length > 0 && (
        <div style={{ ...panel, fontSize: 12, color: 'var(--text2)' }}>
          {(d.notes || []).map((n: string, i: number) => <div key={i}>{n}</div>)}
        </div>
      )}
    </>
  )
}

function RoiDay({ row, onSaved }: { row: any; onSaved: () => void }) {
  const [open, setOpen] = useState(false)
  const [spend, setSpend] = useState('')
  const [payroll, setPayroll] = useState('')
  const [units, setUnits] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const unpriced: any[] = row.phones?.unpriced || []

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      const body: any = {
        store_code: row.store_code, trans_date: row.trans_date,
        event_id: row.event?.id || null,
        title: row.event_prompt?.title,
        phone_unit_costs: Object.fromEntries(
          Object.entries(units).filter(([, v]) => v !== '')),
      }
      if (spend !== '') body.planned_spend = Number(spend)
      if (payroll !== '') body.payroll_cost = Number(payroll)
      await api('/api/v1/marketing/event-sales/roi/link-event',
                { method: 'POST', body: JSON.stringify(body) })
      setMsg('Saved. Re-running the report…')
      onSaved()
    } catch (e: any) {
      setMsg(e?.message || 'Could not save.')
    } finally { setBusy(false) }
  }

  return (
    <div style={{ ...panel, marginBottom: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            {row.store} · {row.trans_date}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>
            {row.event_linked
              ? <>Linked to event <b>{row.event?.title}</b> ({row.event?.status})</>
              : <span style={{ color: '#b45309' }}>
                  No event record covers this store and date — the report still runs, and what it
                  cannot derive is asked for below.
                </span>}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase', color: 'var(--text2)' }}>ROI</div>
          <div style={{ fontSize: 24, fontWeight: 700,
                        color: row.roi_pct === null ? 'var(--text2)' : (row.net >= 0 ? '#16a34a' : '#dc2626') }}>
            {row.roi_pct === null ? 'Not computed' : pct(row.roi_pct)}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)' }}>
            {row.net === null ? '' : `net ${money(row.net)}`}
          </div>
        </div>
      </div>

      {row.reason && (
        <div style={{ marginTop: 8, fontSize: 12, color: '#b45309' }}>{row.reason}</div>
      )}

      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 10 }}>
        <Tile label="Commission (allocated)" value={money(row.commission_received)}
              sub={row.commission_allocation?.share !== null && row.commission_allocation?.share !== undefined
                ? `${nf(row.commission_allocation?.day_register_activations)} of ${nf(row.commission_allocation?.store_month_activations)} store activations that month`
                : (row.commission_allocation?.note || 'no share to allocate by')} />
        {(row.cost_components || []).map((c: any) => (
          <Tile key={c.kind} label={c.label}
                value={c.basis === 'prompt_required' ? 'Not known' : money(c.amount)}
                color={c.basis === 'prompt_required' ? '#b45309' : undefined}
                sub={c.basis === 'derived' ? `derived · ${c.source}`
                  : c.basis === 'entered' ? 'entered by a person' : 'needs a figure'} />
        ))}
      </div>

      <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
        {(row.cost_components || []).filter((c: any) => c.note).map((c: any) => (
          <div key={c.kind} style={{ marginBottom: 3 }}><b>{c.label}:</b> {c.note}</div>
        ))}
      </div>

      <button style={{ ...btn, marginTop: 10 }} onClick={() => setOpen(o => !o)}>
        {open ? 'Close' : (row.event_linked ? 'Correct the cost' : 'Enter the cost / create the event')}
      </button>

      {open && (
        <div style={{ marginTop: 10, display: 'grid', gap: 10,
                      gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
          <div>
            <span style={lbl}>Cost to set up the event ($)</span>
            <input style={input} value={spend} onChange={e => setSpend(e.target.value)}
                   placeholder="e.g. 500" inputMode="decimal" />
          </div>
          <div>
            <span style={lbl}>Payroll paid for the event ($)</span>
            <input style={input} value={payroll} onChange={e => setPayroll(e.target.value)}
                   placeholder="leave blank to use the scheduled/clocked hours" inputMode="decimal" />
          </div>
          {unpriced.map((p: any) => (
            <div key={p.key}>
              <span style={lbl}>Cost of {p.product_desc || p.key} ({nf(p.qty)} given away) ($)</span>
              <input style={input} value={units[p.key] || ''} inputMode="decimal"
                     onChange={e => setUnits(u => ({ ...u, [p.key]: e.target.value }))}
                     placeholder="unit cost of the unlocked phone" />
            </div>
          ))}
          <div style={{ gridColumn: '1 / -1' }}>
            <button style={btnPrimary} onClick={save} disabled={busy}>
              {busy ? 'Saving…' : (row.event_linked ? 'Save the cost' : 'Create the event and save the cost')}
            </button>
            {!row.event_linked && (
              <span style={{ marginLeft: 10, fontSize: 12, color: 'var(--text2)' }}>
                Creates “{row.event_prompt?.title}” with only what is needed to compute the cost.
              </span>
            )}
            {msg && <div style={{ marginTop: 6, fontSize: 12 }}>{msg}</div>}
          </div>
        </div>
      )}
    </div>
  )
}
