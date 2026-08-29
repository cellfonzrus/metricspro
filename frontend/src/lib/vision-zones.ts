/**
 * Drawing a counting line — the decisions, separated from the canvas that renders them.
 *
 * WHY THIS FILE EXISTS. Every camera in the estate is flagged as an entrance and not one of them has
 * a line drawn, so `gates_for()` returns an empty list and every entrance counts zero. Nothing says
 * so — an entrance with no line looks exactly like a door nobody walked through. This module is the
 * missing half: the rules for turning a drag on a video element into a line the analyzer will honour.
 *
 * THE THREE THINGS THAT GO WRONG HERE, none of which a canvas can be trusted to get right by eye:
 *
 *   1. THE LETTERBOX. The video is rendered with `object-fit: contain` inside a box that is rarely
 *      its exact aspect ratio, so there are bars, and the picture does not start at the element's
 *      top-left. Dividing offsetX by the element's width puts the line somewhere the operator did
 *      not click — a little wrong at 16:9 in a 16:9 box, and badly wrong the moment a phone rotates.
 *      The error is invisible: the line still draws under the cursor, because the same wrong maths
 *      renders it. It only shows up as a doorway that counts nothing.
 *
 *   2. `inward` IS NOT A DIRECTION ANYBODY CAN PICTURE. It names which side of the DIRECTED line
 *      A->B is inside the store, 'left' or 'right' — and "left" is measured by a cross product in
 *      image coordinates, where y points DOWN. So drawing the same doorway left-to-right instead of
 *      right-to-left inverts it, and 'left' is frequently the visual right. Asking an operator to
 *      choose is asking them to guess. `crossingDirection` here is a faithful mirror of
 *      geometry.crossing_direction, so the page can WALK A TEST MARKER across the line and show the
 *      answer the analyzer would give. Nobody has to understand the word.
 *
 *   3. SAVING REPLACES THE WHOLE SET. PUT /cameras/{id}/zones is a whole-set replace, so a page that
 *      sends only the line it just drew silently deletes the exclude polygons somebody spent twenty
 *      minutes placing to keep the pavement out of the count. `withCountingLine` keeps them.
 *
 * PURE — no React, no DOM types beyond a plain rectangle, no fetch — so prove_vision_zones.mjs can
 * check the arithmetic offline, including against the Python it has to agree with.
 */

export interface Pt { x: number; y: number }
export interface LineGeometry { x1: number; y1: number; x2: number; y2: number }
export type Inward = 'left' | 'right'

export interface Zone {
  id?: string
  kind: 'line' | 'polygon' | 'exclude'
  name?: string
  zone_key?: string
  /** Shape depends on `kind` — {x1,y1,x2,y2} for a line, {points:[…]} for a polygon — and it
   *  arrives from a JSONB column, so it is read defensively rather than trusted to a type. */
  geometry: unknown
  inward?: string
  is_active?: boolean
  sort_order?: number
}

/** One finite number out of an untrusted JSON object, or NaN. Keeps the narrowing in one place. */
function num(o: unknown, key: string): number {
  if (!o || typeof o !== 'object') return NaN
  const v = (o as Record<string, unknown>)[key]
  const n = typeof v === 'number' ? v : typeof v === 'string' ? Number(v) : NaN
  return Number.isFinite(n) ? n : NaN
}

/** Matches geometry.py's EPS so the two agree on what "on the line" means. */
export const EPS = 1e-9

/**
 * Shortest line worth saving, as a fraction of the frame.
 *
 * The backend rejects only a ZERO-length line, which lets a stray click-and-twitch through as a
 * legal counting line a few pixels long that nobody will ever cross. Refused here, where there is a
 * person to tell, rather than accepted and puzzled over later.
 */
export const MIN_LINE_LENGTH = 0.06

export function clamp01(v: number): number {
  if (!Number.isFinite(v)) return 0
  return v < 0 ? 0 : v > 1 ? 1 : v
}

export interface Rect { left: number; top: number; width: number; height: number }

/**
 * A click on a letterboxed video -> normalized image coordinates, or null if it landed on a bar.
 *
 * `aspect` is the video's intrinsic width/height (videoWidth/videoHeight), NOT the element's. With
 * object-fit: contain the picture is scaled to fit inside the element and centred, so one axis has
 * a gap at both ends. Returning null for the gap matters: a click there is not a point in the image,
 * and clamping it to the edge would let someone anchor a line to a coordinate they never chose.
 */
