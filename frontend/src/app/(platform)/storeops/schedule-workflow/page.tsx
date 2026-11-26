'use client'
// Scheduling a store-week — the GUIDED, RESUMABLE workflow (index §STOREOPS-15).
//
// Owner 2026-11-25: *"...and then simlar workflow for scheduling"*, said of the daily-closing
// workflow a new person can start and come back to.
//
// THIS PAGE DECIDES NOTHING. Every status word, every lamp, every "pick up here" comes from
// GET /storeops/schedule-workflow, which derives them in ONE place (core/guided_workflow) shared
// with the employee-setup and payroll workflows. The page words no status of its own.
//
// AND IT WRITES NOTHING. Each step carries the address of the screen that already does that work —
// the hours-budget page, the time-off board, the schedule grid — so this is a map and a set of
// doors, never a second place to lay out a shift.
//
// THE WEEK IS THE TENANT'S OWN. The server reads this company's declared week start and sends back
// the seven dates; this page never snaps to a Monday of its own.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import StandardFilterBar from '@/components/StandardFilterBar'
import DateField from '@/components/DateField'
import { api } from '@/lib/client'
import { ExportButtons, type ExportColumn, type ExportPayload } from '@/lib/export'
import { SendReportButton } from '@/lib/send-report'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'
// A reader never meets a table name: the one home for a data source's plain words (§19.38).
import { sourceLabel } from '@/lib/sourceLabels'

type Step = {
  key: string
  title: string
  why: string
  required: boolean
  screen: string
  status: string
  label: string
  needs_input: string[]
  blocked_by: string[]
  detail: Record<string, unknown>
}
type StepDecl = { key: string; title: string; why: string; required: boolean; screen: string }
type StatusDecl = { key: string; label: string }
type StoreWeek = {
  store_code: string
  address?: string
  market?: string
  is_active?: boolean
  week_start: string
  week_end: string
  scheduled_hours: number
  status: string
  label: string
  resume: string | null
  resume_title: string
  progress?: { total: number; done: number; required_total: number; required_done: number }
  blocking: string[]
  steps: Step[]
  refused?: string
}
type Stuck = { key: string; title: string; count: number }
type Board = {
  ready: boolean
  week_start: string
  week_end: string
  days: { date: string; name: string }[]
  declared: {
    week_start_dow: number
    week_start_day: string
    week_start_declared: boolean
    open_dows: number[]
    open_days_declared: boolean
  }
  steps: StepDecl[]
  statuses: StatusDecl[]
  summary: { total: number; by_status: Record<string, number>; finished: number; open: number; stuck_at: Stuck[] }
  rows: StoreWeek[]
  inactive_hidden: number
  blockers: { table: string; reason: string }[]
}

// The lamp colour per status. The WORDS come from the server; only the colour is the page's, and
// each is a theme token so it reads in both themes (frontend/prove_theme_tokens.mjs).
const DOT: Record<string, string> = {
  not_started: 'var(--text2)',
  in_progress: 'var(--c-bg-d97706)',
  needs_input: 'var(--c-bg-dc2626)',
  skipped: 'var(--text2)',
  done: 'var(--c-bg-059669)',
}

function errText(e: unknown): string {
  if (e instanceof Error) return e.message
  return String(e)
}

const card: React.CSSProperties = {
  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, padding: 14,
}
const chip: React.CSSProperties = {
  fontSize: 11, padding: '2px 8px', borderRadius: 999, border: '1px solid var(--border)',
  color: 'var(--text2)', whiteSpace: 'nowrap',
}
const th: React.CSSProperties = {
  textAlign: 'left', fontSize: 12, color: 'var(--text2)', fontWeight: 600, padding: '8px 10px',
  borderBottom: '1px solid var(--border)',
}
const td: React.CSSProperties = { fontSize: 13, padding: '8px 10px', verticalAlign: 'top' }
const btn: React.CSSProperties = {
  padding: '5px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 12,
  cursor: 'pointer', background: 'var(--surface)', color: 'var(--text)',
}

