#!/usr/bin/env node
// PROOF for the 2026-08-05 owner directive: "run commission button should also be on the commission
// plan payout and multi month page so when the commission structure is updated it should be easily
// accessible."
//
// This exercises the SHIPPED code, not a re-implementation:
//   • commcalc/_lib/runCommission.ts       — the controller (confirm gate, 409, 502, polling, totals)
//   • commcalc/_lib/RunCommissionButton.tsx — the control + the panel, rendered with react-dom/server
//   • src/lib/rbac.ts                       — the REAL permission gate (compiled verbatim, not a stub)
//
// What it proves
//   1. MOUNTS      — the shared component is imported and mounted on commission-plans, payout-schedules,
//                    plan-installments and payout-plans, and it is the ONLY implementation
//                    (no page still hand-rolls POST /commcalc/calculate).
//   2. CONFIRM     — a click sends NOTHING; only the confirm may POST; confirm() from any other phase
//                    is a hard no-op.
//   3. 409         — rendered as a friendly "already running (started N minutes ago)", not an error,
//                    and it does not start a poll loop.
//   4. 502         — treated as "still running", polled to completion, and NEVER re-fired.
//                    `state.posts === 1` is asserted on every single path, including the timeout path.
//   5. SUCCESS     — the new total and the delta vs before are rendered.
//   6. PERIOD      — the period named in the confirm the human reads is byte-identical to the period in
//                    the POST URL, and to the period the button displays.
//
// Run:  node frontend/tools/run-commission-proof.mjs
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, readFileSync, writeFileSync, symlinkSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import os from 'node:os'
import path from 'node:path'

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const APP = path.join(FRONTEND, 'src/app/(platform)/commcalc')
const LIB = path.join(APP, '_lib')
const OUT = mkdtempSync(path.join(os.tmpdir(), 'runcomm-'))
process.on('exit', () => rmSync(OUT, { recursive: true, force: true }))
symlinkSync(path.join(FRONTEND, 'node_modules'), path.join(OUT, 'node_modules'))
writeFileSync(path.join(OUT, 'package.json'), '{"type":"module"}')

let pass = 0, fail = 0
const ok = (name, cond, detail = '') => {
  if (cond) { pass++; console.log(`  PASS  ${name}`) }
  else { fail++; console.log(`  FAIL  ${name}${detail ? ` — ${detail}` : ''}`) }
}
const section = t => console.log(`\n${t}`)

// ── 1. MOUNTS (static, over the real page sources) ───────────────────────────────────────────────
section('1. one shared implementation, mounted on every commission-structure page')
const PAGES = {
  'commission-plans': 'commission-plans/page.tsx',
  'payout-schedules': 'payout-schedules/page.tsx',
  'plan-installments': 'plan-installments/page.tsx',
  'payout-plans': 'payout-plans/page.tsx',
}
const pageSrc = {}
for (const [name, rel] of Object.entries(PAGES)) {
  const src = readFileSync(path.join(APP, rel), 'utf8')
  pageSrc[name] = src
  ok(`${name} imports the shared component`,
    /import RunCommissionButton from '\.\.\/_lib\/RunCommissionButton'/.test(src))
  ok(`${name} mounts <RunCommissionButton>`, /<RunCommissionButton\b/.test(src))
  ok(`${name} passes its own period to it`, /<RunCommissionButton[\s\S]{0,240}?period=\{period\}/.test(src))
}
// The whole point of extracting it: no page may hand-roll the calculate call any more.
for (const [name, src] of Object.entries(pageSrc)) {
  ok(`${name} no longer hand-rolls POST /commcalc/calculate`, !/commcalc\/calculate\//.test(src),
    'a bespoke calculate call is still in this page')
}

// ── compile the shipped modules (verbatim, only the path aliases repointed) ───────────────────────
const clientSrc = readFileSync(path.join(FRONTEND, 'src/lib/client.ts'), 'utf8')
ok('the fmt shim matches the real fmt in lib/client.ts',
  /export const fmt = \(n: number\) =>\s*\n\s*new Intl\.NumberFormat\('en-US', \{ style: 'currency', currency: 'USD' \}\)\.format\(n \|\| 0\)/
    .test(clientSrc))
ok('the ORG_ID shim matches the real ORG_ID in lib/client.ts',
  /export const ORG_ID = '00000000-0000-0000-0000-000000000001'/.test(clientSrc))

mkdirSync(path.join(OUT, 'src'))
const W = (f, s) => writeFileSync(path.join(OUT, 'src', f), s)