export function normPoint(clientX: number, clientY: number, rect: Rect,
                          aspect: number): Pt | null {
  if (!rect || !(rect.width > 0) || !(rect.height > 0)) return null
  // A video that has not loaded yet reports 0x0, so its aspect is unusable. Fall back to the
  // element's own box: it is what the viewer is looking at, and it is right whenever there are no
  // bars to correct for.
  const a = Number.isFinite(aspect) && aspect > 0 ? aspect : rect.width / rect.height
  const boxAspect = rect.width / rect.height
  let w = rect.width, h = rect.height, offX = 0, offY = 0
  if (boxAspect > a) {                 // bars left and right
    w = rect.height * a
    offX = (rect.width - w) / 2
  } else {                             // bars top and bottom
    h = rect.width / a
    offY = (rect.height - h) / 2
  }
  const x = (clientX - rect.left - offX) / w
  const y = (clientY - rect.top - offY) / h
  // BOUNDARY TOLERANCE. The edge of the picture is part of the picture, but the arithmetic that
  // finds it does not land on exactly 0: clicking the first pixel column of a 16:9 video in a
  // 1000x400 box computes -6.25e-14, and a bare `x < 0` refuses it. So a hair outside is clamped
  // in, and only a click genuinely on the bar is refused.
  const TOL = 1e-6
  if (x < -TOL || x > 1 + TOL || y < -TOL || y > 1 + TOL) return null
  return { x: clamp01(x), y: clamp01(y) }
}

export function lineGeometry(a: Pt, b: Pt): LineGeometry {
  return { x1: clamp01(a.x), y1: clamp01(a.y), x2: clamp01(b.x), y2: clamp01(b.y) }
}

export function lineLength(g: LineGeometry): number {
  return Math.hypot(g.x2 - g.x1, g.y2 - g.y1)
}

/**
 * Why this line cannot be saved yet, in the operator's terms, or '' when it can.
 *
 * A blocker rather than a thrown error because the button is disabled on it and the same sentence is
 * shown beside the button — one source for both, so a disabled control always says why.
 */
export function lineBlocker(g: LineGeometry | null): string {
  if (!g) return 'Drag across the doorway to draw the counting line.'
  const len = lineLength(g)
  if (len < EPS) return 'Drag across the doorway to draw the counting line.'
  if (len < MIN_LINE_LENGTH) {
    return 'That line is too short to be crossed reliably — drag it right across the doorway, '
      + 'from one side of the opening to the other.'
  }
  return ''
}

function cross(ax: number, ay: number, bx: number, by: number, px: number, py: number): number {
  return (bx - ax) * (py - ay) - (by - ay) * (px - ax)
}

/** +1 left of the directed line, -1 right, 0 on it. Mirrors geometry.side_of. */
export function sideOf(g: LineGeometry, p: Pt): number {
  const c = cross(g.x1, g.y1, g.x2, g.y2, p.x, p.y)
  if (Math.abs(c) <= EPS) return 0
  return c > 0 ? 1 : -1
}

function onSegment(a: Pt, b: Pt, p: Pt): boolean {
  return (Math.min(a.x, b.x) - EPS <= p.x && p.x <= Math.max(a.x, b.x) + EPS
    && Math.min(a.y, b.y) - EPS <= p.y && p.y <= Math.max(a.y, b.y) + EPS)
}

/** Mirrors geometry._segments_intersect, including its collinear-touch cases. */
export function segmentsIntersect(p1: Pt, p2: Pt, p3: Pt, p4: Pt): boolean {
  const d1 = cross(p3.x, p3.y, p4.x, p4.y, p1.x, p1.y)
  const d2 = cross(p3.x, p3.y, p4.x, p4.y, p2.x, p2.y)
  const d3 = cross(p1.x, p1.y, p2.x, p2.y, p3.x, p3.y)
  const d4 = cross(p1.x, p1.y, p2.x, p2.y, p4.x, p4.y)
  if (((d1 > EPS && d2 < -EPS) || (d1 < -EPS && d2 > EPS))
    && ((d3 > EPS && d4 < -EPS) || (d3 < -EPS && d4 > EPS))) return true
  const touches: [number, Pt, Pt, Pt][] = [
    [d1, p3, p4, p1], [d2, p3, p4, p2], [d3, p1, p2, p3], [d4, p1, p2, p4],
  ]
  for (const [d, a, b, p] of touches) {
    if (Math.abs(d) <= EPS && onSegment(a, b, p)) return true
  }
  return false
}

/**
 * 'in' | 'out' | null for one step across the line — the SAME answer the analyzer will give.
 *
 * A faithful port of geometry.crossing_direction, and it has to stay one: this is what the page's
 * test marker reports, and a test that disagreed with the analyzer would be worse than no test,
 * because it would send someone away confident about a line that counts backwards.
 */
export function crossingDirection(g: LineGeometry | null, inward: Inward,
                                  prev: Pt, cur: Pt): 'in' | 'out' | null {
  if (!g || lineLength(g) < EPS) return null
  const sPrev = sideOf(g, prev), sCur = sideOf(g, cur)
  if (sPrev === 0 || sCur === 0 || sPrev === sCur) return null
  if (!segmentsIntersect(prev, cur, { x: g.x1, y: g.y1 }, { x: g.x2, y: g.y2 })) return null
  const inwardSide = inward === 'left' ? 1 : -1
  return sCur === inwardSide ? 'in' : 'out'
}

