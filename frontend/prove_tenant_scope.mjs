// Proof harness — "which company am I in" (cross-tenant visibility, 2026-09-13).
//
// Reproduces the live incident this exists to prevent. From `core.access_log`, login
// ss@1313global.us (a member of THREE companies, none flagged is_default_org):
//
//     20:27:50  Vzone   200  /commcalc/email-sweep/accounts
//     20:48:25  HOUSE   200  /commcalc/email-sweep/accounts     ← another company's mailbox
//     20:53:18  Vzone   200  /core/employees
//
// Every response was correct server-side — that login really is an admin in all three. The defect
// was that nothing on screen said which company was being rendered, and the one control that did
// (an unlabelled <select>) could DISPLAY THE WRONG COMPANY, because a <select> whose bound value
// matches no <option> falls through to showing the first one, and memberships arrive oldest-first.
//
// Like prove_carrier_scope.mjs this does NOT re-implement anything: it transpiles the REAL module
// src/lib/tenant-scope.ts with the project's own TypeScript compiler and executes it. Rename or
// delete a function and this fails loudly rather than passing on a stub.
//
// Run:  node frontend/prove_tenant_scope.mjs      (no network, no DB, no browser, no React)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'

const HERE = dirname(fileURLToPath(import.meta.url))
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`) } }
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }

async function loadModule(relPath) {
  const src = readFileSync(join(HERE, relPath), 'utf8')
  const js = ts.transpileModule(src, { compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext } }).outputText
  const dir = mkdtempSync(join(tmpdir(), 'tenantproof-'))
  const modPath = join(dir, 'm.mjs')
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const m = await loadModule('src/lib/tenant-scope.ts')
const { actingCompany, switcherOptions, switcherVisible, switchConfirmText, flowTenantMismatch,
        CHOOSE_COMPANY, UNNAMED_COMPANY } = m
for (const [n, f] of Object.entries({ actingCompany, switcherOptions, switcherVisible, switchConfirmText, flowTenantMismatch }))
  must(typeof f === 'function', `${n} did not export a function from tenant-scope.ts`)
must(typeof CHOOSE_COMPANY === 'string' && CHOOSE_COMPANY, 'CHOOSE_COMPANY must be a non-empty string')

// ── Fixtures: the REAL shape of the incident, memberships oldest-first as list_memberships orders
//    them (storeops.app_users ORDER BY created_at). House is FIRST for this login — that is exactly
//    what makes rule 2 load-bearing.
const HOUSE = '00000000-0000-0000-0000-000000000001'
const LUXE  = '854f6d7b-6590-4e4d-88ab-646f560d4f4c'
const VZONE = 'f4f1c16e-2acf-4221-854a-c29a605754a7'
const MEMS = [
  { org_id: HOUSE, name: 'Cellfonz R Us' },          // created 2026-06-15
  { org_id: LUXE,  name: 'Luxelink Wireless LLC' },  // created 2026-07-14
  { org_id: VZONE, name: 'Vzone' },                  // created 2026-08-09
]
const SOLO = [{ org_id: VZONE, name: 'Vzone' }]

// ── 1. actingCompany NEVER GUESSES ────────────────────────────────────────────────────────────
console.log('\n1. The acting company is resolved from a membership, or not at all')
{
  const a = actingCompany(MEMS, VZONE)
  ck('a chosen membership resolves to its own name', a.resolved && a.name === 'Vzone' && a.org_id === VZONE)
  ck('  …and reports reason ok', a.reason === 'ok')

  const b = actingCompany(MEMS, HOUSE)
  ck('the house org resolves like any other membership (it is a real company here)',
     b.resolved && b.name === 'Cellfonz R Us')

  const none = actingCompany(MEMS, null)
  ck('NO choice ⇒ unresolved, NOT "the first membership"', !none.resolved && none.name === null)
  ck('  …and does NOT quietly name the house org', none.org_id !== HOUSE)
  ck('  …reason none_chosen', none.reason === 'none_chosen')

  const ghost = actingCompany(MEMS, 'deadbeef-0000-0000-0000-000000000000')
  ck('an org this login does NOT belong to ⇒ unresolved', !ghost.resolved)
  ck('  …never downgraded to some other company', ghost.name === null)
  ck('  …reason not_a_membership', ghost.reason === 'not_a_membership')

  ck('empty membership list ⇒ unresolved, no throw', actingCompany([], VZONE).resolved === false)
  ck('null/undefined inputs are tolerated', actingCompany(null, undefined).resolved === false)
  ck('whitespace around the stored org id still matches', actingCompany(MEMS, `  ${VZONE} `).name === 'Vzone')
  ck('a membership with no name renders the neutral noun, never blank',
     actingCompany([{ org_id: VZONE, name: '' }], VZONE).name === UNNAMED_COMPANY)
}

// ── 2. THE REGRESSION: the switcher cannot display a company it is not acting as ───────────────
console.log('\n2. REGRESSION — the <select> can never display the wrong company')
{
  // The exact defect: value={activeOrg || ''} with activeOrg null, options oldest-first. Without a
  // placeholder the browser shows options[0] = Cellfonz R Us while the session acts as nothing.
  const opts = switcherOptions(MEMS, null)
  ck('unresolved ⇒ a placeholder is prepended', opts[0] && opts[0].placeholder === true)
  ck('  …and it is what value="" binds to', opts[0].value === '')
  ck('  …so the FIRST option is not a company name', opts[0].label === CHOOSE_COMPANY)
  ck('  …the house org is NOT what the control displays', opts[0].label !== 'Cellfonz R Us')
  ck('  …every real membership is still offered', opts.filter(o => !o.placeholder).length === MEMS.length)

  const ghostOpts = switcherOptions(MEMS, 'deadbeef-0000-0000-0000-000000000000')
  ck('an org outside the memberships also gets the placeholder', ghostOpts[0].placeholder === true)

  const resolved = switcherOptions(MEMS, VZONE)
  ck('RESOLVED ⇒ no placeholder (byte-identical to the old list)', resolved.every(o => !o.placeholder))
  ck('  …same length as the membership list', resolved.length === MEMS.length)
  ck('  …same order as the membership list',
     resolved.map(o => o.value).join('|') === MEMS.map(t => t.org_id).join('|'))
  ck('  …the bound value matches exactly one option',
     resolved.filter(o => o.value === VZONE).length === 1)

  ck('a membership with no org_id is dropped, not rendered empty',
     switcherOptions([{ org_id: '', name: 'x' }, { org_id: VZONE, name: 'Vzone' }], VZONE).length === 1)
}

// ── 3. Who is offered the switcher at all ─────────────────────────────────────────────────────
console.log('\n3. The switcher is offered only where switching is possible')
{
  ck('>1 membership ⇒ offered', switcherVisible(MEMS, false) === true)
  ck('exactly 1 membership ⇒ hidden (nothing to switch to)', switcherVisible(SOLO, false) === false)
  ck('0 memberships ⇒ hidden', switcherVisible([], false) === false)
  ck('impersonating ⇒ hidden even with 3 memberships (the grant pins the company)',
     switcherVisible(MEMS, true) === false)
  ck('null tenants ⇒ hidden, no throw', switcherVisible(null, false) === false)
}

// ── 4. Leaving a company is confirmed, and both companies are named ───────────────────────────
console.log('\n4. Leaving a company names both companies')
{
  const t = switchConfirmText('Vzone', 'Cellfonz R Us')
  ck('the company being LEFT is named', t.includes('Vzone'))
  ck('the company being ENTERED is named', t.includes('Cellfonz R Us'))
  ck('it says the change is not a view filter', /every page/i.test(t))
  ck('it says the way back', /switch back/i.test(t))
  ck('same company ⇒ no confirmation', switchConfirmText('Vzone', 'Vzone') === '')
  ck('nothing to leave ⇒ no confirmation', switchConfirmText(null, 'Vzone') === '')
  ck('nothing to enter ⇒ no confirmation', switchConfirmText('Vzone', '') === '')
}

// ── 5. A pinned flow notices it is being rendered for another company ─────────────────────────
console.log('\n5. A pinned setup flow notices a company change across a page load')
{
  ck('THE INCIDENT: flow pinned to Vzone, session flipped to house ⇒ mismatch',
     flowTenantMismatch(VZONE, HOUSE) === true)
  ck('same company ⇒ no mismatch', flowTenantMismatch(VZONE, VZONE) === false)
  ck('nothing pinned ⇒ no mismatch (never a false alarm on a fresh flow)',
     flowTenantMismatch(null, HOUSE) === false)
  ck('no active company ⇒ no mismatch (the placeholder already says so)',
     flowTenantMismatch(VZONE, null) === false)
  ck('whitespace does not fake a mismatch', flowTenantMismatch(` ${VZONE}`, `${VZONE} `) === false)
}

console.log(`\n${pass} checks passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
