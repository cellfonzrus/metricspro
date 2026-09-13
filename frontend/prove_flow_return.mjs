// Proof harness — THE WAY BACK (owner bug report 2026-09-13).
//
// "now since it brought me from the implementation wizard once im done here it shoudl take me back
// there, if im a new tenant i dont know how to navaagte … the modules if they take you to the next
// module then they shoudl have the option of continuing with thier original work."
//
// The reported trail, reproduced verbatim in §2 below:
//     /commcalc/implementation  →  /commcalc/onboarding  →  /commcalc/email-imports
// Three hops, and at the end nothing on screen referred to the flow that sent you.
//
// Like prove_carrier_scope.mjs / prove_tenant_scope.mjs this transpiles the REAL src/lib/flow-return.ts
// with the project's own TypeScript and executes it — a rename fails loudly rather than passing a stub.
//
// Run:  node frontend/prove_flow_return.mjs      (no network, no DB, no browser, no React)

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
  const dir = mkdtempSync(join(tmpdir(), 'flowproof-'))
  const modPath = join(dir, 'm.mjs')
  writeFileSync(modPath, js, 'utf8')
  return import(pathToFileURL(modPath).href)
}

const m = await loadModule('src/lib/flow-return.ts')
const { normalizePath, isFlowPath, nextTrail, returnOffer, offerIsStale, FLOW_SUFFIXES } = m
for (const [n, f] of Object.entries({ normalizePath, isFlowPath, nextTrail, returnOffer, offerIsStale }))
  must(typeof f === 'function', `${n} did not export a function from flow-return.ts`)
must(Array.isArray(FLOW_SUFFIXES) && FLOW_SUFFIXES.length > 0, 'FLOW_SUFFIXES must be a non-empty array')

const VZONE = 'f4f1c16e-2acf-4221-854a-c29a605754a7'
const HOUSE = '00000000-0000-0000-0000-000000000001'

// ── 1. A flow is recognised by SHAPE, so a seventh wizard needs no edit ────────────────────────
console.log('\n1. What counts as a setup flow is a shape, not a list')
{
  // The six that exist today (index §26.1) — none of them named in the rule.
  for (const p of ['/commcalc/implementation', '/commcalc/onboarding', '/pos/onboarding',
                   '/vision/onboarding', '/hr/onboarding', '/commcalc/upload/wizard'])
    ck(`${p} is a flow`, isFlowPath(p) === true)
  ck('a flow that does not exist yet is covered for free', isFlowPath('/billing/onboarding') === true)
  ck('  …and a brand-new suffix shape too', isFlowPath('/anything/setup') === true)

  ck('an ordinary page is NOT a flow', isFlowPath('/commcalc/email-imports') === false)
  ck('  …nor a report', isFlowPath('/commcalc/exec/mtd') === false)
  ck('the root is not a flow', isFlowPath('/') === false)
  ck('empty / null are not flows', isFlowPath('') === false && isFlowPath(null) === false)
  ck('a name that merely CONTAINS a suffix mid-path is not a flow',
     isFlowPath('/commcalc/onboarding-notes') === false)

  ck('a query string does not change what a path is', isFlowPath('/x/wizard?step=2') === true)
  ck('  …nor a fragment', isFlowPath('/x/wizard#top') === true)
  ck('  …nor a trailing slash', isFlowPath('/x/wizard/') === true)
  ck('normalizePath strips query, fragment and trailing slash',
     normalizePath('/x/wizard/?a=1#b') === '/x/wizard')
  ck('  …and leaves the root alone', normalizePath('/') === '/')
}

