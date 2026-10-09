'use client'
import { useState, useEffect, useCallback } from 'react'
import { api, fmt } from '@/lib/client'
import { usePeriod } from '@/lib/period-context'
import { useAuth } from '@/lib/auth-context'
import { canDeclareMonthFocus } from '@/lib/rbac'
import Link from 'next/link'

// THIS MONTH'S FOCUS (index §63, owner ask 2026-10-09) — "in the beginning of the month Market manager
// or above when they log in should define the focus for the month … the notification will come every
// week on Monday on the platform".
//
// The page renders and NEVER decides. What is outstanding, which week's check-in is due, whether a
// declared spiff is actually in the pay config, and which plays the month's numbers support are all
// computed by `backend/app/modules/commcalc/month_focus.py` and arrive on the payload — so this
// screen, the Monday banner and the login popup cannot disagree about what the month still owes.
//
// NOTHING HERE PAYS ANYBODY. A temporary spiff declared on this page is an INTENT with a status; the
// money is `payout_config.custom_spiffs`, edited on the commission settings page. The "Declared
// against live" panel exists to show the two sides disagreeing, in both directions.

type Item = { key: string; severity: string; label: string; detail: string; count: number
  deep_link: string; deep_link_label: string }
type Play = { key: string; rank: number; title: string; why: string; move: string
  evidence: Record<string, any>; deep_link: string; also?: string[] }
type Spiff = { name: string; rate: number | null; unit: string; window_start: string
  window_end: string; cap: number | null; stores: string[]; status: string; note: string }
type Decl = {
  headline: string; categories: string[]; note: string
  spiff_initiative: { pay_type: string; label: string; rationale: string }
  temp_spiffs: Spiff[]; target_note: string; declared_by: string; declared_at: string
  checkins: { week_start: string; confirmed_at: string; confirmed_by: string; changes: string[]
    note: string }[]
}
type Opt = { type: string; component: string | null; dollars: number | null; units: number | null
  rate_per_unit: number | null }
type Payload = {
  period: string; today: string; declaration: Decl; declared: boolean
  outstanding: Item[]; plays: Play[]
  reconciliation: { declared_not_live: string[]; live_not_declared: string[]; matched: string[]
    proposed: string[] }
  live_spiff_names: string[]
  checkins: { weekday: number; days: string[]; current: string | null; confirmed: string[] }
  declaration_window_end: string | null
  targets: { stores_total: number | null; with_saved_target: number | null
    without_saved_target: string[] }
  initiative_options: Opt[]
  signal_meta: Record<string, any>
  may_declare?: boolean
  ready: boolean; hint: string | null
}

const SEV: Record<string, { bg: string; fg: string; icon: string }> = {
  error: { bg: '#fee2e2', fg: '#991b1b', icon: '⚑' },
  warning: { bg: '#fef3c7', fg: '#92400e', icon: '⚠︎' },
  info: { bg: 'var(--surface2)', fg: 'var(--text2)', icon: 'ℹ︎' },
}
const STATUSES = ['proposed', 'approved', 'live', 'ended']
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']

const inp: React.CSSProperties = { padding: '6px 9px', borderRadius: 7, border: '1px solid var(--border)',
  fontSize: 13, background: 'var(--surface)', color: 'var(--text)' }
const card: React.CSSProperties = { border: '1px solid var(--border)', borderRadius: 10, padding: 14,
  background: 'var(--surface)', marginBottom: 14 }
const btn: React.CSSProperties = { padding: '7px 13px', borderRadius: 7, border: '1px solid var(--border)',
  background: 'var(--surface2)', color: 'var(--text)', fontSize: 13, cursor: 'pointer' }
const primary: React.CSSProperties = { ...btn, background: '#1d4ed8', color: '#fff', borderColor: '#1d4ed8' }

// A figure the backend could not answer comes back null. Em dash, never 0 — a blank here means
// "cannot be answered", and a 0 would read as a result.
const num = (v: any) => (v === null || v === undefined || v === '' ? '—' : String(v))
const money = (v: any) => (v === null || v === undefined ? '—' : fmt(Number(v)))

function emptyDecl(): Decl {
  return { headline: '', categories: [], note: '',
    spiff_initiative: { pay_type: '', label: '', rationale: '' },
    temp_spiffs: [], target_note: '', declared_by: '', declared_at: '', checkins: [] }
}

