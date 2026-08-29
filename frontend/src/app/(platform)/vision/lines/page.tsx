'use client'
// Vision — DRAW THE COUNTING LINE.
//
// WHY THIS PAGE EXISTS. Every camera in this estate is flagged as an entrance and not one has a line
// drawn across it, so the analyzer builds no gates and every doorway counts zero. Nothing anywhere
// says so: a door with no line looks exactly like a door nobody used. The zones API has existed
// since migration 900 and nothing in the product ever called it, which is the whole bug.
//
// THE DESIGN PROBLEM, and it is not the drawing. Storage needs `inward`: which side of the directed
// line A->B is inside the store, 'left' or 'right'. That is a cross product in image coordinates
// where y points down, so "left" is usually the visual right, and drawing the same doorway in the
// other direction inverts it. No operator should ever be shown that word, and none is.
//
// Instead they get the analyzer's own answer: after drawing, they drag a marker across the line the
// way a customer would walk, and the page says IN or OUT using crossingDirection() — a port of
// geometry.crossing_direction proven against the real Python over 4000 random cases in
// prove_vision_zones.mjs. If it says OUT when they walked in, they press Flip. Nobody has to
// understand the setting, and the arrow on screen is computed from the same rule that counts, so it
// cannot point one way while the counting goes the other.
//
// Every decision here is in lib/vision-zones.ts and proven offline. This file is the surface: a
// still frame, pointer events, and what to call the buttons.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/client'
import { panel, btn, btnPrimary, cameraName, visionError, type Camera } from '@/lib/vision'
import { captureStill, type Still } from '@/lib/vision-live'
import {
  normPoint, lineGeometry, lineBlocker, crossingDirection, inwardNormal, midpoint,
  inwardSentence, flip, withCountingLine, currentLine, zoneStatus,
  type Pt, type LineGeometry, type Inward, type Zone,
} from '@/lib/vision-zones'

type Drag = { a: Pt; b: Pt } | null

