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
check('C4 a keyword is a colour only directly after a property COLON, never in a type or a comparison',
  P.sites([join(P.SRC, 'app', '(platform)', 'admin', 'control-box', 'page.tsx')])
    .every(s => !s.quoted))
check('C5 …and a keyword that IS a colour value is still seen',
  P.sites([join(P.SRC, 'app', 'login', 'page.tsx')]).length > 0
  && P.NAMED.white === '#ffffff' && P.NAMED.crimson === '#dc143c')

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

console.log(`\n${pass} passed, ${fails.length} failed`)
if (fails.length) { fails.forEach(f => console.log('  - ' + f)); process.exit(1) }
console.log(`OK — ${lits.length} colours with one home, ${refs.size} dereferenced, ${amb.length} pinned.`)
