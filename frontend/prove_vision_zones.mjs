/**
 * PROOF — src/lib/vision-zones.ts, the rules behind drawing a counting line.
 *
 * The interesting part is section D. `crossingDirection` is a PORT of the Python the analyzer
 * actually runs (backend/app/modules/vision/geometry.py), and it exists so the page can show an
 * operator what the analyzer would say about the line they just drew. A port that drifted would be
 * worse than no test at all: it would send somebody away confident about a doorway that counts
 * backwards. So it is not checked against hand-written expectations — it is run against the REAL
 * PYTHON over thousands of random cases, and every answer must match.
 *
 * Run:  cd frontend && node prove_vision_zones.mjs
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)
const ts = require('typescript')

const FILE = path.join(__dirname, 'src/lib/vision-zones.ts')
const js = ts.transpileModule(fs.readFileSync(FILE, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText
const mod = {}
new Function('exports', 'module', js)(mod, { exports: mod })
const Z = mod

let P = 0, F = 0
const check = (name, cond, detail = '') => {
  if (cond) { P++; console.log(`  PASS  ${name}`) }
  else { F++; console.log(`  FAIL  ${name}   ${detail}`) }
}
const near = (a, b, tol = 1e-9) => Math.abs(a - b) <= tol

console.log('='.repeat(78))
console.log('A. The letterbox — a click is not where the element thinks it is')
console.log('='.repeat(78))
// A 16:9 video inside a 1000x400 box (2.5:1). Contain fits by HEIGHT: 400*16/9 = 711.1 wide,
// leaving (1000-711.1)/2 = 144.4 of bar down each side.
const wide = { left: 0, top: 0, width: 1000, height: 400 }
const A = 16 / 9
check('A1 centre of the picture is the centre of the image',
  (() => { const p = Z.normPoint(500, 200, wide, A); return p && near(p.x, 0.5, 1e-12) && near(p.y, 0.5, 1e-12) })())
check('A2 a click on the LEFT BAR is not a point in the image (null, not clamped to 0)',
  Z.normPoint(50, 200, wide, A) === null)
check('A3 a click on the right bar is null too', Z.normPoint(950, 200, wide, A) === null)
// The edge of the picture IS the picture. The arithmetic that locates it lands on -6.25e-14 here,
// so a bare `x < 0` refuses the first pixel column of a perfectly good frame.
check('A4 the first pixel column of the picture is accepted, not lost to floating point',
  (() => { const p = Z.normPoint(144.4444444444, 200, wide, A); return p !== null && p.x === 0 })())
check('A4b ...but a click a whole pixel further left is still on the bar',
  Z.normPoint(143.4, 200, wide, A) === null)
// 300px into a 1000px box is 0.3 of the ELEMENT and 0.219 of the IMAGE — 8% of the frame apart,
// roughly a doorway's width at this framing. The line would draw under the cursor and count in the
// wrong place, which is why this is proven rather than eyeballed.
check('A5 the naive answer is wrong by 8% of the frame',
  (() => { const p = Z.normPoint(300, 200, wide, A)
           return p && near(p.x, 0.21875, 1e-9) && Math.abs(p.x - 0.3) > 0.08 })(),
  'this is the bug the whole function exists for')
// Bars top and bottom: a 16:9 video in a 400x400 box.
const tall = { left: 0, top: 0, width: 400, height: 400 }
check('A6 a tall box letterboxes vertically instead',
  (() => { const p = Z.normPoint(200, 200, tall, A); return p && near(p.x, 0.5, 1e-12) && near(p.y, 0.5, 1e-12) })())
check('A7 ...and the top bar is refused', Z.normPoint(200, 10, tall, A) === null)
check('A8 the element offset is honoured (a scrolled page)',
  (() => { const p = Z.normPoint(500 + 137, 200 + 42, { ...wide, left: 137, top: 42 }, A)
           return p && near(p.x, 0.5, 1e-12) && near(p.y, 0.5, 1e-12) })())
check('A9 a video that has not loaded (aspect 0) falls back to the box, it does not divide by zero',
  (() => { const p = Z.normPoint(500, 200, wide, 0); return p && near(p.x, 0.5) && near(p.y, 0.5) })())
check('A10 a zero-sized element yields null rather than NaN',
  Z.normPoint(0, 0, { left: 0, top: 0, width: 0, height: 0 }, A) === null)

console.log()
console.log('='.repeat(78))
console.log('B. A line you cannot cross is not a line')
console.log('='.repeat(78))
check('B1 nothing drawn yet -> asks for a drag', /Drag across the doorway/.test(Z.lineBlocker(null)))
check('B2 a click without a drag is refused',
  Z.lineBlocker({ x1: 0.5, y1: 0.5, x2: 0.5, y2: 0.5 }) !== '')
check('B3 a twitch is refused, and says what to do instead',
  /too short/.test(Z.lineBlocker({ x1: 0.5, y1: 0.5, x2: 0.52, y2: 0.5 })))
check('B4 a line across a doorway is accepted',
  Z.lineBlocker({ x1: 0.2, y1: 0.6, x2: 0.8, y2: 0.6 }) === '')
check('B5 the threshold is a real length, not per-axis (a diagonal twitch is still a twitch)',
  Z.lineBlocker({ x1: 0.5, y1: 0.5, x2: 0.54, y2: 0.54 }) !== '')

console.log()
console.log('='.repeat(78))
console.log('C. Which way is IN — the arrow is derived from the rule, not asserted beside it')
console.log('='.repeat(78))
// A horizontal line drawn left-to-right. In image coords y is DOWN, so the 'left' side by the
// cross-product convention is the BOTTOM of the picture. This is the trap the page exists to hide.
const horiz = { x1: 0.2, y1: 0.5, x2: 0.8, y2: 0.5 }
const nL = Z.inwardNormal(horiz, 'left')
check('C1 "left" of a left-to-right line is the BOTTOM of the picture (y is down)',
  near(nL.x, 0, 1e-12) && near(nL.y, 1, 1e-12), JSON.stringify(nL))
check('C2 ...and is named that way for somebody looking at the picture',
  Z.compassLabel(nL) === 'bottom')
check('C3 "right" is the opposite side', (() => {
  const nR = Z.inwardNormal(horiz, 'right'); return near(nR.y, -1, 1e-12) && Z.compassLabel(nR) === 'top'
})())
check('C4 the normal is a UNIT vector, so the arrow is the same length at any angle', (() => {
  const g = { x1: 0.1, y1: 0.2, x2: 0.9, y2: 0.7 }
  const n = Z.inwardNormal(g, 'left'); return near(Math.hypot(n.x, n.y), 1, 1e-12)
})())
check('C5 drawing the SAME doorway backwards inverts which side is in', (() => {
  const back = { x1: 0.8, y1: 0.5, x2: 0.2, y2: 0.5 }
  return Z.compassLabel(Z.inwardNormal(back, 'left')) === 'top'
})(), 'why the operator must never be asked to pick left or right')
check('C6 the sentence names a direction in the picture, not a setting',
  Z.inwardSentence(horiz, 'left') === 'Someone walking toward the bottom of the picture is coming IN.')
check('C7 a diagonal gets a diagonal name',
  Z.compassLabel(Z.inwardNormal({ x1: 0.2, y1: 0.2, x2: 0.8, y2: 0.8 }, 'right')) === 'top-right')
check('C8 no line -> no sentence, rather than a confident wrong one',
  Z.inwardSentence(null, 'left') === '' && Z.inwardSentence({ x1: 0.5, y1: 0.5, x2: 0.5, y2: 0.5 }, 'left') === '')
check('C9 flip is its own inverse', Z.flip(Z.flip('left')) === 'left' && Z.flip('left') === 'right')
// THE ARROW AND THE RULE MUST AGREE. Step a marker along the inward normal and the rule must say 'in'.
check('C10 walking along the arrow always counts as IN, at every angle', (() => {
  for (let i = 0; i < 360; i += 7) {
    const t = i * Math.PI / 180
    const g = { x1: 0.5 - 0.3 * Math.cos(t), y1: 0.5 - 0.3 * Math.sin(t),
                x2: 0.5 + 0.3 * Math.cos(t), y2: 0.5 + 0.3 * Math.sin(t) }
    for (const inward of ['left', 'right']) {
      const n = Z.inwardNormal(g, inward)
      const m = Z.midpoint(g)
      const prev = { x: m.x - n.x * 0.08, y: m.y - n.y * 0.08 }
      const cur = { x: m.x + n.x * 0.08, y: m.y + n.y * 0.08 }
      if (Z.crossingDirection(g, inward, prev, cur) !== 'in') return false
      if (Z.crossingDirection(g, inward, cur, prev) !== 'out') return false
    }
  }
  return true
})(), 'the arrow the page draws cannot point opposite to the count it reports')

console.log()
console.log('='.repeat(78))
console.log('D. The port must agree with the Python the analyzer actually runs')
console.log('='.repeat(78))
const rnd = (seed => () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff)(20260828)
const cases = []
for (let i = 0; i < 4000; i++) {
  // Coordinates are quantised so both languages parse the same decimal, and deliberately include
  // exactly-on-the-line and past-the-end-of-the-line cases, which is where the two could diverge.
  const q = () => Math.round(rnd() * 20) / 20
  cases.push({
    g: { x1: q(), y1: q(), x2: q(), y2: q() },
    inward: rnd() < 0.5 ? 'left' : 'right',
    prev: { x: q(), y: q() }, cur: { x: q(), y: q() },
  })
}
const tmp = path.join(os.tmpdir(), `zones_cases_${process.pid}.json`)
fs.writeFileSync(tmp, JSON.stringify(cases))
const PY = `
import json, sys
sys.path.insert(0, ${JSON.stringify(path.join(__dirname, '..', 'backend'))})
from app.modules.vision import geometry as GEO
cases = json.load(open(${JSON.stringify(tmp)}))
out = []
for c in cases:
    zone = {"kind": "line", "geometry": c["g"], "inward": c["inward"]}
    out.append(GEO.crossing_direction(zone, (c["prev"]["x"], c["prev"]["y"]),
                                            (c["cur"]["x"], c["cur"]["y"])))
print(json.dumps(out))
`
let expected = null
try {
  expected = JSON.parse(execFileSync('python3', ['-c', PY], { encoding: 'utf8' }))
} catch (e) {
  console.log(`  FAIL  D0 could not run the Python reference   ${String(e).slice(0, 200)}`)
  F++
}
fs.unlinkSync(tmp)
if (expected) {
  let mismatch = null, counted = 0
  for (let i = 0; i < cases.length; i++) {
    const c = cases[i]
    const got = Z.crossingDirection(c.g, c.inward, c.prev, c.cur)
    if (got !== expected[i]) { mismatch = { i, c, got, want: expected[i] }; break }
    if (got) counted++
  }
  check(`D1 all ${cases.length} random cases agree with geometry.crossing_direction`,
    mismatch === null, mismatch ? JSON.stringify(mismatch) : '')
  check('D2 ...and the sample was not trivially all-null',
    counted > 200, `${counted} of ${cases.length} were real crossings`)
}

console.log()
console.log('='.repeat(78))
console.log('E. Saving must not delete what this page did not draw')
console.log('='.repeat(78))
const existing = [
  { kind: 'exclude', name: 'Pavement', geometry: { points: [[0, 0], [1, 0], [1, .3]] } },
  { kind: 'polygon', name: 'Counter', geometry: { points: [[.1, .1], [.4, .1], [.4, .5]] } },
  { kind: 'line', name: 'Old entrance', geometry: { x1: 0, y1: 0, x2: 1, y2: 1 }, inward: 'right' },
]
const saved = Z.withCountingLine(existing, horiz, 'left')
check('E1 the exclude polygon survives — the pavement stays out of the count',
  saved.some(z => z.kind === 'exclude' && z.name === 'Pavement'))
check('E2 the counter polygon survives', saved.some(z => z.name === 'Counter'))
check('E3 the OLD counting line is replaced, not added to',
  saved.filter(z => z.kind === 'line').length === 1,
  'two lines across one doorway counts everyone twice')
check('E4 the new line carries the geometry and the side that was drawn', (() => {
  const l = saved.find(z => z.kind === 'line')
  return l.inward === 'left' && l.geometry.x1 === 0.2 && l.geometry.y2 === 0.5 && l.is_active === true
})())
check('E5 an empty camera is fine', Z.withCountingLine(null, horiz, 'left').length === 1)
check('E6 the line sorts first, so it is the one the drawing surface finds',
  saved[0].kind === 'line')

console.log()
console.log('='.repeat(78))
console.log('F. Reading back what is stored, including the silent-zero case')
console.log('='.repeat(78))
check('F1 a stored line round-trips', (() => {
  const r = Z.currentLine(saved); return r && r.inward === 'left' && r.geometry.x1 === 0.2
})())
check('F2 no line stored -> null, not a zero-length line',
  Z.currentLine([{ kind: 'exclude', geometry: {} }]) === null)
check('F3 a malformed stored line is ignored rather than drawn wrong',
  Z.currentLine([{ kind: 'line', geometry: { x1: 'x', y1: null, x2: 1, y2: 1 } }]) === null)
check('F4 a zero-length stored line is ignored',
  Z.currentLine([{ kind: 'line', geometry: { x1: .5, y1: .5, x2: .5, y2: .5 } }]) === null)
check('F5 THE SILENT ZERO IS NAMED: an entrance with no line says it is counting nobody',
  /counting nobody/.test(Z.zoneStatus(true, [])))
check('F6 a non-entrance camera is told the line will not count',
  /only counts crossings on entrance cameras/.test(Z.zoneStatus(false, [])))
check('F7 once a line exists there is nothing to warn about',
  Z.zoneStatus(true, saved) === '')

console.log()
console.log('='.repeat(78))
console.log('G. The overlay is drawn in a different space to the clicks — they must coincide')
console.log('='.repeat(78))
// The page maps clicks with normPoint (IMAGE coordinates, letterbox corrected) but positions the
// line, the arrow and the marker as percentages of the BOX. Those two spaces are the same only when
// the box has no letterbox — which is why the page gives the box the picture's own aspect ratio once
// it has a picture. If that ever regresses to a fixed 16/9, a 4:3 camera draws its line away from
// the cursor AND stores the coordinate under the cursor, so picture and stored line disagree about
// where the doorway is. Neither is detectably wrong on screen.
const box43 = { left: 0, top: 0, width: 640, height: 480 }
let aligned = true
for (const [cx, cy] of [[0, 0], [160, 120], [320, 240], [639, 479], [500, 90]]) {
  const p = Z.normPoint(cx, cy, box43, 640 / 480)
  if (!p || !near(p.x, cx / 640, 1e-12) || !near(p.y, cy / 480, 1e-12)) { aligned = false; break }
}
check('G1 box aspect == picture aspect -> image coords ARE box fractions (overlay lines up)', aligned)
check('G2 ...and with a MISMATCHED box they diverge, which is the bug being prevented', (() => {
  const p = Z.normPoint(300, 200, { left: 0, top: 0, width: 1000, height: 400 }, 4 / 3)
  return p && Math.abs(p.x - 300 / 1000) > 0.05
})())
check('G3 the page asks for the picture\'s own aspect ratio, not a fixed one', (() => {
  const src = fs.readFileSync(path.join(__dirname, 'src/app/(platform)/vision/lines/page.tsx'), 'utf8')
  return /aspectRatio:\s*still\s*\?\s*`\$\{still\.width\} \/ \$\{still\.height\}`/.test(src)
})(), 'if this fails, the overlay and the clicks are in different spaces again')

console.log()
console.log('='.repeat(78))
console.log(`${P} passed, ${F} failed`)
console.log('='.repeat(78))
process.exit(F ? 1 : 0)
