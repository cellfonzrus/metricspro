'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import StandardFilterBar from '@/components/StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// ZERO SALES — store-days and rep-days with no activation and no upgrade (owner 2026-09-22, index §15).
//
// THE ONE THING THIS SCREEN MUST NOT DO IS PRINT A ZERO WHEN IT MEANS "NOBODY LOOKED".
// A store whose feed did not land is not a store with zero sales. Reporting it as zero sends a
// manager to chase a rep who sold fine, and the report is never trusted again. Measured live on
// 2026-09-22 across the platform's three tenants, 164 store-days in a 30-day window had NO feed at
// all — every one of them would have rendered as "zero sales" on a two-state report. So every cell
// here is one of five states and never two, and the ones that mean "no number" say so in words:
//
//   had sales · MEASURED ZERO (rows landed, none an activation) · NOT REPORTED (no feed) ·
//   closed (not trading) · in progress (day not over)
//
// The backend returns `null` for a day that was not measured and this page renders it as
// "not reported", never as 0. Same posture as /commcalc/carrier-vs-pay and the Exec-MTD silent-zero
// banner; this screen adds no third notion of "no data".
//
// DUPLICATE CHECK: this is not a second sales report. /commcalc/sales-report shows what WAS sold at
// the same grain from the same aggregation; this one answers the opposite question and reads the
// very same cells, the very same activation predicate (line_class) and the very same feed union, so
// the two can never disagree about what an activation is.
//
// RULE TWO: no carrier, tenant, market or store name appears here. Store codes, rep names, market
// names, the threshold and the gap policy are DATA, rendered from the payload.

type Row = {
  grain: 'store' | 'rep'
  scope_key: string | string[]
  label: string
  parent: string | null
  state: string
  day_states: Record<string, string>
  day_counts: Record<string, number | null>
  zero_days: number
  first_zero_day: string | null
  last_zero_day: string | null
  longest_zero_run: number
  gap_days: string[]
  ended_by_gap: boolean
  trading_calendar: string
  days_measured_zero: number
  days_not_reported: number
  days_had_sales: number
  days_closed: number
  alerting: boolean
  note: string
}

type Payload = {
  days: string[]
  rows: Row[]
  totals: {
    scopes: number; store_days_measured_zero: number; store_days_not_reported: number
    stores_alerting: number; stores_unknown_calendar: number
  }
  config: Record<string, any>
  config_source: string
  rules_refused: boolean
  note: string | null
  state_notes: Record<string, string>
  markets: string[]
  market_of: Record<string, string>
  date_from: string
  date_to: string
  scanned_rows: number
}

const card: React.CSSProperties = {
  border: '1px solid var(--border)', borderRadius: 10, padding: '12px 14px', background: 'var(--bg1)',
}

// The five states, their word and their colour. "not reported" is deliberately the loudest thing on
// the screen after an alert: it is the state a reader must never mistake for a zero.
const STATE: Record<string, { word: string; bg: string; fg: string; chip: string }> = {
  had_sales:     { word: 'had sales',     bg: '#dcfce7', fg: '#15803d', chip: '#16a34a' },
  measured_zero: { word: 'measured zero', bg: '#fee2e2', fg: '#b91c1c', chip: '#dc2626' },
  not_reported:  { word: 'not reported',  bg: '#fef3c7', fg: '#92400e', chip: '#d97706' },
  closed:        { word: 'closed',        bg: 'var(--bg2)', fg: 'var(--text3)', chip: 'var(--border)' },
  in_progress:   { word: 'in progress',   bg: 'var(--bg2)', fg: 'var(--text3)', chip: 'var(--border)' },
  off:           { word: 'not scheduled', bg: 'var(--bg2)', fg: 'var(--text3)', chip: 'var(--border)' },
  rule_refused:  { word: 'rule refused',  bg: '#ede9fe', fg: '#6d28d9', chip: '#7c3aed' },
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div style={{ ...card }}>
      <div style={{ fontSize: 12, color: 'var(--text2)' }}>{label}</div>
      <div style={{ fontSize: 24, fontWeight: 700, color: tone || 'var(--text1)', marginTop: 2 }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 3 }}>{sub}</div>}
    </div>
  )
}

