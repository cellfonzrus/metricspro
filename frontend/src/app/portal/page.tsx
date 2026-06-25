'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from '@/lib/client'

// Employee portal (Part B / B4 + B2): mobile-first, standalone (no platform chrome). Face-recognition
// clock-in/out via face-api.js (runs entirely in-browser), with selfie + GPS audit. Employees bookmark
// /portal to their home screen. Served over HTTPS (Vercel) so the camera API is available.
const MODELS = 'https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights'
const FACEAPI_SRC = 'https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js'
const MATCH_THRESHOLD = 0.55

const box: React.CSSProperties = { maxWidth: 460, margin: '0 auto', padding: 16, fontFamily: 'system-ui, -apple-system, sans-serif' }
const bigBtn: React.CSSProperties = { width: '100%', padding: '18px', fontSize: 20, fontWeight: 700, borderRadius: 12, border: 'none', cursor: 'pointer' }

export default function PortalPage() {
  const [employees, setEmployees] = useState<any[]>([])
  const [empId, setEmpId] = useState('')
  const [status, setStatus] = useState<any>(null)        // {clockedIn, entry}
  const [registered, setRegistered] = useState<boolean | null>(null)
  const [modelsReady, setModelsReady] = useState(false)
  const [faceError, setFaceError] = useState(false)
  const [mode, setMode] = useState<'idle' | 'camera'>('idle')
  const [phase, setPhase] = useState('')                 // user-facing camera prompt
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [now, setNow] = useState<string>('')

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gpsRef = useRef<{ lat?: number; lng?: number; acc?: number }>({})

  // live clock
  useEffect(() => { const t = setInterval(() => setNow(new Date().toLocaleTimeString()), 1000); return () => clearInterval(t) }, [])

  // load employees + remembered selection + GPS (background, never blocks) + face-api models
  useEffect(() => {
    api('/api/v1/storeops/employees').then((e: any) => setEmployees(e || [])).catch(() => {})
    const saved = typeof window !== 'undefined' ? localStorage.getItem('portal_emp') : ''
    if (saved) setEmpId(saved)
    if (navigator.geolocation) navigator.geolocation.getCurrentPosition(
      p => { gpsRef.current = { lat: p.coords.latitude, lng: p.coords.longitude, acc: Math.round(p.coords.accuracy) } },
      () => {}, { enableHighAccuracy: true, timeout: 8000 })
    // load face-api.js + models
    const start = () => loadModels()
    if ((window as any).faceapi) { start(); return }
    const s = document.createElement('script')
    s.src = FACEAPI_SRC; s.async = true
    s.onload = start; s.onerror = () => setFaceError(true)
    document.body.appendChild(s)
  }, []) // eslint-disable-line

  async function loadModels() {
    const faceapi = (window as any).faceapi
    if (!faceapi) { setFaceError(true); return }
    try {
      await faceapi.nets.tinyFaceDetector.loadFromUri(MODELS)
      await faceapi.nets.faceLandmark68TinyNet.loadFromUri(MODELS)
      await faceapi.nets.faceRecognitionNet.loadFromUri(MODELS)
      setModelsReady(true)
    } catch { setFaceError(true) }
  }

  const refreshStatus = useCallback((id: string) => {
    if (!id) { setStatus(null); setRegistered(null); return }
    api(`/api/v1/storeops/timeclock/status?employee_id=${encodeURIComponent(id)}`).then(setStatus).catch(() => setStatus(null))
    api(`/api/v1/storeops/timeclock/face?employee_id=${encodeURIComponent(id)}`).then((r: any) => setRegistered(!!r?.registered)).catch(() => setRegistered(null))
  }, [])
  useEffect(() => { refreshStatus(empId) }, [empId, refreshStatus])

  function pickEmp(id: string) { setEmpId(id); setMsg(''); if (id) localStorage.setItem('portal_emp', id) }

  // ── camera helpers ──────────────────────────────────────────────────────────────────────────
  async function openCamera() {
    setMode('camera'); setMsg('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } })
      if (videoRef.current) { videoRef.current.srcObject = stream; await videoRef.current.play() }
    } catch { setMsg('⚠️ Camera unavailable. Allow camera access (and use HTTPS).'); setMode('idle') }
  }
  function closeCamera() {
    const v = videoRef.current
    if (v?.srcObject) (v.srcObject as MediaStream).getTracks().forEach(t => t.stop())
    setMode('idle'); setPhase('')
  }
  async function captureDescriptor(): Promise<number[] | null> {
    const faceapi = (window as any).faceapi
    if (!faceapi || !videoRef.current) return null
    const det = await faceapi.detectSingleFace(videoRef.current,
      new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.4}))
      .withFaceLandmarks(true).withFaceDescriptor()
    return det?.descriptor ? Array.from(det.descriptor as Float32Array) : null
  }
  function captureSelfie(): string {
    const v = videoRef.current!, c = canvasRef.current!
    c.width = v.videoWidth || 320; c.height = v.videoHeight || 240
    const ctx = c.getContext('2d')!
    ctx.setTransform(-1, 0, 0, 1, c.width, 0)   // mirror to match the preview
    ctx.drawImage(v, 0, 0, c.width, c.height)
    return c.toDataURL('image/jpeg', 0.7)
  }

  // ── clock-in (enroll first time, else verify) ────────────────────────────────────────────────
  async function startClockIn() {
    await openCamera()
    // give the camera a moment to start
    setTimeout(() => { registered ? doVerify() : doEnroll() }, 800)
  }

  async function doEnroll() {
    setBusy(true)
    try {
      const caps: number[][] = []
      for (let i = 0; i < 3; i++) {
        setPhase(`Registering your face — hold still (${i + 1}/3)…`)
        let d: number[] | null = null
        for (let tries = 0; tries < 12 && !d; tries++) { d = await captureDescriptor(); if (!d) await wait(250) }
        if (!d) { setMsg('No face detected — make sure your face is well-lit and centered.'); setBusy(false); return }
        caps.push(d); await wait(400)
      }
      const avg = caps[0].map((_, j) => (caps[0][j] + caps[1][j] + caps[2][j]) / 3)
      await api('/api/v1/storeops/timeclock/face', { method: 'POST', body: JSON.stringify({ employee_id: empId, descriptor: avg }) })
      setRegistered(true); setPhase('Registered! Clocking you in…')
      await finalizeClockIn(100)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setBusy(false) }
  }

  async function doVerify() {
    setBusy(true)
    try {
      setPhase('Verifying your face…')
      const faceapi = (window as any).faceapi
      const saved = await api(`/api/v1/storeops/timeclock/face?employee_id=${encodeURIComponent(empId)}&action=descriptor`)
      const ref = new Float32Array(saved?.descriptor || [])
      let best = 1, live: number[] | null = null
      for (let tries = 0; tries < 16; tries++) {
        live = await captureDescriptor()
        if (live) { const dist = faceapi.euclideanDistance(new Float32Array(live), ref); if (dist < best) best = dist; if (dist < MATCH_THRESHOLD) break }
        await wait(250)
      }
      const pct = Math.round((1 - Math.min(best, 1)) * 100)
      if (best < MATCH_THRESHOLD) { setPhase(`Matched (${pct}%) — clocking you in…`); await finalizeClockIn(pct) }
      else { setMsg(`Face didn't match (best ${pct}%). Try again in better light, or re-register.`); setBusy(false) }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setBusy(false) }
  }

  // selfie-only fallback when face models can't load
  async function clockInNoFace() {
    await openCamera(); setBusy(true)
    setTimeout(async () => { setPhase('Capturing…'); await finalizeClockIn(undefined); }, 900)
  }

  async function finalizeClockIn(matchPct?: number) {
    try {
      const selfie = captureSelfie()
      const g = gpsRef.current
      const res: any = await api('/api/v1/storeops/timeclock/clock-in', { method: 'POST', body: JSON.stringify({
        employee_id: empId, selfie, device: 'mobile', face_match_pct: matchPct,
        gps_lat: g.lat, gps_lng: g.lng, gps_accuracy_m: g.acc }) })
      setMsg(`✅ Clocked in at ${res?.data?.time || ''}.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); closeCamera(); refreshStatus(empId) }
  }

  async function clockOut() {
    setBusy(true)
    try { const res: any = await api('/api/v1/storeops/timeclock/clock-out', { method: 'POST', body: JSON.stringify({ employee_id: empId }) }); setMsg(`✅ Clocked out at ${res?.data?.time || ''} — ${res?.data?.hours ?? '?'} hrs.`) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); refreshStatus(empId) }
  }

  const emp = employees.find(e => e.employee_id === empId)
  const clockedIn = status?.clockedIn

  return (
    <div style={box}>
      <div style={{ textAlign: 'center', marginBottom: 12 }}>
        <div style={{ fontSize: 26, fontWeight: 800, color: '#1E3A5F' }}>MetricsPro</div>
        <div style={{ fontSize: 32, fontWeight: 700, fontVariantNumeric: 'tabular-nums', marginTop: 4 }}>{now}</div>
      </div>

      <label style={{ fontSize: 13, fontWeight: 600 }}>Who are you?</label>
      <select value={empId} onChange={e => pickEmp(e.target.value)} style={{ width: '100%', padding: 12, fontSize: 16, borderRadius: 10, border: '1px solid #ccc', marginTop: 4, marginBottom: 16 }}>
        <option value="">Select your name…</option>
        {employees.map(e => <option key={e.employee_id} value={e.employee_id}>{e.name}{e.home_store ? ` — ${e.home_store}` : ''}</option>)}
      </select>

      {mode === 'camera' && (
        <div style={{ marginBottom: 14, textAlign: 'center' }}>
          <video ref={videoRef} playsInline muted style={{ width: '100%', borderRadius: 12, transform: 'scaleX(-1)', background: '#000', maxHeight: 320 }} />
          <div style={{ fontSize: 14, marginTop: 8, minHeight: 20 }}>{phase}</div>
          <button onClick={closeCamera} style={{ ...bigBtn, padding: 10, fontSize: 14, background: '#eee', marginTop: 6 }}>Cancel</button>
        </div>
      )}
      <canvas ref={canvasRef} style={{ display: 'none' }} />

      {empId && mode === 'idle' && (
        <div>
          {clockedIn ? (
            <>
              <div style={{ textAlign: 'center', marginBottom: 10, fontSize: 14, color: '#16794a' }}>
                ● On the clock since {status?.entry?.clock_in ? new Date(status.entry.clock_in).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : ''}
              </div>
              <button disabled={busy} onClick={clockOut} style={{ ...bigBtn, background: '#dc2626', color: '#fff' }}>{busy ? '…' : 'CLOCK OUT'}</button>
            </>
          ) : (
            <>
              <button disabled={busy || (!modelsReady && !faceError)} onClick={faceError ? clockInNoFace : startClockIn}
                style={{ ...bigBtn, background: '#f5a623', color: '#1E3A5F', opacity: (!modelsReady && !faceError) ? 0.5 : 1 }}>
                {busy ? '…' : 'CLOCK IN'}
              </button>
              <div style={{ fontSize: 12, color: '#666', textAlign: 'center', marginTop: 8 }}>
                {faceError ? 'Face models unavailable — clock-in will capture a selfie only.'
                  : !modelsReady ? 'Loading face recognition…'
                    : registered === false ? 'First time — you’ll register your face (3 quick photos).'
                      : 'Look at the camera to verify.'}
              </div>
            </>
          )}
        </div>
      )}

      {msg && <div style={{ marginTop: 16, padding: 12, borderRadius: 10, background: msg.startsWith('✅') ? '#e7f6ec' : '#fdeaea', fontSize: 14, textAlign: 'center' }}>{msg}</div>}

      {emp && (
        <div style={{ marginTop: 20, fontSize: 12, color: '#999', textAlign: 'center' }}>
          {emp.name} · {gpsRef.current.lat ? 'GPS ✓' : 'GPS off'} · {modelsReady ? 'face ✓' : faceError ? 'face ✗' : 'face …'}
        </div>
      )}
    </div>
  )
}

function wait(ms: number) { return new Promise(r => setTimeout(r, ms)) }