export default function VisionLinesPage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [zonesByCam, setZonesByCam] = useState<Record<string, Zone[]>>({})
  const [selected, setSelected] = useState<string>('')
  const [still, setStill] = useState<Still | null>(null)
  const [phase, setPhase] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState('')

  const [geom, setGeom] = useState<LineGeometry | null>(null)
  const [inward, setInward] = useState<Inward>('left')
  const [drag, setDrag] = useState<Drag>(null)

  // The walk test. `marker` is where the dot is; `verdict` is what the analyzer would have said
  // about the last time it crossed. Verdict is kept until the next crossing rather than cleared on
  // pointer-up, because the answer is the thing they are reading.
  const [marker, setMarker] = useState<Pt | null>(null)
  const [verdict, setVerdict] = useState<'in' | 'out' | null>(null)
  const [mode, setMode] = useState<'draw' | 'walk'>('draw')
  // The zone fetch is a round trip per camera, and selecting one READS that map. Clicking before it
  // lands would report "no line" for a camera that has one, and then quietly overwrite it on save.
  // The list is not offered until the answer is in.
  const [ready, setReady] = useState(false)

  const surfaceRef = useRef<HTMLDivElement | null>(null)
  const cam = cameras.find(c => c.id === selected) || null

  useEffect(() => {
    (async () => {
      try {
        const r = await api('/api/v1/vision/cameras')
        const list: Camera[] = r.cameras || []
        setCameras(list)
        // Zones for every camera up front: the list badges say which doorways are counting nobody,
        // and that is the reason somebody opened this page.
        const pairs = await Promise.all(list.map(async c => {
          try {
            const z = await api(`/api/v1/vision/cameras/${c.id}/zones`)
            return [c.id, (z.zones || []) as Zone[]] as const
          } catch { return [c.id, [] as Zone[]] as const }
        }))
        setZonesByCam(Object.fromEntries(pairs))
        setReady(true)
      } catch (e) { setMsg(visionError(e)) }
    })()
  }, [])

  // Selecting a camera loads whatever line it already has, so an existing drawing is edited rather
  // than silently replaced by whatever the operator does next.
  //
  // Done in the click handler and NOT in an effect keyed on `selected`: an effect would also refire
  // whenever `zonesByCam` changed — which is exactly what saving does — and would throw away the
  // operator's unsaved work a moment after they pressed Save.
  const selectCamera = useCallback((id: string) => {
    setSelected(id)
    setStill(null); setMsg(''); setSaved(''); setMarker(null); setVerdict(null); setDrag(null)
    setMode('draw')
    const existing = currentLine(zonesByCam[id])
    setGeom(existing?.geometry || null)
    setInward(existing?.inward || 'left')
  }, [zonesByCam])

  const grab = useCallback(async () => {
    if (!cam) return
    setBusy(true); setMsg(''); setPhase('')
    try {
      setStill(await captureStill(cam.id, cam.stream_protocol, setPhase))
      setPhase('')
    } catch (e) {
      setMsg(visionError(e) || String(e))
    } finally { setBusy(false) }
  }, [cam])

  const aspect = still && still.height ? still.width / still.height : 16 / 9

  const toImage = useCallback((e: { clientX: number; clientY: number }): Pt | null => {
    const el = surfaceRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    return normPoint(e.clientX, e.clientY, r, aspect)
  }, [aspect])

  // DRAW or WALK, on the same surface. The test has to happen ON the picture — a handle somewhere
  // else would be dragged over a doorway the operator cannot see, and the whole value of the test is
  // that they aim it at their own front door.
  const onDown = (e: React.PointerEvent) => {
    if (!still) return
    const p = toImage(e)
    if (!p) return                                  // a click on the letterbox bar is not a point
    ;(e.target as Element).setPointerCapture?.(e.pointerId)
    if (mode === 'walk') { setMarker(p); setVerdict(null); return }
    setDrag({ a: p, b: p }); setSaved('')
  }
  const onMove = (e: React.PointerEvent) => {
    if (!e.buttons) return
    const p = toImage(e)
    if (!p) return
    if (mode === 'walk') {
      // Every step is put through the SAME function the analyzer uses, so the badge below is not an
      // illustration of the rule — it is the rule.
      if (marker) {
        const d = crossingDirection(geom, inward, marker, p)
        if (d) setVerdict(d)
      }
      setMarker(p)
      return
    }
    if (drag) setDrag({ a: drag.a, b: p })
  }
  const onUp = () => {
    if (mode === 'walk') return                     // keep the marker and the verdict on screen
    if (!drag) return
    setGeom(lineGeometry(drag.a, drag.b))
    setDrag(null); setMarker(null); setVerdict(null)
  }

  const live: LineGeometry | null = drag ? lineGeometry(drag.a, drag.b) : geom
  const blocker = lineBlocker(live)
  const sentence = inwardSentence(live, inward)
  const status = cam ? zoneStatus(!!cam.is_entrance, zonesByCam[cam.id]) : ''

  const save = async () => {
    if (!cam || !geom || blocker) return
    setBusy(true); setMsg(''); setSaved('')
    try {
      const next = withCountingLine(zonesByCam[cam.id], geom, inward)
      const r = await api(`/api/v1/vision/cameras/${cam.id}/zones`, {
        method: 'PUT', body: JSON.stringify({ zones: next }),
      })
      setZonesByCam(z => ({ ...z, [cam.id]: (r.zones || next) as Zone[] }))
      setSaved('Saved. The analyzer picks this up at its next config refresh — within a minute.')
    } catch (e) { setMsg(visionError(e) || String(e)) }
    finally { setBusy(false) }
  }

  // Percentages, so the overlay scales with the picture and never needs pixel maths of its own.
  const pct = (v: number) => `${v * 100}%`
  const arrow = useMemo(() => {
    if (!live) return null
    const m = midpoint(live), n = inwardNormal(live, inward)
    return { from: m, to: { x: m.x + n.x * 0.14, y: m.y + n.y * 0.14 } }
  }, [live, inward])

  return (
    <div style={{ padding: 24, maxWidth: 1240, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Counting lines</h1>
        <Link href="/vision" style={{ fontSize: 13, color: 'var(--text2)' }}>← Cameras</Link>
      </div>
      <p style={{ fontSize: 13.5, color: 'var(--text2)', margin: '8px 0 18px', maxWidth: 760 }}>
        A camera only counts people where a line is drawn across the doorway. Take a picture, drag a
        line right across the opening, then drag the test marker through it the way a customer walks
        in — if it says OUT, press flip.
      </p>

      {msg && <div style={{ ...panel, borderColor: '#dc2626', color: '#f87171', marginBottom: 16 }}>{msg}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: 18,
        alignItems: 'start' }}>
        {/* ── camera list ─────────────────────────────────────────────────────────────────────── */}
        <div style={{ ...panel, padding: 0, overflow: 'hidden' }}>
          {!ready && (
            <div style={{ padding: 14, fontSize: 13, color: 'var(--text2)' }}>
              Checking which cameras have a line…
            </div>
          )}
          {ready && cameras.length === 0 && (
            <div style={{ padding: 14, fontSize: 13, color: 'var(--text2)' }}>No cameras yet.</div>
          )}
          {ready && cameras.map(c => {
            const has = !!currentLine(zonesByCam[c.id])
            const silent = !!c.is_entrance && !has
            return (
              <button key={c.id} onClick={() => selectCamera(c.id)}
                style={{ display: 'block', width: '100%', textAlign: 'left', padding: '10px 12px',
                  border: 'none', borderBottom: '1px solid var(--border)', cursor: 'pointer',
                  background: c.id === selected ? 'var(--surface)' : 'transparent',
                  color: 'var(--text)' }}>
                <div style={{ fontSize: 13, fontWeight: c.id === selected ? 700 : 500 }}>
                  {cameraName(c)}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 2 }}>
                  {c.store_code || '—'}
                </div>
                {/* The badge IS the point of the list: it names the doorways counting nobody. */}
                <div style={{ fontSize: 11, marginTop: 4, fontWeight: 600,
                  color: silent ? '#f59e0b' : has ? '#16a34a' : 'var(--text2)' }}>
                  {silent ? '⚠ counting nobody' : has ? '✓ line drawn' : 'not an entrance'}
                </div>
              </button>
            )
          })}
        </div>

        {/* ── the drawing surface ─────────────────────────────────────────────────────────────── */}
        <div>
          {!cam && (
            <div style={{ ...panel, fontSize: 13.5, color: 'var(--text2)' }}>
              Pick a camera to draw its counting line.
            </div>
          )}

          {cam && (
            <>
              {status && (
                <div style={{ ...panel, marginBottom: 12, fontSize: 13,
                  borderColor: cam.is_entrance ? '#f59e0b' : 'var(--border)',
                  color: cam.is_entrance ? '#f59e0b' : 'var(--text2)' }}>
                  {status}
                </div>
              )}

              <div ref={surfaceRef}
                onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}
                // THE BOX TAKES THE PICTURE'S OWN SHAPE once there is a picture, and that is a
                // correctness fix rather than a cosmetic one. Clicks are mapped to IMAGE
                // coordinates by normPoint, which corrects for letterboxing; but the SVG overlay
                // and the markers below are positioned in percentages of THIS BOX. Those two
                // spaces are only the same when the box has no letterbox to correct for. Leave it
                // pinned at 16/9 and a 4:3 camera draws its line visibly away from the cursor —
                // and stores it at the coordinate under the cursor, so the picture and the saved
                // line disagree about where the doorway is.
                style={{ position: 'relative', background: '#000',
                  aspectRatio: still ? `${still.width} / ${still.height}` : '16 / 9',
                  borderRadius: 8, overflow: 'hidden', border: '1px solid var(--border)',
                  cursor: still ? 'crosshair' : 'default', touchAction: 'none' }}>
                {still
                  // A data: URL captured in this browser a moment ago. next/image optimises SERVED
                  // assets through a proxy; there is no URL here for it to fetch.
                  // eslint-disable-next-line @next/next/no-img-element
                  ? <img src={still.dataUrl} alt="" draggable={false}
                      style={{ width: '100%', height: '100%', objectFit: 'contain',
                        userSelect: 'none', pointerEvents: 'none' }} />
                  : (
                    <div style={{ position: 'absolute', inset: 0, display: 'flex',
                      flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
                      gap: 10, color: '#9ca3af', padding: 16, textAlign: 'center' }}>
                      <button style={btnPrimary} onClick={grab} disabled={busy}>
                        {busy ? 'Taking a picture…' : '📷 Take a picture to draw on'}
                      </button>
                      {phase && <div style={{ fontSize: 11 }}>{phase}</div>}
                      <div style={{ fontSize: 11, maxWidth: 340 }}>
                        The stream is opened just long enough to grab one frame, then closed.
                      </div>
                    </div>
                  )}

                {/* The line, the inward arrow and the marker, all positioned in percentages of the
                    picture — the same normalized space the analyzer stores and reads. */}
                {live && still && (
                  <svg viewBox="0 0 100 100" preserveAspectRatio="none"
                    style={{ position: 'absolute', inset: 0, width: '100%', height: '100%',
                      pointerEvents: 'none' }}>
                    <line x1={live.x1 * 100} y1={live.y1 * 100} x2={live.x2 * 100} y2={live.y2 * 100}
                      stroke="#22d3ee" strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
                    {arrow && (
                      <line x1={arrow.from.x * 100} y1={arrow.from.y * 100}
                        x2={arrow.to.x * 100} y2={arrow.to.y * 100}
                        stroke="#16a34a" strokeWidth={0.8} vectorEffect="non-scaling-stroke" />
                    )}
                  </svg>
                )}
                {arrow && still && (
                  <div style={{ position: 'absolute', left: pct(arrow.to.x), top: pct(arrow.to.y),
                    transform: 'translate(-50%, -50%)', background: '#16a34a', color: '#fff',
                    fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 5,
                    pointerEvents: 'none', whiteSpace: 'nowrap' }}>IN</div>
                )}
                {marker && still && (
                  <div style={{ position: 'absolute', left: pct(marker.x), top: pct(marker.y),
                    transform: 'translate(-50%, -50%)', width: 16, height: 16, borderRadius: 16,
                    background: '#f59e0b', border: '2px solid #fff', pointerEvents: 'none' }} />
                )}
              </div>

              {/* ── controls ────────────────────────────────────────────────────────────────── */}
              {still && (
                <div style={{ ...panel, marginTop: 12 }}>
                  <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
                    <button style={btn} onClick={grab} disabled={busy}>↻ New picture</button>
                    <button style={btn} disabled={!live} onClick={() => setInward(flip(inward))}>
                      ⇄ Flip which side is inside
                    </button>
                    <button style={btn} disabled={!live}
                      onClick={() => { setGeom(null); setMarker(null); setVerdict(null) }}>
                      Clear line
                    </button>
                    <div style={{ flex: 1 }} />
                    <button style={btnPrimary} onClick={save} disabled={busy || !geom || !!blocker}>
                      {busy ? 'Saving…' : 'Save counting line'}
                    </button>
                  </div>

                  {/* One sentence for the state of the line, and it is either the reason the save
                      button is disabled or what the arrow means. Never both, never neither. */}
                  <div style={{ fontSize: 13, marginTop: 12,
                    color: blocker ? '#f59e0b' : 'var(--text2)' }}>
                    {blocker || sentence}
                  </div>

                  {!blocker && live && (
                    <div style={{ marginTop: 14, paddingTop: 12,
                      borderTop: '1px solid var(--border)' }}>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>
                        Check it the way a customer walks
                      </div>
                      <div style={{ fontSize: 12.5, color: 'var(--text2)', marginBottom: 10 }}>
                        Switch to walk mode and drag across the line on the picture above, from the
                        street toward the shop floor. This runs the same rule the analyzer runs — if
                        it says OUT, the sides are the wrong way round and Flip fixes it.
                      </div>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center',
                        flexWrap: 'wrap' }}>
                        <button
                          style={mode === 'draw' ? btnPrimary : btn}
                          onClick={() => { setMode('draw'); setMarker(null); setVerdict(null) }}>
                          ✏️ Draw the line
                        </button>
                        <button
                          style={mode === 'walk' ? btnPrimary : btn}
                          onClick={() => { setMode('walk'); setMarker(null); setVerdict(null) }}>
                          ✋ Walk a customer through
                        </button>
                        {verdict && (
                          <span style={{ marginLeft: 6, fontSize: 15, fontWeight: 700,
                            color: verdict === 'in' ? '#16a34a' : '#f59e0b' }}>
                            → counted {verdict.toUpperCase()}
                          </span>
                        )}
                        {mode === 'walk' && !verdict && (
                          <span style={{ marginLeft: 6, fontSize: 12.5, color: 'var(--text2)' }}>
                            drag across the line on the picture
                          </span>
                        )}
                      </div>
                    </div>
                  )}

                  {saved && (
                    <div style={{ fontSize: 13, marginTop: 12, color: '#16a34a' }}>{saved}</div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
