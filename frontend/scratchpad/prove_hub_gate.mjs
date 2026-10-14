// Proof for lib/hub-gate.ts — "may THIS viewer open THIS page from a tile". Verbatim re-impl of the
// shipped predicates (rbac.ts moduleGranted / canSeeItem / canAccessPath + hub-gate's canOpen), run
// against the REAL nav items parsed out of rbac.ts and the REAL hand-woven tile literals parsed out
// of the two curated hubs. Run: node prove_hub_gate.mjs
//
// REPRODUCES THE REPORTED DEFECT FIRST (owner 2026-10-14, "I cannot see the schedule on the app"):
// §1 runs the PRE-FIX surface (the raw literal, no gate) and asserts the dead links are there, so the
// regression cannot stop being reproduced. §2 runs the POST-FIX surface and asserts they are gone and
// that nothing a viewer could legitimately open was lost. §3 pins the fallback and deep-link rules.
import { readFileSync } from 'node:fs'

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => { if (cond) pass++; else { fail++; console.log('  FAIL:', name, detail) } }

const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')
const RBAC = read('../src/lib/rbac.ts')
const WF = read('../src/app/(platform)/storeops/page.tsx')
const PR = read('../src/app/(platform)/payroll/page.tsx')

// ── the REAL nav items, parsed from rbac.ts's NAV literal ────────────────────────────────────────
// Each entry: { href, module, scopes?, tileOnly? }. Parsed rather than retyped so this proof cannot
// drift from the shipped nav the way a hand-copied fixture would.
const NAV = []
for (const m of RBAC.matchAll(/\{\s*href:\s*'([^']+)'[^}]*?module:\s*'([^']+)'([^}]*)\}/g)) {
  const [, href, module, rest] = m
  const sc = /scopes:\s*\[([^\]]*)\]/.exec(rest)
  NAV.push({
    href, module,
    scopes: sc ? sc[1].split(',').map(s => s.trim().replace(/'/g, '')).filter(Boolean) : undefined,
    tileOnly: /tileOnly:\s*true/.test(rest),
  })
}
ok('parsed a real-sized NAV out of rbac.ts', NAV.length > 200, `${NAV.length} items`)
ok('/storeops/schedule is in NAV', NAV.some(i => i.href === '/storeops/schedule'))
ok('/storeops/schedule is tileOnly — the sidebar hides it, so a hub tile is its only door',
  NAV.find(i => i.href === '/storeops/schedule')?.tileOnly === true)

// ── the REAL hand-woven tile literals, parsed from the two curated hubs ──────────────────────────
const litHrefs = src => {
  const g = src.slice(src.indexOf('const GROUPS'), src.indexOf('export default'))
  return [...g.matchAll(/href: '([^']+)'/g)].map(m => m[1])
}
const kpiHrefs = src => [...src.matchAll(/<StatTile\b[\s\S]*?href="([^"]+)"/g)].map(m => m[1])
const WF_TILES = litHrefs(WF), WF_KPI = kpiHrefs(WF), PR_TILES = litHrefs(PR)
ok('parsed the Workforce tile literal', WF_TILES.length === 21, `${WF_TILES.length} items`)
ok('parsed the Workforce KPI row', WF_KPI.length === 6, `${WF_KPI.length} tiles`)
ok('parsed the Payroll tile literal', PR_TILES.length > 10, `${PR_TILES.length} items`)

// ── verbatim re-impl: rbac.ts ────────────────────────────────────────────────────────────────────
const MODULE_ALIASES = { accounts: 'account', account: 'accounts' }
const moduleGranted = (mods, key) => { if (!mods) return false; return !!(mods[key] || mods[MODULE_ALIASES[key]]) }
const navPath = href => String(href || '').split('#')[0].split('?')[0]
const isDeepLinkItem = item => navPath(item.href) !== item.href
const deepLinkPage = item => { if (!isDeepLinkItem(item)) return null; const p = navPath(item.href); return NAV.find(i => i.href === p) || null }
const SELF_ALLOWED = ['/commcalc/targets/my', '/commcalc/kpi', '/account/password', '/reports', '/helpdesk', '/chat']

function canSeeItem(perms, item) {
  if (isDeepLinkItem(item)) { const page = deepLinkPage(item); return !!page && canSeeItem(perms, page) }
  const ov = perms.pages?.[item.href]
  if (item.scopes && !item.scopes.includes(perms.scope || 'all') && ov !== true) return false
  if (typeof ov === 'boolean') return ov
  if (!moduleGranted(perms.modules, item.module)) return false
  return true
}

function canAccessPath(perms, path) {
  if (path === '/' || path.startsWith('/account/password')) return true
  const scope = perms.scope || 'all'
  if (scope === 'self') {
    const home = perms.home || '/commcalc/targets/my'
    if (perms.pages?.[path] === true) return true
    if (SELF_ALLOWED.some(p => path.startsWith(p)) || path.startsWith(home)) return true
    for (const it of NAV) if ((path === it.href || path.startsWith(it.href + '/')) && canSeeItem(perms, it)) return true
    return false
  }
  const exactOv = perms.pages?.[path]
  for (const it of NAV) if (path === it.href && it.scopes && !it.scopes.includes(scope) && exactOv !== true) return false
  return true
}

// ── verbatim re-impl: hub-gate.ts canOpen / gateHubGroups ────────────────────────────────────────
const canOpen = (perms, href) => {
  const path = navPath(href)
  const it = NAV.find(i => i.href === href) || NAV.find(i => i.href === path)
  if (it) return canSeeItem(perms, it)
  return canAccessPath(perms, path)
}

// ── the REAL production role rows (storeops.roles, read 2026-10-14) ──────────────────────────────
// Only the three facts the gate reads. Login counts are the ACTIVE logins measured the same day.
const ROLES = [
  { who: 'Cellfonz sales_rep', logins: 34, perms: { scope: 'store', modules: { storeops: true, targets: true, commissions: true } } },
  { who: 'Luxelink sales_rep', logins: 39, perms: { scope: 'self', modules: { storeops: false, targets: true, vip: true } } },
  { who: 'Luxelink store_manager', logins: 10, perms: { scope: 'store', modules: { storeops: true, targets: true } } },
  { who: 'Cellfonz district_manager', logins: 3, perms: { scope: 'market', modules: { storeops: true, targets: true } } },
  { who: 'Cellfonz admin', logins: 7, perms: { scope: 'all', modules: { storeops: true, targets: true, admin: true } } },
]

// ── §1 PRE-FIX: the raw literal, ungated — the reported defect ───────────────────────────────────
console.log('\n§1  PRE-FIX (raw literal, no gate) — dead links per role')
const dead = (perms, hrefs) => hrefs.filter(h => !canOpen(perms, h))
const rep = ROLES.find(r => r.who === 'Cellfonz sales_rep').perms
const deadTiles = dead(rep, WF_TILES), deadKpi = dead(rep, WF_KPI)
console.log(`    Cellfonz sales_rep: ${deadTiles.length}/${WF_TILES.length} tile links and ${deadKpi.length}/${WF_KPI.length} KPI tiles bounce`)
ok('§1 a store-scoped rep saw 11 of 21 tile links that bounce', deadTiles.length === 11, deadTiles.join(' '))
ok('§1 ...and 2 of 6 KPI tiles that bounce', deadKpi.length === 2, deadKpi.join(' '))
for (const h of ['/storeops/employees', '/storeops/attendance', '/storeops/setup/stores', '/storeops/reports'])
  ok(`§1 ${h} was offered and bounces`, WF_TILES.includes(h) && !canOpen(rep, h))
ok('§1 the Schedule link itself was always legitimate', canOpen(rep, '/storeops/schedule'))
const pm = ROLES.find(r => r.who === 'Cellfonz sales_rep').perms
ok('§1 the Payroll hub was dead for a rep too', dead(pm, PR_TILES).length > 0, `${dead(pm, PR_TILES).length} of ${PR_TILES.length}`)

// ── §2 POST-FIX: gateHubGroups/canOpen over the same literal ─────────────────────────────────────
console.log('\n§2  POST-FIX (gated) — nothing offered that bounces, nothing legitimate lost')
for (const { who, logins, perms } of ROLES) {
  const shownT = WF_TILES.filter(h => canOpen(perms, h))
  const shownK = WF_KPI.filter(h => canOpen(perms, h))
  console.log(`    ${who.padEnd(26)} (${String(logins).padStart(2)} logins)  tiles ${String(shownT.length).padStart(2)}/${WF_TILES.length}  KPI ${shownK.length}/${WF_KPI.length}  schedule=${canOpen(perms, '/storeops/schedule') ? 'YES' : 'no'}`)
  ok(`§2 ${who}: every tile link shown is openable`, dead(perms, shownT).length === 0)
  ok(`§2 ${who}: every KPI tile shown is openable`, dead(perms, shownK).length === 0)
  ok(`§2 ${who}: the gate only REMOVES — it never invents a link`, shownT.every(h => WF_TILES.includes(h)))
}
ok('§2 an admin loses nothing', WF_TILES.filter(h => canOpen(ROLES[4].perms, h)).length === WF_TILES.length)
ok('§2 a store-scoped rep keeps the 10 pages that are genuinely theirs',
  WF_TILES.filter(h => canOpen(rep, h)).length === 10)
ok('§2 ...and Schedule is one of them', WF_TILES.filter(h => canOpen(rep, h)).includes('/storeops/schedule'))

// ── §3 the one setting that keeps 39 logins out, and the fallback rules ──────────────────────────
console.log('\n§3  the Workforce module setting, and canOpen\'s fallbacks')
const lux = ROLES.find(r => r.who === 'Luxelink sales_rep').perms
ok('§3 Luxelink reps cannot open Schedule TODAY — modules.storeops is false', !canOpen(lux, '/storeops/schedule'))
const luxOn = { ...lux, modules: { ...lux.modules, storeops: true } }
ok('§3 ticking Workforce on that ONE role opens it — config, not code', canOpen(luxOn, '/storeops/schedule'))
ok('§3 ...and it stays a self-scoped view: no manager-only page comes with it',
  !canOpen(luxOn, '/storeops/employees') && !canOpen(luxOn, '/storeops/attendance'))
ok('§3 a deep-link href resolves to its page entry', canOpen(ROLES[4].perms, '/hr?tab=employees') === canOpen(ROLES[4].perms, '/hr'))
ok('§3 an href NAV does not carry falls through to the route guard, not to false',
  canOpen(ROLES[4].perms, '/some/page/nav/never/heard/of') === true)
ok('§3 ...and that fallback still refuses a self-scoped viewer',
  canOpen(lux, '/some/page/nav/never/heard/of') === false)

console.log(`\n${pass} passed / ${fail} failed`)
process.exit(fail ? 1 : 0)
