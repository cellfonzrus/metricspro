// THE DARK-THEME LOCK (index §CORE-15). Owner 2026-10-21: "convert to black looks very impressive".
//
// A theme is the easiest thing in a codebase to half-do: one page spells `background: '#fff'`, the
// stylesheet cannot reach an inline style, and that page is a white rectangle on a black app forever.
// So this fails the build when the one home stops being the one home — when globals.css drifts from
// the generator, when a page spells a colour of its own, when a token is defined for one theme only,
// or when a colour arrives on the dark theme that nobody could read.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import * as P from './theme_palette.mjs'

let pass = 0
const fails = []
const check = (name, ok, detail) => {
  if (ok) { pass++; console.log('  ok  ' + name) }
  else { fails.push(name); console.log('  FAIL ' + name + (detail === undefined ? '' : '  → ' + JSON.stringify(detail))) }
}

const css = readFileSync(P.CSS, 'utf8')
const files = P.sourceFiles()
const lits = P.literals(files)
const allSites = P.sites(files)

// ——— §A the one home —————————————————————————————————————————————————————————————————————————
console.log('§A one home')
check('A1 globals.css carries exactly what theme_palette.mjs generates (no hand-edit, no drift)',
  P.render(css, lits) === css)
check('A2 the generated block is produced from the CALLERS, so it cannot go stale', lits.length > 150, lits.length)
const lightBlock = css.slice(css.indexOf(P.BEGIN), css.indexOf(':root[data-theme="dark"] {', css.indexOf(P.BEGIN)))
check('A3 every light value is its own literal — the light theme cannot drift',
  lits.every(k => lightBlock.includes(`  --c-${k}: ${P.split(k)[1]};`)))
const darkBlock = css.slice(css.indexOf(':root[data-theme="dark"] {', css.indexOf(P.BEGIN)), css.indexOf(P.END))
check('A4 every token is defined for the dark theme too — never one theme only',
  lits.every(k => darkBlock.includes(`  --c-${k}: `)))

// ——— §B the callers dereference ——————————————————————————————————————————————————————————————
console.log('§B the callers dereference')
const converted = allSites.filter(s => s.role)
check('B1 no colour literal remains on a property whose role is declared', converted.length === 0, converted.length)
const refs = new Set()
for (const f of files) for (const m of readFileSync(f, 'utf8').matchAll(P.TOKEN_REF)) refs.add(m[1])
check('B2 every token a page reads is defined', [...refs].every(r => lits.includes(r)),
  [...refs].filter(r => !lits.includes(r)).slice(0, 5))
check('B3 no token is defined that nothing reads (a dead colour is a colour that will drift)',
  lits.every(k => refs.has(k)), lits.filter(k => !refs.has(k)).slice(0, 5))

// ——— §C the role is declared, never guessed ——————————————————————————————————————————————————
console.log('§C the role is declared, never guessed')
const amb = P.ambiguous(files)
const PIN = Number(readFileSync(join(P.HERE, 'theme_ambiguous_pin.txt'), 'utf8').trim())
check('C1 the call sites whose property names no role are PINNED and the pin may only shrink',
  amb.length <= PIN, { now: amb.length, pin: PIN })
check('C2 …and the pin is the real number, so it cannot be padded', amb.length === PIN, { now: amb.length, pin: PIN })
check('C3 the two role lists share no name (a property cannot be both)',
  !P.FG_PROPS.some(n => P.BG_PROPS.includes(n)))
// The regression: `c.lamp !== 'green'` and `type Lamp = 'green'` are a STATUS and a type. Converting
// them changed what the page compared, and the type checker caught it — so the keyword rule is strict.
check('C4 a keyword is a colour only where it is the VALUE of a colour property, never in a type or a comparison',
  P.sites([join(P.SRC, 'app', '(platform)', 'admin', 'control-box', 'page.tsx')])
    .every(s => !s.quoted))
