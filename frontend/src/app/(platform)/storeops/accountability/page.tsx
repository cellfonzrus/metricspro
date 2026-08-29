'use client'
import { useState, useEffect, useMemo, Fragment } from 'react'
import { api, ORG_ID } from '@/lib/client'
import { apiCached, LOOKUP, CONFIG } from '@/lib/cache'
import StandardFilterBar from '@/components/StandardFilterBar'
import ReportExportBar, { type ExportColumn } from '@/components/ReportExportBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

// Accountability lens: per-employee attendance patterns, flagged against policy, with POSITIVE coaching
// recommendations + the DATES/TIMES of each late (or left-early) incident so a manager can coach on the
// specifics. Standard filters (period/store/market/rep) + send/export options, and a panel to configure
// the automatic morning lateness alerts (owner directive 2026-08-18). Never proposes discipline.
const FLAG_LABEL: Record<string, string> = { punctuality: 'Punctuality', attendance: 'Attendance', early_departure: 'Leaves early' }
const NO_MARKET = '(no market)'
const foldKey = (s: any) => String(s ?? '').trim().toLowerCase()
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
// Clock times are stored business-local ISO — show just the wall time.
function fmtTime(t: string | null | undefined): string {
  if (!t) return '—'
  try { return new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) } catch { return String(t).slice(11, 16) || '—' }
}

