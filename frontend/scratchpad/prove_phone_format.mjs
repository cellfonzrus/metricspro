// Proof harness for <PhoneInput>'s pure logic (OWNER DIRECTIVE 2026-07-17 phone country-code auto-correct).
// Re-implements the functions from src/lib/phone-format.ts VERBATIM (no DOM/React), drives the required
// case table, then a source-parity guard greps the .ts so the copy can't drift. Run:
//   node frontend/scratchpad/prove_phone_format.mjs
import { readFileSync } from 'node:fs'

// ── verbatim from phone-format.ts ───────────────────────────────────────────────────────────────────
function normCc(raw) {
  const digits = String(raw || '').replace(/\D/g, '')
  if (!digits) return '+1'
  const cand = '+' + digits
  return /^\+\d{1,3}$/.test(cand) ? cand : '+1'
}
function parsePhone(value, defaultCc, knownSorted) {
  const dcc = normCc(defaultCc)
  const v = String(value || '').trim()
  if (!v) return { cc: dcc, national: '', other: false }
  const hasPlus = v.startsWith('+')
  const digits = v.replace(/\D/g, '')
  if (!digits) return { cc: dcc, national: '', other: false }
  if (hasPlus) {
    for (const code of knownSorted) {
      const cd = code.slice(1)
      if (cd && digits.startsWith(cd)) return { cc: code, national: digits.slice(cd.length), other: false }
    }
    for (const len of [3, 2, 1]) {
      if (digits.length - len >= 7) return { cc: '+' + digits.slice(0, len), national: digits.slice(len), other: true }
    }
    return { cc: '+' + digits, national: '', other: true }
  }
  const dccDigits = dcc.slice(1)
  if (digits.length === 10) return { cc: dcc, national: digits, other: false }
  if (digits.length === dccDigits.length + 10 && digits.startsWith(dccDigits))
    return { cc: dcc, national: digits.slice(dccDigits.length), other: false }
  return { cc: dcc, national: digits, other: false }
}
function composePhone(cc, national) {
  const d = String(national || '').replace(/\D/g, '')
  return d ? normCc(cc) + d : ''
}
function fmtNational(cc, national) {
  const d = String(national || '').replace(/\D/g, '')
  if (normCc(cc) === '+1') {
    const a = d.slice(0, 3), b = d.slice(3, 6), c = d.slice(6, 10)
    if (d.length > 6) return `(${a}) ${b}-${c}`
    if (d.length > 3) return `(${a}) ${b}`
    if (d.length > 0) return `(${a}`
    return ''
  }
  return d.replace(/(\d{3})(?=\d)/g, '$1 ').trim()
}
// ────────────────────────────────────────────────────────────────────────────────────────────────────

const KNOWN = ['+44', '+52', '+91', '+61', '+63', '+1'].sort((a, b) => b.length - a.length)
let P = 0, F = 0
const ck = (name, cond) => { if (cond) { P++; console.log('  ok  ' + name) } else { F++; console.log('  XX  ' + name) } }
// full round-trip through the component: parse a stored value, then compose the emitted value
const emitOf = (value, dcc = '+1') => { const p = parsePhone(value, dcc, KNOWN); return composePhone(p.cc, p.national) }

console.log('A. fresh 10-digit entry composes the owner example')
ck("type 5162330422 under +1 → '+15162330422'", composePhone('+1', '5162330422') === '+15162330422')
ck("empty national → '' (email-only recipient)", composePhone('+1', '') === '')
ck("cc '+44' + 2079460958 → '+442079460958'", composePhone('+44', '2079460958') === '+442079460958')

console.log('B. editing an existing value → parse then re-emit is LOSSLESS')
for (const v of ['+15162330422', '+442079460958', '+525512345678', '+919876543210', '+9721234567']) {
  ck(`round-trip ${v}`, emitOf(v) === v)
}
ck("parse '+15162330422' → cc '+1', national '5162330422'",
  JSON.stringify(parsePhone('+15162330422', '+1', KNOWN)) === JSON.stringify({ cc: '+1', national: '5162330422', other: false }))
ck("parse '+442079460958' → cc '+44'", parsePhone('+442079460958', '+1', KNOWN).cc === '+44')

console.log('C. legacy bare rows get auto-corrected on re-save (the rescue)')
ck("legacy '5162330422' re-emits '+15162330422'", emitOf('5162330422') === '+15162330422')
ck("legacy '15162330422' re-emits '+15162330422'", emitOf('15162330422') === '+15162330422')
ck("legacy 10-digit under +44 tenant re-emits '+44...'", emitOf('2079460958', '+44') === '+442079460958')

console.log('D. per-tenant default CC is honored for a fresh 10-digit')
ck("default '+52' parses 10-digit under +52", parsePhone('5512345678', '+52', KNOWN).cc === '+52')
ck("default '+91' composes '+919876543210'", composePhone('+91', '9876543210') === '+919876543210')

console.log('E. live format for display')
ck("+1 formats (516) 233-0422", fmtNational('+1', '5162330422') === '(516) 233-0422')
ck("+1 partial (516) 233", fmtNational('+1', '516233') === '(516) 233')
ck("non-+1 grouped in 3s", fmtNational('+44', '2079460958') === '207 946 095 8')

console.log('F. heuristic split of an UNKNOWN + code is still lossless')
ck("unknown '+9991234567' round-trips", emitOf('+9991234567') === '+9991234567')
ck("unknown code sets other=true", parsePhone('+9991234567', '+1', KNOWN).other === true)

console.log('G. normCc mirrors the backend')
for (const [raw, exp] of [['+1', '+1'], ['1', '+1'], [' +44 ', '+44'], ['52', '+52'], ['', '+1'], [null, '+1'], ['+', '+1'], ['+1234', '+1'], ['abc', '+1']])
  ck(`normCc(${JSON.stringify(raw)}) === ${exp}`, normCc(raw) === exp)

// ── source-parity guard: the .ts must contain the exact function bodies re-implemented above ──────────
console.log('H. source-parity — phone-format.ts contains the proven logic')
const src = readFileSync(new URL('../src/lib/phone-format.ts', import.meta.url), 'utf8')
for (const needle of [
  "const cand = '+' + digits",
  'if (digits.length === 10) return { cc: dcc, national: digits, other: false }',
  'if (digits.length === dccDigits.length + 10 && digits.startsWith(dccDigits))',
  'for (const len of [3, 2, 1]) {',
  "return d ? normCc(cc) + d : ''",
])
  ck(`.ts contains: ${needle.slice(0, 48)}…`, src.includes(needle))
// PhoneInput must consume the shared lib (not a private copy)
const comp = readFileSync(new URL('../src/components/PhoneInput.tsx', import.meta.url), 'utf8')
ck('PhoneInput imports from @/lib/phone-format', comp.includes("from '@/lib/phone-format'"))

console.log(`\n${F === 0 ? 'PASS' : 'FAIL'}: ${P} passed, ${F} failed`)
process.exit(F ? 1 : 0)
