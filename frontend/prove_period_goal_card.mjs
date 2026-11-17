// Proof harness — WHERE MUST THE YEAR LAND: the goal card shows the server's answer and never
// computes one, and what counts toward a goal stays DECLARED (§COMMCALC-79).
//
// THREE DEFECTS THIS FILE EXISTS FOR.
//
// 1. A SECOND ANSWER TO "WHAT DO I STILL NEED". `annual_goal.progress` is the one home, and it
//    dereferences `targets_engine.rate_to_close` — the very home the per-day backward calculation
//    asks. A browser that subtracts and divides for itself is a second answer that will drift, and
//    it drifts silently because both look right.
//
// 2. ONE TENANT'S VOCABULARY BEHIND A HOUSE DEFAULT (owner directive 2026-10-30). The tickable
//    categories are the tenant's OWN declarations, served by the endpoint. A list spelled in the
//    component would offer a shipping store four wireless words, which is index §COMMCALC-49.10 in
//    a new place — and zero is the plausible-looking answer that hides it.
//
// 3. A VERDICT INVENTED OUT OF AN ABSENCE. `on_pace` is three-valued on purpose: null means the
//    goal could not be measured or the horizon could not be placed. Rendering null as "behind"
//    tells a store to act on a figure nobody measured.
//
// No network, no DB, no browser, no React. Run:  node frontend/prove_period_goal_card.mjs

import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + String(extra).slice(0, 300) : ''}`) }
}

const CARD = readFileSync(join(HERE, 'src/components/commcalc/PeriodGoalCard.tsx'), 'utf8')
const PAGE = readFileSync(join(HERE, 'src/app/(platform)/commcalc/targets/page.tsx'), 'utf8')
const ENGINE = readFileSync(join(ROOT, 'backend/app/modules/commcalc/annual_goal.py'), 'utf8')
const TENGINE = readFileSync(join(ROOT, 'backend/app/modules/commcalc/targets_engine.py'), 'utf8')
const ROUTER = readFileSync(join(ROOT, 'backend/app/modules/commcalc/router.py'), 'utf8')
const CATALOG = readFileSync(join(ROOT, 'backend/app/modules/closing/metric_catalog.py'), 'utf8')
const MIG = readFileSync(join(ROOT, 'database/migrations/1125_period_goal.sql'), 'utf8')

// The component's EXECUTABLE text: the comments carry the prose that explains each rule, and these
// scans are about what the CODE does.
const strip = s => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
const CODE = strip(CARD)
const PCODE = strip(PAGE)
// Python: drop comments and the module docstring, so the owner's own words may be QUOTED in prose
// while remaining forbidden in anything that executes.
const PYCODE = ENGINE.replace(/^"""[\s\S]*?"""/m, '').replace(/^\s*#.*$/gm, '')
  .replace(/"""[\s\S]*?"""/g, '')

console.log('\n§A — the card shows the server\'s answer and computes none of its own')
ck('it reads the one endpoint', /commcalc\/period-goal\/\$\{encodeURIComponent\(period\)\}/.test(CODE))
ck('exactly two places touch it — one read, one save',
   (CODE.match(/commcalc\/period-goal\//g) || []).length === 2)
for (const f of ['need', 'per_month', 'run_rate', 'pace_needed', 'on_pace', 'achieved', 'pct']) {
  ck(`the card renders the server's \`${f}\` rather than deriving it`,
     new RegExp(`d\\.${f}\\b`).test(CODE))
}
ck('no reduce() anywhere in the card', !/\.reduce\s*\(/.test(CODE))
ck('no running total is accumulated', !/\+=/.test(CODE))
ck('it does not subtract the earned figure from the goal itself',
   !/goal\s*-\s*(d\.)?achieved/.test(CODE) && !/achieved\s*-\s*(d\.)?goal/.test(CODE))
ck('it does not divide by the months left itself',
   !/months_remaining\s*\)?\s*$|\/\s*d\.months_remaining|\/\s*d\.months_total/.test(CODE))
ck('NEGATIVE CONTROL: a card-side need would be caught',
   /goal\s*-\s*d\.achieved/.test('const need = d.goal - d.achieved'))
ck('NEGATIVE CONTROL: a card-side per-month would be caught',
   /\/\s*d\.months_remaining/.test('const pm = need / d.months_remaining'))

console.log('\n§B — what counts is DECLARED, never a house word')
ck('the tickable categories come from the endpoint\'s axis', /d\.axis\.map/.test(CODE))
ck('and the horizons come from the endpoint\'s vocabulary', /d\.grains\.map/.test(CODE))
ck('an empty axis is SAID, never a silent empty list', /d\.axis\.length === 0/.test(CODE) && /axis_note/.test(CODE))
for (const w of ['activation', 'upgrade', 'byod', 'accessor', 'mailbox', 'printing',
                 'overnight', 'platinum']) {
  ck(`the card does not spell "${w}" (RULE TWO)`, !CODE.toLowerCase().includes(w))
}
ck('the card carries no goal figure of its own',
   !/1000000|1_000_000|1,000,000/.test(CODE))