check('C5 …and a keyword that IS a colour value is still seen, wherever in the value it sits',
  P.roleOf(P.propOfValue("style={{ background: 'white' }}", 22)) === 'bg'
  && P.NAMED.white === '#ffffff' && P.NAMED.crimson === '#dc143c')

// ——— §C′ THE REGRESSION (index §CORE-37) ————————————————————————————————————————————————————————
// 2026-11-12: every other row of the Finance Dashboard expense grid was a white band with no label
// in it. The row said `background: ci % 2 ? 'var(--c-bg-fafbfc)' : 'white'` — the FIRST branch a
// token, the second a literal this generator could not see, because role was read by looking
// BACKWARDS from the literal and what lies between a literal and its property is an expression.
// These assert the rule that replaced it, on the exact shapes that escaped.
console.log("§C' the ternary branch that hid the white rows")
const roleIn = (line, needle) => P.roleOf(P.propOfValue(line, line.indexOf(needle)))
const STRIPE = "<tr style={{ background: ci % 2 ? 'var(--c-bg-fafbfc)' : 'white' }}>"
check("C'1 the SECOND branch of a striped row is a background — the exact line that shipped white rows",
  roleIn(STRIPE, "'white'") === 'bg')
check("C'2 a hex behind a comparison is a background, not an ambiguous site",
  roleIn("style={{ background: f === s.store ? '#eff6ff' : 'white' }}", '#eff6ff') === 'bg')
check("C'3 a value that wraps over lines keeps its property",
  roleIn("style={{\n  background: active\n    ? 'var(--accent)'\n    : '#fef2f2',\n}}", '#fef2f2') === 'bg')
check("C'4 the SIBLING property is not blamed — a white letter on an accent button stays a letter",
  roleIn("style={{ background: 'var(--accent)', color: '#ffffff' }}", '#ffffff') === 'fg')
check("C'5 a nested call inside the value does not end it",
  roleIn("style={{ background: shade(row, i % 2 ? 'var(--surface2)' : '#fafbfc') }}", '#fafbfc') === 'bg')
check("C'6 a colour in a STATUS MAP is still not claimed as a CSS property",
  P.roleOf(P.propOfValue("const TONE = { cancelled: '#fee2e2' }", 25)) === null)
check("C'7 …and the old backwards rule really did miss it, so this is a fix and not a no-op",
  (() => {
    const before = STRIPE.slice(0, STRIPE.indexOf("'white'"))
    return !/([A-Za-z][A-Za-z0-9_-]*)\s*:\s*$/.test(before)
  })())

// ——— §D readable on black ————————————————————————————————————————————————————————————————————
console.log('§D readable on black')
const lum = h => { const [r, g, b] = P.rgb(h).map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const contrast = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) }
const DARK_BG = '#000000', DARK_SURFACE = '#0d0e11'
const fgKeys = lits.filter(k => P.split(k)[0] === 'fg')
const dim = fgKeys.filter(k => {
  const d = P.darkCounterpart(P.split(k)[1], 'fg')
  return Math.min(contrast(d, DARK_BG), contrast(d, DARK_SURFACE)) < 4.5
})
check('D1 every lettering colour clears WCAG AA on the page AND on a card', dim.length === 0, dim.slice(0, 6))
const lifted = lits.filter(k => P.split(k)[0] === 'bg' && P.darkCounterpart(P.split(k)[1], 'bg') !== P.canon(P.split(k)[1]))
const glow = lifted.filter(k => contrast('#ffffff', P.darkCounterpart(P.split(k)[1], 'bg')) < 4.5)
check('D2 every fill the derivation MOVED can carry white lettering', glow.length === 0, glow.slice(0, 6))
const kept = lits.filter(k => P.split(k)[0] === 'bg' && P.darkCounterpart(P.split(k)[1], 'bg') === P.canon(P.split(k)[1]))
check('D3 …and every fill it LEFT ALONE was already dark, so nothing pale survives untouched',
  kept.every(k => P.toHsl(P.rgb(P.canon(P.split(k)[1])))[2] < 0.60),
  kept.filter(k => P.toHsl(P.rgb(P.canon(P.split(k)[1])))[2] >= 0.60).slice(0, 6))
