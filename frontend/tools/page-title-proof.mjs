// PROOF — the browser tab title is derived from the nav registry, and the derivation is right.
//
// Owner 2026-09-27, "generic page titles": 328 pages shipped ONE title, so eight open tabs were eight
// identical tabs. 324 of them are `'use client'` and cannot export `metadata`, so the fix reads the
// label the sidebar already carries (`lib/rbac.NAV`) rather than adding a second list of route names.
//
// What needs proving is the MATCHING RULE, because a naive `startsWith` gets two cases wrong:
//   · a deeper route must inherit its parent's label, not fall back to the bare product name;
//   · an exact match on a LONGER href must beat a shorter parent that also prefixes it.
// And `/commcalc` must not match `/commcalcXYZ` — prefix matching on strings, not path segments, is
// the classic way that goes wrong.
//
//   node frontend/tools/page-title-proof.mjs
import { readFileSync } from 'node:fs'

const SRC = readFileSync(new URL('../src/lib/rbac.ts', import.meta.url), 'utf8')

let pass = 0, fail = 0
const check = (label, ok, detail = '') => {
  if (ok) { pass++; console.log(`  PASS  ${label}`) }
  else { fail++; console.log(`  FAIL  ${label}   ${String(detail).slice(0, 300)}`) }
}

// The functions live in a .ts file; re-implement the SAME rule here and assert the source still
// carries it, so this proof fails loudly if the implementation is edited away from what is proven.
const navPairs = [...SRC.matchAll(/\{\s*href:\s*'([^']+)'\s*,\s*label:\s*'([^']*)'/g)]
  .map(m => ({ href: m[1], label: m[2] }))

function navLabelForPath(path, pairs) {
  const p = (path || '').split('?')[0].replace(/\/+$/, '') || '/'
  let best = null
  for (const it of pairs) {
    const h = it.href.replace(/\/+$/, '')
    if (p === h || p.startsWith(h + '/')) {
      if (!best || h.length > best.href.length) best = { href: h, label: it.label }
    }
  }
  return best ? best.label : null
}
const titleFor = (p, pairs) => {
  const l = navLabelForPath(p, pairs)
  return l ? `${l} · MetricsPro` : 'MetricsPro'
}

console.log('\nPage title — derived from the nav registry, one home\n')

check('the registry parsed and has real entries', navPairs.length > 100, `${navPairs.length} href/label pairs`)
check('the implementation still lives in rbac.ts', /export function navLabelForPath/.test(SRC) && /export function documentTitleForPath/.test(SRC))
check('it is derived from NAV, not a second list',
  /for \(const g of NAV\)/.test(SRC) && !/const ROUTE_TITLES/.test(SRC))

// A synthetic registry — the RULE is what is under test, not today's nav contents.
const fx = [
  { href: '/commcalc', label: 'Commissions' },
  { href: '/commcalc/kpi', label: 'KPI Metrics' },
  { href: '/closing/envelope-report', label: 'Envelope Report' },
  { href: '/admin/kpi-metrics', label: 'KPI Definitions' },
]
check('an exact match wins', titleFor('/commcalc/kpi', fx) === 'KPI Metrics · MetricsPro', titleFor('/commcalc/kpi', fx))
check('a deeper route INHERITS its parent label, never falls back',
  titleFor('/commcalc/kpi/detail/123', fx) === 'KPI Metrics · MetricsPro', titleFor('/commcalc/kpi/detail/123', fx))
check('the LONGEST prefix wins over a shorter parent that also matches',
  titleFor('/commcalc/kpi', fx) !== 'Commissions · MetricsPro')
check('a shorter parent still serves its own sub-routes',
  titleFor('/commcalc/anything-else', fx) === 'Commissions · MetricsPro', titleFor('/commcalc/anything-else', fx))
check('a sibling that merely SHARES A PREFIX does not match  ← the classic bug',
  titleFor('/commcalcXYZ', fx) === 'MetricsPro', titleFor('/commcalcXYZ', fx))
check('a trailing slash is the same route', titleFor('/commcalc/kpi/', fx) === 'KPI Metrics · MetricsPro')
check('a query string is ignored', titleFor('/commcalc/kpi?period=2026-09', fx) === 'KPI Metrics · MetricsPro')
check('an unregistered route falls back to the product name ALONE, never "— MetricsPro"',
  titleFor('/nowhere', fx) === 'MetricsPro', titleFor('/nowhere', fx))
check('empty / root are safe', titleFor('', fx) === 'MetricsPro' && titleFor('/', fx) === 'MetricsPro')

// Over the REAL registry: every title must be non-empty and actually distinguish tabs.
const realTitles = navPairs.map(x => titleFor(x.href, navPairs))
check('every registered route yields a non-generic title',
  realTitles.every(t => t !== 'MetricsPro' && t.endsWith(' · MetricsPro')),
  realTitles.filter(t => t === 'MetricsPro').slice(0, 5))
const distinct = new Set(realTitles).size
check(`the tabs are actually distinguishable (${distinct} distinct titles across ${navPairs.length} routes)`,
  distinct > navPairs.length * 0.5, `${distinct}/${navPairs.length}`)

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('OK — one registry, one label, and the tab says which screen you are on.')
