// Harness — nav SUB-CATEGORIES (roadmap #5, owner directive 2026-08-12).
//
// The whole risk of this package is REGRESSION: applyNavLayout drives the sidebar for every tenant,
// so the bar is that a tenant who has not opted into sub-categories gets a BYTE-IDENTICAL menu.
// This harness proves that against the REAL pre-change function (loaded from main), not against a
// hand-written expectation — an expectation can encode the same mistake twice.
//
// Run:  cd frontend && node_modules/.bin/jiti harness_nav_subcategories.ts
/* eslint-disable @typescript-eslint/no-explicit-any */
import { execSync } from 'child_process'
import { writeFileSync, mkdtempSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'
import { NAV, applyNavLayout, type NavLayout } from './src/lib/rbac'
// Baseline: applyNavLayout EXACTLY as it exists on main. Pulled from git at RUN TIME rather than
// vendored into the repo, so the negative control can never drift away from what is actually shipped.
// rbac.ts has zero imports, which is what makes loading a second copy of it side-effect-free.
const BASE = process.env.NAV_BASELINE_REF || 'main'
const dir = mkdtempSync(join(tmpdir(), 'navbase-'))
const baselinePath = join(dir, 'rbac_baseline.ts')
writeFileSync(baselinePath, execSync(`git show ${BASE}:frontend/src/lib/rbac.ts`, { encoding: 'utf8', maxBuffer: 1 << 24 }))
const OLD = require(baselinePath)

let pass = 0, fail = 0
const ok = (name: string, cond: boolean, detail = '') => {
  if (cond) { pass++; console.log('  PASS  ' + name) }
  else { fail++; console.log('  FAIL  ' + name + (detail ? '\n        ' + detail : '')) }
}
const J = (x: any) => JSON.stringify(x)

// A realistic input: the built-in NAV, exactly what layout.tsx passes after access filtering.
const G = () => NAV.map(g => ({ ...g, items: [...g.items] }))

console.log('\n── NEGATIVE CONTROL — an untouched tenant must be byte-identical ──')
const baselines: [string, NavLayout | undefined][] = [
  ['layout undefined', undefined],
  ['layout {}', {}],
  ['old fields only — move + hide + also', {
    items: {
      '/commcalc/flags':       { group: 'Store Operations' },
      '/commcalc/chargebacks': { hidden: true },
      '/commcalc/discrepancy': { also: ['Store Operations'] },
    },
    groups: ['Store Operations'],
  }],
  ['hideReportsDirectory', { hideReportsDirectory: true }],
]
for (const [name, lay] of baselines) {
  const before = OLD.applyNavLayout(G(), lay as any)
  const after  = applyNavLayout(G(), lay)
  ok(name, J(before) === J(after),
     J(before) === J(after) ? '' : 'first divergence:\n        OLD ' + J(before).slice(0, 300) + '\n        NEW ' + J(after).slice(0, 300))
}
// The `subs` key must be ABSENT, not `undefined` — an explicit undefined would still serialize
// differently through the nav-config JSON and would change the object's shape for every tenant.
const plain = applyNavLayout(G(), undefined)
ok('no `subs` key on any group when unused',
   plain.every(g => !('subs' in g)),
   'groups carrying the key: ' + plain.filter(g => 'subs' in g).map(g => g.group).join(', '))

console.log('\n── SUB-CATEGORIES ──')
const firstGroup = NAV[0].group
const [a, b, c] = NAV[0].items.map(i => i.href)
const subLayout: NavLayout = {
  items: { [a]: { sub: 'Daily' }, [b]: { sub: 'Daily' }, [c]: { sub: 'Monthly' } },
}
const sg = applyNavLayout(G(), subLayout).find(g => g.group === firstGroup)!
ok('subs are built', !!sg.subs && sg.subs.length === 2, 'subs=' + J(sg.subs?.map(s => s.name)))
ok('sub order follows first appearance', J(sg.subs?.map(s => s.name)) === J(['Daily', 'Monthly']))
ok('Daily holds both its items', J(sg.subs?.[0].items.map(i => i.href)) === J([a, b]))
ok('items[] STILL carries every item (search index + active-group read it)',
   [a, b, c].every(h => sg.items.some(i => i.href === h)) && sg.items.length === NAV[0].items.length,
   'items=' + sg.items.length + ' expected=' + NAV[0].items.length)

console.log('\n── ORDERING ──')
const revGroups = applyNavLayout(G(), { groupOrder: [NAV[2].group, NAV[0].group] }).map(g => g.group)
ok('groupOrder puts named groups first, in order',
   revGroups[0] === NAV[2].group && revGroups[1] === NAV[0].group, J(revGroups.slice(0, 4)))
ok('groups NOT named keep their natural relative order',
   (() => { const rest = revGroups.slice(2); const nat = NAV.map(g => g.group).filter(g => rest.includes(g))
            return J(rest.filter(g => nat.includes(g))) === J(nat) })(), J(revGroups))
const io = applyNavLayout(G(), { itemOrder: { [firstGroup]: [c, a] } }).find(g => g.group === firstGroup)!
ok('itemOrder reorders within a group', io.items[0].href === c && io.items[1].href === a,
   J(io.items.slice(0, 3).map(i => i.href)))
const so = applyNavLayout(G(), { ...subLayout, subOrder: { [firstGroup]: ['Monthly'] } }).find(g => g.group === firstGroup)!
ok('subOrder reorders sub-categories', J(so.subs?.map(s => s.name)) === J(['Monthly', 'Daily']))
ok('empty order lists are a no-op (not a wipe)',
   J(applyNavLayout(G(), { groupOrder: [], itemOrder: {}, subOrder: {} })) === J(applyNavLayout(G(), undefined)))

console.log('\n── THE FAILURE MODES THAT WOULD LOSE A PAGE ──')
// A sub named on an item that a MOVE sent to another group must not strand the item.
const moved = applyNavLayout(G(), { items: { [a]: { group: 'Store Operations', sub: 'Daily' } } })
ok('moved+subbed item lands in the destination group',
   moved.find(g => g.group === 'Store Operations')!.items.some(i => i.href === a))
ok('...and is nested there, not in its old group',
   moved.find(g => g.group === 'Store Operations')!.subs?.some(s => s.name === 'Daily' && s.items.some(i => i.href === a)) === true)
// An item hidden AND subbed stays hidden — the sub must never resurrect it.
ok('hidden wins over sub',
   !applyNavLayout(G(), { items: { [a]: { hidden: true, sub: 'Daily' } } })
     .some(g => g.items.some(i => i.href === a)))
// Whitespace-only sub = loose, not a sub named "  ".
const ws = applyNavLayout(G(), { items: { [a]: { sub: '   ' } } }).find(g => g.group === firstGroup)!
ok('whitespace-only sub is treated as loose', !ws.subs)
// Every item that goes in must come out — the renderer's loose-vs-claimed split relies on it.
const all = applyNavLayout(G(), subLayout).find(g => g.group === firstGroup)!
const claimed = new Set((all.subs || []).flatMap(s => s.items.map(i => i.href)))
ok('loose + claimed == every item (no page can vanish from the sidebar)',
   all.items.filter(i => !claimed.has(i.href)).length + claimed.size === all.items.length)

console.log(`\n${pass}/${pass + fail} passed` + (fail ? `  — ${fail} FAILED` : ''))
process.exit(fail ? 1 : 0)
