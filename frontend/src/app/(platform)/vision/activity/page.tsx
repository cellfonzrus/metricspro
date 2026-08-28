'use client'
// Vision — EMPLOYEE ACTIVITY from the cameras.
//
// This page shows what the cameras could work out about people on the shop floor: posture, whether
// they were moving, whether anybody was with them, and — when the tenant has switched it on —
// sustained wide-mouth episodes. It is the camera-derived counterpart to the transcript coaching
// page, and it is a great deal less certain than that one.
//
// THE PAGE IS BUILT AROUND WHAT IT CANNOT SAY. Three things drive the layout:
//
//  1. COVERAGE COMES FIRST because it is the only signal here with no caveats. It names nobody,
//     needs no consent and no pose model, and "the floor was unattended with customers waiting for
//     40 minutes" is directly actionable. The per-person material is below it, not above.
//
//  2. THE ATTRIBUTION PANEL SITS BETWEEN THEM, and is not collapsible. We do no face recognition,
//     so a row is tied to a person only when exactly one consenting employee was clocked in. In a
//     two-person store that is NOTHING, and an operator has to learn that from this page before
//     they build a process on it — not a month later when the report is empty.
//
//  3. UNKNOWN TIME IS DRAWN, not omitted. Every bar carries its unknown segment in neutral grey,
//     and percentages are out of observed seconds INCLUDING it. A rep readable for four minutes of
//     an hour must not render as "62% standing".
//
// Chart choice: share-of-time across three mutually exclusive states, per employee → one stacked
// bar each. Three series means a legend is required and it is present; the two real states get
// validated categorical hues, and "unknown" is deliberately NEUTRAL GREY because it is the absence
// of information rather than a third category — giving it a hue would make missing data look like
// a finding.
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, cell, th, hourLabel, fmtDuration, daysAgo, today,
  storeOptions, withCurrent, visionError, type StoreOption,
} from '@/lib/vision'

// Validated with the palette checker against both chart surfaces: ALL CHECKS PASS in light and
// dark (chroma floor, CVD separation, normal-vision floor, contrast ≥3:1).
const STANDING = '#2563eb'
const SITTING = '#d97706'
// Not a categorical slot. Grey is the point — it reads as "we could not tell", which is what it
// means. #64748b rather than a lighter slate so it still clears 3:1 against the surface.
const UNKNOWN = '#64748b'
// Coverage is its own chart with ONE measure. It reuses the accent rather than inventing a hue:
// a single series needs no separate identity, and a new colour here would compete with the posture
// legend below for meaning.
const COVER = '#2563eb'

interface EmployeeRow {
  employee_id: string; name: string; buckets: number
  seconds_observed: number
  seconds_standing: number; seconds_sitting: number; seconds_posture_unknown: number
  seconds_walking: number; seconds_stationary: number; seconds_motion_unknown: number
  seconds_with_another_person: number; seconds_alone_stationary: number
  wide_mouth_episodes: number | null
  pct_standing: number | null; pct_sitting: number | null; pct_posture_unknown: number | null
  pct_walking: number | null; pct_stationary: number | null; pct_alone_stationary: number | null
}
interface ActivityPayload {
  store_code: string; date_from: string; date_to: string
  employees: EmployeeRow[]
  unattributed: { buckets: number; seconds_observed: number }
  attribution_reasons: Record<string, number>
  attributed_buckets: number
  buckets: number
  face_state_measured: boolean
  caveats: string[]
}
interface CoverageHour {
  hour: number; window: number; staffed: number; unstaffed: number
  waiting: number; peak: number; staffed_pct: number | null
}
interface CoveragePayload {
  by_hour: CoverageHour[]; waiting_seconds: number; note: string
}

// Why a bucket carries no name, in words an operator can act on rather than a database enum.
const WHY: Record<string, string> = {
  single_on_shift: 'one person on shift — named',
  nobody_on_shift: 'nobody clocked in at the time',
  multiple_on_shift: 'more than one person on shift',
  consent_missing: 'no video consent signed',
  consent_declined: 'video consent declined',
  consent_withdrawn: 'video consent withdrawn',
}

