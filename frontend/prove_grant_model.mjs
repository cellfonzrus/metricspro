// Proof harness — THE GRANT MODEL, frontend half (owner rulings #5 / #6 / #7, 2026-08-08).
//
// Reads the REAL source of `src/app/(platform)/admin/roles/page.tsx` and `src/lib/rbac.ts` and
// asserts the behaviour each ruling demands, including the things that must NOT be there any more.
// Anchor-based extraction: if the code moves, extraction throws and this fails loudly rather than
// quietly testing a stale copy.
//
//   #5  the store grant comes from the PICKER — the free-typed `home_store` fallback is GONE from
//       both write paths, the last bare `home_store` text input on this page is GONE, and a value
//       that names no real store is SHOWN rather than silently honoured.
//   #6  the market grant and the store grant are separately editable, separately clearable, and the
//       page says out loud when a market widens a store-scoped role. Nothing strips a market.
//   #7  rbac.ts carries the self-own-store ADOPTION REGISTRY with its paired payroll rule, and no
//       nav item's scope tier was widened to 'self' by this package.
//
// Run:  node frontend/prove_grant_model.mjs      (no network, no DB, no React)

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { execFileSync } from 'node:child_process'

const HERE = dirname(fileURLToPath(import.meta.url))
const PAGE_PATH = 'src/app/(platform)/admin/roles/page.tsx'
const RBAC_PATH = 'src/lib/rbac.ts'
const PAGE = readFileSync(join(HERE, PAGE_PATH), 'utf8')
const RBAC = readFileSync(join(HERE, RBAC_PATH), 'utf8')
const BASE = '88dcac9'
// execFileSync, NOT execSync: the roles page lives under `(platform)/`, and a shell reads those
// parentheses as a subshell — which made every negative control here silently return null the first
// time this ran. A negative control that quietly disappears is worse than none.
const gitShow = (p) => {
  try { return execFileSync('git', ['show', `${BASE}:frontend/${p}`], { cwd: HERE, encoding: 'utf8', maxBuffer: 1 << 26 }) }
  catch { return null }
}
const PAGE_BASE = gitShow(PAGE_PATH)
const RBAC_BASE = gitShow(RBAC_PATH)

