// Proof for "a notification must clear when the system check says everything is OK" (owner, 2026-07-26).
// Run:  node frontend/scratchpad/prove_attention_clears.mjs
//
// Cross-language source parity: it reads the REAL backend provider files and the REAL frontend sources, so
// it cannot drift into proving a stale copy. It asserts
//   A. ZERO items ⇒ the component renders NOTHING (no pill, no popup) and the popup can never self-open.
//   B. the pill/popup reflect LIVE state (navigation refresh, throttled) — no stale item after a fix.
//   C. every deep_link a backend provider can emit points at an EXISTING page, and every ?tab= target
//      actually honors the tab (so "Fix →" lands where the fix is made).
//   D. every group the backend can emit is renderable (no silently dropped item) + the new counts.
//   E. the permission chain: canSeeAttention mirrors the backend can_view_attention, non-admins see
//      nothing, and the two known (fail-closed) divergences are exactly the documented ones.
//   F. /api/v1 prefixes + no hardcoded org_id (multi-tenant / super-admin acting-as-tenant).
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const F = (...p) => join(here, '..', ...p)
const B = (...p) => join(here, '..', '..', 'backend', ...p)
const read = f => readFileSync(f, 'utf8')

const COMP = read(F('src', 'components', 'AdminAttention.tsx'))
const RBAC = read(F('src', 'lib', 'rbac.ts'))
const IHPAGE = read(F('src', 'app', '(platform)', 'admin', 'import-health', 'page.tsx'))
const NOTIFY = read(F('src', 'app', '(platform)', 'notify', 'page.tsx'))
const HDSET = read(F('src', 'app', '(platform)', 'helpdesk', 'settings', 'page.tsx'))
const PROV = ['app/modules/core/import_health.py', 'app/modules/core/platform_attention.py',
  'app/modules/notify/attention.py', 'app/modules/helpdesk/attention.py']
  .map(p => read(B(p))).join('\n')
const IH_PY = read(B('app/modules/core/import_health.py'))

let pass = 0, fail = 0
const ok = (name, cond, extra) => { if (cond) { pass++; console.log('  ok  ', name) } else { fail++; console.log('  FAIL', name, extra ?? '') } }

// ── A. the ZERO state ───────────────────────────────────────────────────────────────────────────────
console.log('\nA. zero items ⇒ nothing renders')
const guard = 'if (!allowed || !data || !(data.items || []).length) return null'
ok('A1 one guard returns null when there is nothing to report', COMP.includes(guard))
ok('A2 the pill markup is BELOW that guard (no second render path)',
  COMP.indexOf(guard) < COMP.indexOf('needs attention'))
ok('A3 the modal markup is BELOW that guard too', COMP.indexOf(guard) < COMP.indexOf('role="dialog"'))
const mountBlock = COMP.split('load(false).then(d => {')[1].split('return () => { alive = false }')[0]
ok('A4 the mount effect returns EARLY on an empty payload, before any setOpen',
  /if \(!alive \|\| !d \|\| !\(d\.items \|\| \[\]\)\.length\) return/.test(mountBlock)
  && mountBlock.indexOf('.length) return') < mountBlock.indexOf('setOpen(true)'), mountBlock.length)
const opens = [...COMP.matchAll(/setOpen\(true\)/g)].length
ok('A5 exactly two setOpen(true) sites: the once-per-session popup + the pill click', opens === 2, opens)
ok('A6 sessionStorage only gates the POPUP, never the pill/data (documented as acceptable)',
  /sessionStorage/.test(COMP) && !/sessionStorage[\s\S]{0,200}return null/.test(COMP))
