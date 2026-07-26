// RENDER proof for the shipped plan-match UI (no browser available in this environment, so this is the
// closest thing to opening the page): it transpiles the REAL
// src/app/(platform)/commcalc/_lib/planMatch.tsx with the TypeScript compiler, stubs only the shared
// EntityPicker (owned by platform-core) and renders the components with react-dom/server.
//
// What it proves that `tsc --noEmit` cannot: the guards actually EMIT their text, the picker passes the
// right props (multi for op 'in', allowCreate only where free entry is legitimate, every saved value
// present as an option), and the zero-wipe/"not in current data" hint really reaches the DOM.
// Run:  node frontend/scratchpad/prove_plan_match_render.cjs
const fs = require('node:fs')
const path = require('node:path')
const Module = require('node:module')
const ts = require('typescript')
const React = require('react')
const { renderToStaticMarkup } = require('react-dom/server')

let PASS = 0, FAIL = 0
const ok = (name, cond, extra = '') => {
  if (cond) { PASS++; console.log(`  ok  ${name}`) }
  else { FAIL++; console.log(`FAIL  ${name}   ${extra}`) }
}

// ── transpile the real .tsx and load it with a stubbed EntityPicker ──────────────────────────────
const SRC = path.join(__dirname, '..', 'src', 'app', '(platform)', 'commcalc', '_lib', 'planMatch.tsx')
const js = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  fileName: SRC,
}).outputText

// the shared picker is platform-core's; stub it so its props are OBSERVABLE (that is what we assert)
const pickerCalls = []
const stub = {
  __esModule: true,
  default: (props) => {
    pickerCalls.push(props)
    return React.createElement('div', {
      'data-picker': '1',
      'data-multi': String(!!props.multi),
      'data-allow-create': String(!!props.allowCreate),
      'data-disabled': String(!!props.disabled),
      'data-value': Array.isArray(props.value) ? props.value.join(',') : (props.value ?? ''),
      'data-options': (props.options || []).map(o => `${o.label}${o.sublabel ? ` [${o.sublabel}]` : ''}`).join(' | '),
    }, props.placeholder || '')
  },
}
const origLoad = Module._load
Module._load = function (req, parent, isMain) {
  if (req === '@/components/EntityPicker') return stub
  return origLoad.call(this, req, parent, isMain)
}
const mod = new Module(SRC, null)
mod.filename = SRC
mod.paths = Module._nodeModulePaths(path.dirname(SRC))
mod._compile(js, SRC)
Module._load = origLoad
const PM = mod.exports

// ── fixtures: the owner's real shape (two overlapping Home-Internet patterns) ────────────────────
const COLS = ['department', 'category', 'contract_type', 'tender_type', 'trans_type', 'product_desc', 'sku']
const opts = {
  ready: true,
  vocab: PM.FALLBACK_VOCAB,
  window: { months: 3, labels: ['July 2026', 'June 2026', 'May 2026'] },
  source: 'rpc', source_table: 'raw_sales',
  fields: {
    category: { values: [{ value: 'Home Internet', lines: 8 }, { value: 'Cases', lines: 7 }], truncated: false, free_text: false },
    product_desc: {
      values: [{ value: 'Home Internet Gateway', lines: 5 }, { value: 'VHI Home Internet Router', lines: 3 }],
      truncated: false, free_text: true,
    },
    activation_bucket: { values: [{ value: 'premium' }, { value: 'upgrade' }, { value: 'byod' }], closed: true },
    any: { values: [], closed: true },
  },
  facets: {
    columns: COLS,
    dict: {
      department: ['Internet', 'Accessories'], category: ['Home Internet', 'Cases'],
      contract_type: ['New Activation', ''], tender_type: ['', 'Acima'], trans_type: ['Sale'],
      product_desc: ['Home Internet Gateway', 'VHI Home Internet Router', 'Otterbox Case'], sku: [''],
    },
    ct_resolved: null,
    rows: [[0, 0, 0, 0, 0, 0, 0, 5], [0, 0, 0, 0, 0, 1, 0, 3], [1, 1, 1, 1, 0, 2, 0, 7]],
    truncated: false, lines_covered: 15, lines_total: 15, combos_total: 3,
  },
  periods: [{ value: 'July 2026', lines: 15 }],
}
const rules = [
  { match_field: 'product_desc', match_op: 'contains', match_value: 'home internet', label: 'Home Internet', qualifies: true },
  { match_field: 'product_desc', match_op: 'contains', match_value: 'vhi', label: 'VHI', qualifies: true },
  { match_field: 'category', match_op: 'equals', match_value: 'Nonexistent', label: 'Typo rule', qualifies: true },
  { match_field: 'activation_bucket', match_op: 'equals', match_value: 'premium', label: 'Premium', qualifies: true },
]

