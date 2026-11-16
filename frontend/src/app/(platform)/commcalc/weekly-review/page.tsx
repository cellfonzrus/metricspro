'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, getActiveOrg } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
import ReportExportBar from '@/components/ReportExportBar'
import DrillDownPanel, { type DrillPanel } from '@/components/DrillDownPanel'
import {
  WR_WINDOW_WORD, detailCount, reviewsOf, rowMoney, rowText, screenFigure, weeklyReviewSheets,
  type Review, type ReviewItem, type ReviewPack, type ReviewRow,
} from '@/lib/weekly-review-export'

// WEEKLY PERFORMANCE REVIEW — the Monday review pack (owner 2026-11-16, index §COMMCALC-76).
//
// Owner, verbatim: *"Add the performance deck to the management tile but allow access only for the
// admins , make this report every week at the end of the week so the same can be reviewed every
// monday with the dm, it should have invenotry aging for rteh repsective carrier also as one of the
// items to review., make a separate report for each market and one consolidated for the entire
// company , is there any way we can make this on demand"* — then *"let us make it on demand"*.
//
// THIS PAGE RENDERS AND NEVER COMPUTES. Every figure, every state and every reason is the server's
// (`commcalc/weekly_review.py`), which in turn QUOTES the report that already owns each number. In
// particular this screen never fills a blank with a 0: a blank means nobody measured it, and the
// item says so in its own words beside the figure.
//
// ACCESS. Administrators only, and the refusal is the SERVER'S (`weekly_review.review_allowed`).
// The nav entry carries `module: 'admin'` so the tile is not offered to anyone else, but hiding a
// tile is not access control — a typed URL lands on the 403 below.
//
// STANDARD FILTERS (RULE FIVE §3d) — the period and the markets. `stores` is deliberately NOT shown:
// the pack's grain is a MARKET and the company, and a single-store review is the Gross Profit report
// rather than this one. The deviation is documented here, as the doctrine requires, rather than the
// control being silently dropped.

const orgParam = () => { const o = getActiveOrg(); return o ? `&org_id=${encodeURIComponent(o)}` : '' }
const iso = (d: Date) => d.toISOString().slice(0, 10)

type Pack = ReviewPack & {
  config?: { week_ends_on: number; weekly_run_enabled: boolean }
  filters?: { markets: string[] }
  error?: string
}
type Refusal = { reason: string; report: string | null }
type ReviewWithDrill = Review & {
  drilldown?: Record<string, DrillPanel>
  drilldown_refused?: Record<string, Refusal>
}

const card: React.CSSProperties = { padding: '14px 16px', marginBottom: 12 }
const tabBtn = (on: boolean): React.CSSProperties => ({
  padding: '6px 12px', borderRadius: 8, fontSize: 13, cursor: 'pointer',
  border: `1px solid ${on ? 'var(--accent)' : 'var(--border)'}`,
  background: on ? 'var(--surface2)' : 'var(--surface)', fontWeight: on ? 700 : 400,
})

function ItemCard({ item, panel, refusal }: {
  item: ReviewItem
  panel?: DrillPanel
  refusal?: Refusal
}) {
  const [open, setOpen] = useState(false)
  const measured = item.state === 'measured'
  return (
    <div className="card" style={card}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: 14 }}>{item.label}</div>
          <div style={{ fontSize: 11, color: 'var(--text3)' }}>
            {WR_WINDOW_WORD[item.window] || item.window} · {item.source}
          </div>
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: measured ? 'var(--text)' : 'var(--text3)' }}>
          {screenFigure(item)}
        </div>
      </div>

      {/* NOT MEASURED IS SAID, NOT DRAWN AS A ZERO. */}
      {!measured && (
        <div style={{ marginTop: 8, fontSize: 13, color: 'var(--text2)' }}>
          <b>Not measured.</b> {item.reason}
        </div>
      )}

      {measured && !!item.rows.length && <ItemRows item={item} />}

      {measured && item.key === 'profit' && detailCount(item.detail, 'stores_losing_money') > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
          {detailCount(item.detail, 'stores_losing_money')} store(s) lost money this period.
        </div>
      )}
      {measured && item.key === 'accessory' && detailCount(item.detail, 'stores_without_a_target') > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
          {detailCount(item.detail, 'stores_without_a_target')} store(s) have no accessory target saved, so they are
          listed without a gap rather than scored against another store&rsquo;s goal.
        </div>
      )}
      {measured && item.key === 'aging'
        && detailCount(item.detail, 'carriers_with_no_declared_window') > 0 && (
        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>
          {detailCount(item.detail, 'carriers_with_no_declared_window')} carrier(s) have not declared a selling
          window, so the house default answered for them.
        </div>
      )}

      {/* WHERE THE FIGURE CAME FROM — the shared panel where the relation behind every row is
          certain, and the server's own refusal, naming the report that does answer, where it is
          not. */}
      {panel && (
        <div style={{ marginTop: 10 }}>
          <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setOpen(!open)}>
            {open ? '▾' : '▸'} Where this came from
          </button>
          {open && <DrillDownPanel panel={panel} />}
        </div>
      )}
      {!panel && refusal && (
        <div style={{ marginTop: 10, fontSize: 11, color: 'var(--text3)' }}>
          {refusal.reason}
          {refusal.report && <> — <a href={refusal.report}>open that report</a></>}
        </div>
      )}
    </div>
  )
}