export default function MonthFocusPage() {
  const { period } = usePeriod()
  const { permissions } = useAuth()
  const mayEdit = canDeclareMonthFocus(permissions)
  const [data, setData] = useState<Payload | null>(null)
  const [draft, setDraft] = useState<Decl>(emptyDecl())
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [checkNote, setCheckNote] = useState('')
  const [changes, setChanges] = useState('')

  const load = useCallback(async () => {
    setErr(''); setMsg('')
    try {
      const d: Payload = await api(`/api/v1/commcalc/month-focus/${encodeURIComponent(period)}?plays=1`)
      setData(d)
      setDraft({ ...emptyDecl(), ...(d.declaration || {}) })
    } catch (e: any) { setErr(e?.message || 'Could not load this month.') }
  }, [period])
  useEffect(() => { load() }, [load])

  async function save() {
    setBusy(true); setErr(''); setMsg('')
    try {
      await api(`/api/v1/commcalc/month-focus/${encodeURIComponent(period)}`,
        { method: 'PUT', body: JSON.stringify(draft) })
      setMsg('Saved.')
      await load()
    } catch (e: any) { setErr(e?.message || 'Could not save.') } finally { setBusy(false) }
  }

  async function doCheckin() {
    setBusy(true); setErr(''); setMsg('')
    try {
      const r: any = await api(`/api/v1/commcalc/month-focus/${encodeURIComponent(period)}/checkin`,
        { method: 'POST', body: JSON.stringify({ note: checkNote,
          changes: changes.split('\n').map(s => s.trim()).filter(Boolean) }) })
      setMsg(r?.already_confirmed ? 'This week was already confirmed.'
        : `Week of ${r?.confirmed_week} confirmed.`)
      setCheckNote(''); setChanges('')
      await load()
    } catch (e: any) { setErr(e?.message || 'Could not confirm.') } finally { setBusy(false) }
  }

  const setSpiff = (i: number, patch: Partial<Spiff>) =>
    setDraft(d => ({ ...d, temp_spiffs: d.temp_spiffs.map((s, j) => (j === i ? { ...s, ...patch } : s)) }))
  const addSpiff = () => setDraft(d => ({ ...d, temp_spiffs: [...d.temp_spiffs,
    { name: '', rate: null, unit: '', window_start: '', window_end: '', cap: null, stores: [],
      status: 'proposed', note: '' }] }))
  const dropSpiff = (i: number) =>
    setDraft(d => ({ ...d, temp_spiffs: d.temp_spiffs.filter((_, j) => j !== i) }))

  return (
    <div style={{ padding: 18, maxWidth: 1100 }}>
      <h1 style={{ fontSize: 21, margin: '0 0 4px' }}>This month&apos;s focus</h1>
      <div style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 14 }}>
        What {period} is about, which pay type is driving its spiffs, which stores have a target, and
        the temporary spiffs on the table. The weekly check-in is here too.
      </div>

      {err && <div style={{ ...card, background: SEV.error.bg, color: SEV.error.fg }}>{err}</div>}
      {msg && <div style={{ ...card, background: 'var(--surface2)' }}>{msg}</div>}
      {data && !data.ready && (
        <div style={{ ...card, background: SEV.warning.bg, color: SEV.warning.fg }}>{data.hint}</div>
      )}
      {data && !mayEdit && (
        <div style={{ ...card, background: 'var(--surface2)', fontSize: 13 }}>
          You can read this month&apos;s focus. Declaring it, and confirming the weekly check-in, is
          for a market manager or above.
        </div>
      )}

      {/* ── WHAT THIS MONTH STILL NEEDS. Computed, never stored, so every row disappears the moment
            its cause is fixed. An empty list is the honest "nothing outstanding". ───────────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 8px' }}>What this month still needs</h2>
        {!data ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>Loading…</div>
          : data.outstanding.length === 0
            ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>
                Nothing outstanding. The month is declared, this week&apos;s check-in is done, every
                store has a target, and the declared spiffs match the pay config.
              </div>
            : data.outstanding.map(it => {
              const s = SEV[it.severity] || SEV.info
              return (
                <div key={it.key} style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                  padding: '8px 10px', borderRadius: 8, background: s.bg, color: s.fg, marginBottom: 6 }}>
                  <span aria-hidden>{s.icon}</span>
                  <div style={{ flex: 1 }}>
                    <b style={{ fontSize: 13 }}>{it.label}</b>
                    <div style={{ fontSize: 12.5, opacity: 0.95 }}>{it.detail}</div>
                  </div>
                  <Link style={{ fontSize: 12.5, color: "#1d4ed8" }} href={it.deep_link}>{it.deep_link_label}</Link>
                </div>
              )
            })}
        {data?.declaration_window_end && !data.declared && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 6 }}>
            Declaration is due by {data.declaration_window_end}.
          </div>
        )}
      </div>

      {/* ── THE DECLARATION ─────────────────────────────────────────────────────────────────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 10px' }}>Declare the month</h2>
        <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>
          The focus, in your own words
        </label>
        <input style={{ ...inp, width: '100%', marginBottom: 10 }} value={draft.headline}
          disabled={!mayEdit} placeholder="e.g. every box leaves with a case and a screen protector"
          onChange={e => setDraft(d => ({ ...d, headline: e.target.value }))} />

        <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>
          Categories this month is about (comma separated)
        </label>
        <input style={{ ...inp, width: '100%', marginBottom: 10 }} disabled={!mayEdit}
          value={draft.categories.join(', ')}
          onChange={e => setDraft(d => ({ ...d,
            categories: e.target.value.split(',').map(s => s.trim()).filter(Boolean) }))} />

        <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>
          Which initiative is driving spiffs this month
        </label>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
          {/* DERIVED from this tenant's own statement rows (§60) — the list can never offer a pay
              type this tenant was not paid on, and no pay-type name lives in code. */}
          <select style={{ ...inp, minWidth: 280 }} disabled={!mayEdit}
            value={draft.spiff_initiative.pay_type}
            onChange={e => setDraft(d => ({ ...d,
              spiff_initiative: { ...d.spiff_initiative, pay_type: e.target.value,
                label: e.target.value } }))}>
            <option value="">— pick a pay type —</option>
            {(data?.initiative_options || []).map(o => (
              <option key={o.type} value={o.type}>
                {o.type}{o.component ? ` · ${o.component}` : ''}
                {o.dollars !== null && o.dollars !== undefined ? ` · ${money(o.dollars)}` : ''}
              </option>
            ))}
          </select>
          <input style={{ ...inp, flex: 1, minWidth: 240 }} disabled={!mayEdit}
            placeholder="Why this one" value={draft.spiff_initiative.rationale}
            onChange={e => setDraft(d => ({ ...d,
              spiff_initiative: { ...d.spiff_initiative, rationale: e.target.value } }))} />
        </div>
        {(data?.initiative_options || []).length === 0 && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
            No pay types were read for {period}, so the list is empty. That is a missing carrier
            statement for the month, not an absence of initiatives — the month can still be declared.
          </div>
        )}

        <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>
          A note on targets
        </label>
        <input style={{ ...inp, width: '100%', marginBottom: 10 }} disabled={!mayEdit}
          value={draft.target_note}
          onChange={e => setDraft(d => ({ ...d, target_note: e.target.value }))} />

        <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>Anything else</label>
        <textarea style={{ ...inp, width: '100%', minHeight: 60, marginBottom: 10 }} disabled={!mayEdit}
          value={draft.note} onChange={e => setDraft(d => ({ ...d, note: e.target.value }))} />

        {mayEdit && (
          <button style={primary} onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save the declaration'}
          </button>
        )}
        {data?.declaration?.declared_at && (
          <span style={{ fontSize: 12, color: 'var(--text2)', marginLeft: 10 }}>
            Last saved by {data.declaration.declared_by || 'somebody'} at {data.declaration.declared_at}
          </span>
        )}
      </div>

      {/* ── TEMPORARY SPIFFS, AND THE PAY CONFIG THEY ARE MEANT TO MATCH ───────────────────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 4px' }}>Temporary spiffs on the table</h2>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
          Declaring one here does not pay anybody. The money is the commission settings page — mark a
          spiff <b>approved</b> or <b>live</b> and this page will tell you whether the pay config
          actually carries it.
        </div>
        {draft.temp_spiffs.map((s, i) => (
          <div key={i} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 10,
            marginBottom: 8, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input style={{ ...inp, minWidth: 190 }} disabled={!mayEdit} placeholder="Name"
              value={s.name} onChange={e => setSpiff(i, { name: e.target.value })} />
            <input style={{ ...inp, width: 100 }} disabled={!mayEdit} placeholder="Rate"
              value={s.rate === null || s.rate === undefined ? '' : String(s.rate)}
              onChange={e => setSpiff(i, { rate: e.target.value === '' ? null : Number(e.target.value) })} />
            <input style={{ ...inp, width: 110 }} disabled={!mayEdit} placeholder="per…"
              value={s.unit} onChange={e => setSpiff(i, { unit: e.target.value })} />
            <input style={{ ...inp, width: 140 }} disabled={!mayEdit} type="date"
              value={s.window_start} onChange={e => setSpiff(i, { window_start: e.target.value })} />
            <input style={{ ...inp, width: 140 }} disabled={!mayEdit} type="date"
              value={s.window_end} onChange={e => setSpiff(i, { window_end: e.target.value })} />
            <input style={{ ...inp, width: 100 }} disabled={!mayEdit} placeholder="Cap"
              value={s.cap === null || s.cap === undefined ? '' : String(s.cap)}
              onChange={e => setSpiff(i, { cap: e.target.value === '' ? null : Number(e.target.value) })} />
            <select style={{ ...inp, width: 120 }} disabled={!mayEdit} value={s.status}
              onChange={e => setSpiff(i, { status: e.target.value })}>
              {STATUSES.map(x => <option key={x} value={x}>{x}</option>)}
            </select>
            {/* A blank rate stays blank all the way to the row — it is a spiff nobody has priced,
                not a free one, and the due list says so. */}
            {s.rate === null && (
              <span style={{ fontSize: 12, color: SEV.warning.fg }}>no rate — cannot be costed</span>
            )}
            {mayEdit && <button style={btn} onClick={() => dropSpiff(i)}>Remove</button>}
          </div>
        ))}
        {mayEdit && (
          <div style={{ display: 'flex', gap: 8 }}>
            <button style={btn} onClick={addSpiff}>Add a temporary spiff</button>
            <button style={primary} onClick={save} disabled={busy}>Save</button>
          </div>
        )}

        <h3 style={{ fontSize: 13.5, margin: '14px 0 6px' }}>Declared against what is being paid</h3>
        {!data ? null : (
          <div style={{ fontSize: 13, display: 'grid', gap: 4 }}>
            <div>
              <b>Matched</b>{' '}
              {data.reconciliation.matched.length
                ? data.reconciliation.matched.join(', ')
                : <span style={{ color: 'var(--text2)' }}>none</span>}
            </div>
            <div style={{ color: data.reconciliation.declared_not_live.length ? SEV.error.fg : undefined }}>
              <b>Approved but not in the pay config</b>{' '}
              {data.reconciliation.declared_not_live.length
                ? data.reconciliation.declared_not_live.join(', ') + ' — nobody is being paid this'
                : <span style={{ color: 'var(--text2)' }}>none</span>}
            </div>
            <div style={{ color: data.reconciliation.live_not_declared.length ? SEV.warning.fg : undefined }}>
              <b>Being paid but never declared</b>{' '}
              {data.reconciliation.live_not_declared.length
                ? data.reconciliation.live_not_declared.join(', ')
                : <span style={{ color: 'var(--text2)' }}>none</span>}
            </div>
            <div style={{ color: 'var(--text2)' }}>
              <b>Proposed</b> (on the table, not in the money){' '}
              {data.reconciliation.proposed.length ? data.reconciliation.proposed.join(', ') : 'none'}
            </div>
            <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 4 }}>
              The pay config for {period} carries {data.live_spiff_names.length} custom spiff(s).{' '}
              <Link style={{ fontSize: 12.5, color: "#1d4ed8" }} href="/commcalc/commission-settings">Open commission settings</Link>
            </div>
          </div>
        )}
      </div>

      {/* ── THE PLAYS. Rules over measured numbers; each carries its figure and where it came from.
            No signal, no play — the panel is empty rather than filled with a horoscope. ────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 4px' }}>What the numbers suggest this month</h2>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
          Each one is a rule over a measured number, cheapest first: fix the free things, then spend.
          The figure behind every suggestion is shown — nothing here is generated prose.
        </div>
        {!data ? null : data.plays.length === 0 ? (
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>
            No play is suggested for {period}. Nothing is invented to fill this panel — check the notes
            below for whether a measurement simply did not run.
          </div>
        ) : data.plays.map(p => (
          <div key={p.key} style={{ border: '1px solid var(--border)', borderRadius: 8, padding: 12,
            marginBottom: 8 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>{p.rank}</span>
              <b style={{ fontSize: 13.5 }}>{p.title}</b>
            </div>
            <div style={{ fontSize: 13, margin: '4px 0' }}>{p.why}</div>
            <div style={{ fontSize: 13, color: 'var(--text2)' }}>{p.move}</div>
            <div style={{ fontSize: 12, marginTop: 6, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link style={{ fontSize: 12.5, color: "#1d4ed8" }} href={p.deep_link}>See the numbers</Link>
              {(p.also || []).map(h => <Link key={h} style={{ fontSize: 12.5, color: "#1d4ed8" }} href={h}>{h}</Link>)}
            </div>
            <details style={{ marginTop: 6 }}>
              <summary style={{ fontSize: 12, cursor: 'pointer', color: 'var(--text2)' }}>
                What this is made of
              </summary>
              <pre style={{ fontSize: 11.5, whiteSpace: 'pre-wrap', margin: '6px 0 0' }}>
                {JSON.stringify(p.evidence, null, 2)}
              </pre>
            </details>
          </div>
        ))}
        {data?.signal_meta && (
          <div style={{ fontSize: 12, color: 'var(--text2)', marginTop: 8 }}>
            {Object.entries(data.signal_meta).map(([k, v]: any) => (
              <div key={k}>
                {k}: {v?.ran === false
                  ? <span style={{ color: SEV.warning.fg }}>did not run — {v?.error}. {v?.note}</span>
                  : JSON.stringify(v)}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── THE WEEKLY CHECK-IN ─────────────────────────────────────────────────────────────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 4px' }}>
          The weekly check-in{data ? ` · every ${WEEKDAYS[data.checkins.weekday] || 'Monday'}` : ''}
        </h2>
        <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
          Confirm any commission or spiff change, assign targets to stores that have none, and decide
          whether to put a temporary spiff on the table. Confirming the same week twice writes nothing.
        </div>
        {data && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
            {data.checkins.days.map(d => {
              const done = data.checkins.confirmed.includes(d)
              const isNow = data.checkins.current === d
              return (
                <span key={d} style={{ fontSize: 12, padding: '4px 8px', borderRadius: 6,
                  border: `1px solid ${isNow ? '#1d4ed8' : 'var(--border)'}`,
                  background: done ? '#dcfce7' : 'var(--surface2)',
                  color: done ? '#166534' : 'var(--text2)' }}>
                  {done ? '✓ ' : ''}{d}{isNow ? ' · this week' : ''}
                </span>
              )
            })}
          </div>
        )}
        {data?.checkins.current === null && (
          <div style={{ fontSize: 12.5, color: 'var(--text2)' }}>
            No check-in is due for {period} — either the month has not reached its first one, or you
            are looking at a month that is not the current one.
          </div>
        )}
        {mayEdit && data?.checkins.current && (
          <>
            <label style={{ display: 'block', fontSize: 12, color: 'var(--text2)' }}>
              What changed this week (one per line)
            </label>
            <textarea style={{ ...inp, width: '100%', minHeight: 56, marginBottom: 8 }} value={changes}
              onChange={e => setChanges(e.target.value)} />
            <input style={{ ...inp, width: '100%', marginBottom: 8 }} placeholder="Note (optional)"
              value={checkNote} onChange={e => setCheckNote(e.target.value)} />
            <button style={primary} onClick={doCheckin} disabled={busy}>
              Confirm the week of {data.checkins.current}
            </button>
          </>
        )}
        {(data?.declaration?.checkins || []).length > 0 && (
          <div style={{ fontSize: 12.5, marginTop: 12 }}>
            {(data!.declaration.checkins).slice().reverse().map(c => (
              <div key={c.week_start} style={{ padding: '6px 0', borderTop: '1px solid var(--border)' }}>
                <b>{c.week_start}</b> — {c.confirmed_by || 'somebody'} at {c.confirmed_at}
                {c.changes?.length ? <ul style={{ margin: '4px 0 0 18px' }}>
                  {c.changes.map((x, i) => <li key={i}>{x}</li>)}</ul> : null}
                {c.note ? <div style={{ color: 'var(--text2)' }}>{c.note}</div> : null}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ── TARGETS. A SAVED row is the test, not what the targets screen displays. ─────────────── */}
      <div style={card}>
        <h2 style={{ fontSize: 15, margin: '0 0 4px' }}>Targets for {period}</h2>
        {!data ? null : data.targets.stores_total === null ? (
          <div style={{ fontSize: 13, color: SEV.warning.fg }}>
            The target count could not be read, so nothing is claimed about it. That is not the same as
            no store having a target.
          </div>
        ) : (
          <div style={{ fontSize: 13 }}>
            <div>
              {num(data.targets.with_saved_target)} of {num(data.targets.stores_total)} stores have a
              target a manager actually saved for this month.
            </div>
            <div style={{ fontSize: 12, color: 'var(--text2)', margin: '4px 0 8px' }}>
              The targets screen seeds a store with no row from last month, so a store can show a
              figure nobody assigned. This counts saved rows only.
            </div>
            {data.targets.without_saved_target.length > 0 && (
              <div style={{ fontSize: 12.5, marginBottom: 8 }}>
                <b>No saved target:</b> {data.targets.without_saved_target.join(', ')}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
              <Link style={{ fontSize: 12.5, color: "#1d4ed8" }} href="/commcalc/targets">Assign targets</Link>
              <Link style={{ fontSize: 12.5, color: "#1d4ed8" }} href="/commcalc/accessory-target-plan">
                Split one accessory goal across the stores
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
