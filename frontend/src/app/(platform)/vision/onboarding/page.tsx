'use client'
// CAMERA SETUP WIZARD — owner directive 2026-08-22.
//
//   "This is very complex set up for any tenant to follow , need t make it very easy for them to
//    onboard their camera, if i was to do it agin i cannot - so we need a detailed wizard to set up
//    the cameras with every minute details possible with links and storing the information as we go
//    along so the user does not have to go back and forth like we did earlier"
//
// THE PROBLEM THIS PAGE SOLVES is not that the API is hard. It is that the work happens in three
// separate Google consoles, several values are called the same thing while being different things,
// and every mistake fails LATE and SILENTLY. The setup was done once before this page existed — by
// the owner, with an assistant and the API reference open — and it took a day.
//
// FOUR RULES, each answering a specific thing that went wrong that day:
//
//   1. ONE STEP ON SCREEN. The rail shows where you are; only the current step renders. "if i was to
//      do it agin i cannot" is a statement about volume, not difficulty.
//   2. NOTHING IS DESCRIBED THAT CAN BE HANDED OVER. Every value Google compares byte for byte —
//      redirect URIs, the full topic path, the push endpoint — is rendered with a copy button and
//      computed server-side. "Add your app's URL as a redirect URI" is how you get
//      redirect_uri_mismatch; a copy button is how you don't.
//   3. TYPED VALUES SAVE ON BLUR, one field per call. The operator is moving between browser tabs
//      the whole way through. A form that persists only when every box is filled is exactly how the
//      project ids kept getting lost the first time.
//   4. THE PAGE CANNOT MARK ITSELF DONE. Completion comes from the server, re-derived from what the
//      tenant actually has. The only steps a person can tick are the ones with nothing observable
//      from our side, and the server refuses a tick on any other — a wizard that says "finished"
//      over a setup that does not work is the failure being designed against.
//
// The last step asks the operator to walk past a camera while we poll for the sighting. That is not
// a flourish: four things have to be right for events to arrive, all four fail silently, and Google
// provides no test button anywhere. It is the only honest proof.
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, btnPrimary, visionError } from '@/lib/vision'

interface CopyItem { label: string; value: string; note: string }
interface Step {
  key: string; number: number; group: string; title: string; minutes: number | null
  why: string; body: string[]; gotcha: string; expect: string; link: string
  copy: CopyItem[]; verify: string; field: string; check: string
  needs: string[]; optional: boolean; critical: boolean
  state: 'done' | 'current' | 'blocked' | 'todo'; blocked_by: string[]
}
interface Progress {
  total: number; done: number; required_total: number; required_done: number
  required_left: number; minutes_left: number; minutes_left_required: number
  optional_left: number; complete: boolean
}
interface Wizard {
  steps: Step[]; progress: Progress
  values: Record<string, string>
  linked: boolean; has_secret: boolean
  events: { last_7d: number; last_event_at: string | null } | null
  cameras: number
  last_error: string; last_error_help: string | null
  token_warning: string | null
  push_ready: boolean
}
interface VerifyResult { ok: boolean; message: string; help?: string | null }

const OK = '#16a34a'
const WARN = '#b45309'
const BAD = '#dc2626'