check('D4 a black panel stays black rather than inverting to near-white',
  P.darkCounterpart('#000000', 'bg') === '#000000' && P.darkCounterpart('#111111', 'bg') === '#111111')
check('D5 white LETTERING on a coloured button is left exactly as it is',
  P.darkCounterpart('#ffffff', 'fg') === '#ffffff' && P.darkCounterpart('#fff', 'fg') === '#ffffff')
check('D6 …while a white PANEL comes down to black', lum(P.darkCounterpart('#ffffff', 'bg')) < 0.02)

// ——— §E one home for the switch ——————————————————————————————————————————————————————————————
console.log('§E one home for the switch')
const theme = readFileSync(join(P.SRC, 'lib', 'theme.ts'), 'utf8')
check('E1 lib/theme.ts declares the attribute, the key, the themes and the default',
  ['THEME_ATTR', 'THEME_KEY', 'THEMES', 'DEFAULT_THEME', 'PRE_PAINT'].every(n => theme.includes(`export const ${n}`)))
check('E2 the default is dark, as the owner asked', /DEFAULT_THEME: Theme = 'dark'/.test(theme))
check('E3 light is reachable, so nobody is trapped in the dark app', theme.includes("'light'"))
const spellers = files.filter(f => f !== join(P.SRC, 'lib', 'theme.ts') &&
  /'data-theme'|"data-theme"|'mp-theme'/.test(readFileSync(f, 'utf8')))
check('E4 nothing but lib/theme.ts spells the attribute or the storage key',
  spellers.length === 0, spellers.map(f => f.replace(P.SRC, 'src')))
const root = readFileSync(join(P.SRC, 'app', 'layout.tsx'), 'utf8')
check('E5 the document carries the theme before the first paint (no white flash)',
  root.includes('PRE_PAINT') && root.includes('THEME_ATTR') && root.indexOf('PRE_PAINT') < root.indexOf('<body'))
