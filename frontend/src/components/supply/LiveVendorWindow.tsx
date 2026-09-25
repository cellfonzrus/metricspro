'use client'
// The live vendor window — the streamed browser of a vendor login's live session (index §36).
//
// It REUSES the platform's one live-login stream: the commcalc /data-sources/{sid}/live-login/{frame,input,
// submit,cancel} endpoints over commcalc/live_login.py (CDP screencast, normalized click coords, the
// high-priority input queue). Supply starts the session (catalog read or assisted order); this panel only
// shows it and forwards the human's clicks and typing — which is how the human signs in past a captcha /
// code, and how they press the vendor's own Place-order button.
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@/lib/client'
import { panel, btn, input } from '@/lib/supply'

// The fields of the live-login /frame payload this panel reads.
interface LiveFrame { phase?: string; message?: string; shot?: string | null; seq?: number; changed?: boolean }
// An input event forwarded to the live session (/input).
type LiveInput =
  | { type: 'type'; text: string }
  | { type: 'key'; key: string }
  | { type: 'scroll'; deltaY: number }
  | { type: 'click' | 'dblclick'; x: number; y: number }

export default function LiveVendorWindow({ sid, title, onClose, children }: {
  sid: string; title: string; onClose: () => void; children?: React.ReactNode
}) {
  const [state, setState] = useState<{ phase?: string; message?: string; shot?: string | null }>({ phase: 'starting', message: 'Starting…' })
  const [code, setCode] = useState('')
  const seq = useRef(0)
  const view = useRef<HTMLDivElement>(null)
  const wheelTs = useRef(0)
  const base = `/api/v1/commcalc/data-sources/${sid}/live-login`

  const refresh = useCallback(async () => {
    try {
      const r: LiveFrame | null = await api(`${base}/frame?since=${seq.current}`)
      if (!r || (r.phase === 'idle' && !r.shot)) return
      seq.current = r.seq ?? seq.current
      setState(p => ({ ...p, phase: r.phase, message: r.message, ...(r.changed && r.shot ? { shot: r.shot } : {}) }))
    } catch { /* keep the last frame */ }
  }, [base])

  useEffect(() => {
    seq.current = 0
    refresh()
    const iv = setInterval(refresh, 350)
    return () => clearInterval(iv)
  }, [refresh])

  async function send(ev: LiveInput) {
    try { await api(`${base}/input`, { method: 'POST', body: JSON.stringify(ev) }) } catch { /* the frame poll shows it */ }
    for (const d of [140, 400, 900]) setTimeout(refresh, d)
  }
  function xy(e: React.MouseEvent<HTMLImageElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }
  }
  async function key(e: React.KeyboardEvent) {
    const k = e.key
    if (['Shift', 'Control', 'Alt', 'Meta', 'CapsLock', 'Dead', 'Process', 'Unidentified'].includes(k)) return
    e.preventDefault()
    if (k.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) await send({ type: 'type', text: k })
    else {
      const mods = [e.ctrlKey && 'Control', e.altKey && 'Alt', e.metaKey && 'Meta'].filter(Boolean) as string[]
      const kk = k.length === 1 ? k.toUpperCase() : k
      await send({ type: 'key', key: mods.length ? [...mods, kk].join('+') : kk })
    }
  }
  function wheel(e: React.WheelEvent) {
    const now = Date.now()
    if (now - wheelTs.current < 110) return
    wheelTs.current = now
    send({ type: 'scroll', deltaY: e.deltaY })
  }
  async function submitCode() {
    if (!code.trim()) return
    try { await api(`${base}/submit`, { method: 'POST', body: JSON.stringify({ code: code.trim() }) }); setCode('') } catch { /* shown by poll */ }
  }
  async function close() {
    try { await api(`${base}/cancel`, { method: 'POST', body: '{}' }) } catch { /* best-effort */ }
    onClose()
  }

  const phase = state.phase || 'starting'
  const color = phase === 'error' ? '#dc2626' : phase === 'authenticated' ? '#16a34a' : phase === 'pulling' ? '#2563eb' : '#6b7280'
  return (
    <section style={{ ...panel, marginTop: 14 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
        <strong style={{ fontSize: 14 }}>{title}</strong>
        <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, background: color, color: '#fff' }}>{phase}</span>
        <span style={{ flex: 1 }} />
        <button style={btn} onClick={close}>Close window</button>
      </div>
      {state.message && <div style={{ fontSize: 13, marginBottom: 8, color: 'var(--text2)' }}>{state.message}</div>}
      {children}
      {phase === 'awaiting_code' && (
        <div style={{ display: 'flex', gap: 8, margin: '8px 0' }}>
          <input style={{ ...input, maxWidth: 200 }} placeholder="Code the vendor sent" value={code} onChange={e => setCode(e.target.value)} />
          <button style={btn} onClick={submitCode}>Submit code</button>
        </div>
      )}
      <div ref={view} tabIndex={0} onKeyDown={key} onWheel={wheel}
           style={{ outline: 'none', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', background: '#111', minHeight: 120 }}>
        {state.shot
          // eslint-disable-next-line @next/next/no-img-element -- live CDP screencast frame streamed as a data: URL; next/image cannot optimise data URLs
          ? <img alt="Live vendor window — click and type here" src={state.shot.startsWith('data:') ? state.shot : `data:image/jpeg;base64,${state.shot}`}
                 style={{ width: '100%', display: 'block', cursor: 'pointer' }}
                 onClick={e => { view.current?.focus(); send({ type: 'click', ...xy(e) }) }}
                 onDoubleClick={e => send({ type: 'dblclick', ...xy(e) })} />
          : <div style={{ color: '#aaa', padding: 30, fontSize: 13 }}>Waiting for the first frame…</div>}
      </div>
      <p style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6 }}>
        This is the vendor&apos;s real site. Click and type on the picture to use it — nothing is ordered unless you (or
        the Confirm button, when the vendor is set up for it) place the order.
      </p>
    </section>
  )
}
