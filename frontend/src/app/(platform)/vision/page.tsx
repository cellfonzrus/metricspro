'use client'
// Vision — LIVE WALL. The "pull the camera feed in live mode" surface.
//
// HOW THE VIDEO GETS HERE: it does not go through our backend. The browser creates an
// RTCPeerConnection, we POST its SDP offer to /vision/cameras/{id}/stream, the backend forwards it to
// Google's Smart Device Management API and hands back Google's answer. From that moment the media
// flows browser <-> Google directly. Our server brokered the handshake and never sees a frame.
//
// THE FIVE-MINUTE CLOCK: a Nest live-stream grant expires in about five minutes. The backend tells us
// `extend_after_seconds`; we call /stream/{id}/extend on that schedule while the tile is open, and we
// stop the grant on unmount so a closed tab does not hold a store's camera open.
import { useCallback, useEffect, useRef, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, btnPrimary, cameraName, fmtDateTime, buildSha, type Camera, type VisionConfig, visionError,
} from '@/lib/vision'

export default function VisionLiveWall() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [config, setConfig] = useState<VisionConfig | null>(null)
  const [msg, setMsg] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/vision/cameras')
        setCameras(r.cameras || [])
        setConfig(r.config || null)
      } catch (e: any) { setMsg(visionError(e)) }
      finally { setLoading(false) }
    })()
  }, [])

  if (loading) return <div style={{ padding: 20, color: 'var(--text2)' }}>Loading…</div>

  if (config && !config.available) return <Notice
    title="Camera analytics is not installed yet"
    body="Migration 900 has not been run on this database. Once it is, an administrator can turn the module on in Vision → Settings." />

  if (config && !config.enabled) return <Notice
    title="Camera analytics is turned off"
    body="This module is off for your company by default. An administrator can enable it, connect the Google account that owns the store cameras, and assign each camera to a store."
    action={{ href: '/vision/settings', label: 'Open Vision Settings' }} />

  const byStore: Record<string, Camera[]> = {}
  for (const c of cameras.filter(c => c.enabled)) {
    (byStore[c.store_code || 'Unassigned'] ||= []).push(c)
  }

  return (
    <div style={{ padding: 20, maxWidth: 1400 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>
          📹 Live Cameras
          {buildSha() && (
            <span style={{ fontSize: 11, fontWeight: 400, color: 'var(--text3)', marginLeft: 8 }}>
              build {buildSha()}
            </span>
          )}
        </h1>
        <div style={{ display: 'flex', gap: 8 }}>
          <Link href="/vision/heatmap" style={{ ...btn, textDecoration: 'none' }}>🔥 Heat Map</Link>
          <Link href="/vision/behavior" style={{ ...btn, textDecoration: 'none' }}>🎧 Coaching</Link>
          <Link href="/vision/settings" style={{ ...btn, textDecoration: 'none' }}>⚙️ Settings</Link>
        </div>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--text3)', marginBottom: 14 }}>
        Every live view is recorded in the camera viewing log — who watched which camera, when, and for
        how long. Sessions end automatically after {config?.stream_max_minutes ?? 30} minutes.
      </div>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#dc2626', marginBottom: 14 }}>{msg}</div>}

      {Object.keys(byStore).length === 0 && (
        <div style={{ ...panel, color: 'var(--text2)' }}>
          No cameras yet. In <Link href="/vision/settings">Settings</Link>, connect the Google account
          that owns the store cameras and press <b>Sync cameras</b>.
        </div>
      )}

      {Object.entries(byStore).sort(([a], [b]) => a.localeCompare(b)).map(([store, cams]) => (
        <div key={store} style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8, color: 'var(--text2)' }}>
            {store === 'Unassigned' ? '⚠️ Not assigned to a store' : `Store ${store}`}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(340px,1fr))', gap: 12 }}>
            {cams.map(c => <CameraTile key={c.id} camera={c} />)}
          </div>
        </div>
      ))}
    </div>
  )
}

function Notice({ title, body, action }: { title: string; body: string; action?: { href: string; label: string } }) {
  return (
    <div style={{ padding: 20, maxWidth: 620 }}>
      <div style={{ ...panel }}>
        <div style={{ fontSize: 16, fontWeight: 700, marginBottom: 6 }}>{title}</div>
        <div style={{ fontSize: 13.5, color: 'var(--text2)', marginBottom: action ? 14 : 0 }}>{body}</div>
        {action && <Link href={action.href} style={{ ...btnPrimary, textDecoration: 'none' }}>{action.label}</Link>}
      </div>
    </div>
  )
}

type TileState = 'idle' | 'connecting' | 'live' | 'error'

