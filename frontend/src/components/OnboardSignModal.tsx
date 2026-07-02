'use client'
import { useEffect, useRef, useState } from 'react'

// Online FILL & SIGN modal (migration 082) — shared by the public /onboard/[token] portal and the
// logged-in /portal "My Onboarding" tab. Renders the item's configurable form fields (if any), a
// draw-to-sign canvas + typed legal name, and submits through the surface-specific onSubmit (the
// caller owns the API call + auth). Backend 400s (missing fields) surface inline in the modal.

export type SignableTask = {
  id: string; label: string; description?: string
  form_fields?: { key?: string; label?: string; required?: boolean }[] | null
  requires_signature?: boolean
  missing_fields?: string[] | null; returned_reason?: string | null
}

const inp: React.CSSProperties = { padding: '9px 11px', borderRadius: 8, border: '1px solid #cbd5e1', fontSize: 14, width: '100%', boxSizing: 'border-box', background: '#fff', color: '#0f172a' }
const lbl: React.CSSProperties = { fontSize: 13, fontWeight: 600, color: '#334155', display: 'block', margin: '0 0 4px' }

export default function OnboardSignModal({ task, onCancel, onSubmit }: {
  task: SignableTask
  onCancel: () => void
  onSubmit: (payload: { form_data: Record<string, string>; signature: string; signed_name: string }) => Promise<void>
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const drawing = useRef(false)
  const [hasInk, setHasInk] = useState(false)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [name, setName] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const fields = (task.form_fields || []).filter(f => f && (f.key || f.label))
  const needsSig = task.requires_signature !== false

  useEffect(() => {
    const c = canvasRef.current
    if (!c) return
    const ctx = c.getContext('2d')!
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height)
    ctx.strokeStyle = '#111'; ctx.lineWidth = 2.2; ctx.lineCap = 'round'; ctx.lineJoin = 'round'
  }, [])

  function pos(e: React.PointerEvent<HTMLCanvasElement>) {
    const c = canvasRef.current!
    const r = c.getBoundingClientRect()
    return { x: (e.clientX - r.left) * (c.width / r.width), y: (e.clientY - r.top) * (c.height / r.height) }
  }
  function down(e: React.PointerEvent<HTMLCanvasElement>) {
    e.preventDefault(); (e.target as HTMLCanvasElement).setPointerCapture?.(e.pointerId)
    drawing.current = true
    const ctx = canvasRef.current!.getContext('2d')!
    const p = pos(e); ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p.x + 0.1, p.y + 0.1); ctx.stroke()
    setHasInk(true)
  }
  function move(e: React.PointerEvent<HTMLCanvasElement>) {
    if (!drawing.current) return
    e.preventDefault()
    const ctx = canvasRef.current!.getContext('2d')!
    const p = pos(e); ctx.lineTo(p.x, p.y); ctx.stroke()
  }
  function clear() {
    const c = canvasRef.current!; const ctx = c.getContext('2d')!
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height); setHasInk(false)
  }
  async function submit() {
    setErr('')
    const missing = fields.filter(f => f.required !== false && !String(vals[f.key || f.label || ''] || '').trim()).map(f => f.label || f.key)
    if (needsSig && !hasInk) missing.push('your signature (draw in the box)')
    if (!name.trim()) missing.push('your full legal name')
    if (missing.length) { setErr('Please complete: ' + missing.join(', ')); return }
    setBusy(true)
    try {
      await onSubmit({ form_data: vals, signature: hasInk ? canvasRef.current!.toDataURL('image/png') : '', signed_name: name.trim() })
    } catch (e: any) { setErr(e?.message || 'Could not submit — please try again'); setBusy(false); return }
    setBusy(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60, padding: 12 }} onClick={onCancel}>
      <div style={{ background: '#fff', borderRadius: 14, padding: 20, width: 560, maxWidth: '96vw', maxHeight: '92vh', overflow: 'auto', color: '#0f172a' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ fontSize: 17, fontWeight: 800, margin: '0 0 2px' }}>✍️ {task.label}</h3>
        <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 12px' }}>Fill in the details below and sign — no printing needed. Prefer paper? Close this and use the download + upload buttons instead.</p>

        {(task.missing_fields?.length || task.returned_reason) && (
          <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, padding: '8px 10px', fontSize: 13, color: '#991b1b', marginBottom: 12 }}>
            ↩ Returned for corrections{task.missing_fields?.length ? <> — missing: <b>{task.missing_fields.join(', ')}</b></> : ''}
            {task.returned_reason && <div style={{ fontSize: 12, marginTop: 2 }}>{task.returned_reason}</div>}
          </div>
        )}

        {fields.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
            {fields.map(f => {
              const k = f.key || f.label || ''
              return (
                <div key={k}>
                  <label style={lbl}>{f.label || f.key}{f.required !== false && <span style={{ color: '#ef4444' }}> *</span>}</label>
                  <input style={inp} value={vals[k] || ''} onChange={e => setVals(v => ({ ...v, [k]: e.target.value }))} />
                </div>
              )
            })}
          </div>
        )}

        {needsSig && (
          <div style={{ marginBottom: 12 }}>
            <label style={lbl}>Draw your signature *</label>
            <canvas ref={canvasRef} width={520} height={150}
              style={{ width: '100%', height: 150, border: '1.5px dashed #94a3b8', borderRadius: 10, touchAction: 'none', background: '#fff', cursor: 'crosshair' }}
              onPointerDown={down} onPointerMove={move} onPointerUp={() => { drawing.current = false }} onPointerLeave={() => { drawing.current = false }} />
            <button onClick={clear} style={{ marginTop: 4, fontSize: 12, color: '#64748b', background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', padding: 0 }}>Clear and sign again</button>
          </div>
        )}

        <div style={{ marginBottom: 12 }}>
          <label style={lbl}>Type your full legal name *</label>
          <input style={inp} value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Jane Q. Public" />
        </div>

        <p style={{ fontSize: 12, color: '#64748b', margin: '0 0 12px' }}>
          By signing, I certify that the information I provided is true, correct, and complete, and I intend this electronic signature to be as valid as my handwritten signature.
        </p>
        {err && <div style={{ background: '#fef2f2', border: '1px solid #fca5a5', borderRadius: 8, padding: '8px 10px', fontSize: 13, color: '#991b1b', marginBottom: 10 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={{ padding: '9px 14px', borderRadius: 8, border: '1px solid #cbd5e1', background: '#fff', fontSize: 14, cursor: 'pointer', color: '#334155' }}>Cancel</button>
          <button onClick={submit} disabled={busy} style={{ padding: '9px 16px', borderRadius: 8, border: 'none', background: '#059669', color: '#fff', fontSize: 14, fontWeight: 700, cursor: 'pointer', opacity: busy ? 0.6 : 1 }}>{busy ? 'Submitting…' : 'Sign & submit'}</button>
        </div>
      </div>
    </div>
  )
}