// `api` MUST NOT be reachable from a static render — if the component ever calls it during render this
// throws and the proof fails loudly instead of silently hitting the network.
W('lib-client.ts', `
export const fmt = (n: number) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n || 0)
export const ORG_ID = '00000000-0000-0000-0000-000000000001'
export const apiCalls: string[] = []
export async function api(p: string, init?: RequestInit): Promise<unknown> {
  apiCalls.push(((init && init.method) || 'GET') + ' ' + p)
  throw new Error('the component must not call api() during render')
}
`)
W('lib-auth.tsx', `
type Perms = Record<string, unknown>
let _perms: Perms = { scope: 'all', modules: { commissions: true } }
let _tenant: { name?: string } | null = { name: 'luxelink' }
export function __set(p: Perms, t: { name?: string } | null) { _perms = p; _tenant = t }
export function useAuth() { return { permissions: _perms, tenant: _tenant } as any }
`)
W('EntityPicker.tsx', `
export default function EntityPicker(props: any) {
  return <span data-testid="period-picker" data-value={props.value || ''}>{props.value || props.placeholder}</span>
}
`)
// the REAL rbac (no imports of its own) — the permission gate is proven, not stubbed
W('rbac.ts', readFileSync(path.join(FRONTEND, 'src/lib/rbac.ts'), 'utf8'))
W('runCommission.ts', readFileSync(path.join(LIB, 'runCommission.ts'), 'utf8'))
W('RunCommissionButton.tsx', readFileSync(path.join(LIB, 'RunCommissionButton.tsx'), 'utf8')
  .replace("'@/lib/client'", "'./lib-client'")
  .replace("'@/lib/auth-context'", "'./lib-auth'")
  .replace("'@/lib/rbac'", "'./rbac'")
  .replace("'@/components/EntityPicker'", "'./EntityPicker'"))

execFileSync(path.join(FRONTEND, 'node_modules/.bin/tsc'), [
  ...['lib-client.ts', 'lib-auth.tsx', 'EntityPicker.tsx', 'rbac.ts', 'runCommission.ts', 'RunCommissionButton.tsx']
    .map(f => path.join(OUT, 'src', f)),
  '--outDir', path.join(OUT, 'js'), '--target', 'es2020', '--module', 'es2020',
  '--moduleResolution', 'node', '--jsx', 'react-jsx', '--esModuleInterop', '--skipLibCheck',
], { stdio: 'inherit' })
for (const f of ['lib-client.js', 'lib-auth.js', 'EntityPicker.js', 'rbac.js', 'runCommission.js', 'RunCommissionButton.js']) {
  const p = path.join(OUT, 'js', f)
  writeFileSync(p, readFileSync(p, 'utf8').replace(/from '(\.\/[^']+?)'/g, (m, s) => `from '${s}.js'`))
}

const React = (await import('react')).default
const { renderToStaticMarkup } = await import('react-dom/server.node')
const RC = await import(pathToFileURL(path.join(OUT, 'js/runCommission.js')).href)
const BTN = await import(pathToFileURL(path.join(OUT, 'js/RunCommissionButton.js')).href)
const AUTH = await import(pathToFileURL(path.join(OUT, 'js/lib-auth.js')).href)
const RunCommissionButton = BTN.default
const { RunCommissionPanel } = BTN
const ORG = '00000000-0000-0000-0000-000000000001'
const PERIOD = 'July 2026'
const ENC_POST = `POST /api/v1/commcalc/calculate/July%202026?org_id=${ORG}`

// ── a scripted api() that RECORDS every call ─────────────────────────────────────────────────────
function harness(script) {
  const calls = []
  const api = async (p, init) => {
    const method = (init && init.method) || 'GET'
    calls.push(`${method} ${p}`)
    for (const [re, h] of script) if (re.test(p) && (h.method || 'GET') === method) return h.fn(calls)
    return {}
  }
  const states = []
  const ctrl = RC.createRunCommission({
    api, orgId: ORG, tenant: 'luxelink', onChange: s => states.push({ ...s }),
    sleep: async () => {}, pollEveryMs: 1, maxPollMs: 50,
  })
  const posts = () => calls.filter(c => c.startsWith('POST ')).length
  return { api, calls, states, ctrl, posts }
}
const rows = total => [{ epay_salesperson: 'A', total_payout: total }]
const GET = fn => ({ method: 'GET', fn })
const POST = fn => ({ method: 'POST', fn })