export default function AccountabilityPage() {
  const today = new Date()
  const monthAgo = new Date(); monthAgo.setDate(today.getDate() - 27)
  const [filt, setFilt] = useState<StandardFilterValue>(() => ({ ...emptyStandardFilter(iso(monthAgo)), periodTo: iso(today) }))
  const [data, setData] = useState<any>(null)
  const [stores, setStores] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)

  // Default the window to the CURRENT PAY PERIOD (the same window the morning alerts use), falling back
  // to the last ~4 weeks if tenant settings aren't available.
  useEffect(() => {
    apiCached('/api/v1/core/tenant-settings', CONFIG).then((r: any) => {
      const cur = (r?.preview || [])[0]
      if (cur?.start && cur?.end) setFilt(f => ({ ...f, period: cur.start, periodTo: cur.end }))
    }).catch(() => {})
    apiCached('/api/v1/storeops/stores?include_inactive=true', LOOKUP).then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
  }, [])

  useEffect(() => {
    const s = filt.period, e = filt.periodTo
    if (!s || !e) return
    setLoading(true); setMsg('')
    api(`/api/v1/storeops/accountability?start=${s}&end=${e}&org_id=${ORG_ID}`)
      .then((r: any) => { setData(r); if (r.limit_hit) setMsg('Large range — results may be capped; narrow the dates for a complete picture.') })
      .catch((e2: any) => { setMsg('❌ ' + (e2?.message || e2)); setData(null) })
      .finally(() => setLoading(false))
  }, [filt.period, filt.periodTo])

  const mktOf = useMemo(() => {
    const m: Record<string, string> = {}
    for (const s of stores) if (s.store_code) m[s.store_code] = (s.market && String(s.market).trim()) ? s.market : NO_MARKET
    return m
  }, [stores])
  const storeOptions = useMemo(() => stores.filter(s => s.store_code)
    .map(s => ({ id: s.store_code, label: s.store_code, sublabel: s.address || s.market || undefined }))
    .sort((a, b) => a.label.localeCompare(b.label)), [stores])
  const marketOptions = useMemo(() => Array.from(new Set(stores.map(s => (s.market && String(s.market).trim()) ? s.market : NO_MARKET))).sort(), [stores])

  const allEmps: any[] = data?.employees || []
  const repOptions = useMemo(() => allEmps.filter(e => e.employee).map(e => ({ id: e.employee, label: e.employee })), [allEmps])

  // rep filter = name; store/market filter = the employee had ≥1 incident at a matching store.
  const emps = useMemo(() => {
    const selReps = new Set(filt.reps.map(foldKey))
    const selStores = new Set(filt.stores.map(foldKey))
    const selMarkets = new Set(filt.markets.map(foldKey))
    return allEmps.filter(e => {
      if (selReps.size && !selReps.has(foldKey(e.employee))) return false
      if (selStores.size || selMarkets.size) {
        const incStores = (e.incidents || []).map((i: any) => i.store_code).filter(Boolean)
        if (selStores.size && !incStores.some((s: string) => selStores.has(foldKey(s)))) return false
        if (selMarkets.size && !incStores.some((s: string) => selMarkets.has(foldKey(mktOf[s] || NO_MARKET)))) return false
      }
      return true
    })
  }, [allEmps, filt.reps, filt.stores, filt.markets, mktOf])

  const th = data?.thresholds || {}
  // Export/send rows: one per LATE/left-early incident (delivers the dates + clock-in times), scoped to
  // the visible/filtered employees — what you see is what exports.
  const incidentRows = useMemo(() => emps.flatMap(e => (e.incidents || []).map((i: any) => ({
    employee: e.employee, work_date: i.work_date, store_code: i.store_code,
    clock_in: i.actual_clock_in, clock_in_local: i.actual_clock_in_local, minutes_late: i.late ? i.minutes_late : '',
    clock_out: i.actual_clock_out, clock_out_local: i.actual_clock_out_local, minutes_early: i.left_early ? i.minutes_early : '',
    times_late_period: e.late,
  })).filter((r: any) => r.minutes_late !== '' || r.minutes_early !== '')), [emps])
  const cols: ExportColumn[] = [
    { header: 'Employee', field: 'employee', role: 'rep', get: r => r.employee || '' },
    { header: 'Date', field: 'work_date', role: 'date', type: 'date', get: r => r.work_date || '' },
    { header: 'Store', field: 'store_code', role: 'store', get: r => r.store_code || '' },
    { header: 'Clock in', field: 'clock_in', get: r => r.clock_in_local || fmtTime(r.clock_in) },
    { header: 'Min late', field: 'minutes_late', type: 'number', get: r => r.minutes_late === '' ? '' : String(r.minutes_late) },
    { header: 'Clock out', field: 'clock_out', get: r => r.clock_out_local || (r.clock_out ? fmtTime(r.clock_out) : '') },
    { header: 'Min early', field: 'minutes_early', type: 'number', get: r => r.minutes_early === '' ? '' : String(r.minutes_early) },
    { header: 'Times late (period)', field: 'times_late_period', type: 'number', get: r => String(r.times_late_period ?? '') },
  ]

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 10, marginBottom: 14 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Accountability</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Lateness &amp; attendance patterns to <b>coach</b> on — with the dates and clock-in times, for a supportive conversation. You decide any action.
          </p>
        </div>
        <ReportExportBar title="Accountability — Lateness" subtitle={`${filt.period} → ${filt.periodTo}`}
          filename={`accountability-${filt.period}_${filt.periodTo}`} columns={cols} rows={incidentRows} />
      </div>

      <LatenessAlertConfig />

      <StandardFilterBar value={filt} onChange={setFilt} periodMode="range"
        storeOptions={storeOptions} marketOptions={marketOptions} repOptions={repOptions} />

      {msg && <div style={{ fontSize: 12.5, color: 'var(--text2)', background: 'var(--surface2)', borderRadius: 8, padding: '8px 12px', margin: '10px 0' }}>{msg}</div>}

      {loading ? (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 50 }}><div className="spinner" /></div>
      ) : data && (
        <div className="card" style={{ padding: 14, marginTop: 12 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>By employee <span style={{ color: 'var(--text3)', fontWeight: 400 }}>· click a row for the late dates &amp; times</span></div>
          <div style={{ overflowX: 'auto', marginTop: 10 }}>
            <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
              <thead><tr>{['Employee', 'Shifts', 'Late', 'Late %', 'No-show', 'Left early', 'Excused', 'Flags'].map((h, i) =>
                <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '4px 12px 8px 0', color: 'var(--text3)' }}>{h}</th>)}</tr></thead>
              <tbody>
                {emps.map((r, i) => {
                  const key = String(r.employee_id || r.employee || i)
                  const open = expanded === key
                  const lateInc = (r.incidents || []).filter((x: any) => x.late)
                  const earlyInc = (r.incidents || []).filter((x: any) => x.left_early)
                  return (
                    <Fragment key={key}>
                      <tr style={{ borderTop: '1px solid var(--border)', cursor: 'pointer' }}
                        onClick={() => setExpanded(open ? null : key)}>
                        <td style={{ padding: '5px 12px 5px 0', fontWeight: 500 }}>{(r.incidents || []).length ? (open ? '▾ ' : '▸ ') : ''}{r.employee}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.total_shifts}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.late}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: r.late_rate >= (th.late_rate_flag ?? 0.25) ? '#b45309' : 'var(--text2)' }}>{Math.round(r.late_rate * 100)}%</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: r.no_show >= (th.no_show_flag ?? 2) ? '#dc2626' : 'var(--text2)' }}>{r.no_show}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>{r.left_early}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right', color: 'var(--text3)' }}>{r.excused}</td>
                        <td style={{ padding: '5px 12px 5px 0', textAlign: 'right' }}>
                          {(r.flags || []).map((f: string) => <span key={f} className="badge badge-amber" style={{ fontSize: 10, marginLeft: 4 }}>{FLAG_LABEL[f] || f}</span>)}
                        </td>
                      </tr>
                      {open && (r.incidents || []).length > 0 && (
                        <tr style={{ background: 'var(--surface2)' }}>
                          <td colSpan={8} style={{ padding: '8px 12px 12px' }}>
                            {lateInc.length > 0 && (
                              <div style={{ marginBottom: earlyInc.length ? 8 : 0 }}>
                                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>Late — {lateInc.length}×</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                  {lateInc.map((x: any, j: number) => (
                                    <span key={j} style={{ fontSize: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '3px 8px' }}>
                                      <b>{x.work_date}</b> · in {x.actual_clock_in_local || fmtTime(x.actual_clock_in)} <span style={{ color: '#b45309' }}>({x.minutes_late}m late)</span>{x.store_code ? ` · ${x.store_code}` : ''}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                            {earlyInc.length > 0 && (
                              <div>
                                <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text3)', textTransform: 'uppercase', marginBottom: 4 }}>Left early — {earlyInc.length}×</div>
                                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                                  {earlyInc.map((x: any, j: number) => (
                                    <span key={j} style={{ fontSize: 12, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '3px 8px' }}>
                                      <b>{x.work_date}</b> · out {x.actual_clock_out_local || fmtTime(x.actual_clock_out)} <span style={{ color: '#b45309' }}>({x.minutes_early}m early)</span>{x.store_code ? ` · ${x.store_code}` : ''}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
                {emps.length === 0 && <tr><td colSpan={8} style={{ padding: 12, color: 'var(--text3)' }}>No attendance exceptions in this range/filter.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>
            Flags at ≥{Math.round((th.late_rate_flag ?? 0.25) * 100)}% late, ≥{th.no_show_flag ?? 2} unexcused no-shows, or ≥{Math.round((th.left_early_rate_flag ?? 0.25) * 100)}% left-early (min {th.min_shifts ?? 5} shifts). Excused time-off is never counted against anyone.
          </div>
        </div>
      )}
    </div>
  )
}

// ── Morning lateness alerts config (owner directive 2026-08-18) ────────────────────────────────────
// Turn on the automatic morning email: every manager ABOVE the DM gets a pay-period lateness digest at
// the send time (default 10:30), and the immediate DM gets a corrective-action email for every employee
// late that day. Default OFF; the owner enables it here. "Preview" is a safe dry-run (sends nothing).
function LatenessAlertConfig() {
  const [cfg, setCfg] = useState<any>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  const [open, setOpen] = useState(false)
  useEffect(() => { api('/api/v1/storeops/accountability/alert-config').then(setCfg).catch(() => setCfg({ enabled: false, send_time: '10:30', available: false })) }, [])

  function save(patch: any) {
    setBusy('save'); setMsg('')
    api('/api/v1/storeops/accountability/alert-config', { method: 'PUT', body: JSON.stringify(patch) })
      .then((r: any) => { setCfg(r); setMsg('✅ Saved') })
      .catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(''))
  }
  function preview(send: boolean) {
    if (send && !confirm('Send the morning lateness emails now to all managers above the DM and the CAP emails to DMs? (Dedupe still applies, so it won’t duplicate today’s scheduled run.)')) return
    setBusy(send ? 'send' : 'preview'); setMsg('')
    api(`/api/v1/storeops/accountability/alerts/run-now?send=${send}`, { method: 'POST', body: JSON.stringify({}) })
      .then((r: any) => {
        const res = (r?.results || [])[0]
        const planned = res?.planned || []
        if (send) { setMsg(`✅ Sent ${res?.sent ?? 0}, skipped ${res?.skipped ?? 0} (already sent).`) }
        else if (!planned.length) { setMsg('No lateness to report right now — nothing would be emailed.') }
        else {
          const mgrs = planned.filter((p: any) => p.kind === 'manager_summary')
          const caps = planned.filter((p: any) => p.kind === 'cap')
          setMsg(`Would email ${mgrs.length} manager(s) above the DM and ${caps.length} DM CAP(s): ` +
            planned.map((p: any) => `${p.to_name || p.to}${p.already_sent ? ' (already sent today)' : ''}`).join(', '))
        }
      })
      .catch((e: any) => setMsg('❌ ' + (e?.message || e))).finally(() => setBusy(''))
  }
  if (!cfg) return null
  return (
    <div className="card" style={{ padding: 14, marginBottom: 12, borderLeft: '3px solid var(--accent)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 700, fontSize: 14 }}>📧 Automatic morning lateness alerts {cfg.enabled ? <span className="badge" style={{ fontSize: 10, background: '#16794a', color: '#fff' }}>ON</span> : <span className="badge" style={{ fontSize: 10 }}>off</span>}</div>
        <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Configure'}</button>
      </div>
      {open && (
        <div style={{ marginTop: 12 }}>
          <p style={{ fontSize: 12.5, color: 'var(--text3)', margin: '0 0 10px' }}>
            When on: every morning at the send time, each manager <b>above the DM</b> gets a lateness digest for the current pay period (dates + clock-in times + how many times each employee was late), and the <b>immediate DM</b> gets a corrective-action email for every employee late that day. Excused time off is never counted.
          </p>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, fontWeight: 600 }}>
              <input type="checkbox" checked={!!cfg.enabled} disabled={busy === 'save'} onChange={e => save({ enabled: e.target.checked })} /> Enabled
            </label>
            <label style={{ fontSize: 13 }}>Send time
              <input type="time" value={cfg.send_time || '10:30'} disabled={busy === 'save'} style={{ marginLeft: 6 }}
                onChange={e => setCfg((c: any) => ({ ...c, send_time: e.target.value }))}
                onBlur={e => e.target.value && e.target.value !== '' && save({ send_time: e.target.value })} />
            </label>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} disabled={!!busy} onClick={() => preview(false)}>{busy === 'preview' ? '…' : '👁 Preview recipients'}</button>
            <button className="btn" style={{ fontSize: 12 }} disabled={!!busy} onClick={() => preview(true)}>{busy === 'send' ? '…' : '📤 Send now'}</button>
          </div>
          {cfg.last_run && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 8 }}>Last run: {cfg.last_detail || cfg.last_run}</div>}
          {!cfg.available && <div style={{ fontSize: 11.5, color: '#b45309', marginTop: 8 }}>⚠️ Migration 433 isn&apos;t applied yet — saving is disabled until it runs.</div>}
          {msg && <div style={{ fontSize: 12.5, marginTop: 8 }}>{msg}</div>}
        </div>
      )}
    </div>
  )
}