function Dot({ status }: { status: string }) {
  return (
    <span aria-hidden style={{
      display: 'inline-block', width: 9, height: 9, borderRadius: 999,
      background: DOT[status] || 'var(--text2)', marginRight: 6,
    }} />
  )
}

/** The week this page opens on: today's week, which the SERVER then snaps to the company's own
 *  week start. A date is sent rather than a week number so no two surfaces can count weeks apart. */
function today(): string {
  return new Date().toISOString().slice(0, 10)
}

export default function ScheduleWorkflowPage() {
  const [data, setData] = useState<Board | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(true)
  const [week, setWeek] = useState<string>(today())
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())
  const [open, setOpen] = useState<string>('')

  // The SERVER filters, so what is on screen is what exports and what a reader is allowed to see.
  const url = useMemo(() => {
    const q = new URLSearchParams()
    q.set('week_start', week)
    if (filter.stores?.length) q.set('stores', filter.stores.join(','))
    if (filter.markets?.length) q.set('markets', filter.markets.join(','))
    if (filter.showInactive) q.set('include_inactive', 'true')
    return `/api/v1/storeops/schedule-workflow?${q.toString()}`
  }, [week, filter])

  // NO setState IN THE EFFECT BODY: the request starts there and every state change happens in a
  // callback, so a filter change cannot cascade renders (react-hooks/set-state-in-effect).
  const load = useCallback(() => api(url)
    .then((r: unknown) => { setData(r as Board); setMsg('') })
    .catch((e: unknown) => setMsg('❌ ' + errText(e)))
    .finally(() => setBusy(false)), [url])

  useEffect(() => { void load() }, [load])
  const reload = useCallback(() => { setBusy(true); void load() }, [load])

  const rows = useMemo(() => data?.rows || [], [data])
  const steps = useMemo(() => data?.steps || [], [data])
  const stepTitle = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of steps) m[s.key] = s.title
    return m
  }, [steps])

  const shiftWeek = useCallback((weeks: number) => {
    const base = data?.week_start || week
    const d = new Date(`${base}T00:00:00Z`)
    d.setUTCDate(d.getUTCDate() + weeks * 7)
    setWeek(d.toISOString().slice(0, 10))
  }, [data, week])

  const columns: ExportColumn[] = useMemo(() => {
    const base: ExportColumn[] = [
      { header: 'Store', field: 'store_code', role: 'store', get: (r: StoreWeek) => r.store_code },
      { header: 'Market', field: 'market', get: (r: StoreWeek) => r.market || '' },
      { header: 'Week', field: 'week', get: (r: StoreWeek) => `${r.week_start} to ${r.week_end}` },
      { header: 'Scheduling', field: 'label', get: (r: StoreWeek) => r.label },
      { header: 'Picks up at', field: 'resume_title', get: (r: StoreWeek) => r.resume_title },
      {
        header: 'Hours scheduled', field: 'scheduled_hours', type: 'number', agg: 'sum',
        get: (r: StoreWeek) => r.scheduled_hours,
      },
      {
        header: 'Steps done', field: 'done', type: 'number', agg: 'none',
        get: (r: StoreWeek) => (r.progress ? `${r.progress.done} of ${r.progress.total}` : ''),
      },
    ]
    for (const s of steps) {
      base.push({
        header: s.title, field: `step_${s.key}`,
        get: (r: StoreWeek) => r.steps.find(x => x.key === s.key)?.label || '',
      })
    }
    base.push({
      header: 'Waiting on', field: 'needs',
      get: (r: StoreWeek) => r.steps.flatMap(x => x.needs_input).join('; '),
    })
    return base
  }, [steps])

  const payload = useCallback((): ExportPayload => ({
    title: 'Scheduling — where each store-week is',
    subtitle: data ? `Week of ${data.week_start} to ${data.week_end}` : '',
    filename: 'schedule-workflow',
    sheets: [{ name: 'Scheduling', columns, rows }],
  }), [columns, rows, data])

  return (
    <div style={{ padding: 18, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Scheduling a week</h1>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>
          Five steps per store, from the hours you may use to telling the team. Leave it half-done
          and come back — nothing is lost, because every step reads the real rota.
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '14px 0 0' }}>
        <button style={btn} onClick={() => shiftWeek(-1)} disabled={busy}>← Previous week</button>
        <DateField value={week} onChange={e => setWeek(e.target.value)}
          style={{ ...btn, cursor: 'text' }} aria-label="Any date in the week" />
        <button style={btn} onClick={() => shiftWeek(1)} disabled={busy}>Next week →</button>
        <ExportButtons payload={payload} compact csv />
        <SendReportButton exportPayload={payload} compact
          title="Scheduling — where each store-week is"
          filters={{ stores: filter.stores, markets: filter.markets }} />
        <button style={btn} onClick={reload} disabled={busy}>{busy ? 'Loading…' : '↻ Refresh'}</button>
      </div>

      {data && (
        <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
          Week of <strong>{data.week_start}</strong> to <strong>{data.week_end}</strong> — this
          company&apos;s week starts on <strong>{data.declared.week_start_day}</strong>
          {!data.declared.week_start_declared && ' (not set up yet, so the column default is being used)'}.
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <StandardFilterBar
          value={filter} onChange={setFilter}
          periodMode="none"
          optionsUrl="/api/v1/core/filter-options"
          inactiveHidden={data?.inactive_hidden ?? null}
        />
      </div>

      {msg && <div style={{ ...card, borderColor: 'var(--c-bg-dc2626)', marginBottom: 12 }}>{msg}</div>}

      {data && !data.ready && data.blockers.length > 0 && (
        <div style={{ ...card, marginBottom: 12 }}>
          <strong>Some of this could not be read, so the board may be incomplete.</strong>
          <ul style={{ margin: '6px 0 0 18px', fontSize: 13, color: 'var(--text2)' }}>
            {data.blockers.map((b, i) => (
              <li key={i}><strong>{sourceLabel(b.table) || 'a data source'}</strong> — {b.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {data && !data.declared.open_days_declared && (
        <div style={{ ...card, marginBottom: 12 }}>
          <strong>Which days each store opens is not set up yet.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
            So a day with nobody on it is listed for you to look at, rather than treated as a gap —
            a seven-day week is not assumed on your behalf.
          </div>
        </div>
      )}

      {/* THE SUMMARY — how many weeks are ready, and the step stores are getting stuck at. */}
      {data && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ ...card, minWidth: 150 }}>
            <div style={{ fontSize: 11, color: 'var(--text2)' }}>Weeks ready</div>
            <div style={{ fontSize: 22, fontWeight: 700 }}>
              {data.summary.finished}<span style={{ fontSize: 13, color: 'var(--text2)' }}> of {data.summary.total}</span>
            </div>
          </div>
          {data.statuses.filter(s => (data.summary.by_status[s.key] || 0) > 0 && s.key !== 'done').map(s => (
            <div key={s.key} style={{ ...card, minWidth: 150 }}>
              <div style={{ fontSize: 11, color: 'var(--text2)' }}><Dot status={s.key} />{s.label}</div>
              <div style={{ fontSize: 22, fontWeight: 700 }}>{data.summary.by_status[s.key]}</div>
            </div>
          ))}
          {data.summary.stuck_at.length > 0 && (
            <div style={{ ...card, minWidth: 220 }}>
              <div style={{ fontSize: 11, color: 'var(--text2)' }}>Most stores are stopping at</div>
              <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
                {data.summary.stuck_at[0].title}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                {data.summary.stuck_at[0].count} {data.summary.stuck_at[0].count === 1 ? 'store' : 'stores'}
              </div>
            </div>
          )}
        </div>
      )}

      {/* THE STEPS, named once so a new person can read the shape of the job before starting. */}
      {steps.length > 0 && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>The five steps</div>
          <ol style={{ margin: 0, paddingLeft: 20, display: 'grid', gap: 4 }}>
            {steps.map(s => (
              <li key={s.key} style={{ fontSize: 13 }}>
                <Link href={s.screen}>{s.title}</Link>
                {!s.required && <span style={{ ...chip, marginLeft: 6 }}>optional</span>}
                <span style={{ color: 'var(--text2)' }}> — {s.why}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* THE BOARD. One row per store-week; click to open its rail. */}
      <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
          <thead>
            <tr>
              <th style={th}>Store</th>
              <th style={th}>Scheduling</th>
              <th style={th}>Picks up at</th>
              <th style={th}>Hours</th>
              <th style={th}>Steps done</th>
              <th style={th} />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !busy && (
              <tr><td style={{ ...td, color: 'var(--text2)' }} colSpan={6}>
                No stores match these filters.
              </td></tr>
            )}
            {rows.map(r => {
              const isOpen = open === r.store_code
              return (
                <tr key={r.store_code} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>
                      {r.store_code}
                      {r.is_active === false && <span style={{ ...chip, marginLeft: 6 }}>closed</span>}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text2)' }}>{r.market || ''}</div>
                  </td>
                  <td style={td}><Dot status={r.status} />{r.label}</td>
                  <td style={td}>
                    {r.resume_title || <span style={{ color: 'var(--text2)' }}>Nothing left</span>}
                  </td>
                  <td style={td}>{typeof r.scheduled_hours === 'number' ? r.scheduled_hours : '—'}</td>
                  <td style={td}>{r.progress ? `${r.progress.done} of ${r.progress.total}` : '—'}</td>
                  <td style={{ ...td, textAlign: 'right' }}>
                    <button style={btn} onClick={() => setOpen(isOpen ? '' : r.store_code)}>
                      {isOpen ? 'Hide steps' : 'Open steps'}
                    </button>
                  </td>
                </tr>
              )
            })}
            {rows.map(r => open === r.store_code && (
              <tr key={`${r.store_code}-rail`}>
                <td style={{ ...td, background: 'var(--bg)' }} colSpan={6}>
                  {r.refused && <div style={{ fontSize: 13, marginBottom: 8 }}>{r.refused}</div>}
                  <div style={{ display: 'grid', gap: 8 }}>
                    {r.steps.map((s, i) => (
                      <div key={s.key} style={{
                        display: 'flex', gap: 10, alignItems: 'flex-start',
                        opacity: s.blocked_by.length ? 0.6 : 1,
                      }}>
                        <div style={{ fontSize: 12, color: 'var(--text2)', width: 18, flex: '0 0 auto' }}>
                          {i + 1}.
                        </div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 600 }}>
                            <Dot status={s.status} />{s.title}
                            <span style={{ ...chip, marginLeft: 8 }}>{s.label}</span>
                            {r.resume === s.key && <span style={{ ...chip, marginLeft: 6 }}>pick up here</span>}
                            {!s.required && <span style={{ ...chip, marginLeft: 6 }}>optional</span>}
                          </div>
                          <div style={{ fontSize: 12, color: 'var(--text2)' }}>{s.why}</div>
                          {s.blocked_by.length > 0 && (
                            <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                              Waiting on: {s.blocked_by.map(b => stepTitle[b] || b).join(', ')}
                            </div>
                          )}
                          {s.needs_input.length > 0 && (
                            <ul style={{ margin: '4px 0 0 16px', fontSize: 12 }}>
                              {s.needs_input.map((n, j) => <li key={j}>{n}</li>)}
                            </ul>
                          )}
                          {Array.isArray(s.detail?.days_with_nobody) && (s.detail.days_with_nobody as string[]).length > 0 && (
                            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>
                              Nobody on: {(s.detail.days_with_nobody as string[]).join(', ')}
                              {typeof s.detail?.days_with_nobody_note === 'string'
                                && ` — ${s.detail.days_with_nobody_note as string}`}
                            </div>
                          )}
                          {typeof s.detail?.last_sent_at === 'string' && s.detail.last_sent_at && (
                            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
                              Last sent {s.detail.last_sent_at as string}
                            </div>
                          )}
                        </div>
                        <Link href={s.screen} style={{ ...btn, textDecoration: 'none', flex: '0 0 auto' }}>
                          Go →
                        </Link>
                      </div>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