/** The rows behind a measured item, in the shape that item carries. Rendering only; the server
 *  decided what a row says. */
function ItemRows({ item }: { item: ReviewItem }) {
  const head: React.CSSProperties = { textAlign: 'left', fontSize: 11, color: 'var(--text3)',
                                      padding: '4px 8px 4px 0', fontWeight: 600 }
  const cell: React.CSSProperties = { fontSize: 12, padding: '3px 8px 3px 0' }
  const rows = item.rows.slice(0, 25)
  const headers: Record<string, string[]> = {
    profit: ['Store', 'Revenue', 'Net profit'],
    accessory: ['Store', 'Target', 'Achieved', 'Gap'],
    commission: ['Rep', 'Store', 'Payout'],
    rep_watch: ['Rep', 'Store', 'Verdict'],
    aging: ['Carrier', 'Sell within', 'Devices', 'Past the window', 'Owed'],
    payroll: ['Store', 'Payroll'],
  }
  const cells = (r: ReviewRow): string[] => {
    switch (item.key) {
      case 'profit': return [rowText(r.address || r.store_code), rowMoney(r.revenue),
                             rowMoney(r.net_profit)]
      // A store with no saved target says so in the cell: a gap of "—" could be read as nothing
      // owing, and nobody set it a goal at all.
      case 'accessory': return [rowText(r.store_code), rowMoney(r.target), rowMoney(r.achieved),
                                r.gap === null ? 'no target saved' : rowMoney(r.gap)]
      case 'commission': return [rowText(r.rep), rowText(r.store_code), rowMoney(r.payout)]
      case 'rep_watch': return [rowText(r.rep), rowText(r.store_code), rowText(r.verdict)]
      case 'aging': return [rowText(r.label),
                            r.sell_within_days === null || r.sell_within_days === undefined
                              ? '—' : `${r.sell_within_days} days`,
                            rowText(r.devices), rowText(r.missed), rowMoney(r.owed)]
      case 'payroll': return [rowText(r.store_code), rowMoney(r.amount)]
      default: return ['—']
    }
  }
  const cols = headers[item.key]
  if (!cols) return null
  return (
    <div style={{ marginTop: 10, overflowX: 'auto' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%' }}>
        <thead><tr>{cols.map(h => <th key={h} style={head}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cells(r).map((c, j) => <td key={j} style={cell}>{c}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {item.rows.length > rows.length && (
        <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4 }}>
          Showing {rows.length} of {item.rows.length} — the file carries them all.
        </div>
      )}
    </div>
  )
}

export default function WeeklyReviewPage() {
  const today = new Date()
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({
    ...emptyStandardFilter(iso(today)), periodTo: iso(today),
  }))
  const [pack, setPack] = useState<Pack | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [scope, setScope] = useState(0)
  const [refresh, setRefresh] = useState(0)   // the Refresh button, as a dependency the effect can see

  const asOf = filt.periodTo || filt.period || iso(today)

  // The markets as ONE dependency value: an array literal would be a new object every render and
  // the effect would fetch forever.
  const marketKey = (filt.markets || []).join(',')

  // THE FETCH IS THE EXTERNAL SYSTEM THIS EFFECT SYNCHRONISES WITH, and `loading` is derived from
  // the request rather than set synchronously in the effect body (react-hooks/set-state-in-effect —
  // the same shape `/closing/declared-vs-captured` settled on). The old answer stays on screen
  // while a filter change re-fetches, instead of flashing the page back to "Loading…".
  useEffect(() => {
    let live = true
    const q = new URLSearchParams({ as_of: asOf })
    for (const m of marketKey ? marketKey.split(',') : []) q.append('markets', m)
    api(`/api/v1/commcalc/weekly-review?${q.toString()}${orgParam()}`)
      .then((d: Pack) => { if (live) { setPack(d); setScope(0); setErr(''); setLoading(false) } })
      .catch((e: unknown) => {
        if (live) {
          setPack(null)
          setErr(e instanceof Error ? e.message : String(e))
          setLoading(false)
        }
      })
    return () => { live = false }
  }, [asOf, marketKey, refresh])

  // The on-demand run is an EVENT, not an effect: it may set state directly.
  const generate = useCallback(() => {
    setGenerating(true); setErr('')
    const q = new URLSearchParams({ as_of: asOf })
    api(`/api/v1/commcalc/weekly-review/run-now?${q.toString()}${orgParam()}`, { method: 'POST' })
      .then((d: Pack) => { setPack(d); setScope(0) })
      .catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setGenerating(false))
  }, [asOf])

  const reviews = useMemo(() => reviewsOf(pack) as ReviewWithDrill[], [pack])
  const current = reviews[scope]
  const sheets = useMemo(() => weeklyReviewSheets(pack), [pack])

  return (
    <div style={{ padding: 20, maxWidth: 1180, margin: '0 auto' }}>
      <h1 style={{ fontSize: 20, fontWeight: 700, marginBottom: 4 }}>📋 Weekly Performance Review</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 6 }}>
        The week that closed before the meeting — one review per market and one consolidated for the
        company. Administrators only.
      </div>
      {pack?.week && (
        <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 10 }}>
          Week of <b>{pack.week.label}</b> ({pack.week.date_from} to {pack.week.date_to}) ·
          month-to-date items are for <b>{pack.period}</b>
        </div>
      )}

      <StandardFilterBar
        value={filt} onChange={setFilt} periodMode="range"
        show={{ period: true, stores: false, markets: true, reps: false }}
        optionsUrl="/api/v1/core/filter-options"
        right={
          <>
            <button className="btn btn-secondary" onClick={() => setRefresh(n => n + 1)}
                    disabled={loading}>
              &#8635; Refresh
            </button>
            <button className="btn" onClick={generate} disabled={generating}>
              {generating ? 'Generating…' : '▶ Generate now'}
            </button>
            {pack && !pack.error && (
              <ReportExportBar
                title={`Weekly Performance Review — week ${pack.week?.label || ''}`}
                subtitle={'Every item says whether it was measured and what it covers. An item '
                  + 'nobody measured exports blank, never 0.'}
                filename={`weekly_review_${pack.week?.week_ending || ''}`}
                sheets={sheets}
              />
            )}
          </>
        }
      />

      {err && (
        <div className="card" style={{ ...card, marginTop: 14, background: 'var(--c-bg-fef3c7)',
                                       color: 'var(--c-fg-92400e)' }}>
          {err.toLowerCase().includes('administrator')
            ? <>This report is restricted to administrators. {err}</>
            : <>The review could not be loaded. {err}</>}
        </div>
      )}

      {loading && !pack && <div style={{ marginTop: 16, color: 'var(--text2)' }}>Loading&hellip;</div>}

      {!!reviews.length && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '16px 0 12px' }}>
          {reviews.map((r, i) => (
            <button key={r.scope.label} style={tabBtn(i === scope)} onClick={() => setScope(i)}>
              {r.scope.label}
              <span style={{ fontSize: 11, color: 'var(--text3)', marginLeft: 6 }}>
                {r.coverage.measured}/{r.coverage.items} measured
              </span>
            </button>
          ))}
        </div>
      )}

      {current && (
        <>
          {current.scope.note && (
            <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
              ℹ︎ {current.scope.note}
            </div>
          )}
          {current.items.map(i => (
            <ItemCard key={i.key} item={i}
                      panel={(current.drilldown || {})[i.key]}
                      refusal={(current.drilldown_refused || {})[i.key]} />
          ))}
          <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 10 }}>
            {current.scope.store_codes.length} store(s) in this scope.
            Figures are read from the reports that own them; this page adds nothing to them.
            {pack?.config?.weekly_run_enabled === false
              && ' The standing end-of-week run is off, so the pack is produced on demand.'}
          </div>
        </>
      )}
    </div>
  )
}