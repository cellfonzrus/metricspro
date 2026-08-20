'use client'
// Vision — EMPLOYEE COACHING from voice transcripts.
//
// This page shows what a rep SAID during recorded interactions, scored against the company's own
// rubric. It is a coaching aid. It is not a performance rating, and the platform gives it no path
// into any pay calculation — the disclaimer the backend returns is printed here rather than being
// dropped, because a number about a person needs to arrive with its own limits attached.
//
// The score is COVERAGE, not volume: what share of a rep's interactions included each behaviour.
// That is deliberate — saying "protection plan" nine times to one customer must not beat saying it
// once to nine customers.
import { Fragment, useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import {
  panel, btn, btnPrimary, cell, th, fmtDuration, daysAgo, today,
  type BehaviorEmployee, type Camera, type VisionConfig,
} from '@/lib/vision'

export default function VisionBehaviorPage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [config, setConfig] = useState<VisionConfig | null>(null)
  const [store, setStore] = useState('')
  const [from, setFrom] = useState(daysAgo(6))
  const [to, setTo] = useState(today())
  const [data, setData] = useState<any>(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/vision/cameras')
        setCameras(r.cameras || []); setConfig(r.config || null)
        const first = (r.cameras || []).map((c: Camera) => c.store_code).filter(Boolean)[0]
        if (first) setStore(first)
      } catch (e: any) { setMsg(e?.message || String(e)) }
    })()
  }, [])

  const load = useCallback(async () => {
    if (!store) return
    setBusy(true); setMsg('')
    try {
      setData(await api(`/api/v1/vision/behavior?store_code=${encodeURIComponent(store)}&date_from=${from}&date_to=${to}`))
    } catch (e: any) { setMsg(e?.message || String(e)); setData(null) }
    finally { setBusy(false) }
  }, [store, from, to])

  useEffect(() => { void load() }, [load])

  async function recompute() {
    setBusy(true); setMsg('')
    try {
      const r = await api(`/api/v1/vision/behavior/recompute?store_code=${encodeURIComponent(store)}&date_from=${from}&date_to=${to}`, { method: 'POST' })
      setMsg(`Re-scored ${r.rows} employee-day row(s).`)
      await load()
    } catch (e: any) { setMsg(e?.message || String(e)) }
    finally { setBusy(false) }
  }

  const stores = Array.from(new Set(cameras.map(c => c.store_code).filter(Boolean))) as string[]
  const employees: BehaviorEmployee[] = data?.employees || []

  // The three "off" states have completely different fixes, so they get completely different pages.
  if (config?.audio_kill_switch) return <Blocked
    title="Voice analytics is disabled for this deployment"
    body="Transcript capture is switched off at the server (VISION_AUDIO_ENABLED is not set), so no
    speech is recorded or scored for any company. This is a deliberate deployment-level control:
    turning voice capture on takes a server change, not a checkbox." />

  if (config && !config.enabled) return <Blocked
    title="Camera analytics is turned off"
    body="An administrator enables it in Vision → Settings."
    action={{ href: '/vision/settings', label: 'Open Vision Settings' }} />

  if (config && !config.behavior_scoring_enabled) return <Blocked
    title="Behaviour scoring is turned off for this company"
    body="Voice transcripts and coaching scores are off by default and are enabled separately from the
    heat map. Enabling them also requires a signed consent record for each employee whose speech would
    be captured."
    action={{ href: '/vision/settings', label: 'Open Vision Settings' }} />

  return (
    <div style={{ padding: 20, maxWidth: 1200 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🎧 Coaching from Conversations</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/vision/heatmap" style={{ ...btn, textDecoration: 'none' }}>🔥 Heat Map</Link>
          <Link href="/vision/settings" style={{ ...btn, textDecoration: 'none' }}>⚙️ Settings</Link>
        </div>
      </div>

      <div style={{ ...panel, display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end', marginBottom: 14 }}>
        <Field label="Store">
          <select value={store} onChange={e => setStore(e.target.value)} style={input}>
            {stores.length === 0 && <option value="">No cameras assigned</option>}
            {stores.map(s => <option key={s} value={s}>{s}</option>)}
          </select>
        </Field>
        <Field label="From"><input type="date" value={from} onChange={e => setFrom(e.target.value)} style={input} /></Field>
        <Field label="To"><input type="date" value={to} onChange={e => setTo(e.target.value)} style={input} /></Field>
        <button style={btn} onClick={() => void load()} disabled={busy}>{busy ? 'Loading…' : 'Refresh'}</button>
        <button style={btn} onClick={() => void recompute()} disabled={busy}
          title="Re-score the stored transcripts. Run this after editing the rubric.">Re-score</button>
      </div>

      {msg && <div style={{ ...panel, marginBottom: 14, fontSize: 13 }}>{msg}</div>}

      {data?.disclaimer && (
        <div style={{ ...panel, marginBottom: 14, borderLeft: '3px solid #f39c12', fontSize: 12.5, color: 'var(--text2)' }}>
          ⚠️ {data.disclaimer}
        </div>
      )}

      {employees.length === 0 ? (
        <div style={{ ...panel, color: 'var(--text2)', fontSize: 13.5 }}>
          Nothing scored for this store and period. Scores appear once an edge analyzer posts
          transcript segments for an employee who has a <b>signed</b> consent record on a camera with
          audio enabled.
        </div>
      ) : (
        <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={th}>Employee</th>
                <th style={th}>Score</th>
                <th style={th}>Conversations</th>
                <th style={th}>Greeted</th>
                <th style={th}>Talk time</th>
                <th style={th}>Work on</th>
              </tr>
            </thead>
            <tbody>
              {employees.map(e => (
                <Fragment key={e.employee_id}>
                  <tr onClick={() => setOpen(open === e.employee_id ? null : e.employee_id)}
                    style={{ cursor: 'pointer' }}>
                    <td style={{ ...cell, fontWeight: 600 }}>{open === e.employee_id ? '▾' : '▸'} {e.name}</td>
                    <td style={cell}><ScoreChip score={e.score} /></td>
                    <td style={cell}>{e.interactions}</td>
                    <td style={cell}>
                      {e.greet_rate === null ? '—' : `${Math.round(e.greet_rate * 100)}%`}
                      {e.missed_greetings > 0 && (
                        <span style={{ color: 'var(--text3)', fontSize: 11.5 }}> ({e.missed_greetings} missed)</span>
                      )}
                    </td>
                    <td style={cell}>{fmtDuration(e.talk_seconds)}</td>
                    <td style={cell}>
                      {e.coaching.slice(0, 2).map(c => (
                        <span key={c.rule_key} style={{
                          display: 'inline-block', marginRight: 6, padding: '2px 7px', borderRadius: 5,
                          fontSize: 11.5, background: c.severity === 'flag' ? '#7f1d1d' : 'var(--surface)',
                          color: c.severity === 'flag' ? '#fecaca' : 'var(--text2)',
                          border: '1px solid var(--border)',
                        }}>{c.label}</span>
                      ))}
                    </td>
                  </tr>
                  {open === e.employee_id && (
                    <tr>
                      <td colSpan={6} style={{ padding: 14, background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                        <EmployeeDetail employee={e} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function EmployeeDetail({ employee }: { employee: BehaviorEmployee }) {
  const peak = Math.max(1, ...employee.series.map(s => s.score))
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: 16 }}>
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Coaching, most useful first</div>
        {employee.coaching.length === 0
          ? <div style={{ fontSize: 13, color: 'var(--text2)' }}>Nothing stands out for this period.</div>
          : employee.coaching.map(c => (
            <div key={c.rule_key} style={{ fontSize: 13, marginBottom: 6 }}>
              <span style={{ color: c.severity === 'flag' ? '#f87171' : 'var(--text)' }}>
                {c.severity === 'flag' ? '⚠️' : '•'} {c.label}
              </span>
              <span style={{ color: 'var(--text3)' }}> — on {Math.round(c.coverage * 100)}% of conversations</span>
            </div>
          ))}
      </div>
      <div>
        <div style={{ fontWeight: 700, fontSize: 13, marginBottom: 8 }}>Day by day</div>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 70 }}>
          {employee.series.map(s => (
            <div key={s.local_date} title={`${s.local_date}: ${s.score} over ${s.interactions} conversation(s)`}
              style={{ flex: 1, height: `${(s.score / peak) * 100}%`, minHeight: 2, background: '#2563eb', borderRadius: '3px 3px 0 0' }} />
          ))}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 6 }}>
          {employee.days} day(s), {employee.segments} transcript segment(s)
        </div>
      </div>
    </div>
  )
}

function ScoreChip({ score }: { score: number }) {
  const color = score >= 75 ? '#16a34a' : score >= 50 ? '#f39c12' : '#dc2626'
  return (
    <span style={{ display: 'inline-block', minWidth: 42, textAlign: 'center', padding: '2px 8px',
      borderRadius: 6, background: color, color: '#fff', fontWeight: 700, fontSize: 12.5 }}>
      {score}
    </span>
  )
}

function Blocked({ title, body, action }: { title: string; body: string; action?: { href: string; label: string } }) {
  return (
    <div style={{ padding: 20, maxWidth: 620 }}>
      <div style={panel}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: 13.5, color: 'var(--text2)', marginBottom: action ? 14 : 0 }}>{body}</div>
        {action && <Link href={action.href} style={{ ...btnPrimary, textDecoration: 'none' }}>{action.label}</Link>}
      </div>
    </div>
  )
}

const input: React.CSSProperties = {
  padding: '6px 9px', borderRadius: 6, border: '1px solid var(--border)',
  background: 'var(--surface)', color: 'var(--text)', fontSize: 13,
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text3)' }}>{label}</span>
      {children}
    </label>
  )
}