let pass = 0, fail = 0
const ck = (label, cond, extra) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`, extra === undefined ? '' : extra) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

// ═════════════════════════════════════════════════════════════════════════════════════════════
console.log('\nA. #5 — the free-text door into a PERMISSION is closed')
{
  const i0 = PAGE.indexOf('async function assign(e: Emp')
  must(i0 > 0, 'anchor not found: async function assign(e: Emp')
  const i1 = PAGE.indexOf('// ---- per-employee widget overrides', i0)
  must(i1 > i0, 'anchor not found: end of assign()')
  const assignSrc = PAGE.slice(i0, i1)
  ck('assign() no longer falls back to the free-typed home_store for the store SET',
     !/store_codes.*\n?.*e\.home_store/.test(assignSrc) && !assignSrc.includes('(e.home_store ? [e.home_store] : [])'))
  ck('assign() no longer falls back to the free-typed home_store for the PRIMARY pin',
     !assignSrc.includes('codes[0] || e.home_store'))
  ck('the primary pin is now exactly the first PICKED store',
     assignSrc.includes('store_code: codes[0] || null'))
  ck('the full picked set is still sent (a person may hold several stores)',
     assignSrc.includes('store_codes: codes'))
  if (PAGE_BASE) {
    const b0 = PAGE_BASE.indexOf('async function assign(e: Emp')
    const bAssign = PAGE_BASE.slice(b0, PAGE_BASE.indexOf('// ---- per-employee widget overrides', b0))
    ck('[NEGATIVE CONTROL] the base DID have both home_store fallbacks',
       bAssign.includes('codes[0] || e.home_store') && bAssign.includes('(e.home_store ? [e.home_store] : [])'))
  } else { ck('[NEGATIVE CONTROL] base revision readable', false) }
}
{
  // The one remaining home_store WRITE control must be the picker, not an <input>.
  const inputs = [...PAGE.matchAll(/<input[^>]*home_store[^>]*>/g)].map(m => m[0])
  ck('no bare <input> writes home_store on this page any more', inputs.length === 0, inputs)
  ck('home store is edited through the shared StorePicker (single-select)',
     /<StorePicker\s+value=\{e\.home_store \? \[e\.home_store\] : \[\]\}[\s\S]*?single/.test(PAGE))
  if (PAGE_BASE) {
    ck('[NEGATIVE CONTROL] the base DID have a bare home_store text input',
       /<input[^>]*value=\{e\.home_store \|\| ''\}/.test(PAGE_BASE))
  }
  ck('the store picker offers MANY (checkbox) by default and ONE only when asked',
     PAGE.includes("type={single ? 'radio' : 'checkbox'}"))
  ck('an unresolvable value already on the record is shown, not dropped',
     PAGE.includes('invalid={!!e.home_store && !storeExists(e.home_store)}'))
  ck('the picker explains WHY a value is flagged',
     PAGE.includes('is not a store in this company'))
  ck('long rosters stay pickable (typeahead filter, not a scroll)',
     PAGE.includes('Type to filter…'))
}
{
  // Duplicate physical stores must collapse to ONE option.
  ck('the picker collapses two code vocabularies for one store into one option',
     PAGE.includes('store_groups') && PAGE.includes('roster_codes'))
  ck('the alternate spelling is disclosed on the option, not hidden',
     PAGE.includes('(also: '))
}

console.log('\nB. #6 — market and store are TWO grants, separately assignable')
{
  ck('the market picker can be cleared on its own', PAGE.includes('✕ Clear market grant'))
  ck('the store picker can be cleared on its own', PAGE.includes('✕ Clear store grant'))
  ck('clearing the market does not touch the store grant (separate onChange targets)',
     PAGE.includes("onChange={v => setEmp(e.id, { app_market: v })}")
     && PAGE.includes('onChange={codes => setEmp(e.id, { app_store_codes: codes, app_store: codes[0] || null })}'))
  ck('nothing in this page STRIPS a market automatically',
     !/setEmp\([^)]*app_market:\s*(null|'')/.test(PAGE.replace(/onChange=\{v => setEmp\(e\.id, \{ app_market: v \}\)\}/g, '')))
  ck('a store-scoped role holding a market is called out in the grid',
     PAGE.includes('market grant widens past'))
  ck('...and in the Access preview, with the store count',
     PAGE.includes('market_widens_beyond_store_scope'))
  ck('the Access preview attributes stores to the grant that produced them',
     PAGE.includes('<strong>Market grant</strong>') && PAGE.includes('<strong>Store grant</strong>'))
  ck('a market that grants nothing is flagged (the live `15` fragment)',
     PAGE.includes('not a market in this company'))
}
{
  // rbac.ts mirror of the model.
  ck('rbac.ts declares the two grant kinds', /export const GRANT_KINDS: GrantKind\[\] = \['market', 'store'\]/.test(RBAC))
  const i0 = RBAC.indexOf('export function grantWidening')
  must(i0 > 0, 'anchor not found: grantWidening')
  const fn = RBAC.slice(i0, RBAC.indexOf('\n}', i0) + 2)
  const grantWidening = new Function(`${fn.replace(/export function/, 'function')
    .replace(/:\s*\{ markets: string\[\]; marketStores: number; ownStores: number \} \| null/, '')
    .replace(/scope: Scope \| undefined, g: GrantBreakdown \| undefined/, 'scope, g')}\nreturn grantWidening`)()
  const G = { market: { granted: ['Chicago'], codes: ['a', 'b', 'c'] }, store: { granted: ['Diversey'], codes: ['Diversey'] } }
  ck('grantWidening fires for a STORE-scoped role holding a market',
     grantWidening('store', G)?.marketStores === 3)
  ck('grantWidening fires for a SELF-scoped role holding a market',
     grantWidening('self', G)?.marketStores === 3)
  ck('grantWidening is silent for a MARKET-scoped role (a market is what they are for)',
     grantWidening('market', G) === null)
  ck('grantWidening is silent for an ALL-scoped role', grantWidening('all', G) === null)
  ck('grantWidening is silent when there is no market grant',
     grantWidening('store', { market: { granted: [], codes: [] }, store: { codes: ['x'] } }) === null)
  ck('grantWidening never throws on missing data',
     grantWidening('store', undefined) === null && grantWidening(undefined, {}) === null)
}

console.log('\nC. #7 — own-store adoption registry, and the payroll guardrail')
{
  ck('rbac.ts carries the adoption registry', RBAC.includes('SELF_OWN_STORE_SURFACES'))
  const i0 = RBAC.indexOf('export const SELF_OWN_STORE_SURFACES')
  const reg = RBAC.slice(i0, RBAC.indexOf('\n]', i0))
  ck('the registry lists My Targets', reg.includes('/commcalc/targets/my'))
  ck('the registry lists My Commission', reg.includes('/commcalc/commissions'))
  ck('the registry is SMALL — this package widened two surfaces, not the app',
     (reg.match(/\{ path: '/g) || []).length === 2, (reg.match(/\{ path: '/g) || []).length)
  ck('the paired payroll rule is stated where a future agent will read it',
     /per-employee pay \/ commission \/ compensation \/ PII column/.test(RBAC)
     && /self_employee_ids\(\)/.test(RBAC))
  ck('selfSurfaceAdopted is prefix-safe', (() => {
    const i = RBAC.indexOf('export function selfSurfaceAdopted')
    const src = RBAC.slice(i, RBAC.indexOf('\n}', i) + 2)
    return src.includes("path.startsWith(s.path + '/')")
  })())
}
{
  // The load-bearing negative: no nav item gained 'self', and SELF_ALLOWED did not grow.
  const scopesOf = (src) => (src.match(/scopes: \[[^\]]*\]/g) || [])
  const selfScoped = scopesOf(RBAC).filter(s => s.includes("'self'"))
  ck('NO nav item was given scope tier \'self\' by this package', selfScoped.length === 0, selfScoped)
  if (RBAC_BASE) {
    const cur = (RBAC.match(/const SELF_ALLOWED = \[[^\]]*\]/) || [''])[0]
    const base = (RBAC_BASE.match(/const SELF_ALLOWED = \[[^\]]*\]/) || [''])[0]
    ck('SELF_ALLOWED is BYTE-IDENTICAL to origin/main (no page handed to reps here)', cur === base && !!cur)
    // Every NAV row must be untouched — including the parked flags-dm-gate row, which this
    // package must neither carry nor revert.
    const navOf = (s) => s.slice(s.indexOf('export const NAV: NavGroup[]'), s.indexOf('export type NavLayout'))
    ck('the ENTIRE NAV table is byte-identical to origin/main', navOf(RBAC) === navOf(RBAC_BASE))
    ck('...so the parked flags-dm-gate row is neither duplicated nor reverted here',
       !RBAC.includes("{ href: '/commcalc/flags', label: 'Flags', icon: '🚩', module: 'commissions', scopes:"))
    const fns = ['canSeeItem', 'canAccessPath', 'hasReport', 'hasDataGrant', 'isSuperAdmin', 'navBlockReason']
    for (const f of fns) {
      const cut = (s) => { const i = s.indexOf(`export function ${f}(`); return s.slice(i, s.indexOf('\n}', i) + 2) }
      ck(`${f}() is byte-identical to origin/main`, cut(RBAC) === cut(RBAC_BASE))
    }
  } else { ck('[NEGATIVE CONTROL] base rbac.ts readable', false) }
}

console.log('\nD. the rest of /admin/roles is untouched')
if (PAGE_BASE) {
  const cut = (s, a, b) => { const i = s.indexOf(a); return i < 0 ? null : s.slice(i, s.indexOf(b, i)) }
  for (const [a, b, label] of [
    ['function accessState(e: Emp): Access {', '\ntype Role = ', 'accessState()'],
    ['function ViewAsEmployeeCard()', '// ── Access state', 'ViewAsEmployeeCard'],
    ['async function saveWidgets(', 'function resetWidgets(', 'saveWidgets()'],
    ['async function removeEmp(', 'async function createLogin(', 'removeEmp()'],
    ['function confirmNoRole(', 'async function assign(', 'confirmNoRole()'],
  ]) ck(`${label} byte-identical to origin/main`, cut(PAGE, a, b) === cut(PAGE_BASE, a, b))
  ck('saveDetails() still PATCHes the same StoreOps fields (only the input changed)',
     cut(PAGE, 'async function saveDetails(', '\n  async function removeEmp(')
     === cut(PAGE_BASE, 'async function saveDetails(', '\n  async function removeEmp('))
}

console.log(`\n${'='.repeat(72)}\n  RESULT: ${pass} passed, ${fail} failed\n${'='.repeat(72)}`)
process.exit(fail ? 1 : 0)
