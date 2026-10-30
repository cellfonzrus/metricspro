#!/usr/bin/env node
// THE BUILD LOCK for the narrow-viewport one home (src/lib/viewport.ts, index §CORE-25).
//
// WHAT IT STOPS. A screen that splits the page into a fixed pane and a "whatever is left" pane is
// a screen that shows NOTHING on a phone once "whatever is left" reaches zero — measured on the
// POS register at iPhone width on 2026-10-30, where the cart pane computed to 0px and the cashier
// saw no products. The fix was one shared fact (`twoPaneColumns` / `twoPaneFrame`); without this
// lock the next hand-written `gridTemplateColumns: '1fr 340px'` quietly restores the defect.
//
// THE RULE. In src/app/**, a grid template literal carrying a px track of PANE_MIN or more must
// not be written inline — it must come from src/lib/viewport.ts. Smaller tracks (icon columns,
// number cells, badge rails) are left alone: they do not starve their sibling.
//
// Shrink-only, like tools/proof_red_pending.txt and backend/harness_unrun_pending.txt: there is no
// allow-list to add to and no --accept flag.
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = dirname(dirname(fileURLToPath(import.meta.url)))   // frontend/
const APP = join(ROOT, 'src', 'app')
const PANE_MIN = 180            // px: at or above this, a fixed track can starve its sibling
const HOME = 'src/lib/viewport.ts'

function walk(dir) {
  const out = []
  for (const e of readdirSync(dir)) {
    const p = join(dir, e)
    if (statSync(p).isDirectory()) out.push(...walk(p))
    else if (p.endsWith('.tsx') || p.endsWith('.ts')) out.push(p)
  }
  return out
}

// gridTemplateColumns: '…' — the literal, not an expression.
const TEMPLATE = /gridTemplateColumns:\s*'([^']*)'/g

/** Split a template into tracks, treating a bracketed function as one track. */
function tracks(template) {
  const out = []
  let depth = 0, cur = ''
  for (const ch of template) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ' ' && depth === 0) { if (cur.trim()) out.push(cur.trim()); cur = '' }
    else cur += ch
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

/** The px width a track is PINNED to, or 0 when it can give way. `minmax(220px, 280px)` is pinned
 *  (it cannot go below 220 and will not grow past 280); `minmax(220px, 1fr)` can grow, and a
 *  `repeat(auto-fit, …)` track WRAPS onto another row rather than starving its sibling — a card
 *  wall is already responsive and is not what this lock is about. */
function pinnedPx(track) {
  if (/^repeat\(\s*auto-(fit|fill)/i.test(track)) return 0
  const plain = /^(\d+)px$/.exec(track)
  if (plain) return Number(plain[1])
  const mm = /^minmax\(\s*(\d+)px\s*,\s*(\d+)px\s*\)$/i.exec(track)
  if (mm) return Number(mm[1])
  return 0
}

/** Can this track absorb what is left over? */
function flexible(track) {
  return /fr\b/.test(track) || /auto/i.test(track)
}

const offences = []
for (const file of walk(APP)) {
  const src = readFileSync(file, 'utf8')
  const rel = file.slice(ROOT.length + 1)
  for (const m of src.matchAll(TEMPLATE)) {
    const template = m[1]
    const ts = tracks(template)
    if (!ts.some(flexible)) continue            // no "whatever is left" pane ⇒ nothing to starve
    const widest = ts.map(pinnedPx).reduce((a, b) => Math.max(a, b), 0)
    if (widest < PANE_MIN) continue
    const line = src.slice(0, m.index).split('\n').length
    offences.push(`${rel}:${line}  gridTemplateColumns: '${template}'  (pinned track ${widest}px beside a flexible one)`)
  }
}

// The shell's own rail is the other half of the same fact: a sidebar that cannot collapse starves
// every page behind it, whatever that page's own grid says.
const shell = readFileSync(join(APP, '(platform)', 'layout.tsx'), 'utf8')
const shellWired = /useNarrowViewport\s*\(/.test(shell)

let bad = offences.length
if (!shellWired) {
  console.error(`FAIL  src/app/(platform)/layout.tsx does not call useNarrowViewport().`)
  console.error(`      The sidebar is a fixed width; on a phone it must fall back to the icon rail.`)
  bad++
}
for (const o of offences) console.error(`FAIL  ${o}`)

if (bad) {
  console.error(`\n${bad} place(s) decide a pane width without ${HOME}.`)
  console.error(`      Use twoPaneColumns(fixedPx, narrow, side) or twoPaneFrame(...) and take`)
  console.error(`      \`narrow\` from useNarrowViewport(). Tracks under ${PANE_MIN}px are exempt.`)
  process.exit(1)
}
console.log(`PASS  every pane width >= ${PANE_MIN}px in src/app/** comes from ${HOME}, and the shell rail collapses.`)
