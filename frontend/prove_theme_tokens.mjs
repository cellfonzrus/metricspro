// THE DARK-THEME LOCK (index §CORE-15). Owner 2026-10-21: "convert to black looks very impressive".
//
// A theme is the easiest thing in a codebase to half-do: one page spells `background: '#fff'`, the
// stylesheet cannot reach an inline style, and that page is a white rectangle on a black app forever.
// So this fails the build when the one home stops being the one home — when globals.css drifts from
// the generator, when a page spells a colour of its own, when a token is defined for one theme only,
// or when a colour arrives on the dark theme that nobody could read.
import { readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import * as P from './theme_palette.mjs'

let pass = 0
const PENDING_HEADER = `# TOKENS A PAGE READS THAT NOTHING DEFINES — the register §I holds to.
# Each line is a token and how many times it is read with NO fallback, so the read paints nothing.
# THE LIST MAY ONLY SHRINK. A new token fails the build; fix the reads, then re-pin with
#   cd frontend && node prove_theme_tokens.mjs --bless
# The house palette is in src/app/globals.css: --bg --surface --surface2 --border --border2
# --accent --accent2 --green --amber --red --text --text2 --text3. Most of these are near-misses
# of one of those (--text1 is --text, --bg2 is --surface2, --danger is --red).
`
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
// NAMED FOR WHAT IT CHECKS (2026-11-20). This read "every token a page reads is defined", which was
// wider than the check: `P.TOKEN_REF` only matches the GENERATED `--c-(bg|fg)-xxxxxx` family, so a
// token one letter outside that shape was never looked at — and one shipped, invisible, for two
// days. §I below now carries the general claim, over EVERY token and every source file.
check('B2 every GENERATED token a page reads is defined (the general claim is §I)',
  [...refs].every(r => lits.includes(r)),
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

// ——— §I EVERY TOKEN A PAGE READS RESOLVES — not only the generated ones ————————————————————————
// THE HOLE §B2 LEFT, found 2026-11-20 on a sales rep's phone. The screen showed the buttons and the
// input boxes as bare floating text: no borders, nothing to tap at. The page asked for
// `var(--c-border-e5e7eb)`, which NOTHING in this repository defines — one file used it and no
// stylesheet declared it — so `border: 1px solid var(--c-border-e5e7eb)` was invalid at computed
// value time and the browser dropped the border entirely.
//
// WHY EVERY EXISTING CHECK MISSED IT, which is the part worth keeping. This lock hunts colour
// LITERALS; an undefined token is invisible to it precisely because it is not a literal. §B2 does
// say "every token a page reads is defined" — but it reads through `P.TOKEN_REF`,
// `/--c-((?:bg|fg)-[0-9a-f]{6})\b/`, so it only ever saw the GENERATED family. A token one letter
// outside that shape was never looked at. The claim was wider than the check. So the fix is here,
// widening §B2's own promise, rather than in a second lock beside it.
//
// AND IT IS NOT ONE PAGE. Derived, not guessed: 9 tokens, 159 reads, across the app — `--text1` 59
// times (the palette's token is `--text`), `--bg2` 30, `--muted` 20. Every one of those is a colour
// that silently does not apply. They are REGISTERED in `theme_undefined_token_pending.txt` and the
// register may only SHRINK: a new one fails the build today, and the backlog is paid down
// deliberately rather than in one 80-file sweep nobody can review.
console.log('§I every token a page reads resolves')
// A token is DEFINED by a stylesheet (`--x:`) or at RUNTIME through a quoted key — which is how
// `--tone` arrives (`style={{ ['--tone']: … }}`) and how next/font publishes `--font-inter`
// (`Inter({ variable: '--font-inter' })`). Both are real definitions, so both count, and neither
// needs a hand-written excuse that would go stale.
const T_DEFN = /(--[A-Za-z0-9_-]+)\s*:/g
const T_QUOTED = /['"](--[A-Za-z0-9_-]+)['"]/g
// Only a read with NO FALLBACK can go blank: `var(--x, #fff)` paints `#fff` when `--x` is missing.
const T_READ = /var\(\s*(--[A-Za-z0-9_-]+)\s*\)/g
const noCssComments = t => t.replace(/\/\*[\s\S]*?\*\//g, '')
function tokenFiles(dir, out = []) {
  for (const e of readdirSync(dir)) {
    const q = join(dir, e)
    if (statSync(q).isDirectory()) tokenFiles(q, out)
    else if (/\.(tsx?|css)$/.test(e)) out.push(q)
  }
  return out
}
const defs = new Set()
const reads = new Map()
for (const f of tokenFiles(P.SRC)) {
  const t = noCssComments(readFileSync(f, 'utf8'))
  for (const m of t.matchAll(T_DEFN)) defs.add(m[1])
  for (const m of t.matchAll(T_QUOTED)) defs.add(m[1])
  for (const m of t.matchAll(T_READ)) reads.set(m[1], (reads.get(m[1]) || 0) + 1)
}
const undefined_ = [...reads].filter(([t]) => !defs.has(t)).sort((a, b) => a[0] < b[0] ? -1 : 1)
const PENDING = join(P.HERE, 'theme_undefined_token_pending.txt')
const registered = new Map(readFileSync(PENDING, 'utf8').split('\n')
  .filter(l => l.trim() && !l.startsWith('#'))
  .map(l => { const [t, n] = l.split('\t'); return [t.trim(), Number(n)] }))
if (process.argv.includes('--bless')) {
  // Re-pins the register after some were fixed. It can only ever write what the code now says, so
  // blessing cannot hide a NEW one — the check below runs first and the build is already red.
  writeFileSync(PENDING, PENDING_HEADER + undefined_.map(([t, n]) => `${t}\t${n}`).join('\n') + '\n')
  console.log('  blessed: ' + undefined_.length + ' token(s), ' +
              undefined_.reduce((a, [, n]) => a + n, 0) + ' read(s)')
}
const arrived = undefined_.filter(([t]) => !registered.has(t)).map(([t]) => t)
const grew = undefined_.filter(([t, n]) => registered.has(t) && n > registered.get(t))
            .map(([t, n]) => `${t} ${registered.get(t)}→${n}`)
const shrank = undefined_.filter(([t, n]) => registered.has(t) && n < registered.get(t))
            .map(([t, n]) => `${t} ${registered.get(t)}→${n}`)
const left = [...registered].filter(([t]) => !reads.has(t) || defs.has(t)).map(([t]) => t)
check('I1 no NEW undefined token — a colour that resolves to nothing paints nothing', !arrived.length, arrived)
check('I2 no registered token spread to more call sites', !grew.length, grew)
check('I3 the register is current — re-pin it with `node prove_theme_tokens.mjs --bless` when you fix some',
  !shrank.length && !left.length, { shrank, fixed: left })
console.log(`    ${undefined_.length} undefined token(s), ${undefined_.reduce((a, [, n]) => a + n, 0)} read(s) — the list may only shrink`)
check('I-G1 CONTROL a token named only inside a CSS comment is not a definition',
  !/(--[A-Za-z0-9_-]+)\s*:/.test(noCssComments('/* --ghost: red; */ a{color:blue}')))
check('I-G2 CONTROL a token set at runtime through a quoted key IS a definition',
  // A FRESH regex, not T_QUOTED: a /g regex carries `lastIndex` across `.test()` calls, so reusing
  // the one the scan just ran would make this control pass or fail on where that scan happened to
  // stop. A control that depends on state is not a control.
  /['"](--[A-Za-z0-9_-]+)['"]/.test("style={{ ['--tone']: x }}")
  && defs.has('--tone') && defs.has('--font-inter'))
check('I-G3 CONTROL a read WITH a fallback is not counted — it cannot go blank',
  [...'border: 1px solid var(--nope, #fff)'.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)\s*\)/g)].length === 0
  && [...'color: var(--nope)'.matchAll(/var\(\s*(--[A-Za-z0-9_-]+)\s*\)/g)].length === 1)
check('I-G4 CONTROL an unregistered undefined token would be caught',
  [...reads.keys(), '--a-token-nobody-defined'].filter(t => !defs.has(t) && !registered.has(t))
    .includes('--a-token-nobody-defined'))
check('I-G5 CONTROL the palette this app actually ships is seen as defined',
  ['--border', '--text', '--surface', '--bg', '--accent'].every(t => defs.has(t)))
check('I-G6 CONTROL the token that started this is fixed, and its house replacement resolves',
  !reads.has('--c-border-e5e7eb') && defs.has('--border'))

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