console.log('\n§C — an absence is never a verdict')
ck('the on-pace verdict is three-valued in the card', /d\.on_pace == null/.test(CODE))
ck('and null is NOT rendered as behind',
   /on_pace == null \? 'Not measured'/.test(CODE) || /on_pace == null\s*\?\s*'Not measured'/.test(CODE))
ck('an unmeasured goal shows its reason in words', /d\.why/.test(CODE))
ck('a category the catalog dropped is still shown, marked',
   /m\.declared/.test(CODE) && /no longer declared/.test(CARD))
ck('a horizon that could not be read says so', /horizon\.from \?/.test(CODE))
ck('a migration that has not landed says so, separately from "no goal set"',
   /migration_pending/.test(CODE) && /!d\.declared && !d\.migration_pending/.test(CODE))

console.log('\n§D — the one home stays dereferenced on the server')
ck('annual_goal dereferences rate_to_close in executing code',
   /_targets_engine\.rate_to_close\(/.test(PYCODE))
ck('and carries no subtraction of achieved from goal itself',
   !/max\(0\.0,\s*goal\s*-/.test(PYCODE))
ck('rate_to_close exists in targets_engine as the shared home',
   /^def rate_to_close\(/m.test(TENGINE))
ck('and the per-day caller dereferences it too, so there is ONE arithmetic',
   /closed = rate_to_close\(goal, achieved, open_days_left\)/.test(TENGINE))
ck('rate_to_close names no unit, so a month cannot wear the word "per day"',
   !/per_day|per_month/.test(TENGINE.slice(TENGINE.indexOf('def rate_to_close'),
                                           TENGINE.indexOf('def backward_from_goal'))))
ck('the endpoint asks annual_goal.progress rather than folding its own',
   /_annual_goal\.progress\(/.test(ROUTER))
ck('and asks annual_goal for the horizon\'s days rather than building them',
   /_annual_goal\.horizon_bounds\(/.test(ROUTER))
ck('the DOLLAR read goes through metric_catalog.money_of, not figure_of',
   /^def money_of\(/m.test(CATALOG) && /in_money=True/.test(ROUTER))
ck('and gross_revenue dereferences the same accessor, so one rule reads the dollars',
   /money_of\(\(declared_map or \{\}\)\.get\(k\)\)/.test(CATALOG))
ck('the ranged feed read is a PARAMETER on the one read, not a sibling',
   /def _portal_metric_rows\(client, org_id, store_code, close_date, date_to=None\)/.test(ROUTER))
ck('the save is gated by the SAME permission the monthly targets use',
   /_require_target_edit\(authorization, org_id, code\)/.test(ROUTER))

console.log('\n§E — reachable, and declared once')
ck('the card is rendered on the Daily Targets screen', /<PeriodGoalCard/.test(PCODE))
ck('exactly once', (PCODE.match(/<PeriodGoalCard/g) || []).length === 1)
ck('the page does not reach the goal endpoint itself',
   !/period-goal/.test(PCODE))
// THE WHOLE TREE, not an assertion about it. A second reader of this endpoint is a second screen
// that could show a different goal, which is the §19.18 class the index rules forbid.
const SRC = []
const walk = d => {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    if (e.name === 'node_modules' || e.name === '.next') continue
    const f = join(d, e.name)
    if (e.isDirectory()) walk(f)
    else if (/\.(tsx?|jsx?|mjs)$/.test(e.name)) SRC.push(f)
  }
}
walk(join(HERE, 'src'))
const readers = SRC.filter(f => /period-goal/.test(readFileSync(f, 'utf8')))
ck('the card is the only reader of that endpoint in the whole frontend',
   readers.length === 1 && readers[0].endsWith('PeriodGoalCard.tsx'),
   readers.map(f => f.slice(HERE.length)))

console.log('\n§F — the migration says what it is and grants what it creates')
ck('it creates the table idempotently', /CREATE TABLE IF NOT EXISTS commcalc\.period_goal/.test(MIG))
ck('one goal per scope per horizon', /UNIQUE \(org_id, store_code, period, grain\)/.test(MIG))
ck('the horizon vocabulary mirrors the engine', /CHECK \(grain IN \('year', 'quarter'\)\)/.test(MIG))
ck('a goal cannot be negative', /CHECK \(goal_value >= 0\)/.test(MIG))
ck('it grants what it creates, to service_role only',
   /GRANT SELECT, INSERT, UPDATE, DELETE ON commcalc\.period_goal TO service_role;/.test(MIG)
   && !/TO anon/.test(MIG))
ck('it carries a REVERT note', /-- REVERT:/.test(MIG))
ck('it states WHY it is not a target_line row, so nobody re-derives the question',
   /target_line/.test(MIG) && /grain/.test(MIG))
ck('it seeds NO tenant row (RULE TWO)', !/INSERT INTO commcalc\.period_goal/.test(MIG))
ck('empty metric_keys is documented as unmeasured, never as "all"',
   /never read as "every category"/.test(MIG))

console.log(`\n${'='.repeat(70)}\n  ${pass} passed / ${fail} failed\n${'='.repeat(70)}`)
process.exit(fail ? 1 : 0)
