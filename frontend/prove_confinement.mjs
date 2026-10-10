// Proof harness — THE CONFINEMENT RULE: one home for "the server says fix this first"
// (index §39 the setup wizard, §14z the clock-variance gate; owner directives 2026-09-27 + 2026-10-10).
//
// Owner, 2026-10-10: "the dm should be notified as an alert as soon as they log in to fix it and
// block them from logging in unless they fix it by tuesday so the payroll can be run on Wednesday."
//
// THE PROPERTY THIS EXISTS FOR IS ★ NO LOCKOUT ★ (index §22): a confinement is never a refused
// sign-in. A person who cannot sign in cannot fix what blocks them, so the session signs in normally
// and is held on the one page that clears the block — with the account pages always reachable, and
// with an inactive, destination-less or unreadable gate confining nobody at all.
//
// WHY ONE RULE AND NOT TWO (CLAUDE.md, "A fix is a DESIGN fix"): the setup wizard was the first of
// these gates and the clock-variance gate is the second. Two copies of "is this path allowed, else
// send them to the page that clears it" is a future divergence — one copy would get the no-lockout
// rule right and the other would not. So `confineRedirect` is THE rule and every gate is a
// `Confinement` asked about it; `setupRedirect` is an adapter onto it and is proved to still answer
// byte-identically to the behaviour it had before.
//
// Like prove_flow_return.mjs this transpiles the REAL src/lib/setup-gate.ts with the project's own
// TypeScript and executes it — a rename fails loudly rather than passing a stub. It also reads the
// REAL (platform) layout source, because a rule no gate calls is not a rule.
//
// Run:  node frontend/prove_confinement.mjs      (no network, no DB, no browser, no React)

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
  const dir = mkdtempSync(join(tmpdir(), 'confineproof-'))
  const modPath = join(dir, 'm.mjs')
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const m = await loadModule('src/lib/setup-gate.ts')
const { confineRedirect, setupRedirect, setupGateApplies, clockGateConfinement } = m
for (const [n, f] of Object.entries({ confineRedirect, setupRedirect, setupGateApplies, clockGateConfinement }))
  must(typeof f === 'function', `${n} did not export a function from setup-gate.ts`)

const BOARD = '/storeops/payroll/clock-variances'
// The server's own answer, shape-for-shape: GET /storeops/payroll/clock-gate.
const gatedReply = {
  alert: true, gated: true, correction_path: BOARD,
  allowed_paths: [BOARD, '/login', '/logout', '/auth', '/api/storeops/payroll/clock-variance'],
  reason: '3 clock variance(s) are still uncorrected and payroll runs on this org\'s run weekday (2).',
}

// ── 1. ★ NO LOCKOUT ★ — what a confined person can still reach ─────────────────────────────────
console.log('\n1. ★ NO LOCKOUT ★ : confined to the fix, never shut out')
{
  const c = clockGateConfinement(gatedReply)
  ck('the correction board itself opens', confineRedirect(c, BOARD) === null)
  ck('a sub-page of the board opens too', confineRedirect(c, BOARD + '/history') === null)
  // THE PROPERTY, in one assertion: the owner asked for the sign-in to be blocked, and it is not.
  ck('the sign-in page is NOT closed', confineRedirect(c, '/login') === null)
  ck('signing OUT is not closed either', confineRedirect(c, '/logout') === null)
  // Without these a confined person cannot change a password or leave — the lockout wearing a hat.
  ck('the account pages stay open, though no gate listed them',
     confineRedirect(c, '/account/password') === null)
  // And everything else is held, with the destination NAMED rather than a bare refusal.
  for (const p of ['/storeops/payroll', '/commcalc/sales-report', '/hr', '/'])
    ck(`${p} is held on the correction board`, confineRedirect(c, p) === BOARD)
}

// ── 2. A gate fails OPEN, in every way it can fail ─────────────────────────────────────────────
console.log('\n2. An inactive, empty or unreadable gate confines NOBODY')
{
  // `null` is what the layout holds when the fetch failed. A gate that cannot look does not gate —
  // failing closed here would lock a DM out of the whole app over a database hiccup, which is the
  // property failing in the exact direction it exists to prevent.
  ck('an unreadable gate (null) holds nothing', confineRedirect(null, '/hr') === null)
  ck('undefined holds nothing', confineRedirect(undefined, '/hr') === null)
  ck('the server answering gated:false holds nothing',
     confineRedirect(clockGateConfinement({ gated: false, alert: true, correction_path: BOARD }), '/hr') === null)
  ck('the feature switched off (an empty reply) holds nothing',
     confineRedirect(clockGateConfinement({}), '/hr') === null)
  // A gate with nowhere to send anybody would redirect to '' — i.e. nowhere, i.e. a trap.
  ck('a gate with NO destination holds nothing rather than redirecting to nowhere',
     confineRedirect({ active: true, destination: '', allow_paths: ['/x'] }, '/hr') === null)
  ck('…and whitespace is not a destination',
     confineRedirect({ active: true, destination: '   ' }, '/hr') === null)
}

