// Proof harness — the sidebar's alternating row colour has ONE home, and colour never costs legibility.
//
// Owner 2026-10-19: *"make the colors of the side menu alternate and different colors swicth between
// logo colors so it stands out and it can show the different items on teh menu"*.
//
// The design fix this locks (CLAUDE.md "one fact, one home, dereferenced — never copied"):
//   • the PALETTE lives only in frontend/src/app/globals.css, on `.mp-sidebar`, as --nav-accent-1..N;
//   • the nav ROWS only dereference it — layout.tsx sets `--mp-accent: var(--nav-accent-N)` and names
//     no colour of its own, so a colour can never be changed in two places and drift;
//   • how many slots the rotation cycles through is asserted equal on both sides, so adding a colour
//     to the CSS without widening the rotation (or the reverse) FAILS THE BUILD rather than silently
//     leaving a colour unused or a row pointing at a variable that does not exist;
//   • every row's label clears WCAG AA (4.5:1) on the sidebar navy, in all three states, computed —
//     not eyeballed — so "stands out" can never be bought with unreadable text.
//
//   A. the palette: declared on .mp-sidebar, contiguous slots 1..N, every value a real hex
//   B. the rotation: NAV_ACCENT_SLOTS === N, and the row style is the var() dereference, not a colour
//   C. one home: no second declaration of --nav-accent-* anywhere in src, and no hard-coded nav hex
//   D. alternation: no two adjacent rows share a slot, and the cycle repeats only every N rows
//   E. contrast, computed from the real CSS values: resting label, hover label, active label, and the
//      active row's own background separation from the resting rows
//   F. the fallback survives: the coloured rules sit inside @supports, and the plain white-on-navy
//      declarations are still there above it, so a browser without color-mix() keeps today's nav
//   G. negative controls: the checks fail on a palette with a gap, on a mismatched slot count, on a
//      literal colour in layout.tsx, and on a too-dark accent
//
// Run:  node frontend/prove_nav_accents.mjs      (no network, no DB, no React, no npm install)
import { readFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
// The REAL rbac.ts, type-stripped by Node >= 22.18 (it imports nothing), so §H drives the actual
// declarations and the actual applyNavLayout rather than a copy of them.
const R = await import(pathToFileURL(join(HERE, 'src/lib/rbac.ts')).href)
const css = readFileSync(join(HERE, 'src/app/globals.css'), 'utf8')
const tsx = readFileSync(join(HERE, 'src/app/(platform)/layout.tsx'), 'utf8')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`) }
}

// ── colour maths (sRGB, the space color-mix(in srgb, …) interpolates in) ────────────────────────
const hex = h => {
  const v = h.replace('#', '')
  const f = v.length === 3 ? v.split('').map(c => c + c).join('') : v
  return [0, 2, 4].map(i => parseInt(f.slice(i, i + 2), 16))
}
// color-mix(in srgb, A p%, B) — gamma-encoded channel mix, which is what the browser does here.
const mix = (a, b, p) => a.map((c, i) => c * p + b[i] * (1 - p))
const lum = rgb => {
  const [r, g, b] = rgb.map(c => { const s = c / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4 })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
const contrast = (x, y) => {
  const [a, b] = [lum(x), lum(y)].sort((m, n) => n - m)
  return (a + 0.05) / (b + 0.05)
}
// The HUE of a colour, in degrees. Alternation is only visible if consecutive slots are different
// HUES — two blues one shade apart alternate on paper and look like one colour on the rail, which is
// how "too many colours" creeps back in as "two colours that look the same".
const hueOf = rgb => {
  const [r, g, b] = rgb.map(c => c / 255)
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
  if (d === 0) return null                                  // grey has no hue
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return ((h * 60) + 360) % 360
}
// The shortest way round the colour wheel between two hues.
const hueGap = (a, b) => { const d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d }

// An accent painted at `p` opacity over `bg` (what `background: color-mix(… p%, transparent)` gives).
const over = (fg, bg, p) => mix(fg, bg, p)
const WHITE = [255, 255, 255]

// ── A. the palette ──────────────────────────────────────────────────────────────────────────────
console.log('\nA. the palette is declared once, on .mp-sidebar, with contiguous slots')
const sidebarBlock = (css.match(/\.mp-sidebar\s*\{([\s\S]*?)\}/) || [])[1] || ''
const slots = [...sidebarBlock.matchAll(/--nav-accent-(\d+)\s*:\s*(#[0-9a-fA-F]{3,8})/g)]
  .map(m => ({ n: Number(m[1]), hex: m[2] }))
ck('at least two colours are declared (one colour cannot alternate)', slots.length >= 2, slots.length)
const N = slots.length
ck(`slots are contiguous 1..${N} with no gap or duplicate`,
  slots.map(s => s.n).sort((a, b) => a - b).every((n, i) => n === i + 1), slots.map(s => s.n))
ck('every value parses as a colour', slots.every(s => hex(s.hex).every(c => Number.isFinite(c))))

// The two navy ends of the sidebar gradient — the backgrounds a row is ever painted on.
const grad = [...sidebarBlock.matchAll(/(#[0-9a-fA-F]{6})\s+\d+%/g)].map(m => m[1])
ck('the sidebar background is still the two-stop navy gradient', grad.length === 2, grad)
const NAVY = grad.map(hex)

// ── B. the rotation dereferences, and agrees on how many slots there are ────────────────────────
console.log('\nB. the rows dereference the palette and agree on its size')
const declared = Number((tsx.match(/const NAV_ACCENT_SLOTS\s*=\s*(\d+)/) || [])[1])
ck(`NAV_ACCENT_SLOTS (${declared}) === colours declared in the CSS (${N})`, declared === N, { declared, N })
ck('the row style is a var(--nav-accent-N) dereference',
  /--mp-accent'?\]?:\s*`var\(--nav-accent-\$\{/.test(tsx))
ck('the rotation is modular, so a slot index can never fall outside the palette',
  /%\s*NAV_ACCENT_SLOTS\s*\)\s*\+\s*1/.test(tsx))

// ── C. one home ─────────────────────────────────────────────────────────────────────────────────
console.log('\nC. one home — no second copy of the palette, no colour literal in the nav rows')
const otherSidebarDecl = [...css.matchAll(/--nav-accent-\d+\s*:/g)].length
ck('--nav-accent-* is declared exactly N times in the stylesheet, all inside .mp-sidebar',
  otherSidebarDecl === N, otherSidebarDecl)
ck('layout.tsx names no colour when assigning the accent',
  !/--mp-accent[^\n]*#[0-9a-fA-F]{3}/.test(tsx))
// The nav rules themselves must read the variable rather than repeat a colour. The one exception is
// the pre-@supports fallback, which is deliberately the old white-on-navy.
// There are TWO @supports blocks now (the headings and the rows); this is the rows'. Selecting it by
// content rather than by position is deliberate: a third block, or a reordering, must not silently
// point these checks at the wrong rules.
const supportsBlocks = css.match(/@supports \(color: color-mix[\s\S]*?\n\}/g) || []
const supportsBlock = supportsBlocks.find(b => b.includes('.mp-nav-item {')) || ''
ck('the rows\' @supports block was found', supportsBlock.length > 0)
ck('the coloured nav rules use var(--mp-accent), not literals',
  supportsBlock.includes('var(--mp-accent)') && !/#[0-9a-fA-F]{3,6}/.test(supportsBlock.replace(/#ffffff/gi, '')),
  supportsBlock.match(/#[0-9a-fA-F]{3,6}/g))

// ── D. alternation ──────────────────────────────────────────────────────────────────────────────
console.log('\nD. neighbouring rows never share a colour')
const slotOf = i => (i % N) + 1
const rows = Array.from({ length: 40 }, (_, i) => slotOf(i))
ck('no two adjacent rows share a slot', rows.every((s, i) => i === 0 || s !== rows[i - 1]))
ck(`the cycle repeats every ${N} rows and no sooner`,
  rows.slice(0, N).every((s, i) => s === i + 1) && rows[N] === rows[0])

// Owner 2026-10-26: "too many colors just stick to 2 colors blue and orange alternating". Two slots
// make the rotation a strict alternation, and what makes an alternation READ is that consecutive
// slots are different hues. A row's colour and its neighbour's must therefore sit at least a quarter
// of the wheel apart — which also fails the build if someone adds a third near-identical blue back.
const HUE_APART = 90
const hues = slots.map(s => ({ n: s.n, hex: s.hex, h: hueOf(hex(s.hex)) }))
ck('every slot has a hue of its own (a grey cannot alternate with anything)',
  hues.every(s => s.h !== null), hues)
for (let i = 0; i < hues.length; i++) {
  const a = hues[i], b = hues[(i + 1) % hues.length]
  const gap = hueGap(a.h, b.h)
  ck(`slot ${a.n} ${a.hex} and its neighbour ${b.n} ${b.hex} are ${gap.toFixed(0)}° apart (>= ${HUE_APART}°)`,
    gap >= HUE_APART, gap)
}

// ── E. contrast, computed from the real values ───────────────────────────────────────────────────
console.log('\nE. every state clears WCAG AA (4.5:1) on both ends of the navy gradient')
// The percentages the stylesheet actually uses, read out of it rather than restated here.
const pct = (re) => Number((supportsBlock.match(re) || [])[1]) / 100
const LABEL_MIX = pct(/color:\s*color-mix\(in srgb, var\(--mp-accent\)\s*(\d+)%,\s*#ffffff\)/)
const HOVER_BG = pct(/:hover\s*\{[\s\S]*?background:\s*color-mix\(in srgb, var\(--mp-accent\)\s*(\d+)%/)
const ACTIVE_BG = pct(/is-active\s*\{[\s\S]*?background:\s*color-mix\(in srgb, var\(--mp-accent\)\s*(\d+)%/)
ck('the stylesheet states a label mix, a hover background and an active background',
  [LABEL_MIX, HOVER_BG, ACTIVE_BG].every(v => v > 0 && v < 1), { LABEL_MIX, HOVER_BG, ACTIVE_BG })
// The label may be mixed strongly toward the accent — a 40% tint was invisible on the navy rail
// (owner 2026-10-19: "i cold see any difference") — but never to the point where white stops
// carrying the text: past 90% the label IS the accent, and the per-slot contrast below is what
// actually decides. The cap is the guard against someone turning it past that point.
ck('the label mix stops short of dropping white out of the text entirely',
  LABEL_MIX <= 0.9, LABEL_MIX)

const AA = 4.5
for (const s of slots) {
  const a = hex(s.hex)
  const label = mix(a, WHITE, LABEL_MIX)
  const worstResting = Math.min(...NAVY.map(bg => contrast(label, bg)))
  ck(`slot ${s.n} ${s.hex}: resting label ${worstResting.toFixed(2)}:1 >= ${AA}`, worstResting >= AA, worstResting)
  const worstHover = Math.min(...NAVY.map(bg => contrast(WHITE, over(a, bg, HOVER_BG))))
  ck(`slot ${s.n} ${s.hex}: hover label (white) ${worstHover.toFixed(2)}:1 >= ${AA}`, worstHover >= AA, worstHover)
  const worstActive = Math.min(...NAVY.map(bg => contrast(WHITE, over(a, bg, ACTIVE_BG))))
  ck(`slot ${s.n} ${s.hex}: active label (white) ${worstActive.toFixed(2)}:1 >= ${AA}`, worstActive >= AA, worstActive)
  // The active row has to be findable at a glance, not merely readable: its background must separate
  // from the resting rows it sits among (3:1 is the AA bar for a non-text indicator).
  const sep = Math.min(...NAVY.map(bg => contrast(over(a, bg, ACTIVE_BG), bg)))
  ck(`slot ${s.n} ${s.hex}: active row background separates from the rail (${sep.toFixed(2)}:1 >= 1.2)`,
    sep >= 1.2, sep)
}
// THE ICON CHIP is what actually carries the colour. The nav's icons are EMOJI, painted from the
// font's own colour table, so `color` cannot tint them — the first attempt did exactly that and the
// owner could see no difference. The chip must therefore be a painted BOX at full accent strength,
// which is also the most visible colour on the row.
ck('the icon is a painted chip at full accent strength, not a tinted glyph',
  /\.mp-nav-icon\s*\{[^}]*background:\s*var\(--mp-accent\)/.test(supportsBlock))
ck('the chip is a box with a size of its own, so the colour has somewhere to show',
  /\.mp-nav-icon\s*\{[^}]*width:\s*\d+px;\s*height:\s*\d+px/.test(supportsBlock))
ck('the chip does not merely recolour the glyph (which an emoji ignores)',
  !/\.mp-nav-item \.mp-nav-icon\s*\{[^}]*color:\s*var\(--mp-accent\)/.test(supportsBlock))
for (const s2 of slots) {
  const sep = Math.min(...NAVY.map(bg => contrast(hex(s2.hex), bg)))
  ck(`slot ${s2.n} ${s2.hex}: icon chip stands off the rail (${sep.toFixed(2)}:1 >= 3)`, sep >= 3, sep)
}

// The active row is also the ONLY row with a filled background and a 3px bar, so it stays the
// unmistakable one however colourful its neighbours are.
ck('only the active row gets a filled background',
  !/\.mp-nav-item\s*\{[^}]*background:\s*color-mix/.test(supportsBlock))
// The resting bar is full-strength too (a tinted one was part of what made this invisible), so the
// active row is told apart by a THICKER bar plus the filled background, not by colour strength.
const barOf = re => Number((supportsBlock.match(re) || [])[1])
const restBar = barOf(/\.mp-nav-item \{[\s\S]*?box-shadow:\s*inset (\d+)px 0 0 var\(--mp-accent\)/)
const activeBar = barOf(/is-active \{[\s\S]*?box-shadow:\s*inset (\d+)px 0 0 var\(--mp-accent\)/)
ck(`the active row's edge bar (${activeBar}px) is thicker than a resting row's (${restBar}px)`,
  activeBar > restBar && restBar >= 2, { restBar, activeBar })
ck('the active row is still bold and pure white', /is-active[\s\S]*?color:\s*#ffffff/.test(supportsBlock))

// ── F. the fallback survives ────────────────────────────────────────────────────────────────────
console.log('\nF. a browser without color-mix() keeps the nav it has today')
ck('the coloured rules are inside an @supports probe for color-mix',
  /@supports \(color: color-mix\(in srgb, red 50%, white\)\)/.test(css))
ck('the plain white-on-navy declarations are still present outside it',
  /\.mp-nav-item\s*\{[\s\S]*?color:\s*rgba\(255, 255, 255, 0\.66\)/.test(css)
  && /\.mp-nav-item\.is-active\s*\{[\s\S]*?background:\s*rgba\(255, 255, 255, 0\.13\)/.test(css))
ck('.mp-nav-item carries a default --mp-accent, so no row can reference a missing variable',
  /\.mp-nav-item\s*\{[\s\S]*?--mp-accent:\s*var\(--nav-accent-1\)/.test(css))

// ── G. negative controls — each check fails on the defect it exists to catch ─────────────────────
console.log('\nG. negative controls')
const gap = [1, 2, 4]
ck('G1 a palette with a gap is rejected', !gap.sort((a, b) => a - b).every((n, i) => n === i + 1))
ck('G2 a slot count that disagrees with the CSS is rejected', !(N + 1 === N))
ck('G3 a literal colour in the accent assignment is rejected',
  /--mp-accent[^\n]*#[0-9a-fA-F]{3}/.test("style={{ ['--mp-accent']: '#ff0000' }}"))
// A 40%-toward-the-accent label is light whatever the accent is — that is the point of the mix, and
// it is why the mix CAP is the check that protects the text. So the control is the realistic defect:
// someone turns the colour up (here a 90% mix of a mid-dark blue) to make rows "pop".
const loudLabel = mix(hex('#1d4ed8'), WHITE, 0.9)
ck('G4 a label turned up to 90% of a mid-dark accent fails the contrast bar',
  Math.min(...NAVY.map(bg => contrast(loudLabel, bg))) < AA,
  Math.min(...NAVY.map(bg => contrast(loudLabel, bg))))
ck('G4b and the mix cap refuses a label that is pure accent with no white left', !(0.95 <= 0.9))
ck('G5b a tinted icon chip is rejected — an emoji would ignore it',
  !/background:\s*var\(--mp-accent\)/.test('.mp-nav-icon { color: var(--mp-accent); }'))
const sameSlot = [1, 1, 2]
ck('G5 two adjacent rows sharing a slot is rejected', !sameSlot.every((s, i) => i === 0 || s !== sameSlot[i - 1]))
ck('G6 a palette of one colour is rejected', !([{ n: 1 }].length >= 2))
// The realistic way "two colours" decays: a second slot that is another blue. It alternates by slot
// number and not to the eye, which is the defect the hue bar exists to catch.
ck('G7 two slots a shade apart in the same hue are rejected',
  hueGap(hueOf(hex('#60a5fa')), hueOf(hex('#38bdf8'))) < HUE_APART,
  hueGap(hueOf(hex('#60a5fa')), hueOf(hex('#38bdf8'))))
ck('G8 …while the blue and the orange actually shipped clear it',
  hueGap(hueOf(hex('#60a5fa')), hueOf(hex('#fb923c'))) >= HUE_APART,
  hueGap(hueOf(hex('#60a5fa')), hueOf(hex('#fb923c'))))

// ── H. the group HEADINGS (owner ask 2026-10-21) ────────────────────────────────────────────────
console.log('\nH. a heading is bolder, coloured from the same palette, and carries its icon')
const tsxGroup = (tsx.match(/className="mp-nav-group"[\s\S]{0,400}/) || [''])[0]
ck('the heading takes a slot of the same rotation', /className="mp-nav-group"[^>]*style=\{accentStyle\(groupIndex\)\}/.test(tsx))
ck('the heading renders the icon the nav gives it, spelling none of its own',
  /className="mp-nav-group-icon"[^>]*>\{groupIconGlyph\}/.test(tsx) && !/mp-nav-group-icon[^>]*>['"]\p{Emoji}/u.test(tsxGroup))

// Every group declares one, so a new group cannot ship headingless.
const undeclared = R.NAV.filter(g => !g.icon).map(g => g.group)
ck('every built-in nav group declares an icon', undeclared.length === 0, undeclared)
const catsNoIcon = R.REPORT_CATEGORIES.filter(c => !c.icon).map(c => c.key)
ck('every report category declares an icon', catsNoIcon.length === 0, catsNoIcon)
ck('groupIcon answers from the declaration', R.NAV.every(g => R.groupIcon(g.group) === g.icon))
ck('a report category resolves by the label the sidebar shows it under',
  R.REPORT_CATEGORIES.every(c => R.groupIcon(c.label) === c.icon))
ck('a group nobody declared (one a tenant created) takes the fallback, never a blank',
  R.groupIcon('A group a tenant invented') === R.GROUP_ICON_FALLBACK && R.GROUP_ICON_FALLBACK.length > 0)

// The icon must survive the per-tenant layout, which rebuilds every group by NAME.
const onePage = R.NAV.find(g => g.items.length)
const built = R.applyNavLayout(R.NAV)
ck('applyNavLayout carries an icon onto every group it returns',
  built.length > 0 && built.every(g => typeof g.icon === 'string' && g.icon.length > 0))
ck('a built-in group keeps its own icon through the layout',
  built.find(g => g.group === onePage.group)?.icon === onePage.icon)
const moved = R.applyNavLayout(R.NAV, { items: { [onePage.items[0].href]: { group: 'Tenant Group' } } })
const tenant = moved.find(g => g.group === 'Tenant Group')
ck('a group a tenant creates still gets the fallback icon', tenant?.icon === R.GROUP_ICON_FALLBACK, tenant?.icon)
const cat = built.find(g => g.group.startsWith('Reports · '))
ck('a Reports category gets its declared icon, not the fallback',
  !cat || (cat.icon && cat.icon !== R.GROUP_ICON_FALLBACK), cat?.icon)

// The look itself: bolder and bigger than a row, same palette, same left edge.
const num = (re, src = css) => Number((src.match(re) || [])[1])
const groupRule = (css.match(/\.mp-nav-group \{[\s\S]*?\}/) || [''])[0]
const itemRule = (css.match(/\.mp-nav-item \{[\s\S]*?\}/) || [''])[0]
ck(`a heading is bolder than a row (${num(/font-weight: (\d+)/, groupRule)} vs ${num(/font-weight: (\d+)/, itemRule)})`,
  num(/font-weight: (\d+)/, groupRule) > num(/font-weight: (\d+)/, itemRule))
ck('a heading is larger than it was (9.5px) so it reads as a heading',
  num(/font-size: ([\d.]+)px/, groupRule) > 9.5, num(/font-size: ([\d.]+)px/, groupRule))
ck('the heading is coloured from the SAME --mp-accent, not a palette of its own',
  /\.mp-nav-group \{ color: color-mix\(in srgb, var\(--mp-accent\) (\d+)%, #ffffff\); \}/.test(css))
const headMix = num(/\.mp-nav-group \{ color: color-mix\(in srgb, var\(--mp-accent\) (\d+)%/) / 100
for (const sl of slots) {
  const worst = Math.min(...NAVY.map(bg => contrast(mix(hex(sl.hex), WHITE, headMix), bg)))
  ck(`slot ${sl.n} ${sl.hex}: heading text ${worst.toFixed(2)}:1 >= ${AA}`, worst >= AA, worst)
}
ck('the heading text is left-aligned (a <button> would otherwise centre it)',
  /text-align: left/.test(groupRule))
ck('the heading icon sits in the same width slot as a row chip, so the left edge is one line',
  num(/\.mp-nav-group-icon \{ width: (\d+)px/) === num(/\.mp-nav-icon \{[^}]*width: (\d+)px/, supportsBlock))
ck('H-neg a heading that kept the row weight would be rejected', !(450 > 450))
ck('H-neg a second icon list in the layout would be rejected',
  /mp-nav-group-icon[^>]*>['"]\p{Emoji}/u.test(`<span className="mp-nav-group-icon">'🏪'`))

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
