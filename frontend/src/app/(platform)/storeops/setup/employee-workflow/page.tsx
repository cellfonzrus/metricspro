'use client'
// Employee Setup — the GUIDED, RESUMABLE workflow (index §STOREOPS-13).
//
// Owner 2026-11-25: *"The way we have set up teh closing workflow , it is very easy for a new person
// to start and then pick up where tehy left off, need similar for the Employee set up in logical
// steps leading up to onboaring"*.
//
// THIS PAGE DECIDES NOTHING. Every status word, every lamp, every "pick up here" comes from
// GET /storeops/employee-setup, which derives them in ONE place (core/guided_workflow) shared with
// the scheduling and payroll workflows. The page words no status of its own — `statuses` arrives
// with the response and the lock harness fails the build if this file starts spelling them.
//
// AND IT WRITES NOTHING. Each step carries the address of the screen that already does that work,
// so this is a map and a set of doors — never a second place to type an employee's details.
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import StandardFilterBar from '@/components/StandardFilterBar'
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
type Person = {
  employee_id: string
  employee_name: string
  home_store: string
  is_active: boolean
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
  declared: { roles: string[]; pays_incentive_plans: boolean; hours_from_clock: boolean; hours_basis_mode: string }
  steps: StepDecl[]
  statuses: StatusDecl[]
  summary: { total: number; by_status: Record<string, number>; finished: number; open: number; stuck_at: Stuck[] }
  rows: Person[]
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

export default function EmployeeSetupWorkflowPage() {
  const [data, setData] = useState<Board | null>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(true)
  const [filter, setFilter] = useState<StandardFilterValue>(emptyStandardFilter())
  const [open, setOpen] = useState<string>('')

  // The SERVER filters, so what is on screen is what exports and what a reader is allowed to see.
  const url = useMemo(() => {
    const q = new URLSearchParams()
    if (filter.stores?.length) q.set('stores', filter.stores.join(','))
    if (filter.markets?.length) q.set('markets', filter.markets.join(','))
    if (filter.reps?.length) q.set('reps', filter.reps.join(','))
    if (filter.showInactive) q.set('include_inactive', 'true')
    const s = q.toString()
    return '/api/v1/storeops/employee-setup' + (s ? `?${s}` : '')
  }, [filter])

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

  const columns: ExportColumn[] = useMemo(() => {
    const base: ExportColumn[] = [
      { header: 'Employee', field: 'employee_name', get: (r: Person) => r.employee_name },
      { header: 'Employee ID', field: 'employee_id', get: (r: Person) => r.employee_id },
      { header: 'Home store', field: 'home_store', role: 'store', get: (r: Person) => r.home_store },
      { header: 'Setup', field: 'label', get: (r: Person) => r.label },
      { header: 'Picks up at', field: 'resume_title', get: (r: Person) => r.resume_title },
      {
        header: 'Steps done', field: 'done', type: 'number', agg: 'none',
        get: (r: Person) => (r.progress ? `${r.progress.done} of ${r.progress.total}` : ''),
      },
    ]
    for (const s of steps) {
      base.push({
        header: s.title, field: `step_${s.key}`,
        get: (r: Person) => r.steps.find(x => x.key === s.key)?.label || '',
      })
    }
    base.push({
      header: 'Waiting on', field: 'needs',
      get: (r: Person) => r.steps.flatMap(x => x.needs_input).join('; '),
    })
    return base
  }, [steps])

  const payload = useCallback((): ExportPayload => ({
    title: 'Employee Setup — where each person is',
    subtitle: data ? `${data.summary.finished} of ${data.summary.total} fully set up` : '',
    filename: 'employee-setup-workflow',
    sheets: [{ name: 'Employee Setup', columns, rows }],
  }), [columns, rows, data])

  return (
    <div style={{ padding: 18, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, margin: 0 }}>Employee Setup</h1>
        <span style={{ fontSize: 13, color: 'var(--text2)' }}>
          Six steps to a new person being ready, ending with their onboarding pack. Leave it
          half-done and come back — nothing is lost, because every step reads the real record.
        </span>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '14px 0 0' }}>
        <ExportButtons payload={payload} compact csv />
        <SendReportButton exportPayload={payload} compact
          title="Employee Setup — where each person is"
          filters={{ stores: filter.stores, markets: filter.markets, reps: filter.reps }} />
        <button style={btn} onClick={reload} disabled={busy}>{busy ? 'Loading…' : '↻ Refresh'}</button>
      </div>

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

      {data && data.declared.roles.length === 0 && (
        <div style={{ ...card, marginBottom: 12 }}>
          <strong>No roles are declared for this company yet.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
            Until they are, nobody&apos;s role can be checked — so every person reads as waiting on
            input rather than being measured against another company&apos;s role names.{' '}
            <Link href="/admin/roles">Declare the roles</Link>.
          </div>
        </div>
      )}

      {/* THE SUMMARY — how many are ready, and the step people are getting stuck at. */}
      {data && (
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 14 }}>
          <div style={{ ...card, minWidth: 150 }}>
            <div style={{ fontSize: 11, color: 'var(--text2)' }}>Fully set up</div>
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
              <div style={{ fontSize: 11, color: 'var(--text2)' }}>Most people are stopping at</div>
              <div style={{ fontSize: 15, fontWeight: 600, marginTop: 2 }}>
                {data.summary.stuck_at[0].title}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                {data.summary.stuck_at[0].count} {data.summary.stuck_at[0].count === 1 ? 'person' : 'people'}
              </div>
            </div>
          )}
        </div>
      )}

      {/* THE STEPS, named once so a new person can read the shape of the job before starting. */}
      {steps.length > 0 && (
        <div style={{ ...card, marginBottom: 14 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>The six steps</div>
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

      {/* THE BOARD. One row per person; click to open their rail. */}
      <div style={{ ...card, padding: 0, overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
          <thead>
            <tr>
              <th style={th}>Employee</th>
              <th style={th}>Home store</th>
              <th style={th}>Setup</th>
              <th style={th}>Picks up at</th>
              <th style={th}>Steps done</th>
              <th style={th} />
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !busy && (
              <tr><td style={{ ...td, color: 'var(--text2)' }} colSpan={6}>
                No people match these filters.
              </td></tr>
            )}
            {rows.map(p => {
              const isOpen = open === p.employee_id
              return (
                <tr key={p.employee_id} style={{ borderTop: '1px solid var(--border)' }}>
                  <td style={td}>
                    <div style={{ fontWeight: 600 }}>
                      {p.employee_name || p.employee_id}
                      {!p.is_active && <span style={{ ...chip, marginLeft: 6 }}>inactive</span>}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text2)' }}>{p.employee_id}</div>
                  </td>
                  <td style={td}>{p.home_store || <span style={{ color: 'var(--text2)' }}>—</span>}</td>
                  <td style={td}><Dot status={p.status} />{p.label}</td>
                  <td style={td}>
                    {p.resume_title || <span style={{ color: 'var(--text2)' }}>Nothing left</span>}
                  </td>
                  <td style={td}>
                    {p.progress ? `${p.progress.done} of ${p.progress.total}` : '—'}
                  </td>
                  <td style={{ ...td, textAlign: 'right' }}>
                    <button style={btn} onClick={() => setOpen(isOpen ? '' : p.employee_id)}>
                      {isOpen ? 'Hide steps' : 'Open steps'}
                    </button>
                  </td>
                </tr>
              )
            })}
            {rows.map(p => open === p.employee_id && (
              <tr key={`${p.employee_id}-rail`}>
                <td style={{ ...td, background: 'var(--bg)' }} colSpan={6}>
                  {p.refused && (
                    <div style={{ fontSize: 13, marginBottom: 8 }}>{p.refused}</div>
                  )}
                  <div style={{ display: 'grid', gap: 8 }}>
                    {p.steps.map((s, i) => (
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
                            {p.resume === s.key && <span style={{ ...chip, marginLeft: 6 }}>pick up here</span>}
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
                          {Array.isArray(s.detail?.warnings) && (s.detail.warnings as string[]).map((w, j) => (
                            <div key={j} style={{ fontSize: 12, marginTop: 4 }}>⚠ {w}</div>
                          ))}
                          {typeof s.detail?.why === 'string' && s.status === 'skipped' && (
                            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 2 }}>
                              {s.detail.why as string}
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
