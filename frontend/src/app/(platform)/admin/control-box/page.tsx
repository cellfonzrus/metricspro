'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '@/lib/client'
import { safeHref } from '@/lib/safe-url'   // deep_link is config-editable (core.system_check), so it is never trusted raw

// SUPER-ADMIN CONTROL BOX (owner directive 2026-09-05, migs 970–972).
//
// "a separate agent is needed to work on the super admin side control box to monitor the functions of
// all aspects of the platform, showing red light or green light of the system and a daily check
// required to make sure the system is working, the control box will have a link to those module and a
// way to fix that problem connected with Claude code so that can be fixed, must protected from third
// party misuse of the ai api and only restricted to this module" — sanjot@.
//
// The board COMPOSES the platform's existing health mechanisms (the ~44 registered attention
// providers, merchant-portal session health, feed freshness) — it holds no second opinion about any
// subsystem. Every lamp is decided SERVER-SIDE and deterministically; the optional "Explain" button
// only asks for commentary about a lamp that is already red, and is refused for anyone who is not a
// platform super-admin.
//
// THREE THINGS THIS SCREEN REFUSES TO DO, on purpose:
//   1. Show green for something it does not check. Unmonitored subsystems render as an explicit grey
//      lamp and the header states the coverage fraction out loud.
//   2. Let the browser decide anything. The gate, the lamps, the AI budget and the fix bundle are all
//      server-side; this page renders what it is given.
//   3. Apply an AI-authored fix. "Fix with Claude Code" copies a scoped, server-assembled task — a
//      human runs it and reviews the diff.

type Lamp = 'green' | 'unmonitored' | 'amber' | 'unknown' | 'red'

type Check = {
  key: string; subsystem: string; label: string; kind: string; lamp: Lamp
  headline: string; detail: string; count: number; monitored: boolean; actionable: boolean
  deep_link: string | null; deep_link_label: string | null
  index_ref: string | null; code_refs: string[]; owner_agent: string | null
  evidence: Record<string, any>; measured_at: string
}
type Coverage = {
  registered: number; monitored: number; unmonitored: number
  unmonitored_keys: string[]; note: string
}
type Board = {
  ok: boolean; org_id: string; lamp: Lamp; headline: string
  counts: Record<Lamp, number>; actionable: number; coverage: Coverage
  by_subsystem: Record<string, Lamp>; checks: Check[]; generated_at: string; duration_ms: number
  daily_check?: { enabled: boolean; cadence_hours: number | null; last_run_at: string | null; next_run_at: string | null }
}
type FixBundle = { check_key: string; label: string; task: string; note: string; owner_agent: string | null }

// Grey for "not watched" is deliberate: it must not read as a pass, and it must not read as an alarm
// either. An operator should be able to tell at a glance which lamps are claims and which are gaps.
const LAMP: Record<Lamp, { dot: string; label: string; color: string; bg: string; border: string }> = {
  green:       { dot: '#16a34a', label: 'OK',            color: '#166534', bg: '#f0fdf4', border: '#bbf7d0' },
  amber:       { dot: '#f59e0b', label: 'ATTENTION',     color: '#92400e', bg: '#fffbeb', border: '#fde68a' },
  unknown:     { dot: '#7c3aed', label: 'NOT MEASURED',  color: '#5b21b6', bg: '#f5f3ff', border: '#ddd6fe' },
  red:         { dot: '#dc2626', label: 'FAILING',       color: '#b91c1c', bg: '#fef2f2', border: '#fecaca' },
  unmonitored: { dot: '#9ca3af', label: 'NOT MONITORED', color: '#4b5563', bg: '#f9fafb', border: '#e5e7eb' },
}
const ORDER: Lamp[] = ['red', 'unknown', 'amber', 'unmonitored', 'green']

const when = (iso?: string | null) => {
  if (!iso) return 'never'
  try { return new Date(iso).toLocaleString() } catch { return iso }
}