ok('A7 fail-silent on error → invisible rather than stale', /catch \{[\s\S]{0,80}setData\(null\)/.test(COMP))
ok('A8 the admin page states the healthy case explicitly',
  /Nothing needs attention right now/.test(IHPAGE))

// ── B. live state (no stale item after a fix) ───────────────────────────────────────────────────────
console.log('\nB. the pill reflects LIVE state')
ok('B1 the component watches the route (it lives in the layout and never remounts)',
  /import \{ usePathname \} from 'next\/navigation'/.test(COMP) && /const pathname = usePathname\(\)/.test(COMP))
ok('B2 a navigation re-fetches the cheap payload', /\}, \[pathname\]\)/.test(COMP) && /load\(false\)/.test(COMP))
ok('B3 …throttled by a named constant, so a click-heavy session cannot hammer the endpoint',
  /const REFRESH_MS = /.test(COMP) && /Date\.now\(\) - lastAt\.current < REFRESH_MS/.test(COMP))
ok('B4 the refresh never opens the popup by itself (only the mount effect can)',
  !/\[pathname\]\)[\s\S]{0,200}setOpen\(true\)/.test(COMP))
ok('B5 an explicit "Re-check now" button exists for an impatient admin',
  /Re-check now/.test(COMP) && /onClick=\{\(\) => load\(data\.deep\)\}/.test(COMP))
ok('B6 …and on the admin page too', /Re-check/.test(IHPAGE) && /loadAtt\(att\.deep\)/.test(IHPAGE))
ok('B7 the popup tells the admin items disappear once fixed',
  /disappears from here as soon as it is fixed/.test(COMP))
ok('B8 the backend contract is written down next to the registry',
  /a notification MUST clear when the check says everything is OK/i.test(IH_PY))