export default function VisionOnboardingPage() {
  const [w, setW] = useState<Wizard | null>(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string>('')          // which step the operator is looking at
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [fieldErr, setFieldErr] = useState<Record<string, string>>({})
  const [verdict, setVerdict] = useState<Record<string, VerifyResult>>({})
  const [copied, setCopied] = useState('')
  const [watching, setWatching] = useState(false)
  const [watchMsg, setWatchMsg] = useState('')
  const watchStop = useRef<(() => void) | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const origin = typeof window === 'undefined' ? '' : window.location.origin
      const r: Wizard = await api(`/api/v1/vision/onboarding?app_base=${encodeURIComponent(origin)}`)
      setW(r)
      // Follow the server's idea of "current" unless the operator has deliberately opened another
      // step. Re-snapping on every poll would yank the page out from under someone reading ahead.
      setOpen(o => o || (r.steps.find(s => s.state === 'current')?.key || r.steps[0]?.key || ''))
    } catch (e) {
      setW(null); setErr(visionError(e))
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])
  // Stop polling if the operator leaves mid-watch, so a background timer cannot outlive the page.
  useEffect(() => () => { watchStop.current?.() }, [])

  const step = w?.steps.find(s => s.key === open) || null

  function copy(value: string, label: string) {
    if (typeof navigator === 'undefined' || !navigator.clipboard) return
    void navigator.clipboard.writeText(value).then(() => {
      setCopied(label); setTimeout(() => setCopied(c => (c === label ? '' : c)), 1600)
    }).catch(() => {})
  }

  /** Save one typed value. Called on blur — see rule 3. */
  async function saveValue(field: string, value: string) {
    setFieldErr(f => ({ ...f, [field]: '' }))
    try {
      await api('/api/v1/vision/onboarding/value', {
        method: 'PUT', body: JSON.stringify({ field, value }),
      })
      await load()
    } catch (e) {
      // The server's message names WHICH of the look-alike values was pasted. Surface it verbatim.
      setFieldErr(f => ({ ...f, [field]: visionError(e) }))
    }
  }

  async function ack(key: string, done: boolean) {
    setBusy(true)
    try {
      await api('/api/v1/vision/onboarding/ack', {
        method: 'POST', body: JSON.stringify({ step: key, done }),
      })
      await load()
    } catch (e) { setErr(visionError(e)) } finally { setBusy(false) }
  }

  async function verify(key: string) {
    setBusy(true)
    try {
      const r: VerifyResult = await api(`/api/v1/vision/onboarding/verify/${key}`, { method: 'POST' })
      setVerdict(v => ({ ...v, [key]: r }))
      if (r.ok) await load()
    } catch (e) {
      setVerdict(v => ({ ...v, [key]: { ok: false, message: visionError(e) } }))
    } finally { setBusy(false) }
  }

  /** The walk test. Polls for two minutes, then gives up and says what to check. */
  function watchForEvent() {
    if (watching) return
    const since = new Date().toISOString()
    setWatching(true); setWatchMsg('Watching… walk in front of a camera now.')
    const started = Date.now()
    const timer = setInterval(async () => {
      if (Date.now() - started > 120_000) {
        stop()
        // No step numbers here on purpose. The gotcha rendered directly below already lists the
        // causes in order, with numbers resolved from the real step positions — repeating them in
        // the client is how the two drift apart.
        setWatchMsg('Nothing arrived in two minutes. The causes are listed below, in the order '
          + 'worth checking them.')
        return
      }
      try {
        const r = await api(`/api/v1/vision/onboarding/watch?since=${encodeURIComponent(since)}`)
        if (r?.seen) { stop(); setWatchMsg(r.message || 'Event received.'); void load() }
        else if (r && r.available === false) { stop(); setWatchMsg(r.message || 'Cannot read events.') }
      } catch { /* a blip mid-watch is not a result — keep polling until the deadline */ }
    }, 2500)
    function stop() { clearInterval(timer); setWatching(false); watchStop.current = null }
    watchStop.current = stop
  }

  if (loading) return <div style={{ padding: 20, color: 'var(--text2)' }}>Loading…</div>

  const p = w?.progress
  const groups: string[] = []
  for (const s of w?.steps || []) if (!groups.includes(s.group)) groups.push(s.group)

  return (
    <div style={{ padding: 20, maxWidth: 1180 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>🎥 Camera setup</h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/vision/settings" style={{ ...btn, textDecoration: 'none' }}>⚙️ Settings</Link>
          <Link href="/vision" style={{ ...btn, textDecoration: 'none' }}>📹 Cameras</Link>
        </div>
      </div>
      <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 13.5, margin: '4px 0 16px' }}>
        About half an hour, across three Google consoles. Everything you type is saved as you go —
        stop whenever you like and pick up here, on any computer.
      </p>

      {err && <div style={{ ...panel, borderColor: BAD, color: BAD, fontSize: 13, marginBottom: 14 }}>{err}</div>}

      {w?.token_warning && (
        <div style={{ ...panel, borderLeft: `3px solid ${WARN}`, marginBottom: 14, fontSize: 13 }}>
          <b>Your Google connection is ageing.</b> {w.token_warning}
        </div>
      )}
      {w?.last_error && (
        <div style={{ ...panel, borderLeft: `3px solid ${BAD}`, marginBottom: 14, fontSize: 13 }}>
          <b>Google last reported:</b> {w.last_error}
          {w.last_error_help && <div style={{ marginTop: 6, color: 'var(--text2)' }}>{w.last_error_help}</div>}
        </div>
      )}

      {/* Progress. Required-only, because a tenant that never wants an analyzer or per-employee
          data must be able to reach "done" without the optional steps nagging forever. */}
      {p && (
        <div style={{ ...panel, marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: 8 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              {p.complete
                ? <span style={{ color: OK }}>✓ Setup complete — your cameras are connected.</span>
                : <>{p.required_done} of {p.required_total} required steps done</>}
            </div>
            <div style={{ fontSize: 12.5, color: 'var(--text2)', fontVariantNumeric: 'tabular-nums' }}>
              {p.complete
                ? (p.optional_left > 0
                  ? `${p.optional_left} optional step${p.optional_left === 1 ? '' : 's'} still available`
                  : 'Everything done, including the optional steps.')
                : `about ${p.minutes_left_required} min left`}
            </div>
          </div>
          <div style={{ height: 6, background: 'var(--border)', borderRadius: 3, marginTop: 10, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 3, background: p.complete ? OK : '#2563eb',
              width: `${Math.round(100 * p.required_done / Math.max(1, p.required_total))}%`,
              transition: 'width .2s',
            }} />
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start', flexWrap: 'wrap' }}>
        {/* ── The rail: where you are, what is left ─────────────────────────────────────────── */}
        <div style={{ ...panel, flex: '0 0 250px', minWidth: 230 }}>
          {groups.map(g => (
            <div key={g} style={{ marginBottom: 12 }}>
              <div style={{ fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '.5px', color: 'var(--text3)', marginBottom: 6 }}>{g}</div>
              {(w?.steps || []).filter(s => s.group === g).map(s => {
                const isOpen = s.key === open
                const mark = s.state === 'done' ? '✓' : s.state === 'blocked' ? '·' : '○'
                return (
                  <button key={s.key} onClick={() => setOpen(s.key)}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left', border: 'none', cursor: 'pointer',
                      background: isOpen ? 'var(--surface)' : 'transparent',
                      borderRadius: 6, padding: '6px 8px', fontSize: 12.5, marginBottom: 2,
                      color: s.state === 'blocked' ? 'var(--text3)' : 'var(--text)',
                      fontWeight: isOpen ? 700 : 400,
                    }}>
                    <span style={{ color: s.state === 'done' ? OK : 'var(--text3)', marginRight: 6 }}>{mark}</span>
                    <span style={{ color: 'var(--text3)', fontVariantNumeric: 'tabular-nums' }}>{s.number}. </span>
                    {s.title}
                    {s.optional && <span style={{ color: 'var(--text3)', fontWeight: 400 }}> · optional</span>}
                  </button>
                )
              })}
            </div>
          ))}
        </div>

        {/* ── The step ──────────────────────────────────────────────────────────────────────── */}
        <div style={{ flex: '1 1 480px', minWidth: 320 }}>
          {!step ? null : (
            <div style={{ ...panel }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <h2 style={{ fontSize: 17, fontWeight: 700 }}>
                  <span style={{ color: 'var(--text3)', fontVariantNumeric: 'tabular-nums' }}>{step.number}. </span>
                  {step.title}
                  {step.state === 'done' && <span style={{ color: OK, fontSize: 13, fontWeight: 600 }}> · done</span>}
                </h2>
                {step.minutes ? <span style={{ fontSize: 12, color: 'var(--text3)' }}>~{step.minutes} min</span> : null}
              </div>
              <p style={{ fontSize: 13.5, color: 'var(--text2)', margin: '6px 0 12px' }}>{step.why}</p>

              {step.state === 'blocked' && (
                <div style={{ ...panel, background: 'var(--surface)', fontSize: 13, marginBottom: 12 }}>
                  Finish <b>{step.blocked_by.map(b => w?.steps.find(s => s.key === b)?.title || b).join(', ')}</b> first
                  — this step needs it.
                </div>
              )}

              {/* What to do. Numbered, because these are instructions to follow in order. */}
              {step.body.length > 0 && (
                <ol style={{ fontSize: 13.5, lineHeight: 1.65, paddingLeft: 20, margin: '0 0 14px' }}>
                  {step.body.map((b, i) => <li key={i} style={{ marginBottom: 5 }}>{b}</li>)}
                </ol>
              )}

              {step.link && (
                <a href={step.link} target="_blank" rel="noopener noreferrer"
                  style={{ ...btnPrimary, display: 'inline-block', textDecoration: 'none', marginBottom: 14 }}>
                  Open in Google ↗
                </a>
              )}

              {/* RULE 2: hand it over, never describe it. */}
              {step.copy.map(c => (
                <div key={c.label} style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', marginBottom: 4 }}>
                    {c.label}
                  </div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'stretch' }}>
                    <code style={{
                      flex: 1, background: 'var(--surface)', border: '1px solid var(--border)',
                      borderRadius: 6, padding: '7px 9px', fontSize: 12, wordBreak: 'break-all',
                    }}>{c.value}</code>
                    <button style={{ ...btn, whiteSpace: 'nowrap' }} onClick={() => copy(c.value, c.label)}>
                      {copied === c.label ? '✓ Copied' : 'Copy'}
                    </button>
                  </div>
                  {c.note && <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 3 }}>{c.note}</div>}
                </div>
              ))}

              {/* RULE 3: one field, saved on blur. */}
              {step.verify === 'value' && step.field && (
                <div style={{ marginTop: 14 }}>
                  <div style={{ fontSize: 11.5, textTransform: 'uppercase', letterSpacing: '.4px', color: 'var(--text2)', marginBottom: 4 }}>
                    Paste it here
                  </div>
                  <input
                    value={drafts[step.field] ?? (w?.values[step.field] || '')}
                    onChange={e => setDrafts(d => ({ ...d, [step.field]: e.target.value }))}
                    onBlur={e => void saveValue(step.field, e.target.value)}
                    placeholder={step.expect || ''}
                    spellCheck={false} autoComplete="off"
                    style={{
                      width: '100%', padding: '8px 10px', borderRadius: 7, fontSize: 13,
                      border: `1px solid ${fieldErr[step.field] ? BAD : 'var(--border)'}`,
                      background: 'var(--surface)', color: 'var(--text)',
                    }} />
                  {fieldErr[step.field]
                    ? <div style={{ color: BAD, fontSize: 12.5, marginTop: 5 }}>{fieldErr[step.field]}</div>
                    : w?.values[step.field]
                      ? <div style={{ color: OK, fontSize: 12.5, marginTop: 5 }}>✓ Saved.</div>
                      : <div style={{ color: 'var(--text3)', fontSize: 12, marginTop: 5 }}>Saves as soon as you click away.</div>}
                </div>
              )}

              {/* THE TRAP. Below the instructions, because it is what to watch for while doing them —
                  and visually loud on the one step where getting it wrong costs a week. */}
              {step.gotcha && (
                <div style={{
                  ...panel, marginTop: 14, fontSize: 13,
                  borderLeft: `3px solid ${step.critical ? BAD : WARN}`,
                  background: 'var(--surface)',
                }}>
                  <b>{step.critical ? '⚠️ Do not skip this' : 'Watch out'}</b>
                  <div style={{ marginTop: 4 }}>{step.gotcha}</div>
                </div>
              )}

              {step.expect && (
                <div style={{ fontSize: 12.5, color: 'var(--text2)', marginTop: 12 }}>
                  <b>You should see:</b> {step.expect}
                </div>
              )}

              {/* ── Finish the step ───────────────────────────────────────────────────────── */}
              <div style={{ marginTop: 16, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
                {step.verify === 'ack' && (
                  <button style={step.state === 'done' ? btn : btnPrimary} disabled={busy}
                    onClick={() => void ack(step.key, step.state !== 'done')}>
                    {step.state === 'done' ? 'Mark not done' : "I've done this"}
                  </button>
                )}
                {(step.verify === 'probe' || step.verify === 'watch') && (
                  <button style={btn} disabled={busy} onClick={() => void verify(step.key)}>
                    Check
                  </button>
                )}
                {step.verify === 'watch' && (
                  <button style={btnPrimary} disabled={busy || watching} onClick={watchForEvent}>
                    {watching ? 'Watching…' : 'Watch for an event'}
                  </button>
                )}
                {step.key === 'authorize' && (
                  <Link href="/vision/settings" style={{ ...btnPrimary, textDecoration: 'none' }}>
                    Connect Google →
                  </Link>
                )}
                {(step.key === 'sync' || step.key === 'assign_stores' || step.key === 'entrance') && (
                  <Link href="/vision/settings" style={{ ...btn, textDecoration: 'none' }}>
                    Open settings →
                  </Link>
                )}
                {/* Next is always available: an operator who has done a step in another tab should
                    not have to satisfy our check to move on. */}
                <NextButton steps={w?.steps || []} current={step.key} onGo={setOpen} />
              </div>

              {watchMsg && step.verify === 'watch' && (
                <div style={{
                  ...panel, marginTop: 12, fontSize: 13,
                  borderLeft: `3px solid ${watchMsg.startsWith('Event received') ? OK : WARN}`,
                }}>{watchMsg}</div>
              )}
              {verdict[step.key] && (
                <div style={{
                  ...panel, marginTop: 12, fontSize: 13,
                  borderLeft: `3px solid ${verdict[step.key].ok ? OK : WARN}`,
                }}>
                  <b style={{ color: verdict[step.key].ok ? OK : WARN }}>
                    {verdict[step.key].ok ? '✓ ' : ''}{verdict[step.key].message}
                  </b>
                  {verdict[step.key].help && (
                    <div style={{ marginTop: 5, color: 'var(--text2)' }}>{verdict[step.key].help}</div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* A standing readout of the two things that actually prove the setup works, visible on
              every step — an operator should never have to navigate to find out whether it worked. */}
          <div style={{ ...panel, marginTop: 14, fontSize: 12.5, color: 'var(--text2)', display: 'flex', gap: 20, flexWrap: 'wrap' }}>
            <span>Google: <b style={{ color: w?.linked ? OK : 'var(--text3)' }}>{w?.linked ? 'connected' : 'not connected'}</b></span>
            <span>Cameras: <b style={{ color: (w?.cameras || 0) > 0 ? OK : 'var(--text3)' }}>{w?.cameras ?? 0}</b></span>
            <span>Events (7d): <b style={{ color: (w?.events?.last_7d || 0) > 0 ? OK : 'var(--text3)' }}>{w?.events?.last_7d ?? 0}</b></span>
            {!w?.push_ready && (
              <span style={{ color: WARN }}>Push endpoint not configured on the server yet</span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Move to the next step in the list, whatever its state. */
function NextButton({ steps, current, onGo }: { steps: Step[]; current: string; onGo: (k: string) => void }) {
  const i = steps.findIndex(s => s.key === current)
  const next = i >= 0 && i < steps.length - 1 ? steps[i + 1] : null
  if (!next) return null
  return <button style={btn} onClick={() => onGo(next.key)}>Next: {next.number}. {next.title} →</button>
}