// the stats hook is a useMemo — safe to drive through a component render
function Harness({ index }) {
  const stats = PM.usePlanMatchStats(opts, rules)
  return React.createElement(PM.MatchWarnings, { opts, rules, stats, index })
}

console.log('── the two guards actually render ──')
const h0 = renderToStaticMarkup(React.createElement(Harness, { index: 0 }))
ok('rule 1 shows its matched-line count', h0.includes('8 lines in the last 3 months'), h0)
ok('rule 1 warns about the OVERLAP with rule 2 (the double-pay guard)',
  h0.includes('3 of them also match “VHI”') && h0.includes('both rules pay on those lines'), h0)
const h2 = renderToStaticMarkup(React.createElement(Harness, { index: 2 }))
ok('the typo rule renders the dead-rule warning',
  h2.includes('matches nothing in the last 3 months'), h2)
ok('the dead rule shows NO overlap line', !h2.includes('also match'), h2)
const h3 = renderToStaticMarkup(React.createElement(Harness, { index: 3 }))
ok('a synthetic field says so instead of inventing a count',
  h3.includes('classified per line at calculation time'), h3)

console.log('── the value picker gets the right props ──')
const eq = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts, field: 'category', op: 'equals', value: 'Cases', onChange: () => { },
}))
ok('equals on a complete list = single picker, NO free entry',
  eq.includes('data-multi="false"') && eq.includes('data-allow-create="false"'), eq)
ok('the options carry the line counts', eq.includes('Home Internet [8 lines]'), eq)
const cont = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts, field: 'product_desc', op: 'contains', value: 'home internet', onChange: () => { },
}))
ok("contains = free entry ALLOWED (a substring isn't a value)", cont.includes('data-allow-create="true"'), cont)
const inop = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts, field: 'category', op: 'in', value: 'Cases, Home Internet', onChange: () => { },
}))
ok("op 'in' renders a MULTI picker with both values selected",
  inop.includes('data-multi="true"') && inop.includes('data-value="Cases,Home Internet"'), inop)
const anyf = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts, field: 'any', op: 'equals', value: '', onChange: () => { },
}))
ok("field 'any' disables the value box", anyf.includes('data-disabled="true"'), anyf)
const zero = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts, field: 'category', op: 'equals', value: 'Retired Category', onChange: () => { },
}))
ok('ZERO-WIPE: a saved value no longer in the data still renders, flagged',
  zero.includes('Retired Category [not in current data]') && zero.includes('data-value="Retired Category"'), zero)
const empty = renderToStaticMarkup(React.createElement(PM.MatchValuePicker, {
  opts: { ...opts, fields: { ...opts.fields, category: { values: [], truncated: false } } },
  field: 'category', op: 'equals', value: '', onChange: () => { },
}))
ok('a tenant with NO observed values is never locked out (free entry re-enabled)',
  empty.includes('data-allow-create="true"'), empty)

console.log('── provenance strip ──')
const note = renderToStaticMarkup(React.createElement(PM.OptionsSourceNote, { opts }))
ok('names the source + the window', note.includes('monthly sales') && note.includes('July 2026, June 2026, May 2026'), note)
const degraded = renderToStaticMarkup(React.createElement(PM.OptionsSourceNote, {
  opts: { ...opts, note: "This tenant's sales could not be read right now" },
}))
ok('an unreadable source is stated, not hidden', degraded.includes('could not be read'), degraded)

console.log()
console.log(`${PASS} passed, ${FAIL} failed`)
process.exit(FAIL ? 1 : 0)