// ── 2. CONFIRM IS THE GATE ───────────────────────────────────────────────────────────────────────
section('2. confirm is required before anything is sent')
{
  const h = harness([[/calculate/, POST(() => ({ status: 'started' }))]])
  // a cold confirm() — the "double-click / stray call" case
  await h.ctrl.confirm()
  ok('confirm() from idle sends nothing', h.calls.length === 0, h.calls.join(' | '))
  ok('confirm() from idle leaves phase idle', h.ctrl.get().phase === 'idle', h.ctrl.get().phase)

  h.ctrl.request(PERIOD)
  ok('pressing the button sends NOTHING (only opens the confirm)', h.calls.length === 0, h.calls.join(' | '))
  ok('pressing the button moves to phase=confirm', h.ctrl.get().phase === 'confirm')
  ok('the confirm carries the period that was pressed', h.ctrl.get().period === PERIOD)

  h.ctrl.cancel()
  ok('cancel sends nothing and returns to idle',
    h.calls.length === 0 && h.ctrl.get().phase === 'idle')
  await h.ctrl.confirm()
  ok('confirm() after cancel is a no-op (still nothing sent)', h.calls.length === 0, h.calls.join(' | '))
}

// ── 3. SUCCESS ───────────────────────────────────────────────────────────────────────────────────
section('3. success — one POST, polled to completion, total + delta reported')
let doneState = null
{
  let commissionReads = 0
  let statusReads = 0
  const h = harness([
    [/\/commissions\//, GET(() => (++commissionReads === 1 ? rows(2830.72) : rows(2142.37)))],
    [/\/calculate\//, POST(() => ({ status: 'started', period: PERIOD }))],
    [/\/calc-status\//, GET(() => (++statusReads < 3 ? { calc_status: 'running' } : { calc_status: 'done' }))],
  ])
  h.ctrl.request(PERIOD)
  await h.ctrl.confirm()
  const s = h.ctrl.get()
  doneState = s
  ok('phase = done', s.phase === 'done', s.phase)
  ok('exactly ONE POST /calculate was issued', h.posts() === 1 && s.posts === 1, h.calls.join(' | '))
  ok('the POST carries the period that was confirmed', h.calls.includes(ENC_POST), h.calls.join(' | '))
  ok('the POST is org-scoped as a query param (contract §2)', ENC_POST.includes(`org_id=${ORG}`))
  ok('before-total captured from the live report', s.before === 2830.72, String(s.before))
  ok('after-total captured from the live report', s.after === 2142.37, String(s.after))
  ok('delta is reported to the owner', RC.deltaLabel(s) === '−$688.35 vs before ($2,830.72)', String(RC.deltaLabel(s)))
  ok('it polled calc-status rather than re-POSTing',
    h.calls.filter(c => c.includes('/calc-status/')).length === 3, h.calls.join(' | '))
}

// ── 4. 409 — ALREADY RUNNING ─────────────────────────────────────────────────────────────────────
section('4. 409 = a recompute is already running — friendly wait, not an error')
let busyState = null
{
  const since = new Date(Date.now() - 3 * 60000).toISOString()
  const msg = `A calculation for ${PERIOD} is already running (started ${since}). Refusing to start a ` +
    `second one — two recomputes at once would interleave the delete-and-rewrite of the commission rows.`
  const h = harness([
    [/\/commissions\//, GET(() => rows(2830.72))],
    [/\/calculate\//, POST(() => { throw new Error(msg) })],
    [/\/calc-status\//, GET(() => ({ calc_status: 'running' }))],
  ])
  h.ctrl.request(PERIOD)
  await h.ctrl.confirm()
  const s = h.ctrl.get()
  busyState = s
  ok('the 409 is classified as busy, not failure', RC.isBusyError(new Error(msg)) === true)
  ok('phase = busy', s.phase === 'busy', s.phase)
  ok('the running-since timestamp is recovered from the server message', s.runningSince === since, String(s.runningSince))
  ok('it says "already running" and that this press started nothing',
    /already running/i.test(s.message) && /started by this press/i.test(s.message), s.message)
  ok('a 409 does NOT start a poll loop', h.calls.filter(c => c.includes('/calc-status/')).length === 0, h.calls.join(' | '))
  ok('a 409 issues exactly one POST and never a second', h.posts() === 1 && s.posts === 1, h.calls.join(' | '))
  ok('"3 minutes ago" is rendered for the operator', RC.humanSince(since) === '3 minutes ago', RC.humanSince(since))
}

// ── 5. 502 — GATEWAY TIMEOUT ON A RUN THAT COMPLETES ─────────────────────────────────────────────
section('5. 502 = still running — polled, never re-fired ([[recompute-gateway-timeout]])')
let gatewayState = null
{
  let statusReads = 0, commissionReads = 0
  const h = harness([
    [/\/commissions\//, GET(() => (++commissionReads === 1 ? rows(1000) : rows(1250)))],
    [/\/calculate\//, POST(() => { throw new Error('Bad Gateway') })],
    [/\/calc-status\//, GET(() => (++statusReads < 4 ? { calc_status: 'running' } : { calc_status: 'done' }))],
  ])
  h.ctrl.request(PERIOD)
  gatewayState = h.states.find(() => false) // placeholder, replaced below
  await h.ctrl.confirm()
  const s = h.ctrl.get()
  gatewayState = h.states.find(x => x.phase === 'running' && x.gatewayTimeout)
  ok('"Bad Gateway" is classified as a gateway timeout, not a busy-guard refusal',
    RC.isGatewayError(new Error('Bad Gateway')) === true && RC.isBusyError(new Error('Bad Gateway')) === false)
  ok('a 502 is shown as "still running", never as a failure',
    !!gatewayState && /still running/i.test(gatewayState.message), gatewayState && gatewayState.message)
  ok('THE 502 IS NOT AUTO-RETRIED — exactly one POST for the whole run',
    h.posts() === 1 && s.posts === 1, h.calls.join(' | '))
  ok('it polled calc-status through to completion instead',
    h.calls.filter(c => c.includes('/calc-status/')).length === 4, h.calls.join(' | '))
  ok('the run is reported done once the server finishes', s.phase === 'done', s.phase)
  ok('the post-run total is read after the gateway timeout', s.after === 1250, String(s.after))
}

// ── 5b. a run that never confirms — still exactly one POST ───────────────────────────────────────
let stuckState = null
{
  const h = harness([
    [/\/commissions\//, GET(() => rows(1000))],
    [/\/calculate\//, POST(() => { throw new Error('Bad Gateway') })],
    [/\/calc-status\//, GET(() => ({ calc_status: 'running' }))],
  ])
  h.ctrl.request(PERIOD)
  await h.ctrl.confirm()
  stuckState = h.ctrl.get()
  ok('a run that never lands times out WITHOUT re-firing', h.posts() === 1 && stuckState.posts === 1, h.calls.join(' | '))
  ok('and it says so in plain English', /was NOT re-sent/i.test(stuckState.message), stuckState.message)
}

// ── 5c. the calc itself reports error → last good snapshot kept ──────────────────────────────────
{
  const h = harness([
    [/\/commissions\//, GET(() => rows(1000))],
    [/\/calculate\//, POST(() => ({ status: 'started' }))],
    [/\/calc-status\//, GET(() => ({ calc_status: 'error', save_errors: ['REFUSED: plan mode, no plan assigned.'] }))],
  ])
  h.ctrl.request(PERIOD)
  await h.ctrl.confirm()
  const s = h.ctrl.get()
  ok('a refused calculation surfaces the server reason', s.phase === 'failed' && /REFUSED/.test(s.message), s.message)
  ok('a refused calculation is not retried either', h.posts() === 1, h.calls.join(' | '))
}

// ── 6. RENDERING — every response mode, through the real panel ────────────────────────────────────
section('6. the panel renders each response mode (real component, react-dom/server)')
const render = el => renderToStaticMarkup(el)
const panel = state => render(React.createElement(RunCommissionPanel, {
  state, onConfirm: () => {}, onCancel: () => {}, onDismiss: () => {},
}))
{
  const confirmCtrl = harness([]).ctrl
  confirmCtrl.request(PERIOD, 'luxelink')
  const cs = confirmCtrl.get()
  const html = panel(cs)
  ok('confirm dialog renders', /data-testid="run-commission-confirm"/.test(html))
  ok('the confirm names the PERIOD', html.includes('July 2026'), html.slice(0, 200))
  ok('the confirm names the TENANT', html.includes('luxelink'))
  ok('the confirm warns it replaces the stored payout numbers',
    /replaces the stored payout numbers/i.test(html))
  ok('the confirm text is the shared one (no per-page wording drift)',
    html.includes(RC.confirmText(PERIOD, 'luxelink')))

  const busyHtml = panel(busyState)
  ok('409 renders the friendly wait banner', /data-testid="run-commission-busy"/.test(busyHtml))
  ok('409 tells the operator how long it has been running', /3 minutes ago/.test(busyHtml), busyHtml)
  ok('409 is NOT rendered as an error', !/run-commission-failed/.test(busyHtml))

  const gwHtml = panel(gatewayState)
  ok('502 renders the running banner', /data-testid="run-commission-running"/.test(gwHtml))
  ok('502 says "still running"', /still running/i.test(gwHtml))
  ok('502 explicitly tells the operator NOT to press it again',
    /data-testid="run-commission-no-retry"/.test(gwHtml) && /Do not press it again/i.test(gwHtml))
  ok('502 is NOT rendered as an error', !/run-commission-failed/.test(gwHtml))

  const doneHtml = panel(doneState)
  ok('success renders the done banner', /data-testid="run-commission-done"/.test(doneHtml))
  ok('success shows the NEW total', /data-testid="run-commission-total"[^>]*>\$2,142\.37</.test(doneHtml), doneHtml)
  ok('success shows the DELTA vs before', /run-commission-delta/.test(doneHtml) && doneHtml.includes('688.35'))
  ok('success links to the Rep Incentive report', /href="\/commcalc"/.test(doneHtml))

  ok('idle renders nothing at all', panel(RC.initialState(PERIOD, 'luxelink')) === '')
}

// ── 7. THE CONTROL — period displayed == period recomputed, and the permission gate ──────────────
section('7. the control: the period it shows is the period it recomputes')
{
  AUTH.__set({ scope: 'all', modules: { commissions: true } }, { name: 'luxelink' })
  const html = render(React.createElement(RunCommissionButton, { period: PERIOD }))
  ok('the control renders for a user who can reach /commcalc/payout-schedules',
    /data-testid="run-commission-button"/.test(html))
  ok('the target period is spelled out next to the button',
    /data-testid="run-commission-target"[\s\S]*?July 2026/.test(html), html)
  ok('the tenant is named next to the button', html.includes('luxelink'))
  ok('the period is PICKED, not typed (RULE THREE)',
    /data-testid="period-picker" data-value="July 2026"/.test(html), html)
  ok('the button label is the owner-facing "Run Incentive"', /Run Incentive/.test(html))
  ok('no confirm dialog is open on first render (nothing can fire by itself)',
    !/run-commission-confirm/.test(html))

  // the displayed period and the posted period are the SAME state field — proven end to end
  const h = harness([
    [/\/commissions\//, GET(() => rows(0))],
    [/\/calculate\//, POST(() => ({ status: 'started' }))],
    [/\/calc-status\//, GET(() => ({ calc_status: 'done' }))],
  ])
  h.ctrl.request(PERIOD, 'luxelink')
  const shown = panel(h.ctrl.get())
  await h.ctrl.confirm()
  const posted = h.calls.find(c => c.startsWith('POST '))
  ok('the period in the confirm the human read is the period in the POST URL',
    shown.includes(PERIOD) && posted === ENC_POST, `${posted}`)

  // a different period must produce a different POST — no silently cached target
  const h2 = harness([
    [/\/commissions\//, GET(() => rows(0))],
    [/\/calculate\//, POST(() => ({ status: 'started' }))],
    [/\/calc-status\//, GET(() => ({ calc_status: 'done' }))],
  ])
  h2.ctrl.request('June 2026', 'luxelink')
  await h2.ctrl.confirm()
  ok('changing the period changes the period that is recomputed',
    h2.calls.find(c => c.startsWith('POST ')) === `POST /api/v1/commcalc/calculate/June%202026?org_id=${ORG}`,
    h2.calls.join(' | '))
}

section('8. permission gate — matched to the existing Calculate surface, never widened')
{
  ok('the gate path is /commcalc/payout-schedules (the strictest surface that already has Calculate)',
    BTN.RUN_COMMISSION_GATE_PATH === '/commcalc/payout-schedules')
  for (const [label, perms] of [
    ['market-scoped manager', { scope: 'market', modules: { commissions: true } }],
    ['store-scoped manager', { scope: 'store', modules: { commissions: true } }],
    ['self-scoped rep', { scope: 'self', modules: { commissions: true } }],
    ['no commissions module', { scope: 'all', modules: {} }],
  ]) {
    AUTH.__set(perms, { name: 'luxelink' })
    const h = render(React.createElement(RunCommissionButton, { period: PERIOD }))
    ok(`hidden from a ${label}`, h === '', h.slice(0, 120))
  }
  AUTH.__set({ scope: 'all', modules: { commissions: true } }, { name: 'luxelink' })
  ok('visible to a company-wide commissions user',
    render(React.createElement(RunCommissionButton, { period: PERIOD })).includes('run-commission-button'))
  AUTH.__set({ modules: { admin: true } }, { name: 'house' })
  ok('visible to a super-admin',
    render(React.createElement(RunCommissionButton, { period: PERIOD })).includes('run-commission-button'))
}

console.log(`\n${fail === 0 ? 'ALL PASS' : 'FAILURES'} — ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
