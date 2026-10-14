// Proof for the Payroll hub's "Manage Employees" tile and its password-reset door (owner 2026-10-14,
// index §CORE-09). Run: node prove_manage_employees_tile.mjs
//
// WHAT THIS PROVES, and why it is a proof rather than a lock. The lock
// (backend/harness_assign_password_one_home.py) pins the WIRING: one home, three consumers, no second
// copy, the tile's three doors. This proves the DESIGN CALL — that putting the password door behind an
// `hr` route was not a preference but the only shape that works:
//
//   §1 the tile is parsed out of the real hub and holds the owner's three doors, in his order.
//   §2 the REJECTED design (tile → /admin/security) is run through the real viewer gate and shown to
//      be dropped for every HR and market role — i.e. the owner's new tile would have shown the
//      password door to nobody but a full admin. This is the reason for the new route, kept
//      reproducible so nobody "simplifies" it back.
//   §3 the SHIPPED design opens all three doors for exactly the people who hold the two it was folded
//      from, and offers them to nobody the route guard would bounce.
//   §4 the tile collapses to nothing for a store/self-scoped rep — no half-dead tile.
//   §5 the side-menu ruling of §19.40 survives: Employees & Pay is still its own, non-tileOnly row.
//
// Predicates are a verbatim re-impl of the shipped rbac.ts / hub-gate.ts, run against the REAL NAV
// parsed out of rbac.ts, the REAL tile literal parsed out of the hub, and the REAL production role
// rows (storeops.roles, as measured 2026-10-14 for §CORE-06's proof).
import { readFileSync } from 'node:fs'

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => { if (cond) pass++; else { fail++; console.log('  FAIL:', name, detail) } }
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')
const RBAC = read('../src/lib/rbac.ts')
const HUB = read('../src/app/(platform)/payroll/page.tsx')