/**
 * A unit vector pointing INTO the store, in image coordinates (y down).
 *
 * Derived from the same cross product the counting uses rather than from a mental model of "left":
 * for a->b the left-hand normal is (-dy, dx), because cross(a, b, a + (-dy, dx)) = dx^2 + dy^2 > 0.
 * The page draws its arrow from this, so the arrow cannot point the opposite way to the rule.
 */
export function inwardNormal(g: LineGeometry | null, inward: Inward): Pt {
  if (!g) return { x: 0, y: 0 }
  const dx = g.x2 - g.x1, dy = g.y2 - g.y1
  const len = Math.hypot(dx, dy)
  if (len < EPS) return { x: 0, y: 0 }
  const s = inward === 'left' ? 1 : -1
  return { x: (-dy / len) * s, y: (dx / len) * s }
}

/** The midpoint, where the page anchors the arrow and the IN/OUT badge. */
export function midpoint(g: LineGeometry): Pt {
  return { x: (g.x1 + g.x2) / 2, y: (g.y1 + g.y2) / 2 }
}

/**
 * Which way a vector points, named the way somebody looking at the picture would name it.
 *
 * y is DOWN in image coordinates, so a positive y component is toward the BOTTOM of the frame. Said
 * in terms of the picture rather than compass points or "left of the line", because the operator is
 * looking at a photograph of their own doorway, not at a coordinate system.
 */
export function compassLabel(v: Pt): string {
  if (Math.hypot(v.x, v.y) < EPS) return 'nowhere'
  const vert = v.y < -0.383 ? 'top' : v.y > 0.383 ? 'bottom' : ''
  const horz = v.x < -0.383 ? 'left' : v.x > 0.383 ? 'right' : ''
  if (vert && horz) return `${vert}-${horz}`
  return vert || horz
}

/**
 * The one sentence that tells an operator whether they have it the right way round.
 *
 * Phrased as a consequence they can check against the picture — "someone walking toward X is coming
 * in" — rather than as a setting. If it is wrong, the fix is the flip button, and they can tell it is
 * wrong by looking.
 */
export function inwardSentence(g: LineGeometry | null, inward: Inward): string {
  if (!g || lineLength(g) < EPS) return ''
  const label = compassLabel(inwardNormal(g, inward))
  if (label === 'nowhere') return ''
  return `Someone walking toward the ${label} of the picture is coming IN.`
}

export function flip(inward: Inward): Inward {
  return inward === 'left' ? 'right' : 'left'
}

/**
 * The zone set to PUT: this counting line, plus every zone that is not a counting line.
 *
 * The endpoint replaces the whole set, so anything omitted is deleted. Exclude polygons — the thing
 * keeping the pavement and the back office out of the count — are not this page's to throw away, and
 * a page that sent only its own line would delete them without a word.
 *
 * Existing LINE zones are replaced rather than appended: two counting lines across one doorway count
 * every customer twice, and the drawing surface only ever shows one.
 */
export function withCountingLine(existing: Zone[] | null | undefined,
                                 g: LineGeometry, inward: Inward,
                                 name = 'Entrance'): Zone[] {
  const others = (existing || []).filter(z => (z?.kind || 'polygon') !== 'line')
  const line: Zone = {
    kind: 'line',
    name,
    zone_key: 'entrance',
    geometry: { x1: g.x1, y1: g.y1, x2: g.x2, y2: g.y2 },
    inward,
    is_active: true,
    sort_order: 10,
  }
  return [line, ...others]
}

/** The counting line already stored for this camera, or null when none has been drawn. */
export function currentLine(zones: Zone[] | null | undefined):
  { geometry: LineGeometry; inward: Inward } | null {
  for (const z of (zones || [])) {
    if ((z?.kind || '') !== 'line') continue
    const nums = ['x1', 'y1', 'x2', 'y2'].map(k => num(z.geometry, k))
    if (nums.some(n => !Number.isFinite(n))) continue
    const geom = { x1: clamp01(nums[0]), y1: clamp01(nums[1]),
                   x2: clamp01(nums[2]), y2: clamp01(nums[3]) }
    if (lineLength(geom) < EPS) continue
    return { geometry: geom, inward: (z.inward || 'left') === 'right' ? 'right' : 'left' }
  }
  return null
}

/**
 * What to tell the operator about this camera before they have drawn anything.
 *
 * A camera flagged as an entrance with no line is the silent-zero case that prompted this page, so
 * it gets said plainly rather than left as an empty drawing surface.
 */
export function zoneStatus(isEntrance: boolean, zones: Zone[] | null | undefined): string {
  const line = currentLine(zones)
  if (line) return ''
  if (isEntrance) {
    return 'This camera is marked as an entrance but has no counting line, so it is counting '
      + 'nobody. Draw a line across the doorway to start counting.'
  }
  return 'This camera is not marked as an entrance. A line drawn here will be saved, but the '
    + 'analyzer only counts crossings on entrance cameras.'
}