// ── 3. A prefix is a PATH prefix, not a string prefix ──────────────────────────────────────────
console.log('\n3. An allowed path matches a path, not a spelling')
{
  const c = { active: true, destination: '/a/board', allow_paths: ['/a/open'] }
  ck('the exact path opens', confineRedirect(c, '/a/open') === null)
  ck('a child path opens', confineRedirect(c, '/a/open/row/7') === null)
  // The trap: '/a/opened' starts with '/a/open' as a STRING. A sibling route whose name merely
  // begins with an allowed one must stay held, or a gate is bypassed by naming a page carefully.
  ck('a SIBLING whose name starts the same is still held', confineRedirect(c, '/a/opened') === '/a/board')
  ck('…and so is a longer sibling', confineRedirect(c, '/a/openxyz') === '/a/board')
}

// ── 4. THE SETUP WIZARD STILL ANSWERS EXACTLY AS IT DID ───────────────────────────────────────
console.log('\n4. The first gate is unchanged — the rule was factored out, not rewritten')
{
  ck('inactive → nothing', setupRedirect({ active: false }, '/hr') === null)
  ck('null → nothing', setupRedirect(null, '/hr') === null)
  const g = { active: true, wizard_path: '/commcalc/upload/wizard', allow_paths: ['/commcalc/email-imports'] }
  ck('the wizard itself opens', setupRedirect(g, '/commcalc/upload/wizard') === null)
  ck('a wizard sub-page opens', setupRedirect(g, '/commcalc/upload/wizard/step-2') === null)
  ck('a server-allowed page opens', setupRedirect(g, '/commcalc/email-imports') === null)
  ck('the account pages open', setupRedirect(g, '/account/password') === null)
  ck('anything else goes to the wizard', setupRedirect(g, '/hr') === '/commcalc/upload/wizard')
  // The house default destination belongs to THIS gate, not to the rule.
  ck('a gate that names no wizard still has one',
     setupRedirect({ active: true }, '/hr') === '/commcalc/upload/wizard')
  ck('who is walked there is unchanged', setupGateApplies(true, false) === true
     && setupGateApplies(true, true) === false && setupGateApplies(false, false) === false)
}

// ── 5. THE RULE IS ACTUALLY CALLED — a rule no gate uses is not a rule ────────────────────────
console.log('\n5. Both gates go through the one rule, in the shipped layout')
{
  const layout = readFileSync(join(HERE, 'src/app/(platform)/layout.tsx'), 'utf8')
  ck('the layout imports the rule', /confineRedirect/.test(layout))
  ck('the clock gate is asked through it', /confineRedirect\(\s*clockGateConfinement\(/.test(layout))
  ck('the setup gate still uses its adapter', /setupRedirect\(/.test(layout))
  // THE LOCK: no gate may compute its own redirect. A hand-rolled `startsWith` ladder beside the
  // rule is the second copy this whole module exists to prevent.
  const rolled = layout.match(/router\.replace\([^)]*\)/g) || []
  ck('every gate redirect comes from a named decision, never an inline path ladder',
     !/allowed_paths[\s\S]{0,200}?startsWith/.test(layout), `router.replace sites: ${rolled.length}`)
  // THE ALERT IS SEPARATE FROM THE CONFINEMENT. A manager told on Monday and confined on Monday has
  // lost the day they were given to fix it, so the banner reads `alert` and the hold reads `gated`.
  ck('the banner fires on `alert`, which is not the same field as `gated`',
     /clockGate\?\.alert/.test(layout) && /clockGate\.gated/.test(layout))
  // ★ NO LOCKOUT ★, at the layout level: the gate must not be able to sign anybody out.
  ck('the clock gate never signs anybody out', !/clockGate[\s\S]{0,400}?signOut\(/.test(layout))
}

// ── 6. ARMED NEGATIVE CONTROL ─────────────────────────────────────────────────────────────────
console.log('\n6. Armed negative control — the assertions above can fail')
{
  const before = fail
  ck('ARMED: an active gate lets an unrelated page through', confineRedirect(
    clockGateConfinement(gatedReply), '/hr') === null)
  ck('ARMED: a dead gate confines', confineRedirect(null, '/hr') === '/x')
  const fired = fail - before
  if (fired === 2) { fail = before; pass++; console.log('  ok  both wrong expectations failed (the checks are live)') }
  else { console.error(`  XX  NEGATIVE CONTROL DID NOT FIRE — ${fired}/2 wrong answers accepted`) }
}

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) { console.error('THE CONFINEMENT RULE IS BROKEN, or a gate has stopped using it.'); process.exit(1) }
console.log('OK — one rule for every confinement, and ★ NO LOCKOUT ★ holds in all of them.')