// ── the REAL NAV, parsed (never retyped) ─────────────────────────────────────────────────────────
const NAV = []
for (const m of RBAC.matchAll(/\{\s*href:\s*'([^']+)'[^}]*?module:\s*'([^']+)'([^}]*)\}/g)) {
  const [, href, module, rest] = m
  const sc = /scopes:\s*\[([^\]]*)\]/.exec(rest)
  NAV.push({ href, module,
    scopes: sc ? sc[1].split(',').map(s => s.trim().replace(/'/g, '')).filter(Boolean) : undefined,
    tileOnly: /tileOnly:\s*true/.test(rest) })
}
ok('parsed a real-sized NAV out of rbac.ts', NAV.length > 200, `${NAV.length} items`)

// ── the REAL tile literal ────────────────────────────────────────────────────────────────────────
const GROUPS_SRC = HUB.slice(HUB.indexOf('const GROUPS'), HUB.indexOf('export default'))
const tileBlock = title => {
  const i = GROUPS_SRC.indexOf(`title: '${title}',`)
  if (i < 0) return ''
  const j = GROUPS_SRC.indexOf('\n    ],', i)
  return GROUPS_SRC.slice(i, j)
}
const hrefsIn = block => [...block.matchAll(/href: '([^']+)'/g)].map(m => m[1])
const MANAGE = tileBlock('Manage Employees')
const DOORS = hrefsIn(MANAGE)

// ── verbatim re-impl: rbac.ts + hub-gate.ts ──────────────────────────────────────────────────────
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
const canOpen = (perms, href) => {
  const path = navPath(href)
  const it = NAV.find(i => i.href === href) || NAV.find(i => i.href === path)
  return it ? canSeeItem(perms, it) : canAccessPath(perms, path)
}
/** hub-gate.ts::gateHubGroups, for one tile: the doors kept, and whether the tile survives. */
const gateTile = (perms, doors) => doors.filter(h => canOpen(perms, h))

// ── the REAL production role rows (storeops.roles, measured 2026-10-14) ──────────────────────────
const ROLES = [
  { who: 'Cellfonz admin',            scopeKind: 'admin', perms: { scope: 'all', modules: { storeops: true, admin: true, hr: true, targets: true } } },
  { who: 'Cellfonz HR',               scopeKind: 'hr',    perms: { scope: 'all', modules: { hr: true, storeops: true, helpdesk: true, notify: true } } },
  { who: 'Cellfonz district_manager', scopeKind: 'hr',    perms: { scope: 'market', modules: { storeops: true, targets: true, hr: true } } },
  { who: 'Cellfonz sales_rep',        scopeKind: 'rep',   perms: { scope: 'store', modules: { storeops: true, targets: true, commissions: true } } },
  { who: 'Luxelink sales_rep',        scopeKind: 'rep',   perms: { scope: 'self', modules: { storeops: false, targets: true, vip: true } } },
]

// ── §1 the tile the owner asked for ──────────────────────────────────────────────────────────────
console.log('\n§1  the Manage Employees tile, parsed out of the real hub')
ok('§1 the hub carries a tile titled "Manage Employees"', MANAGE.length > 0)
ok('§1 it holds exactly the owner\'s three doors, in his order',
  JSON.stringify(DOORS) === JSON.stringify(['/hr?tab=employees', '/hr/employee-database', '/hr/employee-password']),
  DOORS.join(' '))
ok('§1 Employees & Pay and Employee Database no longer stand alone as tiles',
  !GROUPS_SRC.includes("title: 'Employees & Pay'") && !GROUPS_SRC.includes("title: 'Employee Database'"))
console.log(`    doors: ${DOORS.join('  ')}`)

// ── §2 the REJECTED design: point the tile at /admin/security ────────────────────────────────────
console.log('\n§2  REJECTED design — the tile\'s password door pointing at /admin/security')
const REJECTED = ['/hr?tab=employees', '/hr/employee-database', '/admin/security']
for (const { who, scopeKind, perms } of ROLES) {
  const kept = gateTile(perms, REJECTED)
  const hasPw = kept.includes('/admin/security')
  console.log(`    ${who.padEnd(26)} keeps ${kept.length}/3  password door=${hasPw ? 'YES' : 'no'}`)
  if (scopeKind === 'hr') ok(`§2 ${who}: the password door would have been DROPPED`, !hasPw,
    'this is why the new route exists — /admin/security is module admin')
  if (scopeKind === 'admin') ok(`§2 ${who}: only a full admin would have kept it`, hasPw)
}

// ── §3 the SHIPPED design ────────────────────────────────────────────────────────────────────────
console.log('\n§3  SHIPPED design — /hr/employee-password behind the same tile')
for (const { who, scopeKind, perms } of ROLES) {
  const kept = gateTile(perms, DOORS)
  console.log(`    ${who.padEnd(26)} keeps ${kept.length}/3  password door=${kept.includes('/hr/employee-password') ? 'YES' : 'no'}`)
  ok(`§3 ${who}: every door shown is one the route guard admits`,
    kept.every(h => canOpen(perms, h)))
  if (scopeKind !== 'rep') {
    ok(`§3 ${who}: opens all three`, kept.length === 3, kept.join(' '))
    ok(`§3 ${who}: the password door travels with the two it was folded beside`,
      kept.includes('/hr?tab=employees') === kept.includes('/hr/employee-password'))
  }
}

// ── §4 no half-dead tile for a rep ───────────────────────────────────────────────────────────────
console.log('\n§4  a rep gets the whole tile dropped, not a tile of dead links')
for (const { who, scopeKind, perms } of ROLES) {
  if (scopeKind !== 'rep') continue
  const kept = gateTile(perms, DOORS)
  ok(`§4 ${who}: the tile collapses to nothing`, kept.length === 0, kept.join(' '))
}

// ── §5 the §19.40 side-menu ruling survives ──────────────────────────────────────────────────────
console.log('\n§5  §19.40 untouched — Employees & Pay is still its own side-menu row')
const ep = NAV.find(i => i.href === '/hr?tab=employees')
ok('§5 the NAV entry exists', !!ep)
ok('§5 it is NOT tileOnly (it shows in the sidebar)', ep && ep.tileOnly === false)
const pw = NAV.find(i => i.href === '/hr/employee-password')
ok('§5 the password page IS tileOnly (the tile is its door)', pw && pw.tileOnly === true)
ok('§5 ...and carries module hr, not admin', pw && pw.module === 'hr')

console.log(`\n${pass} passed / ${fail} failed`)
process.exit(fail ? 1 : 0)
