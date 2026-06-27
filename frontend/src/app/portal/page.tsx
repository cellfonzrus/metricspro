'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { api, ORG_ID, localToday, supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import EmployeeWidgets from '@/components/EmployeeWidgets'
import ClosingSubmitForm from '@/components/ClosingSubmitForm'
import TeamSnapshot from '@/components/TeamSnapshot'
import PortalReports from '@/components/PortalReports'

// Employee kiosk (Part B / B4 + B2): mobile-first, standalone (no platform chrome). Now GUARDED by a
// real login — an employee signs in with their email + password, so a punch is locked to the
// authenticated person (no more "pick any name"). Face-recognition + GPS are kept as audit (defense in
// depth). After signing in they see ALL their own widgets, then log out for the next person (shared
// store device). Served over HTTPS (Vercel) so the camera API is available.
const MODELS = 'https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights'
const FACEAPI_SRC = 'https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js'
const MATCH_THRESHOLD = 0.55

const shell: React.CSSProperties = { maxWidth: 900, margin: '0 auto', padding: 16, fontFamily: 'system-ui, -apple-system, sans-serif' }
const box: React.CSSProperties = { maxWidth: 460, margin: '0 auto' }
const bigBtn: React.CSSProperties = { width: '100%', padding: '18px', fontSize: 20, fontWeight: 700, borderRadius: 12, border: 'none', cursor: 'pointer' }
const inp: React.CSSProperties = { width: '100%', marginTop: 5, padding: '11px 12px', borderRadius: 9, border: '1px solid #cbd5e1', fontSize: 15, boxSizing: 'border-box' }