const toggle = readFileSync(join(P.SRC, 'components', 'ThemeToggle.tsx'), 'utf8')
check('E6 the toggle dereferences that home and spells no colour of its own',
  toggle.includes("from '@/lib/theme'") && !/#[0-9a-fA-F]{3,6}\b/.test(toggle))
check('E7 the storage read survives a browser that refuses storage', /catch/.test(theme))

// ——— §F the dark theme redefines the SAME tokens —————————————————————————————————————————————
console.log('§F the dark theme redefines the SAME tokens')
const coreLight = [...css.slice(css.indexOf(':root {'), css.indexOf('}', css.indexOf(':root {'))).matchAll(/--([a-z0-9-]+):/g)].map(m => m[1])
const coreDark = [...css.slice(css.indexOf(':root[data-theme="dark"] {'), css.indexOf('}', css.indexOf(':root[data-theme="dark"] {'))).matchAll(/--([a-z0-9-]+):/g)].map(m => m[1])
check('F1 the dark theme names no token the light theme does not declare',
  coreDark.every(t => coreLight.includes(t)), coreDark.filter(t => !coreLight.includes(t)))
check('F2 the page, the card and the lettering are all restated', ['bg', 'surface', 'text'].every(t => coreDark.includes(t)))
check('F3 the rail is NOT restated — its accent contrast was measured against the navy (§CORE-14)',
  !css.includes(':root[data-theme="dark"] .mp-sidebar {'))

// ——— §G negative controls ————————————————————————————————————————————————————————————————————
console.log('§G negative controls')
check('G1 a hand-edited stylesheet is caught', P.render(css.replace('--c-bg-fef2f2: #fef2f2;', '--c-bg-fef2f2: #ff0000;'), lits) !== css.replace('--c-bg-fef2f2: #fef2f2;', '--c-bg-fef2f2: #ff0000;'))
check('G2 a new literal at a call site would be seen',
  P.roleOf('background') === 'bg' && P.roleOf('color') === 'fg' && P.roleOf('zIndex') === null)
check('G3 a pale fill that stayed pale would fail D3', P.toHsl(P.rgb('#fef2f2'))[2] >= 0.60)
check('G4 the contrast measure is real: black on black fails, white on black passes',
  contrast('#000000', '#000000') === 1 && contrast('#ffffff', '#000000') > 20)
check('G5 the derivation keeps the HUE — a red warning does not arrive blue',
  Math.abs(P.toHsl(P.rgb(P.darkCounterpart('#b91c1c', 'fg')))[0] - P.toHsl(P.rgb('#b91c1c'))[0]) < 1)

// ——— §H one home for a STRIPED ROW (index §CORE-37) —————————————————————————————————————————————
// Tokens alone do not finish the job the white rows exposed. 46 tables each wrote their own answer to
// "which colour is row 3", and the two shapes they used were `i % 2 ? 'var(--surface2)' : 'transparent'`
// and `… : 'white'` — one correct, one invisible, and nothing could tell them apart at a glance. The
// rule now lives in `lib/surface.rowStripe`, and the even row is an OPAQUE token rather than
// `transparent`, because a transparent row under a `position: sticky` first column shows the scrolled
// body through it. A table that hand-rolls the rule again fails here.
console.log('§H one home for a striped row')
const surface = readFileSync(join(P.SRC, 'lib', 'surface.ts'), 'utf8')
check('H1 lib/surface.ts declares the striping rule and the two surfaces',
  /export function rowStripe\(/.test(surface) && /export const SURFACE\b/.test(surface)
  && /export const SURFACE2\b/.test(surface))
check('H2 the one home spells no colour of its own — it answers with tokens',
  !/#[0-9a-fA-F]{3,6}\b/.test(surface.replace(/\/\*[\s\S]*?\*\/|\/\/.*/g, '')))
check('H3 the even row is opaque, so a sticky first column cannot show the body through it',
  !/return\s+'transparent'/.test(surface))
const STRIPE_IN_BG = /\b(?:background|backgroundColor)\s*:((?:[^,;\n}]|\n\s*[?:])*)/g
const handRolled = []
for (const f of files) {
  if (f === join(P.SRC, 'lib', 'surface.ts')) continue
  const src = readFileSync(f, 'utf8')
  for (const m of src.matchAll(STRIPE_IN_BG)) {
    if (!/%\s*2\b/.test(m[1])) continue
    handRolled.push(f.replace(P.SRC, 'src') + ':' + src.slice(0, m.index).split('\n').length)
  }
}
check('H4 no table hand-rolls its own stripe — they all call rowStripe()',
  handRolled.length === 0, handRolled.slice(0, 8))
check('H5 the tables that were converted really do dereference the home',
  files.filter(f => /rowStripe\(/.test(readFileSync(f, 'utf8'))).length >= 25,
  files.filter(f => /rowStripe\(/.test(readFileSync(f, 'utf8'))).length)
check('H6 NEGATIVE CONTROL: the planted shape that shipped the white rows is caught',
  [..."<tr style={{ background: ci % 2 ? 'var(--surface2)' : 'white' }}>".matchAll(STRIPE_IN_BG)]
    .some(m => /%\s*2\b/.test(m[1])))
check('H7 NEGATIVE CONTROL: a call through the home is NOT caught',
  [..."<tr style={{ background: rowStripe(ci) }}>".matchAll(STRIPE_IN_BG)]
    .every(m => !/%\s*2\b/.test(m[1])))

console.log(`\n${pass} passed, ${fails.length} failed`)
if (fails.length) { fails.forEach(f => console.log('  - ' + f)); process.exit(1) }
console.log(`OK — ${lits.length} colours with one home, ${refs.size} dereferenced, ${amb.length} pinned.`)