function CameraTile({ camera }: { camera: Camera }) {
  const videoRef = useRef<HTMLVideoElement | null>(null)
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const sessionRef = useRef<string | null>(null)
  const timerRef = useRef<any>(null)
  const watchRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [state, setState] = useState<TileState>('idle')
  const [error, setError] = useState('')
  // HOW FAR IT GOT, and WHAT WENT WRONG — both kept OUTSIDE the state machine on purpose.
  //
  // Twice now a real diagnosis has been computed and then lost: the message only rendered while
  // `state === 'error'`, so anything that moved the tile to another state threw the answer away and
  // the operator saw a bare play button. A failure report must not depend on winning a race with a
  // state transition. These two are cleared when a NEW attempt starts, and at no other time.
  const [phase, setPhase] = useState('')
  const [note, setNote] = useState('')
  const [expires, setExpires] = useState<string | null>(null)

  const clearWatchdog = useCallback(() => {
    if (watchRef.current) { clearTimeout(watchRef.current); watchRef.current = null }
  }, [])

  // Takes the state to LAND IN, because releasing the connection and deciding what the viewer sees
  // are different jobs and merging them cost us a whole round of debugging: every failure path set
  // 'error', then called teardown(), which unconditionally set 'idle' — so the error state was gone
  // before React rendered it, the message was discarded, and the tile just showed the play button
  // again as though nothing had happened.
  const teardown = useCallback(async (next: TileState = 'idle') => {
    clearWatchdog()
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    const sid = sessionRef.current
    sessionRef.current = null
    try { pcRef.current?.close() } catch { /* already closed */ }
    pcRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    // Hand the grant back rather than letting it expire — a released stream is one fewer concurrent
    // session against the store's camera, and the audit row gets its real end time.
    if (sid) { try { await api(`/api/v1/vision/stream/${sid}/stop`, { method: 'POST' }) } catch { /* it expires anyway */ } }
    setState(next); setExpires(null)
  }, [clearWatchdog])

  // A grant can be issued, an answer returned, and the media still never arrive. Without a deadline
  // the tile spins indefinitely and the operator has nothing to report but "it doesn't connect".
  const startWatchdog = useCallback(() => {
    clearWatchdog()
    watchRef.current = setTimeout(() => {
      if (pcRef.current?.connectionState === 'connected') return
      setNote('Google issued the stream but no video arrived within 20 seconds. The camera may be '
        + 'offline or asleep, or this network may be blocking the UDP traffic WebRTC needs.')
      void teardown('error')
    }, 20000)
  }, [clearWatchdog, teardown])

  // Always release the grant when the tile goes away — navigating off the page must not leave a
  // camera streaming to nobody.
  useEffect(() => () => { void teardown() }, [teardown])

  const scheduleExtend = useCallback((afterSeconds: number) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(async () => {
      const sid = sessionRef.current
      if (!sid) return
      try {
        const r = await api(`/api/v1/vision/stream/${sid}/extend`, { method: 'POST' })
        setExpires(r.expires_at || null)
        scheduleExtend(Number(r.extend_after_seconds) || 200)
      } catch (e: any) {
        // The commonest cause is the company's maximum session length, which is a deliberate stop,
        // not a fault — say which happened instead of showing a bare error.
        setNote(visionError(e) || 'The live view ended.')
        void teardown('error')
      }
    }, Math.max(15, afterSeconds) * 1000)
  }, [teardown])

  async function start() {
    setError(''); setNote(''); setPhase('starting'); setState('connecting')
    try {
      if (camera.stream_protocol !== 'webrtc') {
        throw new Error('This camera streams over RTSP. RTSP cameras are read by the edge analyzer, '
          + 'not by the browser — live view here supports WebRTC cameras.')
      }
      const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] })
      pcRef.current = pc
      // Nest sends media and expects us to be receive-only; declaring the transceivers up front is
      // what makes the offer contain the m-lines Google's answer replies to.
      // GOOGLE REQUIRES A DATA CHANNEL IN THE OFFER. Device Access rejects — or answers without
      // establishing media on — an SDP that carries no m=application line, which is why the
      // handshake could succeed and no video ever arrive. Google's own WebRTC sample opens this
      // channel and never sends a byte down it; it exists purely to make the offer acceptable.
      // ORDER IS PART OF THE CONTRACT. Google: "Offer must contain each of audio, video and
      // application m lines in that order." M-lines appear in the SDP in the order they are
      // created, so these three calls ARE the order — audio, then video, then the data channel
      // last. Creating the channel first (as this did) produced application/video/audio and Google
      // refused the whole offer.
      pc.addTransceiver('audio', { direction: 'recvonly' })
      pc.addTransceiver('video', { direction: 'recvonly' })
      pc.createDataChannel('dataSendChannel')
      pc.ontrack = ev => { if (videoRef.current) videoRef.current.srcObject = ev.streams[0] }
      // "Live" must mean MEDIA IS ARRIVING, not "the handshake completed". Those are different
      // events and the gap between them is where every real failure lives: the SDP exchange
      // succeeds, ICE then fails to find a path, and nothing notices. The tile sat on "connecting"
      // forever with no error, which is the least actionable thing it could possibly do.
      pc.onconnectionstatechange = () => {
        if (!pcRef.current) return                    // torn down; this is a late event
        if (pc.connectionState === 'connected') { clearWatchdog(); setPhase(''); setState('live') }
        if (pc.connectionState === 'failed') {
          clearWatchdog()
          setNote('The connection to the camera could not be established. Google answered, but no '
            + 'media path could be opened — usually a network that blocks the UDP traffic WebRTC '
            + 'needs. Try another network to confirm.')
          void teardown('error')
        }
      }

      setPhase('building the offer')
      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      setPhase('gathering network candidates')
      // Wait for ICE gathering: Google's SDM expects a complete offer, not a trickled one.
      await new Promise<void>(resolve => {
        if (pc.iceGatheringState === 'complete') return resolve()
        const check = () => { if (pc.iceGatheringState === 'complete') { pc.removeEventListener('icegatheringstatechange', check); resolve() } }
        pc.addEventListener('icegatheringstatechange', check)
        setTimeout(resolve, 3000)   // never hang the tile on a stalled gather
      })

      setPhase('asking Google for the stream')
      const res = await api(`/api/v1/vision/cameras/${camera.id}/stream`, {
        method: 'POST',
        body: JSON.stringify({ offer_sdp: pc.localDescription?.sdp, purpose: 'live_view' }),
      })
      if (!res.answer_sdp) throw new Error('Google did not return a stream answer for this camera.')
      setPhase("applying Google's answer")
      await pc.setRemoteDescription({ type: 'answer', sdp: res.answer_sdp })
      sessionRef.current = res.session_id
      setExpires(res.expires_at || null)
      // NOT setState('live') here — the handshake is done, the media is not. onconnectionstatechange
      // promotes the tile once a path actually opens; the watchdog gives up if it never does.
      setPhase('waiting for video to arrive')
      scheduleExtend(Number(res.extend_after_seconds) || 200)
      startWatchdog()
    } catch (e: any) {
      setNote(visionError(e) || String(e))
      void teardown('error')
    }
  }

  const dot = camera.status === 'online' ? '#16a34a' : camera.status === 'offline' ? '#dc2626' : '#9ca3af'

  return (
    <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
      <div style={{ position: 'relative', background: '#000', aspectRatio: '16 / 9' }}>
        <video ref={videoRef} autoPlay playsInline muted
          style={{ width: '100%', height: '100%', objectFit: 'contain', display: state === 'live' ? 'block' : 'none' }} />
        {state !== 'live' && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 10, color: '#9ca3af', padding: 16, textAlign: 'center' }}>
            {state === 'connecting'
              ? <div style={{ fontSize: 13 }}>Connecting…</div>
              : <button style={btnPrimary} onClick={start}>▶ Watch live</button>}
            {(note || error) && (
              <div style={{ fontSize: 12, color: '#f87171', maxWidth: 320 }}>{note || error}</div>
            )}
            {/* Where it stopped. Shown even when nothing threw — a run that ends with no error at
                all still tells us which step it died on, which is the thing we could never see. */}
            {phase && <div style={{ fontSize: 11, color: '#9ca3af' }}>stopped at: {phase}</div>}
          </div>
        )}
        {state === 'live' && (
          <div style={{ position: 'absolute', top: 8, left: 8, display: 'flex', gap: 6, alignItems: 'center',
            background: 'rgba(0,0,0,.6)', padding: '3px 8px', borderRadius: 6 }}>
            <span style={{ width: 7, height: 7, borderRadius: 7, background: '#dc2626' }} />
            <span style={{ fontSize: 11, color: '#fff', fontWeight: 600, letterSpacing: '.5px' }}>LIVE</span>
          </div>
        )}
      </div>
      <div style={{ padding: '10px 12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
            <span style={{ width: 8, height: 8, borderRadius: 8, background: dot, flexShrink: 0 }} />
            <span style={{ fontWeight: 600, fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {cameraName(camera)}
            </span>
          </div>
          {state === 'live' && <button style={btn} onClick={() => void teardown()}>Stop</button>}
        </div>
        <div style={{ fontSize: 11.5, color: 'var(--text3)', marginTop: 5, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <span>{camera.stream_protocol.toUpperCase()}</span>
          {camera.is_entrance && <span title="Carries the in/out counting line">🚪 entrance</span>}
          {camera.analytics_enabled && <span title="Contributes to the heat map">🔥 analytics</span>}
          {camera.audio_enabled && <span title="Voice transcripts are enabled on this camera">🎙️ audio</span>}
          {state === 'live' && expires && <span>renews {fmtDateTime(expires)}</span>}
        </div>
      </div>
    </div>
  )
}
