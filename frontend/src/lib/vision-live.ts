/**
 * One still frame from a camera, for drawing on.
 *
 * WHY A STILL AND NOT THE LIVE TILE. Drawing a counting line on moving video is worse than it
 * sounds: the doorway drifts under the cursor, a person walks through the line being drawn, and the
 * operator is aiming at a target that moves. It also holds a Google stream grant open for however
 * long somebody takes to get the line right, which is minutes, and every one of those is an
 * executeCommand the analyzer's own cameras are competing for.
 *
 * So this connects, waits for the first frame, paints it into a canvas, and hangs up. What the
 * operator draws on is a photograph.
 *
 * NOT PURE — it talks to the backend and opens a peer connection. The decisions that act on what it
 * returns live in vision-zones.ts, which is pure and proven; this file only fetches the picture.
 *
 * The handshake below is the same one vision/page.tsx performs inline, including the two quirks that
 * cost real debugging time (the data channel Google requires, and the m-line ORDER it enforces).
 * That page predates this helper and still has its own copy; it should be moved onto this one, which
 * is a change to a working live view and therefore not something to slip into a drawing feature.
 */
import { api } from '@/lib/client'

export interface Still { dataUrl: string; width: number; height: number }

const STILL_TIMEOUT_MS = 25_000

export async function captureStill(cameraId: string, protocol: string | null | undefined,
                                   onPhase: (p: string) => void = () => {}): Promise<Still> {
  if (protocol !== 'webrtc') {
    throw new Error('This camera streams over RTSP. RTSP cameras are read by the edge analyzer, not '
      + 'by the browser, so a still cannot be pulled here — draw the line on a WebRTC camera, or '
      + 'use a photograph of the doorway taken at the camera position.')
  }
  const pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] })
  const video = document.createElement('video')
  video.autoplay = true; video.muted = true; video.playsInline = true

  const done = (() => {
    let closed = false
    return () => {
      if (closed) return
      closed = true
      try { pc.getSenders().forEach(s => s.track && s.track.stop()) } catch { /* already gone */ }
      try { pc.close() } catch { /* already gone */ }
      try { video.srcObject = null } catch { /* already gone */ }
    }
  })()

  try {
    // ORDER IS PART OF THE CONTRACT: Google requires audio, video and application m-lines in that
    // order, and m-lines appear in the SDP in the order the transceivers are created. The data
    // channel carries no bytes; the offer is simply refused without it.
    pc.addTransceiver('audio', { direction: 'recvonly' })
    pc.addTransceiver('video', { direction: 'recvonly' })
    pc.createDataChannel('dataSendChannel')

    const firstFrame = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(
        'The camera connected but no picture arrived within 25 seconds. This is usually a network '
        + 'that blocks the UDP traffic WebRTC needs — try another network to confirm.')),
        STILL_TIMEOUT_MS)
      pc.ontrack = ev => {
        video.srcObject = ev.streams[0]
        // 'playing' rather than 'loadeddata': a track can attach and produce no picture at all,
        // which is the failure this whole flow keeps hitting. Waiting for a painted frame means a
        // success here is a success.
        video.onplaying = () => {
          // One more rAF so the first frame is actually composited before it is copied out.
          requestAnimationFrame(() => { clearTimeout(timer); resolve() })
        }
        void video.play().catch(() => { /* autoplay policy; onplaying may still fire */ })
      }
    })

    onPhase('building the offer')
    await pc.setLocalDescription(await pc.createOffer())
    onPhase('gathering network candidates')
    await new Promise<void>(resolve => {
      if (pc.iceGatheringState === 'complete') return resolve()
      const check = () => {
        if (pc.iceGatheringState === 'complete') {
          pc.removeEventListener('icegatheringstatechange', check); resolve()
        }
      }
      pc.addEventListener('icegatheringstatechange', check)
      setTimeout(resolve, 3000)          // never hang on a stalled gather
    })

    onPhase('asking Google for the picture')
    const res = await api(`/api/v1/vision/cameras/${cameraId}/stream`, {
      method: 'POST',
      body: JSON.stringify({ offer_sdp: pc.localDescription?.sdp, purpose: 'live_view' }),
    })
    if (!res.answer_sdp) throw new Error('Google did not return a stream answer for this camera.')
    onPhase("applying Google's answer")
    await pc.setRemoteDescription({ type: 'answer', sdp: res.answer_sdp })

    onPhase('waiting for the picture')
    await firstFrame

    const w = video.videoWidth, h = video.videoHeight
    if (!w || !h) throw new Error('The camera sent a stream with no picture in it.')
    const canvas = document.createElement('canvas')
    canvas.width = w; canvas.height = h
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('This browser would not give us a canvas to copy the frame into.')
    ctx.drawImage(video, 0, 0, w, h)
    return { dataUrl: canvas.toDataURL('image/jpeg', 0.9), width: w, height: h }
  } finally {
    // Always hang up, including on the timeout path. A grant left open is one the analyzer's own
    // cameras have to queue behind.
    done()
  }
}
