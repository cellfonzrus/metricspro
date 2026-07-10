'use client'
import { useState, useEffect, useRef, useCallback } from 'react'
import { createClient } from '@supabase/supabase-js'
import { api, ORG_ID, localToday, supabase } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import EmployeeWidgets from '@/components/EmployeeWidgets'
import ClosingSubmitForm from '@/components/ClosingSubmitForm'
import TeamSnapshot from '@/components/TeamSnapshot'
import PortalReports from '@/components/PortalReports'
import PortalHelpdesk from '@/components/PortalHelpdesk'
import PortalOnboarding from '@/components/PortalOnboarding'

// Employee kiosk (Part B / B4 + B2): mobile-first, standalone (no platform chrome). Now GUARDED by a
// real login — an employee signs in with their email + password, so a punch is locked to the
// authenticated person (no more "pick any name"). Face-recognition + GPS are kept as audit (defense in
// depth). After signing in they see ALL their own widgets, then log out for the next person (shared
// store device). Served over HTTPS (Vercel) so the camera API is available.
const MODELS = 'https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js@master/weights'
const FACEAPI_SRC = 'https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/dist/face-api.min.js'
const MATCH_THRESHOLD_DEFAULT = 0.60   // face-api's own default; looser than the old 0.55 to stop false rejects. Tenant-overridable via GET /timeclock/config.
const PORTAL_TZ = 'America/New_York'   // business tz — keep punch times consistent with reports

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
  const [stores, setStores] = useState<string[]>([])     // stores this employee may clock in at today (no override)
  const [allStores, setAllStores] = useState<{ code: string; label: string }[]>([])  // every store (for the picker)
  const [selStore, setSelStore] = useState('')           // the store they're clocking in at
  const [ovr, setOvr] = useState<{ store_code: string; selfie: string; g: any } | null>(null)  // pending override
  const [prio, setPrio] = useState<any | null>(null)          // pending priority-sell ack (module 095)
  const [prioChecked, setPrioChecked] = useState(false)
  const [mgr, setMgr] = useState({ email: '', pw: '', busy: false, err: '' })
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
  const [dashErr, setDashErr] = useState('')
  const [coach, setCoach] = useState<any>(null)
  const [repTargets, setRepTargets] = useState<any>(null)

  // tabs + manager span (the "My Team" tab only shows if this employee manages an org unit)
  const [tab, setTab] = useState<'dashboard' | 'closing' | 'team' | 'reports' | 'helpdesk' | 'onboarding'>('dashboard')
  const [span, setSpan] = useState<any>(null)
  const [hdOpen, setHdOpen] = useState(0)   // employee's open helpdesk tickets (tab badge)
  const [onbLeft, setOnbLeft] = useState<number | null>(null)   // remaining onboarding items (tab badge)

  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gpsRef = useRef<{ lat?: number; lng?: number; acc?: number }>({})
  const initedRef = useRef(false)
  const thresholdRef = useRef(MATCH_THRESHOLD_DEFAULT)   // tenant-configurable face-match distance cutoff

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
    authed('/api/v1/storeops/timeclock/config').then((r: any) => { const t = Number(r?.face_match_threshold); if (t) thresholdRef.current = t }).catch(() => {})
  }, [empId, token, authed])
  useEffect(() => { refreshStatus() }, [refreshStatus])

  // which stores can this employee clock in at today (home + scheduled + floater)?
  useEffect(() => {
    if (!empId || !token) { setStores([]); return }
    authed('/api/v1/storeops/timeclock/allowed-stores').then((r: any) => {
      const list: string[] = r?.stores || []
      setStores(list)
      setSelStore(prev => prev || (r?.home_store ? String(r.home_store).toUpperCase() : '') || list[0] || '')
    }).catch(() => setStores([]))
  }, [empId, token, authed])

  // full store list for the "which store are you at?" picker (so a floater/visiting rep can choose)
  useEffect(() => {
    if (!empId) { setAllStores([]); return }
    api('/api/v1/storeops/timeclock/stores').then((r: any) => setAllStores(
      (r || []).filter((s: any) => s.store_code).map((s: any) => ({
        code: String(s.store_code).toUpperCase(),
        label: `${s.store_code}${s.address ? ' — ' + String(s.address).slice(0, 24) : ''}`,
      })))).catch(() => setAllStores([]))
  }, [empId])

  // manager override: verify the manager's password on a THROWAWAY client (so it doesn't replace the
  // employee's kiosk session), then authorize the override with the manager's token.
  async function submitOverride() {
    if (!ovr) return
    setMgr(m => ({ ...m, busy: true, err: '' }))
    try {
      const tmp = createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
        { auth: { persistSession: false, autoRefreshToken: false, storageKey: 'mp-mgr-override' } })
      const { data, error } = await tmp.auth.signInWithPassword({ email: mgr.email.trim(), password: mgr.pw })
      if (error || !data?.session?.access_token) throw new Error(error?.message || 'Manager sign-in failed')
      const mtoken = data.session.access_token
      const res: any = await api('/api/v1/storeops/timeclock/override', {
        method: 'POST', headers: { Authorization: `Bearer ${mtoken}` },
        body: JSON.stringify({ employee_id: empId, store_code: ovr.store_code, selfie: ovr.selfie,
          device: 'kiosk-override', gps_lat: ovr.g?.lat, gps_lng: ovr.g?.lng, gps_accuracy_m: ovr.g?.acc }) })
      try { await tmp.auth.signOut() } catch { /* throwaway */ }
      setMsg(`✅ Clocked in at ${res?.data?.time || ''} (approved by ${res?.override_by || 'manager'}).`)
      setOvr(null); setMgr({ email: '', pw: '', busy: false, err: '' }); refreshStatus()
    } catch (e: any) {
      setMgr(m => ({ ...m, busy: false, err: e?.message || 'Override failed' }))
    }
  }

  // load their widgets (scoped to the signed-in employee)
  useEffect(() => {
    if (!empId) { setDash(null); setDashErr(''); return }
    setCoach(null); setRepTargets(null); setDashErr('')
    api(`/api/v1/core/employee-dashboard?org_id=${ORG_ID}&employee_id=${encodeURIComponent(empId)}`)
      .then((d: any) => {
        setDash(d)
        const nm = d?.employee?.name, per = d?.period, store = d?.employee?.store
        if (nm && per) api(`/api/v1/commcalc/coaching/${encodeURIComponent(per)}?rep=${encodeURIComponent(nm)}`)
          .then((c: any) => setCoach((c?.reps || [])[0] || null)).catch(() => {})
        if (nm && per && store) api(`/api/v1/commcalc/targets/${encodeURIComponent(per)}/calendar?scope=rep&store_code=${encodeURIComponent(store)}&rep=${encodeURIComponent(nm)}&today=${localToday()}`)
          .then(setRepTargets).catch(() => {})
      }).catch((e: any) => { setDash(null); setDashErr(e?.message || 'Could not load your dashboard.') })
  }, [empId])

  // is this employee a manager? (drives the "My Team" tab)
  useEffect(() => {
    if (!empId || !token) { setSpan(null); return }
    authed('/api/v1/storeops/org/my-span').then(setSpan).catch(() => setSpan(null))
  }, [empId, token, authed])

  // open-ticket count for the Helpdesk tab badge (so it shows before they open the tab)
  useEffect(() => {
    if (!user?.email) { setHdOpen(0); return }
    api(`/api/v1/helpdesk/tickets?org_id=${ORG_ID}&agent=false&requester=${encodeURIComponent(user.email)}`)
      .then((d: any) => setHdOpen(Array.isArray(d) ? d.filter((t: any) => t.status?.stage !== 'done').length : 0))
      .catch(() => setHdOpen(0))
  }, [user?.email])

  // onboarding tab: only for a hire HR has actually invited (has a profile) with items still left.
  useEffect(() => {
    if (!empId || !token) { setOnbLeft(null); return }
    api('/api/v1/hr/onboarding/me')
      .then((r: any) => {
        if (!r?.ready || !r?.has_profile) { setOnbLeft(null); return }
        const left = Math.max(0, (r.progress?.total || 0) - (r.progress?.done || 0)) + (r.intake_submitted ? 0 : (r.intake_fields?.length ? 1 : 0))
        setOnbLeft(left)
      })
      .catch(() => setOnbLeft(null))
  }, [empId, token])

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
      const thr = thresholdRef.current
      let best = 1, live: number[] | null = null
      for (let tries = 0; tries < 16; tries++) {
        live = await captureDescriptor()
        if (live) { const dist = faceapi.euclideanDistance(new Float32Array(live), ref); if (dist < best) best = dist; if (dist < thr) break }
        await wait(250)
      }
      const pct = Math.round((1 - Math.min(best, 1)) * 100)
      if (best < thr) { setPhase(`Matched (${pct}%) — clocking you in…`); await finalizeClockIn(pct) }
      else {
        setMsg(`Face didn't match (best ${pct}%). Try again in better light, or re-register — a manager can also approve you.`); setBusy(false)
        // Log the false-reject so admins can see recurring issues + the fix (Failure Logs module). Best-effort.
        authed('/api/v1/core/failures', { method: 'POST', body: JSON.stringify({
          category: 'face_mismatch', source: 'kiosk/clock-in', store_code: selStore || undefined,
          employee_name: (user as any)?.full_name || (user as any)?.name || email || undefined,
          message: `Face didn't match at clock-in (best ${pct}%).`,
          detail: { best_pct: pct, best_distance: Number(best.toFixed(3)), threshold: thr },
        }) }).catch(() => {})
      }
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
        selfie, device: 'kiosk', face_match_pct: matchPct, store_code: selStore || undefined,
        gps_lat: g.lat, gps_lng: g.lng, gps_accuracy_m: g.acc }) })
      if (res?.needs_override) {
        // not home/scheduled/floater → hold the punch for a manager to approve (keeps the selfie/GPS)
        setOvr({ store_code: res.store_code || selStore, selfie, g })
        setMsg(res.message || `You're not scheduled at ${res.store_code || selStore} today — manager approval needed.`)
      } else if (res?.needs_priority_ack) {
        // store has phones in the final % of their pay window → rep must acknowledge before clocking in
        setPrio({ store_code: res.store_code || selStore, priority: res.priority || [], selfie, g, matchPct })
        setPrioChecked(false)
        setMsg(res.message || 'Acknowledge the priority phones to clock in.')
      } else {
        setMsg(`✅ Clocked in at ${res?.data?.time || ''}${res?.data?.store_code ? ` · ${res.data.store_code}` : ''}.`)
      }
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); closeCamera(); refreshStatus() }
  }

  async function confirmPriorityAck() {
    if (!prio || !prioChecked) return
    setBusy(true)
    try {
      const res: any = await authed('/api/v1/storeops/timeclock/clock-in', { method: 'POST', body: JSON.stringify({
        selfie: prio.selfie, device: 'kiosk', face_match_pct: prio.matchPct, store_code: prio.store_code || undefined,
        gps_lat: prio.g?.lat, gps_lng: prio.g?.lng, gps_accuracy_m: prio.g?.acc,
        priority_ack: true, priority_ack_count: (prio.priority || []).length }) })
      setMsg(`✅ Clocked in at ${res?.data?.time || ''}${res?.data?.store_code ? ` · ${res.data.store_code}` : ''}.`)
      setPrio(null)
    } catch (e: any) { setMsg('❌ ' + (e?.message || e)) }
    finally { setBusy(false); refreshStatus() }
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
                ● On the clock since {status?.entry?.clock_in ? new Date(status.entry.clock_in).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', timeZone: PORTAL_TZ }) : ''}{status?.entry?.store_code ? ` · ${status.entry.store_code}` : ''}
              </div>
              <button disabled={busy} onClick={clockOut} style={{ ...bigBtn, background: '#dc2626', color: '#fff' }}>{busy ? '…' : 'CLOCK OUT'}</button>
            </>
          ) : (
            <>
              {allStores.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: '#475569', marginBottom: 5 }}>Which store are you at?</div>
                  <select value={selStore} onChange={e => setSelStore(e.target.value)} style={{ ...inp, marginTop: 0 }}>
                    {!selStore && <option value="">Select store…</option>}
                    {allStores.map(s => <option key={s.code} value={s.code}>{s.label}{stores.includes(s.code) ? '  ✓' : ''}</option>)}
                  </select>
                  {selStore && !stores.includes(selStore) && (
                    <div style={{ fontSize: 12, color: '#b45309', marginTop: 5 }}>⚠️ You&apos;re not scheduled here — a manager will need to approve after the photo.</div>
                  )}
                </div>
              )}
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

      {/* priority-sell acknowledgment — store has phones in the final % of their pay window (module 095) */}
      {prio && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60, padding: 16 }}>
          <div style={{ background: '#fff', borderRadius: 14, padding: 22, width: '100%', maxWidth: 420, maxHeight: '85vh', overflow: 'auto' }}>
            <div style={{ fontSize: 17, fontWeight: 800, color: '#1E3A5F' }}>📱 Sell these phones today</div>
            <div style={{ fontSize: 13, color: '#475569', margin: '6px 0 12px' }}>
              These devices at <b>{prio.store_code}</b> are near their payment due date. Prioritize selling them today.
            </div>
            <div style={{ border: '1px solid #e2e8f0', borderRadius: 10, overflow: 'hidden', marginBottom: 14 }}>
              {(prio.priority || []).map((p: any, i: number) => (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 12px', borderBottom: i < prio.priority.length - 1 ? '1px solid #f1f5f9' : 'none', fontSize: 13 }}>
                  <span><b>{p.device_model || '—'}</b> <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#64748b' }}>{p.imei}</span></span>
                  <span style={{ color: '#d97706', fontWeight: 600 }}>due {p.window_end || p.due_date || '—'}</span>
                </div>
              ))}
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', fontSize: 13, color: '#334155', cursor: 'pointer' }}>
              <input type="checkbox" checked={prioChecked} onChange={e => setPrioChecked(e.target.checked)} style={{ marginTop: 3 }} />
              <span>I will prioritize selling these phones today.</span>
            </label>
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button disabled={!prioChecked || busy} onClick={confirmPriorityAck} style={{ ...bigBtn, padding: '12px 0', fontSize: 15, background: prioChecked ? '#059669' : '#94a3b8', color: '#fff', opacity: busy ? 0.7 : 1 }}>{busy ? 'Clocking in…' : 'Acknowledge & clock in'}</button>
              <button onClick={() => { setPrio(null); setMsg('') }} style={{ ...bigBtn, padding: '12px 0', fontSize: 15, background: '#eee', color: '#333', flex: '0 0 90px' }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* manager override — the employee is at a store they're not scheduled for */}
      {ovr && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 60, padding: 16 }}>
          <div style={{ background: '#fff', borderRadius: 14, padding: 22, width: '100%', maxWidth: 380 }}>
            <div style={{ fontSize: 17, fontWeight: 800, color: '#1E3A5F' }}>Manager approval</div>
            <div style={{ fontSize: 13, color: '#475569', margin: '6px 0 14px' }}>
              {empName || 'This employee'} isn&apos;t scheduled at <b>{ovr.store_code}</b> today. A manager can approve — this also adds today&apos;s shift there.
            </div>
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569' }}>Manager email</label>
            <input type="email" value={mgr.email} onChange={e => setMgr(m => ({ ...m, email: e.target.value }))} style={inp} placeholder="manager@…" autoFocus />
            <label style={{ fontSize: 12, fontWeight: 600, color: '#475569', marginTop: 12, display: 'block' }}>Manager password</label>
            <input type="password" value={mgr.pw} onChange={e => setMgr(m => ({ ...m, pw: e.target.value }))} style={inp} placeholder="••••••••" />
            {mgr.err && <div style={{ color: '#dc2626', fontSize: 13, marginTop: 10 }}>{mgr.err}</div>}
            <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
              <button disabled={mgr.busy} onClick={submitOverride} style={{ ...bigBtn, padding: '12px 0', fontSize: 15, background: '#059669', color: '#fff', opacity: mgr.busy ? 0.7 : 1 }}>{mgr.busy ? 'Approving…' : 'Approve & clock in'}</button>
              <button onClick={() => { setOvr(null); setMgr({ email: '', pw: '', busy: false, err: '' }); setMsg('') }} style={{ ...bigBtn, padding: '12px 0', fontSize: 15, background: '#eee', color: '#333', flex: '0 0 90px' }}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        {onbLeft !== null && <TabBtn k="onboarding" label="📝 My Onboarding" tab={tab} setTab={setTab} badge={onbLeft} />}
        <TabBtn k="dashboard" label="📊 My Dashboard" tab={tab} setTab={setTab} />
        <TabBtn k="closing" label="🧾 Daily Closing" tab={tab} setTab={setTab} />
        {span?.is_manager && <TabBtn k="team" label="🫂 My Team" tab={tab} setTab={setTab} />}
        <TabBtn k="reports" label="📊 Reports" tab={tab} setTab={setTab} />
        <TabBtn k="helpdesk" label="🎫 Helpdesk" tab={tab} setTab={setTab} badge={hdOpen} />
      </div>

      {tab === 'dashboard' && (dash
        ? <EmployeeWidgets data={dash} coach={coach} repTargets={repTargets} />
        : dashErr
          ? <div className="card" style={{ padding: 18, color: 'var(--text2)', fontSize: 14 }}>{dashErr}</div>
          : <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}><div className="spinner" /></div>)}

      {tab === 'closing' && <ClosingSubmitForm defaultEmployeeName={empName || dash?.employee?.name} />}

      {tab === 'team' && <TeamSnapshot period={dash?.period || ''} token={token || undefined} />}

      {tab === 'reports' && <PortalReports />}

      {tab === 'helpdesk' && <PortalHelpdesk email={user?.email || ''} name={empName || dash?.employee?.name || ''} empId={empId} onOpenCount={setHdOpen} />}

      {tab === 'onboarding' && <PortalOnboarding onCount={setOnbLeft} />}
    </div>
  )
}

function TabBtn({ k, label, tab, setTab, badge }:
  { k: 'dashboard' | 'closing' | 'team' | 'reports' | 'helpdesk' | 'onboarding'; label: string; tab: string; setTab: (t: any) => void; badge?: number }) {
  const active = tab === k
  return (
    <button onClick={() => setTab(k)} style={{
      padding: '9px 14px', borderRadius: 9, border: '1px solid var(--border)', cursor: 'pointer',
      fontSize: 14, fontWeight: 600, background: active ? '#1E3A5F' : 'var(--surface)',
      color: active ? '#fff' : 'var(--text2)' }}>
      {label}
      {badge ? <span style={{ marginLeft: 6, background: '#dc2626', color: '#fff', borderRadius: 10,
        padding: '0 7px', fontSize: 12, fontWeight: 700 }}>{badge}</span> : null}
    </button>
  )
}

function wait(ms: number) { return new Promise(r => setTimeout(r, ms)) }