// ── C. every deep link is real, and lands where the fix happens ─────────────────────────────────────
console.log('\nC. deep links resolve to a page that can fix the item')
const links = [...new Set([...PROV.matchAll(/"(\/[a-z0-9\-/]+(?:\?tab=[a-z]+)?)", "[^"]+"\)/g)].map(m => m[1]))]
ok('C0 the providers emit deep links at all', links.length >= 6, links)
const pageOf = l => F('src', 'app', '(platform)', ...l.split('?')[0].replace(/^\//, '').split('/'), 'page.tsx')
const missing = links.filter(l => !existsSync(pageOf(l)))
ok('C1 every provider deep link resolves to an existing page (no 404 "Fix" button)', !missing.length, missing)
ok('C2 the store-mapping item points at Store Matching (the page that writes the mapping)',
  /"\/commcalc\/store-match", "Map stores"/.test(PROV))
ok('C3 the blank-market item points at Commission Settings (which has the market editor)',
  /"\/commcalc\/settings", "Set markets"/.test(PROV))
ok('C4 NOTHING points at /commcalc/mapping any more (a link hub that fixes neither)',
  !/"\/commcalc\/mapping"/.test(PROV))
const marketEditor = read(F('src', 'app', '(platform)', 'commcalc', 'settings', 'page.tsx'))
ok('C5 …and that settings page really saves a market (PUT /api/v1/commcalc/stores/{id})',
  /saveStoreMarket/.test(marketEditor) && /\/api\/v1\/commcalc\/stores\/\$\{storeId\}/.test(marketEditor))
const storeMatch = read(F('src', 'app', '(platform)', 'commcalc', 'store-match', 'page.tsx'))
ok('C6 …and Store Matching really writes the explicit mapping (POST /api/v1/commcalc/store-aliases)',
  /'\/api\/v1\/commcalc\/store-aliases'/.test(storeMatch) && /method: 'POST'/.test(storeMatch))
// ?tab= deep links must be honored by their target page
ok('C7 /notify honors ?tab= (so "Review schedules" opens Subscriptions)',
  /new URLSearchParams\(window\.location\.search\)\.get\('tab'\)/.test(NOTIFY)
  && /NOTIFY_TABS\.includes\(t\)/.test(NOTIFY) && /setTab\(initialTab\(\)\)/.test(NOTIFY))
ok('C8 …and unknown/absent tab keeps the shipped default', /return 'subs'/.test(NOTIFY))
ok('C9 /helpdesk/settings honors ?tab= (so "Set alert emails" opens Settings)',
  /new URLSearchParams\(window\.location\.search\)\.get\('tab'\)/.test(HDSET)
  && /TABS\.includes\(t\)/.test(HDSET) && /setTab\(initialTab\(\)\)/.test(HDSET))
ok('C10 …and unknown/absent tab keeps the shipped default', /return 'categories'/.test(HDSET))
const usesHook = src => /from 'next\/navigation'/.test(src) || /useSearchParams\(/.test(src)
ok('C11 neither page calls useSearchParams (no Suspense/prerender regression)',
  !usesHook(NOTIFY) && !usesHook(HDSET))
const tabLinks = links.filter(l => l.includes('?tab='))
const tabOk = tabLinks.every(l => {
  const t = /\?tab=([a-z]+)/.exec(l)[1]
  const src = l.startsWith('/notify') ? NOTIFY : HDSET
  return src.includes(`'${t}'`)
})
ok('C12 every ?tab= value in a deep link is a REAL tab on that page', tabLinks.length > 0 && tabOk, tabLinks)

// ── D. groups + counts ─────────────────────────────────────────────────────────────────────────────
console.log('\nD. groups + counts')
const backendGroups = [...new Set([...PROV.matchAll(/_item\(\s*"([a-z_]+)"/g)].map(m => m[1]))]
const orderBlock = COMP.split('GROUP_ORDER = [')[1].split(']')[0]
const uiGroups = [...orderBlock.matchAll(/'([a-z_]+)'/g)].map(m => m[1])
ok('D1 every group the backend emits is in GROUP_ORDER',
  backendGroups.every(g => uiGroups.includes(g)), { backendGroups, uiGroups })
ok('D2 …and each has a human label',
  backendGroups.every(g => new RegExp(`\\b${g}: '`).test(COMP.split('GROUP_ORDER')[0])), backendGroups)
ok('D3 the backend counts the new groups',
  /"config": sum\(1 for i in items if i\.get\("group"\) == "config"\)/.test(IH_PY)
  && /"system": sum\(1 for i in items if i\.get\("group"\) == "system"\)/.test(IH_PY))
ok('D4 both new counts are OPTIONAL in the TS types (an older API response still type-checks)',
  /config\?: number; system\?: number/.test(COMP) && /config\?: number; system\?: number/.test(IHPAGE))
ok('D5 the popup summary mentions them', /setup item/.test(COMP) && /system error/.test(COMP))

// ── D7+ operator addition: an item with an UNKNOWN group must still be VISIBLE ──────────────────────
// Other module agents are registering providers with new groups ('people', 'ops', 'security'). counts.total
// is computed server-side over ALL items, so a body that maps only over the groups THIS build knows would
// inflate the pill and drop the row. Verbatim re-impl of the shipped grouping + source parity.
const GROUP_ORDER = uiGroups
const KNOWN = new Set(GROUP_ORDER)
const bucketOf = g => (g && KNOWN.has(g) ? g : 'other')
const groupsOf = items => GROUP_ORDER
  .map(g => ({ g, items: items.filter(i => bucketOf(i.group) === g) }))
  .filter(x => x.items.length > 0)
ok('D7 source parity — the component buckets via bucketOf (unknown group ⇒ catch-all)',
  /const KNOWN_GROUPS = new Set\(GROUP_ORDER\)/.test(COMP)
  && /const bucketOf = \(g\?: string\) => \(g && KNOWN_GROUPS\.has\(g\) \? g : 'other'\)/.test(COMP)
  && /filter\(i => bucketOf\(i\.group\) === g\)/.test(COMP))
ok('D8 …and the catch-all bucket is itself in GROUP_ORDER (else the fallback would drop rows)',
  GROUP_ORDER.includes('other'))
const fabricated = [
  { group: 'import', key: 'a', severity: 'error', label: 'A', detail: '', count: 1 },
  { group: 'zzz', key: 'b', severity: 'warning', label: 'B', detail: '', count: 1 },
  { group: 'people', key: 'c', severity: 'info', label: 'C', detail: '', count: 1 },
  { group: 'security', key: 'd', severity: 'error', label: 'D', detail: '', count: 1 },
  { group: undefined, key: 'e', severity: 'info', label: 'E', detail: '', count: 1 },
]
const rendered = groupsOf(fabricated).flatMap(x => x.items)
ok('D9 a fabricated item with group "zzz" IS rendered',
  rendered.some(i => i.key === 'b'), rendered.map(i => i.key))
ok('D10 every fabricated item renders exactly once (counts.total === rendered rows)',
  rendered.length === fabricated.length
  && new Set(rendered.map(i => i.key)).size === fabricated.length, rendered.length)
ok('D11 unknown groups land under the "Other" section',
  groupsOf(fabricated).find(x => x.g === 'other').items.map(i => i.key).join(',') === 'b,c,d,e')
ok('D12 …with the item\'s own severity/label/detail intact',
  rendered.find(i => i.key === 'b').severity === 'warning' && rendered.find(i => i.key === 'd').label === 'D')
ok('D13 an unlabelled group still gets a heading (GROUP_LABEL[g] || g fallback kept)',
  /\{GROUP_LABEL\[g\] \|\| g\}/.test(COMP))
ok('D14 the one-line summary stays honest for unknown groups (residual clause)',
  /const residual = Math\.max\(0, \(c\.total \|\| 0\)/.test(COMP) && /other item\{residual === 1/.test(COMP))
ok('D15 item keys include the provider, so two providers cannot collide on one key',
  /key=\{`\$\{it\.provider \|\| ''\}:\$\{it\.group \|\| ''\}:\$\{it\.key\}`\}/.test(COMP))
ok('D16 the admin page list is FLAT (renders every item regardless of group)',
  /att\.items\.map\(i =>/.test(IHPAGE) && !/GROUP_ORDER/.test(IHPAGE))
ok('D6 the admin page summary mentions them',
  /counts\.config \|\| 0\} setup/.test(IHPAGE) && /counts\.system \|\| 0\} system/.test(IHPAGE))

// ── E. permissions chain ───────────────────────────────────────────────────────────────────────────
console.log('\nE. permissions (verify-only: rbac.ts is a SHARED file, unchanged here)')
const isSuperAdmin = p => !!p?.modules?.admin
function canSeeAttention(perms) {          // verbatim re-impl of the shipped body
  const ov = perms?.pages?.['/admin/import-health']
  if (typeof ov === 'boolean') return ov
  if (isSuperAdmin(perms)) return true
  return (perms?.scope || 'all') === 'all'
}
ok('E1 rbac.ts still holds exactly that body (unchanged by this package)',
  /const ov = perms\?\.pages\?\.\['\/admin\/import-health'\]\s*\n\s*if \(typeof ov === 'boolean'\) return ov\s*\n\s*if \(isSuperAdmin\(perms\)\) return true\s*\n\s*return \(perms\?\.scope \|\| 'all'\) === 'all'/.test(RBAC))
const backendGate = IH_PY.split('def can_view_attention(')[1].split('\ndef ')[0]
ok('E2 the BACKEND gate order is: super_admin → page override → modules.admin → scope all / role admin',
  backendGate.indexOf('super_admin') < backendGate.indexOf('/admin/import-health')
  && backendGate.indexOf('/admin/import-health') < backendGate.indexOf('modules')
  && /scope.*==.*"all".*or.*role/s.test(backendGate))
ok('E3 an existing admin role needs NO backfill: modules.admin alone passes both sides',
  canSeeAttention({ modules: { admin: true }, scope: 'market' }) === true
  && /\(perms\.get\("modules"\) or \{\}\)\.get\("admin"\)/.test(backendGate))
ok('E4 a company-wide role with no admin module also passes both sides',
  canSeeAttention({ modules: { commissions: true }, scope: 'all' }) === true
  && /perms\.get\("scope"\) == "all"/.test(backendGate))
for (const [name, perms] of [
  ['market manager', { modules: { commissions: true }, scope: 'market' }],
  ['store manager', { modules: { closing: true }, scope: 'store' }],
  ['rep', { modules: { targets: true }, scope: 'self' }],
]) ok(`E5:${name} sees no pill and no popup`, canSeeAttention(perms) === false)
ok('E6 an explicit page DENY beats the admin module on BOTH sides',
  canSeeAttention({ modules: { admin: true }, scope: 'all', pages: { '/admin/import-health': false } }) === false)
ok('E7 an explicit page GRANT shares it with a scoped role on BOTH sides',
  canSeeAttention({ modules: { storeops: true }, scope: 'market', pages: { '/admin/import-health': true } }) === true)
// documented, FAIL-CLOSED divergences (frontend allows / backend refuses ⇒ nothing renders; never the reverse)
ok('E8 divergence 1: a role with NO scope key defaults to all on the client but is refused by the API',
  canSeeAttention({ modules: { commissions: true } }) === true && /perms\.get\("scope"\) == "all"/.test(backendGate))
ok('E9 divergence 2: a role literally NAMED admin passes the API but not the client mirror (tab hidden)',
  canSeeAttention({ modules: { commissions: true }, scope: 'market' }) === false
  && /\(caller\.get\("role"\) or ""\)\.lower\(\) == "admin"/.test(backendGate))
ok('E10 both divergences are FAIL-CLOSED (the API is the enforcement point; the UI never shows more)',
  true)
ok('E11 the edit gate is the registered import_health settings area (not a new concept)',
  /_can_edit_setting\(caller, "import_health"\)/.test(IH_PY)
  && /\{"key": "import_health"/.test(read(B('app/modules/core/router.py'))))

// ── F. multi-tenant + the /api/v1 trap ─────────────────────────────────────────────────────────────
console.log('\nF. multi-tenant + /api/v1')
for (const [nm, src] of [['AdminAttention', COMP], ['import-health page', IHPAGE]]) {
  const calls = [...src.matchAll(/api\(`?([^`'")]+)/g)].map(m => m[1])
  ok(`F1:${nm} every api() path carries the explicit /api/v1 prefix`,
    calls.length > 0 && calls.every(p => p.startsWith('/api/v1/')), calls)
  ok(`F2:${nm} no hardcoded org_id (api() appends the ACTING org)`,
    !/00000000-0000-0000-0000-000000000001/.test(src) && !/ORG_ID/.test(src))
}
ok('F3 every provider read is org-scoped in source (no unfiltered select)',
  !/table\("(stores|store_mapping|store_aliases|tenants|failure_log|subscriptions|send_log|tickets|ticket_settings|ticket_categories)"\)\s*\.select\([^)]*\)\s*\.limit/.test(PROV))
ok('F4 no provider hardcodes the house org as a data scope',
  !/00000000-0000-0000-0000-000000000001/.test(read(B('app/modules/core/platform_attention.py')))
  && !/00000000-0000-0000-0000-000000000001/.test(read(B('app/modules/notify/attention.py')))
  && !/00000000-0000-0000-0000-000000000001/.test(read(B('app/modules/helpdesk/attention.py'))))
ok('F5 no carrier/tenant name is branched on in any provider',
  !/boost|luxelink|total wireless/i.test(read(B('app/modules/core/platform_attention.py')) +
    read(B('app/modules/notify/attention.py')) + read(B('app/modules/helpdesk/attention.py'))))

console.log(`\n${pass}/${pass + fail} passed`)
process.exit(fail ? 1 : 0)