// ── 2. THE REPORTED TRAIL, hop by hop ─────────────────────────────────────────────────────────
console.log('\n2. REGRESSION — the owner\'s exact three hops')
{
  let t = null
  t = nextTrail(t, '/commcalc/implementation', VZONE)          // hop 1: the flow
  ck('hop 1 — standing on the implementation wizard, it is remembered', t?.path === '/commcalc/implementation')
  ck('  …and no way-back is offered while you are ON it', returnOffer(t, '/commcalc/implementation', VZONE) === null)

  t = nextTrail(t, '/commcalc/onboarding', VZONE)              // hop 2: another flow
  ck('hop 2 — the setup wizard becomes the remembered flow', t?.path === '/commcalc/onboarding')

  t = nextTrail(t, '/commcalc/email-imports', VZONE)           // hop 3: OUT of the flow
  ck('hop 3 — leaving the flow does not forget it', t?.path === '/commcalc/onboarding')
  const offer = returnOffer(t, '/commcalc/email-imports', VZONE)
  ck('THE FIX: email auto-import now offers the way back', offer !== null)
  ck('  …to the flow that actually sent you', offer?.href === '/commcalc/onboarding')
  ck('  …with the proven wording from pos/layout.tsx', offer?.label === '← Back to setup')
  ck('  …and it is not the stood-down variant', offerIsStale(offer) === false)

  // A further hop away must keep working — the complaint was about the END of the trail.
  const t4 = nextTrail(t, '/commcalc/upload', VZONE)
  ck('still offered two pages later', returnOffer(t4, '/commcalc/upload', VZONE)?.href === '/commcalc/onboarding')
}

// ── 3. It never invents a destination ─────────────────────────────────────────────────────────
console.log('\n3. Nothing is ever invented')
{
  ck('no flow visited ⇒ no offer, ever', returnOffer(null, '/commcalc/email-imports', VZONE) === null)
  ck('  …not even on a flow page', returnOffer(null, '/pos/onboarding', VZONE) === null)
  ck('a trail with no path ⇒ no offer', returnOffer({ path: '', org: VZONE }, '/x', VZONE) === null)
  ck('never links to the page you are already on',
     returnOffer({ path: '/pos/onboarding', org: VZONE }, '/pos/onboarding', VZONE) === null)
  ck('  …including when only a query string differs',
     returnOffer({ path: '/pos/onboarding', org: VZONE }, '/pos/onboarding?step=3', VZONE) === null)
  ck('a browse that never touches a flow keeps the trail null',
     nextTrail(nextTrail(null, '/commcalc/upload', VZONE), '/commcalc/exec/mtd', VZONE) === null)
}

// ── 4. The cross-company case — closes the loop with §28 ──────────────────────────────────────
console.log('\n4. A flow started for one company is not resumed in another')
{
  // The header switcher reloads the whole app, so the trail outlives the switch. Sending someone
  // "back to setup" would drop them into one company's setup while the app acts as another — the
  // exact confusion the 20:48 incident (index §28) was made of.
  const t = nextTrail(null, '/commcalc/onboarding', VZONE)
  const same = returnOffer(t, '/commcalc/email-imports', VZONE)
  ck('same company ⇒ the ordinary way back', same.label === '← Back to setup' && offerIsStale(same) === false)

  const moved = returnOffer(t, '/commcalc/email-imports', HOUSE)
  ck('company switched mid-flow ⇒ the offer STANDS DOWN', offerIsStale(moved) === true)
  ck('  …and says so rather than pretending the trail is good',
     /different company/i.test(moved?.note || ''))
  ck('  …and still names where the flow was, so it is recoverable',
     !!moved?.note?.includes('/commcalc/onboarding'))

  // Never a FALSE alarm: an unknown org on either side is "don't know", not "you moved".
  const noOrg = nextTrail(null, '/commcalc/onboarding', null)
  ck('flow started with no org known ⇒ no false alarm',
     offerIsStale(returnOffer(noOrg, '/x', HOUSE)) === false)
  ck('active org not yet resolved ⇒ no false alarm',
     offerIsStale(returnOffer(t, '/x', null)) === false)
  ck('  …and the way back is still offered in both cases',
     returnOffer(noOrg, '/x', HOUSE) !== null && returnOffer(t, '/x', null) !== null)
}

console.log(`\n${pass} checks passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