function Dot({ lamp, size = 12 }: { lamp: Lamp; size?: number }) {
  const s = LAMP[lamp] || LAMP.unknown
  return <span aria-label={s.label} title={s.label} style={{
    display: 'inline-block', width: size, height: size, borderRadius: '50%',
    background: s.dot, flexShrink: 0,
    boxShadow: lamp === 'red' ? '0 0 0 3px rgba(220,38,38,.18)' : 'none',
  }} />
}

export default function ControlBoxPage() {
  const [board, setBoard] = useState<Board | null>(null)
  const [err, setErr] = useState(''); const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false)
  const [showOk, setShowOk] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [fix, setFix] = useState<Record<string, FixBundle>>({})
  const [ai, setAi] = useState<Record<string, { text?: string; error?: string; busy?: boolean }>>({})

  const load = useCallback(async (deep: boolean) => {
    setErr('')
    try { setBoard(await api(`/api/v1/core/control-box${deep ? '?deep=1' : ''}`)) }
    catch (e: any) { setErr(e?.message || String(e)); setBoard(null) }
  }, [])

  useEffect(() => { setLoading(true); load(false).finally(() => setLoading(false)) }, [load])

  const runNow = async () => {
    setBusy(true); setMsg(''); setErr('')
    try {
      const r: Board = await api('/api/v1/core/control-box/run', { method: 'POST' })
      setBoard(r)
      setMsg(`Checked ${r.coverage.monitored} subsystem(s) in ${(r.duration_ms / 1000).toFixed(1)}s — ${r.headline}`)
    } catch (e: any) { setErr(e?.message || String(e)) } finally { setBusy(false) }
  }

  const loadFix = async (key: string) => {
    try {
      const r: any = await api(`/api/v1/core/control-box/fix-task/${encodeURIComponent(key)}`)
      setFix(s => ({ ...s, [key]: r }))
    } catch (e: any) { setErr(e?.message || String(e)) }
  }

  const copyFix = async (key: string) => {
    const b = fix[key]; if (!b) return
    try { await navigator.clipboard.writeText(b.task); setMsg('Fix task copied — paste it into Claude Code.') }
    catch { setErr('Could not copy. Select the text below and copy it manually.') }
  }

  // The ONLY thing sent to the AI endpoint is the check key, which the server re-validates against its
  // own registry. There is no prompt box on this page by design: a free-form field is exactly the
  // third-party-misuse path the owner asked us to close.
  const explain = async (key: string) => {
    setAi(s => ({ ...s, [key]: { busy: true } }))
    try {
      const r: any = await api('/api/v1/core/control-box/ai-triage', {
        method: 'POST', body: JSON.stringify({ check_key: key }),
      })
      setAi(s => ({ ...s, [key]: { text: r.commentary || undefined, error: r.error || undefined } }))
    } catch (e: any) {
      setAi(s => ({ ...s, [key]: { error: e?.message || String(e) } }))
    }
  }

  const checks = useMemo(
    () => (board?.checks || []).filter(c => showOk || c.lamp !== 'green'),
    [board, showOk])

  const grouped = useMemo(() => {
    const g: Record<string, Check[]> = {}
    for (const c of checks) (g[c.subsystem] = g[c.subsystem] || []).push(c)
    return Object.entries(g).sort((a, b) =>
      ORDER.indexOf(a[1][0].lamp) - ORDER.indexOf(b[1][0].lamp) || a[0].localeCompare(b[0]))
  }, [checks])

  if (loading) return <div style={{ color: 'var(--text3)' }}>Loading the control box…</div>

  const head = board ? (LAMP[board.lamp] || LAMP.unknown) : LAMP.unknown

  return (
    <div style={{ maxWidth: 1180 }}>
      <h1 style={{ fontSize: 22, fontWeight: 800, marginBottom: 4 }}>🛎️ System Control Box</h1>
      <p className="pg-note" style={{ fontSize: 13, color: 'var(--text2)', marginBottom: 16, maxWidth: 940 }}>
        One board for every subsystem the platform can actually check, refreshed by a scheduled daily
        run so a silent failure is caught before anyone notices a hole in the data. Each row links to
        the module it concerns and can hand a scoped, ready-to-run task to Claude Code. Grey rows are
        <strong> not monitored</strong> — they are shown so a gap in coverage is never mistaken for health.
      </p>

      {err && <div className="card" style={{ background: '#fef2f2', color: '#b91c1c', padding: 12, marginBottom: 12, fontSize: 13 }}>{err}</div>}
      {msg && <div className="card" style={{ background: '#f0fdf4', color: '#166534', padding: 12, marginBottom: 12, fontSize: 13 }}>{msg}</div>}

      {board && (
        <>
          {/* ── headline ─────────────────────────────────────────────────────────────────── */}
          <div className="card" style={{
            padding: 16, marginBottom: 14, background: head.bg, border: `1px solid ${head.border}`,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <Dot lamp={board.lamp} size={20} />
              <div style={{ fontSize: 17, fontWeight: 800, color: head.color }}>{head.label}</div>
              <div style={{ fontSize: 14, color: head.color, flex: 1, minWidth: 260 }}>{board.headline}</div>
              <button className="btn" disabled={busy} onClick={runNow}>
                {busy ? 'Running…' : 'Run full check now'}
              </button>
            </div>

            <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap', marginTop: 12, fontSize: 12.5 }}>
              {ORDER.map(l => (
                <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: 'var(--text2)' }}>
                  <Dot lamp={l} /> {LAMP[l].label.toLowerCase()} · <strong>{board.counts?.[l] ?? 0}</strong>
                </span>
              ))}
            </div>

            {/* Coverage is stated out loud, not implied by an absence of red. */}
            <div style={{ marginTop: 10, fontSize: 12.5, color: head.color, opacity: .95 }}>
              {board.coverage.note}
              {board.coverage.unmonitored > 0 && (
                <> Unwatched: <code style={{ fontSize: 11.5 }}>{board.coverage.unmonitored_keys.join(', ')}</code>.</>
              )}
            </div>
          </div>

          {/* ── the daily check, and whether it is actually running ───────────────────────── */}
          {board.daily_check && (
            <div className="card" style={{ padding: 12, marginBottom: 14, fontSize: 12.5, color: 'var(--text2)' }}>
              <strong>Daily check</strong> — {board.daily_check.enabled ? 'enabled' : 'DISABLED'} ·
              every {board.daily_check.cadence_hours ?? 24}h · last ran {when(board.daily_check.last_run_at)} ·
              next due {when(board.daily_check.next_run_at)}. It schedules itself on every backend boot
              (migration 971), and the board watches its own freshness: if the daily run stops, the
              <em> Daily system check</em> row below goes red rather than leaving stale green lamps on screen.
            </div>
          )}

          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13, marginBottom: 10 }}>
            <input type="checkbox" checked={showOk} onChange={e => setShowOk(e.target.checked)} />
            Show subsystems that are OK ({board.counts?.green ?? 0})
          </label>

          {/* ── the rows ─────────────────────────────────────────────────────────────────── */}
          {grouped.length === 0 && (
            <div className="card" style={{ padding: 16, fontSize: 13, color: 'var(--text2)' }}>
              Nothing needs attention. Tick “Show subsystems that are OK” to see everything being watched.
            </div>
          )}

          {grouped.map(([subsystem, rows]) => (
            <div key={subsystem} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: .5,
                            color: 'var(--text3)', margin: '0 0 6px 2px' }}>
                {subsystem} <span style={{ fontWeight: 400 }}>({rows.length})</span>
              </div>

              {rows.map(c => {
                const s = LAMP[c.lamp] || LAMP.unknown
                const isOpen = open === c.key
                const href = c.deep_link ? safeHref(c.deep_link) : null
                return (
                  <div key={c.key} className="card" style={{
                    padding: 12, marginBottom: 8, borderLeft: `4px solid ${s.dot}`,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
                      <div style={{ paddingTop: 3 }}><Dot lamp={c.lamp} /></div>
                      <div style={{ flex: 1, minWidth: 280 }}>
                        <div style={{ fontWeight: 700, fontSize: 14 }}>
                          {c.label}
                          {c.count > 0 && <span style={{ marginLeft: 8, fontSize: 12, color: s.color }}>({c.count})</span>}
                        </div>
                        <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 2 }}>{c.headline}</div>
                        {c.detail && <div style={{ fontSize: 12.5, color: 'var(--text3)', marginTop: 3 }}>{c.detail}</div>}
                      </div>
                      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                        {href && (
                          <a className="btn btn-sm" href={href}>{c.deep_link_label || 'Open module'}</a>
                        )}
                        {c.actionable && (
                          <button className="btn btn-sm" onClick={() => {
                            setOpen(isOpen ? null : c.key)
                            if (!isOpen && !fix[c.key]) loadFix(c.key)
                          }}>{isOpen ? 'Hide' : 'Fix with Claude Code'}</button>
                        )}
                      </div>
                    </div>

                    {isOpen && (
                      <div style={{ marginTop: 10, borderTop: '1px solid var(--border)', paddingTop: 10 }}>
                        {c.owner_agent && (
                          <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 6 }}>
                            Owned by <strong>{c.owner_agent}</strong> (routing directive in CLAUDE.md).
                          </div>
                        )}
                        {c.index_ref && (
                          <div style={{ fontSize: 12.5, color: 'var(--text3)', marginBottom: 6 }}>
                            Index: {c.index_ref}
                            {c.code_refs?.length > 0 && <> · Files: <code style={{ fontSize: 11.5 }}>{c.code_refs.join(', ')}</code></>}
                          </div>
                        )}

                        {fix[c.key] ? (
                          <>
                            <div style={{ display: 'flex', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                              <button className="btn btn-sm" onClick={() => copyFix(c.key)}>Copy task</button>
                              <button className="btn btn-sm" disabled={ai[c.key]?.busy}
                                      onClick={() => explain(c.key)}>
                                {ai[c.key]?.busy ? 'Asking…' : 'Explain likely cause (AI)'}
                              </button>
                            </div>
                            <div style={{ fontSize: 11.5, color: 'var(--text3)', marginBottom: 6 }}>
                              {fix[c.key].note}
                            </div>
                            <textarea readOnly value={fix[c.key].task} rows={12} style={{
                              width: '100%', fontFamily: 'ui-monospace, monospace', fontSize: 11.5,
                              padding: 8, borderRadius: 6, border: '1px solid var(--border)',
                              background: 'var(--bg2)', color: 'var(--text)',
                            }} />
                          </>
                        ) : (
                          <div style={{ fontSize: 12.5, color: 'var(--text3)' }}>Building the fix task…</div>
                        )}

                        {ai[c.key]?.text && (
                          <div className="card" style={{ marginTop: 8, padding: 10, fontSize: 12.5,
                                                         background: '#f5f3ff', color: '#4c1d95' }}>
                            <strong>AI triage (commentary only — the lamp was decided without it):</strong>
                            <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{ai[c.key].text}</div>
                          </div>
                        )}
                        {ai[c.key]?.error && (
                          <div style={{ marginTop: 8, fontSize: 12.5, color: '#92400e' }}>
                            AI triage unavailable: {ai[c.key].error}. The board is unaffected — every
                            lamp is computed without it.
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}

          <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 16 }}>
            Generated {when(board.generated_at)} in {board.duration_ms}ms · tenant {board.org_id}
          </div>
        </>
      )}
    </div>
  )
}