export default function VisionActivityPage() {
  const [data, setData] = useState<ActivityPayload | null>(null)
  const [cover, setCover] = useState<CoveragePayload | null>(null)
  const [stores, setStores] = useState<StoreOption[] | null>(null)
  const [store, setStore] = useState('')
  const [from, setFrom] = useState(daysAgo(6))
  const [to, setTo] = useState(today())
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    if (!store) { setLoading(false); return }
    setErr('')
    const q = `store_code=${encodeURIComponent(store)}&date_from=${from}&date_to=${to}`
    try {
      // Coverage is fetched even if activity is off for this tenant — it is a separate switch and
      // the useful half. A failure on one must not blank the other.
      const [a, c] = await Promise.allSettled([
        api(`/api/v1/vision/activity?${q}`),
        api(`/api/v1/vision/coverage?${q}`),
      ])
      setData(a.status === 'fulfilled' ? a.value : null)
      setCover(c.status === 'fulfilled' ? c.value : null)
      if (a.status === 'rejected' && c.status === 'rejected') setErr(visionError(a.reason))
    } finally { setLoading(false) }
  }, [store, from, to])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    api('/api/v1/storeops/stores')
      .then(r => {
        const list = storeOptions(r)
        setStores(list)
        if (!store && list.length) setStore(list[0].code)
      })
      .catch(() => setStores(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const input: React.CSSProperties = {
    padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)',
    background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
  }
  const emps = data?.employees || []
  const named = data?.attributed_buckets || 0
  const total = data?.buckets || 0
  const namedPct = total > 0 ? Math.round((100 * named) / total) : 0

  return (
    <div style={{ padding: 20, maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 4, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🧍 Floor Activity</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/vision/behavior" style={{ ...btn, textDecoration: 'none' }}>🎧 Coaching</Link>
          <Link href="/vision/busy-hours" style={{ ...btn, textDecoration: 'none' }}>🕐 Busy Hours</Link>
        </div>
      </div>
      <p style={{ color: 'var(--text2)', fontSize: 13.5, margin: '0 0 14px' }}>
        What the cameras could work out about people on the floor. Google reports none of this —
        it is computed in the store and only the numbers are sent.
      </p>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 16, flexWrap: 'wrap' }}>
        <select value={store} onChange={e => setStore(e.target.value)} style={input} aria-label="Store">
          {!store && <option value="">Select a store…</option>}
          {(stores ? withCurrent(stores, store) : []).map(s =>
            <option key={s.code} value={s.code}>{s.label}</option>)}
        </select>
        <input type="date" value={from} onChange={e => setFrom(e.target.value)} style={input} aria-label="From" />
        <input type="date" value={to} onChange={e => setTo(e.target.value)} style={input} aria-label="To" />
      </div>

      {err && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', fontSize: 13, marginBottom: 16 }}>{err}</div>}
      {loading && <div style={{ color: 'var(--text2)' }}>Loading…</div>}

      {/* ── COVERAGE FIRST. The one signal on this page with no caveats attached. ─────────────── */}
      {cover && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 2 }}>Was anybody on the floor?</div>
          <div style={{ fontSize: 11.5, color: 'var(--text2)', marginBottom: 14 }}>
            Store-level. Names nobody and needs no pose analysis — this is just whether the cameras
            could see a person.
          </div>

          <div style={{ display: 'flex', gap: 28, flexWrap: 'wrap', marginBottom: 18 }}>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>
                Unattended with people waiting
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums',
                color: (cover.waiting_seconds || 0) > 0 ? '#b45309' : 'var(--text)' }}>
                {fmtDuration(cover.waiting_seconds)}
              </div>
            </div>
            <div>
              <div style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)' }}>
                Hours with data
              </div>
              <div style={{ fontSize: 26, fontWeight: 700, lineHeight: 1.2, fontVariantNumeric: 'tabular-nums' }}>
                {cover.by_hour.length}
              </div>
            </div>
          </div>

          {cover.by_hour.length > 0 && (
            <div style={{ overflowX: 'auto' }}>
              <div style={{ minWidth: 560 }}>
                {/* ONE measure, so ONE colour. An earlier draft painted well-staffed hours blue and
                    poorly-staffed ones amber — but the legend directly below assigns those same two
                    hues to Standing and Sitting, and a reader carries a colour's meaning down the
                    page. Hours that had people waiting are marked UNDER the axis instead, which is a
                    secondary encoding rather than a second meaning for the same fill. */}
                <div style={{ display: 'flex', alignItems: 'flex-end', gap: 2, height: 120 }}>
                  {cover.by_hour.map(h => {
                    const pct = h.staffed_pct ?? 0
                    return (
                      <div key={h.hour} style={{ flex: 1, display: 'flex', flexDirection: 'column',
                        justifyContent: 'flex-end', alignItems: 'center', height: '100%' }}>
                        <div title={`${hourLabel(h.hour)} · staffed ${pct}% of the time · busiest moment ${h.peak} ${h.peak === 1 ? 'person' : 'people'}${h.waiting > 0 ? ` · ${fmtDuration(h.waiting)} unattended with people waiting` : ''}`}
                          style={{ width: '100%', height: `${Math.max(2, pct)}%`,
                            background: COVER, borderRadius: '4px 4px 0 0' }} />
                      </div>
                    )
                  })}
                </div>
                <div style={{ display: 'flex', gap: 2, marginTop: 6, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                  {cover.by_hour.map(h => (
                    <div key={h.hour} style={{ flex: 1, textAlign: 'center', fontSize: 10,
                      color: h.waiting > 0 ? '#b45309' : 'var(--text3)',
                      fontWeight: h.waiting > 0 ? 600 : 400 }}>
                      {hourLabel(h.hour)}
                      <div style={{ height: 3, marginTop: 3, borderRadius: 2,
                        background: h.waiting > 0 ? '#b45309' : 'transparent' }} />
                    </div>
                  ))}
                </div>
                {cover.by_hour.some(h => h.waiting > 0) && (
                  <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 8,
                    display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 14, height: 3, borderRadius: 2, background: '#b45309', display: 'inline-block' }} />
                    Hours where the floor was unattended while people were waiting
                  </div>
                )}
              </div>
            </div>
          )}
          <div style={{ fontSize: 11.5, color: 'var(--text2)', marginTop: 12 }}>{cover.note}</div>
        </div>
      )}

      {/* ── HOW MUCH OF THIS IS ACTUALLY ABOUT A PERSON. Never collapsed, never below the table. */}
      {data && total > 0 && (
        <div style={{ ...panel, borderLeft: '3px solid #b45309', marginBottom: 16, fontSize: 13 }}>
          <b>{named} of {total} readings ({namedPct}%) could be tied to a person.</b>{' '}
          We do no face recognition, so a reading is matched to somebody only when exactly one
          consenting employee was clocked in at the time. Everything else is counted but anonymous —
          it is on the floor, not on anyone&apos;s record.
          {Object.keys(data.attribution_reasons || {}).length > 0 && (
            <div style={{ marginTop: 10, display: 'flex', gap: 14, flexWrap: 'wrap', fontSize: 12 }}>
              {Object.entries(data.attribution_reasons)
                .sort((a, b) => b[1] - a[1])
                .map(([why, n]) => (
                  <span key={why} style={{ color: 'var(--text2)' }}>
                    <b style={{ color: 'var(--text)', fontVariantNumeric: 'tabular-nums' }}>{n}</b>{' '}
                    {WHY[why] || why}
                  </span>
                ))}
            </div>
          )}
        </div>
      )}

      {/* ── PER-EMPLOYEE ────────────────────────────────────────────────────────────────────── */}
      {data && (emps.length > 0 ? (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 10 }}>How the observed time broke down</div>

          {/* Three series, so a legend is present. It also carries the meaning of the grey, which
              is the segment most people will ask about. */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 16, fontSize: 12 }}>
            {[['Standing', STANDING], ['Sitting', SITTING], ['Could not tell', UNKNOWN]].map(([label, colour]) => (
              <span key={label} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text2)' }}>
                <span style={{ width: 10, height: 10, borderRadius: 2, background: colour, display: 'inline-block' }} />
                {label}
              </span>
            ))}
          </div>

          {emps.map(e => {
            const obs = e.seconds_observed || 0
            const seg = (v: number) => (obs > 0 ? (100 * v) / obs : 0)
            return (
              <div key={e.employee_id} style={{ marginBottom: 18 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, marginBottom: 6, flexWrap: 'wrap' }}>
                  <div style={{ fontSize: 13, fontWeight: 600 }}>{e.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', fontVariantNumeric: 'tabular-nums' }}>
                    {fmtDuration(obs)} observed
                  </div>
                </div>
                {/* 2px gaps between segments, per the mark spec — adjacent fills need a surface gap
                    or the boundary between two categories reads as one continuous block. */}
                <div style={{ display: 'flex', gap: 2, height: 22, borderRadius: 4, overflow: 'hidden' }}>
                  {([['standing', e.seconds_standing, STANDING],
                     ['sitting', e.seconds_sitting, SITTING],
                     ['could not tell', e.seconds_posture_unknown, UNKNOWN]] as const)
                    .filter(([, v]) => v > 0)
                    .map(([label, v, colour]) => (
                      <div key={label}
                        title={`${label}: ${fmtDuration(v)} (${seg(v).toFixed(0)}% of observed time)`}
                        style={{ width: `${seg(v)}%`, background: colour, display: 'flex',
                          alignItems: 'center', justifyContent: 'center', minWidth: 2 }}>
                        {seg(v) >= 12 && (
                          <span style={{ fontSize: 10.5, color: '#fff', fontWeight: 600, whiteSpace: 'nowrap' }}>
                            {seg(v).toFixed(0)}%
                          </span>
                        )}
                      </div>
                    ))}
                </div>
                <div style={{ display: 'flex', gap: 18, marginTop: 8, fontSize: 12, color: 'var(--text2)', flexWrap: 'wrap' }}>
                  <span>Moving about <b style={{ color: 'var(--text)' }}>{fmtDuration(e.seconds_walking)}</b></span>
                  <span>With another person <b style={{ color: 'var(--text)' }}>{fmtDuration(e.seconds_with_another_person)}</b></span>
                  <span>Alone and stationary <b style={{ color: 'var(--text)' }}>{fmtDuration(e.seconds_alone_stationary)}</b></span>
                  {e.wide_mouth_episodes !== null && (
                    <span>Wide-mouth episodes <b style={{ color: 'var(--text)' }}>{e.wide_mouth_episodes}</b></span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ) : total > 0 ? (
        <div style={{ ...panel, marginBottom: 16, fontSize: 13.5, color: 'var(--text2)' }}>
          Readings were recorded, but none could be tied to a named person — see above. This is the
          normal result for a store that runs more than one person at a time.
        </div>
      ) : !loading && (
        <div style={{ ...panel, marginBottom: 16, fontSize: 13.5, color: 'var(--text2)' }}>
          Nothing recorded for this store and date range. Floor activity needs an analyzer running in
          the store with <b>Employee activity</b> switched on in Vision → Settings.
        </div>
      ))}

      {/* ── THE LIMITS, from the server rather than restated here, so they cannot drift apart. */}
      {data?.caveats?.length ? (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>What these numbers cannot tell you</div>
          <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.65 }}>
            {data.caveats.map((c, i) => <li key={i}>{c}</li>)}
          </ul>
        </div>
      ) : null}

      {/* A table view, so the bars are never the only way to read this. */}
      {emps.length > 0 && (
        <details style={{ ...panel }}>
          <summary style={{ cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>Show the numbers</summary>
          <div style={{ overflowX: 'auto', marginTop: 12 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={th}>Employee</th><th style={th}>Observed</th><th style={th}>Standing</th>
                  <th style={th}>Sitting</th><th style={th}>Could not tell</th>
                  <th style={th}>Moving</th><th style={th}>With someone</th><th style={th}>Alone &amp; still</th>
                </tr>
              </thead>
              <tbody>
                {emps.map(e => (
                  <tr key={e.employee_id}>
                    <td style={cell}>{e.name}</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(e.seconds_observed)}</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{e.pct_standing ?? '—'}%</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{e.pct_sitting ?? '—'}%</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{e.pct_posture_unknown ?? '—'}%</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(e.seconds_walking)}</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(e.seconds_with_another_person)}</td>
                    <td style={{ ...cell, fontVariantNumeric: 'tabular-nums' }}>{fmtDuration(e.seconds_alone_stationary)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  )
}
