'use client'
// CARRIER FEED vs THE STORE'S TRANSACTIONS — the per-metric difference, attributed.
//
// OWNER 2026-09-27, verbatim: "the comparison of the dlar report for numbers and the ones we are
// reporting in thr platform , they should be the same - the source of truth is the transaction done in
// thr store so all reporting should have the same data , create a report for the. Difference of thr
// incoming data from dlar and whatever you are using to assess the difference and show it it in
// management dashboard".
//
// THE PAGE RENDERS, IT NEVER DERIVES. GET /commcalc/dlar-vs-platform/{period} carries the comparison
// already made: the PLATFORM side is Executive MTD's own cells (the ONE new-activation count,
// line_class), the FEED side is raw_dlar_rep / raw_dlar_store as landed, and every difference arrives
// with its cause. Three headline numbers, and they are deliberately different things:
//   · ATTRIBUTED   — a counting-definition difference, exact, by named unit kind. Nothing to chase.
//   · UNDECIDABLE  — our stored slice of the feed is older than the period. Not a counting dispute and
//                    not a finding: it waits on a fresh pull. Shown, never hidden, never accusing.
//   · TO LOOK AT   — the feed slice is complete and nothing explains the gap. This is the output.
// A metric neither side reports is `not_reported` and renders as an em-dash — never a 0 that looks like
// a measurement (§19.28: a fabricated zero is what this whole family of defects was).
// RULE FIVE: <StandardFilterBar> core set filtering client-side over the already-span-scoped payload.
// Sorting through useTableSort / SortableTh (the one comparison home).
import { Fragment, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar from '@/components/ReportExportBar'
import StatTile from '@/components/StatTile'
import { SortableTh, useTableSort } from '@/components/SortableTh'
import { emptyStandardFilter, filterRows, type StandardFilterValue } from '@/lib/standard-filters'
import type { ExportSheet } from '@/lib/export'

const th: React.CSSProperties = { textAlign: 'left', padding: '8px 10px', fontSize: 12, color: 'var(--text2)', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const td: React.CSSProperties = { padding: '8px 10px', fontSize: 13, borderBottom: '1px solid var(--border)', verticalAlign: 'top' }
const num: React.CSSProperties = { ...td, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }

const CAUSE_STYLE: Record<string, { bg: string; bd: string; label: string }> = {
  counting_definition: { bg: 'seagreen', bd: 'seagreen', label: 'definition' },
  feed_vintage: { bg: 'goldenrod', bd: 'goldenrod', label: 'stale feed' },
  grain: { bg: 'slateblue', bd: 'slateblue', label: 'grain' },
  unattributed: { bg: 'crimson', bd: 'crimson', label: 'look at this' },
}

function chip(c: any, i: number) {
  const s = CAUSE_STYLE[c.cause] || { bg: 'gray', bd: 'gray', label: c.cause }
  return (
    <span key={i} title={c.detail}
      style={{ display: 'inline-block', margin: '1px 4px 1px 0', padding: '2px 7px', borderRadius: 20, fontSize: 11,
        background: `color-mix(in srgb, ${s.bg} 12%, var(--surface))`,
        border: `1px solid color-mix(in srgb, ${s.bd} 40%, var(--border))` }}>
      {s.label}{c.kind ? ` · ${c.kind}` : ''}{c.units ? ` ×${c.units}` : ''}
    </span>
  )
}

/** A side that does not report a metric is an em-dash, never 0. */
const cell = (v: any, state: string, kind: string) =>
  state === 'not_reported' ? <span style={{ color: 'var(--text2)' }} title="not reported — this side does not carry this metric">—</span>
    : <>{kind === 'rate' ? `${Number(v).toFixed(2)}%` : Number(v)}</>

// WYSIWYG export: exactly the comparable cells the table is showing, with their cause and the feed's
// own as-of date — an exported row that dropped the vintage would read as a counting dispute.
const EXPORT_COLS = [
  { header: 'Grain', get: (r: any) => r.grain },
  { header: 'Entity', get: (r: any) => r.entity },
  { header: 'Store', get: (r: any) => r.store },
  { header: 'Metric', get: (r: any) => r.metric },
  { header: 'Feed reports', get: (r: any) => r.feed, align: 'right' as const },
  { header: 'Store transactions', get: (r: any) => r.platform, align: 'right' as const },
  { header: 'Difference', get: (r: any) => r.delta, align: 'right' as const },
  { header: 'Decidable', get: (r: any) => r.decidable },
  { header: 'Accounted for by', get: (r: any) => r.cause },
  { header: 'Residual', get: (r: any) => r.residual, align: 'right' as const },
  { header: 'Feed as of', get: (r: any) => r.as_of },
]

function thisMonth() { return new Date().toISOString().slice(0, 7) }

export default function DlarVsPlatformPage() {
  const [filt, setFilt] = useState<StandardFilterValue>(() => emptyStandardFilter(thisMonth()))
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [grain, setGrain] = useState<'rep' | 'store'>('rep')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [onlyDiff, setOnlyDiff] = useState(true)

  const period = filt.period || thisMonth()
  useEffect(() => {
    setLoading(true); setErr('')
    api(`/api/v1/commcalc/dlar-vs-platform/${encodeURIComponent(period)}`)
      .then(setData)
      .catch(e => { setErr(e?.message || String(e)); setData(null) })
      .finally(() => setLoading(false))
  }, [period])

  const rows = useMemo(() => {
    const all: any[] = (grain === 'rep' ? data?.reps : data?.stores) || []
    const f = filterRows(all, { ...filt, period: '' }, { store: r => r.store, rep: r => r.label })
    return onlyDiff ? f.filter((r: any) => r.differing > 0) : f
  }, [data, grain, filt, onlyDiff])

  const getCell = (r: any, f: string) => (f === 'label' ? r.label : f === 'store' ? r.store : r[f])
  const { sorted, sort, toggle } = useTableSort(rows, getCell, { field: 'unattributed', dir: 'desc' })

  const s = data?.summary
  const sheets: ExportSheet[] = useMemo(() => [{
    name: 'Feed vs transactions',
    columns: EXPORT_COLS,
    rows: sorted.flatMap((r: any) => r.metrics.filter((m: any) => m.comparable).map((m: any) => ({
      grain: r.grain, entity: r.label, store: r.store, metric: m.label,
      feed: m.feed_state === 'not_reported' ? '' : m.feed,
      platform: m.platform_state === 'not_reported' ? '' : m.platform,
      delta: m.delta, decidable: m.decidable ? 'yes' : 'no',
      cause: (m.causes || []).map((c: any) => c.cause).join(' + '),
      residual: m.residual, as_of: r.vintage?.as_of || '',
    }))),
  }], [sorted])

  return (
    <div style={{ padding: 20, display: 'grid', gap: 14 }}>
      <div>
        <h1 style={{ margin: 0, fontSize: 22 }}>Feed vs store transactions</h1>
        <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text2)' }}>
          What the carrier&rsquo;s report claims, beside what the store&rsquo;s own transactions say — and
          what accounts for every difference. {s?.source_of_truth}
        </p>
      </div>

      <StandardFilterBar value={filt} onChange={setFilt} />

      {err && <div style={{ padding: 10, border: '1px solid crimson', borderRadius: 8, fontSize: 13 }}>{err}</div>}

      {!!s?.stopped_columns?.length && (
        <div style={{ padding: '8px 12px', borderRadius: 8, fontSize: 13,
          background: 'color-mix(in srgb, goldenrod 10%, var(--surface))',
          border: '1px solid color-mix(in srgb, goldenrod 40%, var(--border))' }}>
          <b>The feed stopped sending {s.stopped_columns.length} column(s) for everybody</b>{' '}
          — {s.stopped_columns.join(', ')}. Those metrics read &ldquo;not reported&rdquo; below rather
          than zero. One fact about the feed, not one finding per rep.
        </div>
      )}
      {!!s?.vintage?.grains && Object.entries(s.vintage.grains).some(([, g]: any) => g?.complete === false) && (
        <div style={{ padding: '8px 12px', borderRadius: 8, fontSize: 13,
          background: 'color-mix(in srgb, goldenrod 10%, var(--surface))',
          border: '1px solid color-mix(in srgb, goldenrod 40%, var(--border))' }}>
          <b>Our stored slice of the feed does not reach the end of this period.</b>{' '}
          {Object.entries(s.vintage.grains).map(([k, g]: any) =>
            `${k.replace('raw_dlar_', '')} as of ${g.as_of}${g.days_short ? ` (${g.days_short} day(s) short)` : ''}`).join(' · ')}
          . Differences on those rows are marked <i>undecidable</i> — they are not counting disputes and
          re-pulling the report is what settles them.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 10 }}>
        <StatTile label="Compared" value={s?.cells_compared ?? '—'} sub="metric cells both sides carry" />
        <StatTile label="Agree" value={s ? s.cells_compared - s.cells_differing : '—'} />
        <StatTile label="Attributed" value={s ? s.cells_differing - s.cells_unattributed - s.cells_undecidable : '—'} sub="a counting definition explains it, exactly" />
        <StatTile label="Undecidable" value={s?.cells_undecidable ?? '—'} sub="our feed slice is older than the period — re-pull to settle" />
        <StatTile label="To look at" value={s?.cells_unattributed ?? '—'} sub="the feed slice is complete and nothing explains the gap" />
      </div>

      <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 6 }}>
          {(['rep', 'store'] as const).map(g => (
            <button key={g} type="button" onClick={() => setGrain(g)}
              style={{ padding: '4px 12px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                border: '1px solid var(--border)',
                background: grain === g ? 'var(--surface2)' : 'transparent' }}>
              {g === 'rep' ? 'Per rep' : 'Per store'}
            </button>
          ))}
        </div>
        <label style={{ fontSize: 13, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyDiff} onChange={e => setOnlyDiff(e.target.checked)} />
          only rows with a difference
        </label>
        <ReportExportBar title="Feed vs store transactions" subtitle={period}
          sheets={sheets} filename={`feed-vs-transactions-${period}`} />
      </div>

      {loading ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading…</div> : (
        <div style={{ overflowX: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <SortableTh field="label" sort={sort} onSort={toggle} style={th}>{grain === 'rep' ? 'Rep' : 'Store'}</SortableTh>
                <SortableTh field="store" sort={sort} onSort={toggle} style={th}>Store</SortableTh>
                <SortableTh field="compared" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>Compared</SortableTh>
                <SortableTh field="agreeing" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>Agree</SortableTh>
                <SortableTh field="differing" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>Differ</SortableTh>
                <SortableTh field="undecidable" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>Undecidable</SortableTh>
                <SortableTh field="unattributed" sort={sort} onSort={toggle} style={{ ...th, textAlign: 'right' }}>To look at</SortableTh>
                <th style={th}>Feed as of</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((r: any) => {
                const isOpen = !!open[r.key]
                return (
                  <Fragment key={r.key}>
                    <tr onClick={() => setOpen(o => ({ ...o, [r.key]: !o[r.key] }))}
                      style={{ cursor: 'pointer' }}>
                      <td style={td}>{isOpen ? '▾' : '▸'} {r.label}</td>
                      <td style={td}>{r.store}</td>
                      <td style={num}>{r.compared}</td>
                      <td style={num}>{r.agreeing}</td>
                      <td style={num}>{r.differing}</td>
                      <td style={num}>{r.undecidable || ''}</td>
                      <td style={{ ...num, fontWeight: r.unattributed ? 600 : 400,
                        color: r.unattributed ? 'crimson' : undefined }}>{r.unattributed || ''}</td>
                      <td style={{ ...td, color: 'var(--text2)', fontSize: 12 }}>
                        {r.vintage?.as_of || '—'}
                        {r.vintage?.upper_bound ? ' (≤)' : ''}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr>
                        <td style={{ ...td, background: 'var(--surface2)' }} colSpan={8}>
                          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                            <thead>
                              <tr>
                                <th style={th}>Metric</th>
                                <th style={{ ...th, textAlign: 'right' }}>Feed reports</th>
                                <th style={{ ...th, textAlign: 'right' }}>Store transactions</th>
                                <th style={{ ...th, textAlign: 'right' }}>Difference</th>
                                <th style={th}>Accounted for by</th>
                              </tr>
                            </thead>
                            <tbody>
                              {r.metrics.map((m: any) => (
                                <tr key={m.metric}>
                                  <td style={td}>{m.label}</td>
                                  <td style={num}>{cell(m.feed, m.feed_state, m.kind)}</td>
                                  <td style={num}>{cell(m.platform, m.platform_state, m.kind)}</td>
                                  <td style={num}>{m.delta === null ? <span style={{ color: 'var(--text2)' }}>—</span>
                                    : (m.agree ? '0' : (m.delta > 0 ? `+${m.delta}` : m.delta))}</td>
                                  <td style={td}>
                                    {m.agree ? <span style={{ color: 'var(--text2)', fontSize: 12 }}>agrees</span>
                                      : !m.comparable ? <span style={{ color: 'var(--text2)', fontSize: 12 }}>not comparable — one side does not report it</span>
                                        : (m.causes || []).map(chip)}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
              {!sorted.length && (
                <tr><td style={{ ...td, color: 'var(--text2)' }} colSpan={8}>
                  No rows for this period and selection.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {!!Object.keys(s?.entities_with_several_feed_rows || {}).length && (
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>
          The feed publishes per rep per <b>door</b>. {Object.keys(s.entities_with_several_feed_rows).length}{' '}
          rep(s) have several feed rows, so a single platform figure is not over the same population —
          those rows are marked undecidable rather than compared against one of them.
        </div>
      )}
      {data?.new_activation_rule && (
        <div style={{ fontSize: 12, color: 'var(--text2)' }}>
          New activations counted as: {data.new_activation_rule.classes?.join(' + ')} less{' '}
          {data.new_activation_rule.exclusions?.length ? data.new_activation_rule.exclusions.join(', ') : 'nothing'}{' '}
          ({data.new_activation_rule.source}). {data.new_activation_rule.sentence}
        </div>
      )}
    </div>
  )
}
