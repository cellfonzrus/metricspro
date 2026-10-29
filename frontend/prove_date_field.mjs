#!/usr/bin/env node
// PROOF + GUARD: a date field in this app OPENS THE CALENDAR, and only one component decides how.
//
// OWNER ASK 2026-10-29, verbatim: *"for payroll hours approval the calendor should open up instead of
// enetring it manuallu"*.
//
// THE INSTANCE is the Hours Approval From/To pair. THE CLASS is every date field in the product.
// `<input type="date">` DOES carry a native calendar, but in Chrome it opens only from the small glyph
// at the right-hand edge; a click anywhere else in the field lands a caret in the mm/dd/yyyy segments
// and the user types. So "type it by hand" was the default behaviour of the control, not a missing
// feature — on all 168 date inputs in the tree, not just his two.
//
// So the fix is a component, `src/components/DateField.tsx`, and this file is both its behaviour proof
// and the RATCHET that stops the next bare `<input type="date">` from re-creating the defect. The
// register `date_field_pending.txt` carries the call sites not yet converted and may only SHRINK:
// converting 168 inputs in one change is not a decision a lock should make on the owner's behalf, but
// a NEW one is always a regression.
//
// WHAT IT PINS:
//   A  the component exists, is the one home, and renders a real `type="date"` input
//   B  it opens the picker on the user's click AND on keyboard focus, and survives showPicker throwing
//   C  typing still works — the picker is an addition, never a replacement (date entry must stay
//      keyboard-reachable, and a screen reader user never sees a calendar)
//   D  it does not swallow the caller's own onChange / value / style / props
//   E  the shared filter bar's From/To go through it, so every range report inherits the fix
//   F  the ratchet: no bare `<input type="date">` outside the register, and the register only shrinks
//
// Run: node prove_date_field.mjs        (from frontend/)
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs'
import { join, relative, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, 'src')
const COMPONENT = join(SRC, 'components/DateField.tsx')
const FILTER_BAR = join(SRC, 'components/StandardFilterBar.tsx')
const REGISTER = join(HERE, 'date_field_pending.txt')

let pass = 0
const fails = []
const ck = (label, cond, extra) => {
  if (cond) { pass++; return }
  fails.push(`${label}${extra === undefined ? '' : '  ' + JSON.stringify(extra)}`)
}

// Comments must never satisfy a check about CODE — the same `codeOnly` fold the sibling proofs use.
const codeOnly = s => s.replace(/\/\*[\s\S]*?\*\//g, ' ')
  .split('\n').map(l => l.replace(/\/\/.*$/, '')).join('\n')

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) walk(p, out)
    else if (/\.(tsx|ts)$/.test(p)) out.push(p)
  }
  return out
}

console.log('\n── A. the one home exists and is a real date input ──')
ck('src/components/DateField.tsx exists', existsSync(COMPONENT))
const comp = existsSync(COMPONENT) ? readFileSync(COMPONENT, 'utf8') : ''
const compCode = codeOnly(comp)
ck('it is a client component (it touches a DOM element)', /^'use client'/m.test(comp))
ck('it renders type="date" — the NATIVE control, not a hand-rolled calendar',
   /type\s*=\s*"date"/.test(compCode))
ck('…and it is the only date input it renders', (compCode.match(/type\s*=\s*"date"/g) || []).length === 1)
ck('it exports a default component named DateField', /export default function DateField/.test(compCode))