/** One day of one scope. A day that was not measured shows its word, never a number. */
function DayCell({ day, state, count, notes }: {
  day: string; state: string; count: number | null; notes: Record<string, string>
}) {
  const s = STATE[state] || STATE.closed
  return (
    <span
      title={`${day} — ${s.word}${count != null ? ` (${count} activation/upgrade txn)` : ''}\n${notes[state] || ''}`}
      style={{
        display: 'inline-block', width: 13, height: 13, borderRadius: 3, background: s.chip,
        margin: 1, verticalAlign: 'middle',
      }}
    />
  )
}

/** The dry-run digest, rendered as what it is: the emails that WOULD go out, who gets each one and
 *  which store-days it would name. This panel used to print the raw API payload, which is a debug
 *  view and not something a manager should ever be shown. The backend returns structured items and
 *  this component owns every word on screen. Nothing here sends anything. */
function DigestPreview({ preview }: { preview: any }) {
  if (preview.loading) {
    return <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 8 }}>Building preview&hellip;</div>
  }
  if (preview.error) {
    return (
      <div style={{ fontSize: 13, color: '#b91c1c', marginTop: 8 }}>
        The preview could not be built: {String(preview.error)}
      </div>
    )
  }
  // One entry per tenant the run covered; this screen is single-org, so there is normally one.
  const res: any[] = Array.isArray(preview.results) ? preview.results : []
  const refused = res.filter(r => typeof r.skipped === 'string' || r.error)
  const digests = res.flatMap((r: any) => (r.planned || []).map((p: any) => ({ ...p, org: r.org_id })))
  const flagged = res.reduce((n, r) => n + (Number(r.flagged) || 0), 0)
  const notAssessed = res.reduce((n, r) => n + (Number(r.not_assessed) || 0), 0)
  const emailOff = res.some(r => r.email_configured === false)

  return (
    <div style={{ marginTop: 10, fontSize: 13 }}>
      {refused.map((r, i) => (
        <div key={`refused-${i}`} style={{ color: '#6d28d9', marginBottom: 6 }}>
          Nothing was planned for this organisation &mdash; {String(r.skipped || r.error)}
          {r.detail ? `: ${r.detail}` : ''}
        </div>
      ))}

      {!refused.length && !digests.length && (
        <div style={{ color: 'var(--text2)' }}>
          <strong>No digest would go out.</strong> No store has reached the threshold of consecutive
          zero trading days.
        </div>
      )}

      {digests.length > 0 && (
        <div style={{ color: 'var(--text2)', marginBottom: 8 }}>
          <strong>{digests.length}</strong> {digests.length === 1 ? 'email' : 'emails'} would go out,
          covering <strong>{flagged}</strong> flagged {flagged === 1 ? 'scope' : 'scopes'}.
        </div>
      )}

      {digests.map((d: any, i: number) => (
        <div key={`${d.to}-${i}`} style={{ ...card, marginBottom: 8, background: 'var(--bg2)' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
            <strong>{d.to}</strong>
            {d.already_sent && (
              <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 999,
                             background: 'var(--bg1)', color: 'var(--text3)',
                             border: '1px solid var(--border)' }}>
                already sent today &mdash; would not send again
              </span>
            )}
          </div>
          <div style={{ color: 'var(--text2)', marginTop: 2 }}>{d.subject}</div>
          {Array.isArray(d.items) && d.items.length > 0 && (
            <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: 'var(--text2)' }}>
              {d.items.map((it: any, j: number) => (
                <li key={j}>
                  <strong>{it.store_code}</strong>
                  {it.grain === 'rep' ? ` — ${it.label}` : ''}
                  {' · '}
                  {it.zero_days} consecutive zero {it.zero_days === 1 ? 'day' : 'days'}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}

      {/* A thin preview must never read as a healthy estate — the same reason the digest footer
          carries this count. */}
      {notAssessed > 0 && (
        <div style={{ color: '#92400e', marginTop: 4 }}>
          {notAssessed} store-{notAssessed === 1 ? 'day' : 'days'} in the window could not be
          assessed because no feed landed. Those are never emailed as a sales problem &mdash; they
          are a data gap, reported on this screen and in import health.
        </div>
      )}
      {emailOff && (
        <div style={{ color: 'var(--text3)', marginTop: 4 }}>
          No email provider is configured, so nothing could be delivered even with alerts on.
        </div>
      )}
    </div>
  )
}

export default function ZeroSalesPage() {
  const today = new Date()
  const iso = (d: Date) => d.toISOString().slice(0, 10)
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({
    ...emptyStandardFilter(iso(new Date(today.getTime() - 29 * 864e5))),
    periodTo: iso(new Date(today.getTime() - 864e5)),
  }))
  const [data, setData] = useState<Payload | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<any>(null)

  const load = useCallback(() => {
    const q = new URLSearchParams()
    if (filt.period) q.set('date_from', filt.period)
    if (filt.periodTo) q.set('date_to', filt.periodTo)
    if (filt.markets.length) q.set('market', filt.markets.join(','))
    if (filt.stores.length) q.set('store', filt.stores.join(','))
    if (filt.reps.length) q.set('rep', filt.reps.join(','))
    setLoading(true); setErr('')
    api(`/api/v1/commcalc/zero-sales?${q.toString()}`)
      .then((r: any) => setData(r))
      .catch((e: any) => setErr(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, [filt])
  useEffect(() => { load() }, [load])

  const stores = useMemo(() => (data?.rows || []).filter(r => r.grain === 'store'), [data])
  const repsByStore = useMemo(() => {
    const m: Record<string, Row[]> = {}
    for (const r of data?.rows || []) if (r.grain === 'rep' && r.parent) (m[r.parent] ||= []).push(r)
    return m
  }, [data])

  const cfg = data?.config || {}
  const t = data?.totals

  const dryRun = () => {
    setPreview({ loading: true })
    api('/api/v1/commcalc/zero-sales/alerts/run-now', { method: 'POST' })
      .then((r: any) => setPreview(r))
      .catch((e: any) => setPreview({ error: e?.message || String(e) }))
  }

  return (
    <div style={{ padding: 24, maxWidth: 1400 }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Zero Sales</h1>
      <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
        Every store-day &mdash; and every rep-day underneath it &mdash; with no activation and no
        upgrade, over the dates you choose.
      </p>

      {/* The posture banner. The whole risk of this screen is a reader treating a missing feed as a
          zero, so this is the sentence that stops them. */}
      <div style={{ ...card, marginTop: 14, background: '#f0f9ff', borderColor: '#bae6fd' }}>
        <strong style={{ fontSize: 13 }}>A missing feed is not a zero.</strong>
        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
          A day counts as zero sales only when the feed <em>did</em> carry that store and none of its
          transactions was an activation or an upgrade &mdash; a <strong>measured zero</strong>. A day
          nothing landed for reads <strong>not reported</strong>, never 0, and is never counted towards
          a run of consecutive zero days. Nothing on this screen is booked anywhere; it reads the same
          sales feed, the same activation rule and the same daily aggregation as the Sales Report and
          writes nothing.
        </div>
      </div>

      {data?.rules_refused && (
        <div style={{ ...card, marginTop: 12, background: '#ede9fe', borderColor: '#c4b5fd' }}>
          <strong style={{ fontSize: 13 }}>This report is claiming nothing for this organisation.</strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>{data.note}</div>
        </div>
      )}

      {!data?.rules_refused && !!t?.store_days_not_reported && (
        <div style={{ ...card, marginTop: 12, background: '#fffbeb', borderColor: '#fcd34d' }}>
          <strong style={{ fontSize: 13 }}>
            {t.store_days_not_reported} store-day(s) in this window were not reported.
          </strong>
          <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>{data?.note}</div>
        </div>
      )}

      <div style={{ marginTop: 14 }}>
        <StandardFilterBar
          value={filt} onChange={setFilt} periodMode="range"
          show={{ period: true, stores: true, markets: true, reps: true }}
          optionsUrl="/api/v1/core/filter-options"
          right={<button className="btn btn-secondary" onClick={load}>&#8635; Refresh</button>}
        />
      </div>

      {loading && <div style={{ marginTop: 16, color: 'var(--text2)' }}>Loading&hellip;</div>}
      {err && <div style={{ marginTop: 16, color: '#dc2626', fontSize: 13 }}>&#10060; {err}</div>}

      {t && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 12, marginTop: 16 }}>
            <Tile label="Stores at or past the alert threshold" value={String(t.stores_alerting)}
                  tone="#b91c1c"
                  sub={`${cfg.consecutive_days} or more consecutive trading days with no activation`} />
            <Tile label="Store-days measured zero" value={t.store_days_measured_zero.toLocaleString()}
                  sub="the feed carried the store and it sold no activation or upgrade" />
            <Tile label="Store-days not reported" value={t.store_days_not_reported.toLocaleString()}
                  tone="#92400e" sub="no feed landed — never counted as a zero" />
            <Tile label="Stores with an unknown trading calendar" value={String(t.stores_unknown_calendar)}
                  sub="no schedule in this window, so every day was evaluated" />
          </div>

          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 10 }}>
            Counted as a sale: {(cfg.count_classes || []).join(', ')} &middot; trading days from{' '}
            {cfg.trading_day_source === 'schedule' ? 'the staff schedule' : 'every day in range'}
            {' '}&middot; a not-reported day{' '}
            {cfg.gap_policy === 'bridge'
              ? 'is bridged (the run continues, and the gap is named)'
              : 'ends the run (consecutiveness cannot be asserted across a day nobody measured)'}
            {' '}&middot; settings resolved from the {data.config_source.replace('_', ' ')} row
            {' '}&middot; {data.scanned_rows.toLocaleString()} sales lines read.
          </div>

          {/* Notifications — the existing alert path, a new kind on it. */}
          <div style={{ ...card, marginTop: 14 }}>
            <strong style={{ fontSize: 13 }}>Notifications</strong>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
              A digest goes to the District Manager and every manager above them when a store they
              oversee reaches <strong>{cfg.consecutive_days}</strong> consecutive trading days with no
              activation and no upgrade. It escalates once per day per store, and a store whose feed
              did not land is never emailed as a sales problem &mdash; that is a data gap, and it is
              reported as one. Currently{' '}
              <strong>{cfg.alerts_enabled ? 'on' : 'off'}</strong> for this organisation.
            </div>
            <div style={{ marginTop: 8 }}>
              <button className="btn btn-secondary" onClick={dryRun}>
                Preview today&rsquo;s digest (sends nothing)
              </button>
            </div>
            {preview && <DigestPreview preview={preview} />}
          </div>

          <div style={{ marginTop: 20, overflowX: 'auto' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 13 }}>
              <thead>
                <tr style={{ textAlign: 'left', color: 'var(--text3)' }}>
                  <th style={{ padding: '6px 8px' }}>Store</th>
                  <th style={{ padding: '6px 8px' }}>Market</th>
                  <th style={{ padding: '6px 8px' }}>State</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Consecutive zero days</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Measured zero</th>
                  <th style={{ padding: '6px 8px', textAlign: 'right' }}>Not reported</th>
                  <th style={{ padding: '6px 8px' }}>{data.date_from} &rarr; {data.date_to}</th>
                </tr>
              </thead>
              <tbody>
                {stores.map(r => {
                  const code = String(r.scope_key)
                  const kids = repsByStore[code] || []
                  const st = STATE[r.state] || STATE.closed
                  return (
                    <tr key={code} style={{ borderTop: '1px solid var(--border)' }}>
                      <td style={{ padding: '6px 8px', fontWeight: 600 }}>
                        {kids.length > 0 && (
                          <button onClick={() => setOpen(o => ({ ...o, [code]: !o[code] }))}
                                  style={{ background: 'none', border: 0, cursor: 'pointer',
                                           color: 'var(--text2)', marginRight: 6 }}>
                            {open[code] ? '▾' : '▸'}
                          </button>
                        )}
                        {r.label}
                        {r.alerting && (
                          <span style={{ marginLeft: 8, fontSize: 11, padding: '1px 6px', borderRadius: 9,
                                         background: '#fee2e2', color: '#b91c1c' }}>alerting</span>
                        )}
                        {r.trading_calendar === 'unknown' && (
                          <span title="This store has no schedule in this window, so its trading days are unknown and every day was evaluated."
                                style={{ marginLeft: 6, fontSize: 11, color: 'var(--text3)' }}>
                            calendar unknown
                          </span>
                        )}
                      </td>
                      <td style={{ padding: '6px 8px', color: 'var(--text2)' }}>
                        {data.market_of?.[code] || '—'}
                      </td>
                      <td style={{ padding: '6px 8px' }}>
                        <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 9,
                                       background: st.bg, color: st.fg }}>{st.word}</span>
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', fontWeight: 600 }}>
                        {r.zero_days || '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right' }}>{r.days_measured_zero}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right',
                                   color: r.days_not_reported ? '#92400e' : 'var(--text3)' }}>
                        {r.days_not_reported || '—'}
                      </td>
                      <td style={{ padding: '6px 8px' }} title={r.note}>
                        {data.days.map(d => (
                          <DayCell key={d} day={d} state={r.day_states[d]} count={r.day_counts[d]}
                                   notes={data.state_notes} />
                        ))}
                      </td>
                    </tr>
                  )
                })}
                {stores.flatMap(r => {
                  const code = String(r.scope_key)
                  if (!open[code]) return []
                  return (repsByStore[code] || []).map(k => {
                    const st = STATE[k.state] || STATE.closed
                    return (
                      <tr key={code + '/' + k.label} style={{ background: 'var(--bg2)' }}>
                        <td style={{ padding: '4px 8px 4px 30px', color: 'var(--text2)' }}>{k.label}</td>
                        <td />
                        <td style={{ padding: '4px 8px' }}>
                          <span style={{ fontSize: 11, padding: '2px 7px', borderRadius: 9,
                                         background: st.bg, color: st.fg }}>{st.word}</span>
                        </td>
                        <td style={{ padding: '4px 8px', textAlign: 'right' }}>{k.zero_days || '—'}</td>
                        <td style={{ padding: '4px 8px', textAlign: 'right' }}>{k.days_measured_zero}</td>
                        <td style={{ padding: '4px 8px', textAlign: 'right',
                                     color: k.days_not_reported ? '#92400e' : 'var(--text3)' }}>
                          {k.days_not_reported || '—'}
                        </td>
                        <td style={{ padding: '4px 8px' }} title={k.note}>
                          {data.days.map(d => (
                            <DayCell key={d} day={d} state={k.day_states[d]} count={k.day_counts[d]}
                                     notes={data.state_notes} />
                          ))}
                        </td>
                      </tr>
                    )
                  })
                })}
              </tbody>
            </table>
          </div>

          {/* The legend IS the honesty statement — each state in its own words, from the payload. */}
          <div style={{ ...card, marginTop: 16 }}>
            <strong style={{ fontSize: 13 }}>What each colour means</strong>
            <div style={{ display: 'grid', gap: 6, marginTop: 8 }}>
              {['had_sales', 'measured_zero', 'not_reported', 'closed', 'off', 'in_progress']
                .filter(k => data.state_notes[k])
                .map(k => (
                  <div key={k} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 12 }}>
                    <span style={{ display: 'inline-block', width: 13, height: 13, borderRadius: 3,
                                   background: (STATE[k] || STATE.closed).chip, flex: '0 0 auto', marginTop: 2 }} />
                    <div>
                      <strong style={{ color: (STATE[k] || STATE.closed).fg }}>
                        {(STATE[k] || STATE.closed).word}
                      </strong>{' '}
                      <span style={{ color: 'var(--text2)' }}>{data.state_notes[k]}</span>
                    </div>
                  </div>
                ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