export default function PortalPage() {
  const { loading: authLoading, session, user, token, signOut } = useAuth()
  const empId = user?.employee_id || ''
  const empName = user?.full_name || ''

  // login form
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loginBusy, setLoginBusy] = useState(false)
  const [loginErr, setLoginErr] = useState('')

  // clock state
  const [status, setStatus] = useState<any>(null)        // {clockedIn, entry}
  const [registered, setRegistered] = useState<boolean | null>(null)
  const [modelsReady, setModelsReady] = useState(false)
  const [faceError, setFaceError] = useState(false)
  const [mode, setMode] = useState<'idle' | 'camera'>('idle')
  const [phase, setPhase] = useState('')                 // user-facing camera prompt
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [now, setNow] = useState<string>('')

  // their dashboard widgets
  const [dash, setDash] = useState<any>(null)
  const [coach, setCoach] = useState<any>(null)
  const [repTargets, setRepTargets] = useState<any>(null)

  // tabs + manager span (the "My Team" tab only shows if this employee manages an org unit)
  const [tab, setTab] = useState<'dashboard' | 'closing' | 'team' | 'reports'>('dashboard')
  const [span, setSpan] = useState<any>(null)

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gpsRef = useRef<{ lat?: number; lng?: number; acc?: number }>({})
  const initedRef = useRef(false)

  // every timeclock call carries the Supabase token — the backend derives employee_id from it.
  const authed = useCallback((path: string, opts: RequestInit = {}) =>
    api(path, { ...opts, headers: { ...(opts.headers || {}), Authorization: `Bearer ${token}` } }), [token])

  // live clock
  useEffect(() => { const t = setInterval(() => setNow(new Date().toLocaleTimeString()), 1000); return () => clearInterval(t) }, [])

  // one-time init once signed in: GPS + face-api models (don't prompt before login)
  useEffect(() => {
    if (!session || !empId || initedRef.current) return
    initedRef.current = true
    if (navigator.geolocation) navigator.geolocation.getCurrentPosition(
      p => { gpsRef.current = { lat: p.coords.latitude, lng: p.coords.longitude, acc: Math.round(p.coords.accuracy) } },
      () => {}, { enableHighAccuracy: true, timeout: 8000 })
    const start = () => loadModels()
    if ((window as any).faceapi) { start(); return }
    const s = document.createElement('script')
    s.src = FACEAPI_SRC; s.async = true
    s.onload = start; s.onerror = () => setFaceError(true)
    document.body.appendChild(s)
  }, [session, empId])

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

  const refreshStatus = useCallback(() => {
    if (!empId || !token) { setStatus(null); setRegistered(null); return }
    authed('/api/v1/storeops/timeclock/status').then(setStatus).catch(() => setStatus(null))
    authed('/api/v1/storeops/timeclock/face').then((r: any) => setRegistered(!!r?.registered)).catch(() => setRegistered(null))
  }, [empId, token, authed])
  useEffect(() => { refreshStatus() }, [refreshStatus])

  // load their widgets (scoped to the signed-in employee)
  useEffect(() => {
    if (!empId) { setDash(null); return }
    setCoach(null); setRepTargets(null)
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(empId)}`)
      .then((d: any) => {
        setDash(d)
        const nm = d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`)
          .then((c: any) => setCoach((c?.reps || [])[0] || null)).catch(() => {})
        if (nm && per && store) api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`)
          .then(setRepTargets).catch(() => {})
      }).catch(() => setDash(null))
  }, [empId])

  // is this employee a manager? (drives the "My Team" tab)
  useEffect(() => {
    if (!empId || !token) { setSpan(null); return }
    authed('/api/v1/storeops/org/my-span').then(setSpan).catch(() => setSpan(null))
  }, [empId, token, authed])

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
      new faceapi.TinyFaceDetectorOptions({ inputSize: 224, scoreThreshold: 0.4 }))
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
      await authed('/api/v1/storeops/timeclock/face', { method: 'POST', body: JSON.stringify({ descriptor: avg }) })
      setRegistered(true); setPhase('Registered! Clocking you in…')
      await finalizeClockIn(100)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)); setBusy(false) }
  }

  async function doVerify() {
    setBusy(true)
    try {
      setPhase('Verifying your face…')
      const faceapi = (window as any).faceapi
      const saved = await authed(`/api/v1/storeops/timeclock/face?action=descriptor`)
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
    setTimeout(async () => { setPhase('Capturing…'); await finalizeClockIn(undefined) }, 900)
  }

  async function finalizeClockIn(matchPct?: number) {
    try {
      const selfie = captureSelfie()
      const g = gpsRef.current
      const res: any = await authed('/api/v1/storeops/timeclock/clock-in', { method: 'POST', body: JSON.stringify({
        selfie, device: 'kiosk', face_match_pct: matchPct,
        gps_lat: g.lat, gps_lng: g.lng, gps_accuracy_m: g.acc }) })
      setMsg(`✅ Clocked in at ${res?.data?.time || ''}.`)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); closeCamera(); refreshStatus() }
  }

  async function clockOut() {
    setBusy(true)
    try { const res: any = await authed('/api/v1/storeops/timeclock/clock-out', { method: 'POST', body: JSON.stringify({}) }); setMsg(`✅ Clocked out at ${res?.data?.time || ''} — ${res?.data?.hours ?? '?'} hrs.`) }
    catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); refreshStatus() }
  }

  async function doLogin(e: React.FormEvent) {
    e.preventDefault()
    setLoginErr(''); setLoginBusy(true)
    const { error } = await supabase.auth.signInWithPassword({ email: email.trim(), password })
    setLoginBusy(false)
    if (error) { setLoginErr(error.message || 'Sign-in failed'); return }
    setPassword('')   // don't leave it in state on a shared device
  }

  async function logout() {
    closeCamera()
    setStatus(null); setRegistered(null); setDash(null); setCoach(null); setRepTargets(null)
    setMsg(''); setEmail(''); setPassword(''); initedRef.current = false
    await signOut()
  }

  const clockedIn = status?.clockedIn

  // ── render branches ───────────────────────────────────────────────────────────────────────────
  if (authLoading) {
    return <div style={{ ...shell, textAlign: 'center', paddingTop: 80 }}><div className="spinner" /></div>
  }

  // not signed in → login
  if (!session) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: 'linear-gradient(135deg, #1e3a5f 0%, #0f172a 100%)', padding: 20 }}>
        <div style={{ width: '100%', maxWidth: 380, background: 'white', borderRadius: 14, padding: '34px 30px', boxShadow: '0 20px 60px rgba(0,0,0,0.3)' }}>
          <div style={{ textAlign: 'center', marginBottom: 20 }}>
            <div style={{ fontSize: 22, fontWeight: 800, color: '#1e3a5f' }}>MetricsPro</div>
            <div style={{ fontSize: 13, color: '#64748b', marginTop: 2 }}>Sign in to clock in</div>
            <div style={{ fontSize: 30, fontWeight: 700, fontVariantNumeric: 'tabular-nums', marginTop: 10, color: '#1e3a5f' }}>{now}</div>
          </div>
          <form onSubmit={doLogin}>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Email</label>
            <input type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus style={inp} placeholder="you@cellfonzrus.com" />
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 14, display: 'block' }}>Password</label>
            <input type="password" value={password} onChange={e => setPassword(e.target.value)} required style={inp} placeholder="••••••••" />
            {loginErr && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 12 }}>{loginErr}</div>}
            <button type="submit" disabled={loginBusy} style={{ ...bigBtn, marginTop: 18, padding: '13px 0', fontSize: 16, background: '#1e3a5f', color: 'white', opacity: loginBusy ? 0.7 : 1 }}>
              {loginBusy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
          <div style={{ fontSize: 12, color: '#94a3b8', textAlign: 'center', marginTop: 16 }}>Trouble signing in? Contact your administrator.</div>
        </div>
      </div>
    )
  }

  // signed in but the login isn't linked to an employee
  if (!empId) {
    return (
      <div style={shell}>
        <div style={{ ...box }} className="card">
          <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>Login not linked to an employee</div>
          <div style={{ fontSize: 14, color: 'var(--text2)', marginBottom: 16 }}>
            You're signed in as <b>{user?.email}</b>, but your account isn't linked to an employee record yet. Ask an admin to set your Employee ID in <b>Roles &amp; Access</b>.
          </div>
          <button className="btn btn-secondary" onClick={logout}>Log out</button>
        </div>
      </div>
    )
  }

  // signed in + linked → kiosk
  return (
    <div style={shell}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 20, fontWeight: 800, color: '#1E3A5F' }}>MetricsPro</div>
          <div style={{ fontSize: 13, color: 'var(--text2)' }}>Hi, {empName || dash?.employee?.name || user?.email}{dash?.employee?.store ? ` · ${dash.employee.store}` : ''}</div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 26, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{now}</div>
          <button className="btn btn-secondary" style={{ fontSize: 13, marginTop: 4 }} onClick={logout}>Log out</button>
        </div>
      </div>

      <div className="card" style={{ ...box, marginBottom: 18 }}>
        {mode === 'camera' && (
          <div style={{ marginBottom: 14, textAlign: 'center' }}>
            <video ref={videoRef} playsInline muted style={{ width: '100%', borderRadius: 12, transform: 'scaleX(-1)', background: '#000', maxHeight: 320 }} />
            <div style={{ fontSize: 14, marginTop: 8, minHeight: 20 }}>{phase}</div>
            <button onClick={closeCamera} style={{ ...bigBtn, padding: 10, fontSize: 14, background: '#eee', marginTop: 6 }}>Cancel</button>
          </div>
        )}
        <canvas ref={canvasRef} style={{ display: 'none' }} />

        {mode === 'idle' && (
          clockedIn ? (
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
          )
        )}

        {msg && <div style={{ marginTop: 16, padding: 12, borderRadius: 10, background: msg.startsWith('✅') ? '#e7f6ec' : '#fdeaea', fontSize: 14, textAlign: 'center' }}>{msg}</div>}
        <div style={{ marginTop: 16, fontSize: 12, color: '#999', textAlign: 'center' }}>
          {gpsRef.current.lat ? 'GPS ✓' : 'GPS off'} · {modelsReady ? 'face ✓' : faceError ? 'face ✗' : 'face …'}
        </div>
      </div>

      {/* tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        <TabBtn k="dashboard" label="📊 My Dashboard" tab={tab} setTab={setTab} />
        <TabBtn k="closing" label="🧾 Daily Closing" tab={tab} setTab={setTab} />
        {span?.is_manager && <TabBtn k="team" label="🫂 My Team" tab={tab} setTab={setTab} />}
        <TabBtn k="reports" label="📊 Reports" tab={tab} setTab={setTab} />
      </div>

      {tab === 'dashboard' && (dash
        ? <EmployeeWidgets data={dash} coach={coach} repTargets={repTargets} />
        : <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>)}

      {tab === 'closing' && <ClosingSubmitForm defaultEmployeeName={empName || dash?.employee?.name} />}

      {tab === 'team' && <TeamSnapshot period={dash?.period || ''} token={token || undefined} />}

      {tab === 'reports' && <PortalReports />}
    </div>
  )
}

function TabBtn({ k, label, tab, setTab }:
  { k: 'dashboard' | 'closing' | 'team' | 'reports'; label: string; tab: string; setTab: (t: any) => void }) {
  const active = tab === k
  return (
    <button onClick={() => setTab(k)} style={{
      padding: '9px 14px', borderRadius: 9, border: '1px solid var(--border)', cursor: 'pointer',
      fontSize: 14, fontWeight: 600, background: active ? '#1E3A5F' : 'var(--surface)',
      color: active ? '#fff' : 'var(--text2)' }}>{label}</button>
  )
}

function wait(ms: number) { return new Promise(r => setTimeout(r, ms)) }