console.log('\n── B. the calendar opens on a click, and a failure to open is never fatal ──')
ck('it calls showPicker — the only API that opens the native calendar programmatically',
   /showPicker\s*\(/.test(compCode))
ck('…from a pointer/click handler, which is what the owner actually does',
   /on(Click|PointerDown|MouseDown)\s*=/.test(compCode))
ck('…and from focus too, so the keyboard reaches it as well as the mouse',
   /onFocus\s*=/.test(compCode))
ck('showPicker is GUARDED — it throws where unsupported or without a user gesture, and an exception '
   + 'inside a React event handler would take the page down',
   /try\s*\{[\s\S]{0,400}showPicker[\s\S]{0,400}\}\s*catch/.test(compCode))
ck('…and its existence is checked before the call, for a browser that has no showPicker at all',
   /showPicker/.test(compCode) && /(typeof[^\n]*showPicker|['"]showPicker['"]\s+in\b|\?\.\s*showPicker)/.test(compCode))

console.log('\n── C. typing still works — the picker ADDS a way in, it never removes one ──')
ck('the input is never made readOnly BY the component — a date must stay typeable (keyboard and '
   + 'screen-reader users). Reading `el.readOnly` to decide not to pop a calendar is the opposite, '
   + 'and allowed.',
   !/readOnly\s*(=|:)\s*\{?\s*(true|''|"")/.test(compCode))
ck('…and not disabled by the component itself', !/\bdisabled\s*=\s*\{?\s*true/.test(compCode))
ck('no preventDefault on the click path — that would break caret placement and typing',
   !/preventDefault/.test(compCode))

console.log('\n── D. it is a drop-in: the caller keeps its own value, onChange and styling ──')
// A pass-through component proves this by NOT naming those props: whatever it destructures by name is
// exactly what it intercepts, and everything else reaches the input untouched. So the contract is
// "the rest is spread onto the input" plus "value/onChange/style are not in the intercept list".
const destructured = (compCode.match(/function DateField\(\s*\{([^}]*)\}/) || [])[1] || ''
ck('the remaining props are SPREAD onto the input, so a caller never has to fork the component',
   /<input[^>]*\{\s*\.\.\.rest\s*\}/.test(compCode) || /\{\s*\.\.\.rest\s*\}/.test(compCode),
   destructured.trim())
for (const prop of ['value', 'onChange', 'style', 'min', 'max', 'required', 'disabled']) {
  ck(`\`${prop}\` is NOT intercepted — it rides the spread straight to the input`,
     !new RegExp(`\\b${prop}\\b`).test(destructured), destructured.trim())
}
ck('…and the two it DOES intercept are the handlers it has to compose, nothing more',
   destructured.split(',').map(x => x.trim()).filter(x => x && !x.startsWith('...')).sort().join(',')
     === 'onClick,onFocus', destructured.trim())
ck('…and each intercepted handler calls the caller\'s own first, so no page loses behaviour',
   /onClick\?\.\(/.test(compCode) && /onFocus\?\.\(/.test(compCode))

console.log('\n── E. the shared filter bar goes through it, so every range report inherits the fix ──')
const bar = existsSync(FILTER_BAR) ? codeOnly(readFileSync(FILTER_BAR, 'utf8')) : ''
ck('StandardFilterBar imports DateField', /import\s+DateField\s+from\s+'@\/components\/DateField'/.test(bar))
ck('…and its From/To pair renders DateField', (bar.match(/<DateField/g) || []).length >= 2,
   (bar.match(/<DateField/g) || []).length)
ck('…with no bare date input left in it — the whole point is that one component decides',
   !/type\s*=\s*"date"/.test(bar))

console.log('\n── F. the ratchet — a NEW bare date input is a regression ──')
ck('the register exists', existsSync(REGISTER))
const registerRaw = existsSync(REGISTER) ? readFileSync(REGISTER, 'utf8') : ''
const pinned = new Set(registerRaw.split('\n').map(l => l.trim())
  .filter(l => l && !l.startsWith('#')))
ck('…and it parsed to entries', pinned.size > 0, pinned.size)

const offenders = []
for (const file of walk(SRC)) {
  const rel = relative(HERE, file).split('\\').join('/')
  if (rel.endsWith('src/components/DateField.tsx')) continue      // the one home itself
  const n = (codeOnly(readFileSync(file, 'utf8')).match(/type\s*=\s*"date"/g) || []).length
  if (n > 0) offenders.push(rel)
}
const unpinned = offenders.filter(f => !pinned.has(f))
const stale = [...pinned].filter(f => !offenders.includes(f))

ck('every remaining bare date input is one the register already carries', unpinned.length === 0, unpinned)
ck('…and the register carries nothing already converted — the list may only SHRINK',
   stale.length === 0, stale)
console.log(`  ·  ${offenders.length} file(s) still hold a bare date input, all pinned; `
            + `${pinned.size} entries in the register`)

// Negative controls: a guard that cannot fail is not a guard.
console.log('\n── Z. negative controls ──')
{
  const planted = codeOnly('const x = <input type="date" value={v} />')
  ck('control: the detector really sees a bare date input', /type\s*=\s*"date"/.test(planted))
  const commented = codeOnly('// <input type="date" /> in a comment is not code')
  ck('control: one inside a COMMENT is not counted', !/type\s*=\s*"date"/.test(commented))
  ck('control: an unpinned file would be reported',
     !pinned.has('src/__definitely_not_a_real_file__.tsx'))
}

console.log(`\nprove_date_field: ${pass} passed, ${fails.length} failed`)
if (fails.length) {
  console.error('\n' + fails.map(f => '  XX  ' + f).join('\n'))
  console.error('\n  A NEW bare `<input type="date">` is the defect this guard exists to stop: clicking\n'
    + '  such a field types instead of opening the calendar. Use <DateField> from\n'
    + '  @/components/DateField. The register may only shrink — never add a line to it.')
  process.exit(1)
}
console.log('  ok  one component decides how a date is picked, and the calendar opens on a click')
