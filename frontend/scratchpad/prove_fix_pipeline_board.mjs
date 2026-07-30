// Proof for the AUTO-FIX PIPELINE board + the /failures traceback gap (mig 718, Phase 1).
// Run:  node frontend/scratchpad/prove_fix_pipeline_board.mjs
//
// Strategy (same as prove_import_health_nav / prove_reports_directory): verbatim re-implementations of
// the shipped rbac.ts predicates PLUS source-level guards on the real files, so this can neither drift
// into proving a stale copy nor pass while the shipped page quietly gains a forbidden control.
//
// The two invariants that matter most here and CANNOT be caught by tsc:
//   1. PHASE 1 HAS NO APPROVE ACTION. A button on this board would end in a production deploy; the
//      owner's approval is given in chat. We assert no approve/reject/push control exists at all.
//   2. EVERY api() call carries the explicit /api/v1 prefix. A bare '/core/...' path passes a
//      curl-against-backend check and 404s silently in the UI ([[curl-verified-not-ui-verified-apiv1]]).
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const f = (...p) => readFileSync(join(here, '..', ...p), 'utf8')
const RBAC = f('src', 'lib', 'rbac.ts')
const BOARD = f('src', 'app', '(platform)', 'admin', 'fix-requests', 'page.tsx')
const FAILURES = f('src', 'app', '(platform)', 'failures', 'page.tsx')

let pass = 0, fail = 0
const ok = (name, cond, extra) => { if (cond) { pass++; console.log('  ok  ', name) } else { fail++; console.log('  FAIL', name, extra ?? '') } }

// ── verbatim re-impl of the shipped predicates (Configuration-group item shape) ─────────────────────
const isSuperAdminPerms = p => !!p?.modules?.admin
function canSeeItem(perms, item) {
  if (isSuperAdminPerms(perms)) return true
  if (item.scopes && !item.scopes.includes(perms.scope || 'all')) return false
  const ov = perms.pages?.[item.href]
  if (typeof ov === 'boolean') return ov
  return !!perms.modules?.[item.module]
}

console.log('\nA. NAV entry (rbac.ts — SHARED file, steward change)')
const navLine = RBAC.split('\n').find(l => l.includes("href: '/admin/fix-requests'"))
ok('A1 the Configuration group gains /admin/fix-requests', !!navLine, navLine)
ok('A2 …tagged module admin', /module: 'admin'/.test(navLine || ''), navLine)
ok('A3 …with NO scopes restriction', !/scopes:/.test(navLine || ''), navLine)
const shapeOf = l => ({ module: /module: '([a-z_]+)'/.exec(l || '')?.[1], scoped: /scopes:/.test(l || '') })
const tenantsLine = RBAC.split('\n').find(l => l.includes("href: '/admin/tenants'") && l.includes('label:'))
ok('A4 gate shape is byte-identical to its super-admin-only sibling /admin/tenants',
  JSON.stringify(shapeOf(navLine)) === JSON.stringify(shapeOf(tenantsLine)), [navLine, tenantsLine])
ok('A5 exactly ONE nav href added (NAV otherwise untouched)',
  (RBAC.match(/href: '\/admin\/fix-requests'/g) || []).length === 1)
const dirBlock = /export const REPORT_DIRECTORY[\s\S]*?\n\]/.exec(RBAC)?.[0] || ''
ok('A6 listed in REPORT_DIRECTORY under admin (it IS a report surface with the full export set)',
  dirBlock.length > 100 && /\['\/admin\/fix-requests', 'admin'\]/.test(dirBlock))
ok('A7 NO new module key / permission concept invented',
  !/fix_pipeline|fixRequests|module: 'fixpipe/.test(RBAC))
// visibility behaviour
ok('A8 a super-admin (modules.admin) sees it', canSeeItem({ modules: { admin: true } }, { href: '/admin/fix-requests', module: 'admin' }))
ok('A9 a rep with no admin module does not', canSeeItem({ modules: { commissions: true }, scope: 'self' }, { href: '/admin/fix-requests', module: 'admin' }) === false)
ok('A10 …and an explicit per-page deny still wins for an admin',
  canSeeItem({ modules: { commissions: true }, pages: { '/admin/fix-requests': false }, scope: 'all' },
    { href: '/admin/fix-requests', module: 'admin' }) === false)

console.log('\nB. PHASE 1 HAS NO APPROVE ACTION (the invariant tsc cannot check)')
// Any <button>/onClick that would set a status is forbidden. We look for the words in a CONTROL context.
const controls = [...BOARD.matchAll(/<button[\s\S]{0,400}?<\/button>/g)].map(m => m[0])
const approveish = controls.filter(c => /approve|reject|push|merge|deploy|ship it/i.test(c))
ok('B1 no button on the board mentions approve / reject / push / merge / deploy',
  approveish.length === 0, approveish)
// mig 719 sharpened this: the board DOES now PATCH — but only a user-action CHECKLIST item on an
// already-shipped fix. It must never PATCH the fix's own lifecycle status (that is still chat-only).
const patchCalls = [...BOARD.matchAll(/api\(\s*`([^`]+)`[\s\S]{0,200}?method:\s*'PATCH'/g)].map(m => m[1])
ok('B2 the ONLY PATCH the board performs targets a checklist ITEM, never the fix lifecycle',
  patchCalls.length === 1 && /\/requests\/\$\{fix\.id\}\/actions\//.test(patchCalls[0]), patchCalls)
const lifecycleStatuses = ['approved', 'pushed', 'rejected', 'building', 'gate1_parked', 'triaged', 'reported', 'not_code']
ok('B2b …and the only status value it can ever send is done|pending',
  !new RegExp(`status:\\s*'(${lifecycleStatuses.join('|')})'`).test(BOARD)
  && /next: 'done' \| 'pending'/.test(BOARD), BOARD.match(/status: '[a-z_]+'/g))
const writeVerbs = [...BOARD.matchAll(/method:\s*'([A-Z]+)'/g)].map(m => m[1])
ok('B3 the board writes exactly twice: the token-rate PUT (config) + the checklist PATCH',
  writeVerbs.slice().sort().join(',') === 'PATCH,PUT', writeVerbs)
ok('B4 a parked fix shows the "approve in chat" NOTE instead',
  /Awaiting push approval — approve in chat/.test(BOARD))
ok('B5 …and the page states plainly that nothing here deploys',
  /Nothing here deploys anything/i.test(BOARD) && /board never deploys/i.test(BOARD))
ok('B6 money-touching rows carry the owner-first warning',
  /Money-touching — owner-first/.test(BOARD) && /AGENT_CONTRACT §7/.test(BOARD))

console.log('\nC. /api/v1 on every call (curl-verified ≠ UI-verified)')
const calls = [...BOARD.matchAll(/api\(\s*[`'"]([^`'"]+)/g)].map(m => m[1])
ok(`C1 all ${calls.length} api() paths start with /api/v1`,
  calls.length >= 4 && calls.every(p => p.startsWith('/api/v1/')), calls)
ok('C2 …and they all target the fix-pipeline sub-router',
  calls.every(p => p.startsWith('/api/v1/core/fix-pipeline/')), calls)
const failCalls = [...FAILURES.matchAll(/api\(\s*[`'"]([^`'"]+)/g)].map(m => m[1])
ok('C3 the /failures page keeps its /api/v1 prefixes (untouched by this package)',
  failCalls.length > 0 && failCalls.every(p => p.startsWith('/api/v1/')), failCalls)

console.log('\nD. RULES FOUR + FIVE (exports + the universal filter bar)')
ok('D1 renders through ReportShell → Excel / PDF / Print / Send for free (RULE FOUR)',
  /import ReportShell from '@\/components\/ReportShell'/.test(BOARD) && /<ReportShell/.test(BOARD))
ok('D2 carries the standard filter bar (RULE FIVE)',
  /import StandardFilterBar from '@\/components\/StandardFilterBar'/.test(BOARD) && /<StandardFilterBar/.test(BOARD))
ok('D3 the store/market/rep deviation is EXPLICIT and documented, not silent',
  /show=\{\{ period: true, stores: false, markets: false, reps: false \}\}/.test(BOARD)
  && /documented deviation/.test(BOARD))
ok('D4 filters feed the SAME array to the table AND the export (what you see is what exports)',
  /rows=\{filtered\}/.test(BOARD) && /matchesStandardFilter\(r, filters/.test(BOARD))
ok('D5 the rollup tile is computed over the FILTERED rows (tile can never disagree with the table)',
  /\[filtered\]\)\s*$/m.test(BOARD.split('const tile = useMemo')[1]?.split('\n').slice(0, 14).join('\n') || ''),
  'tile useMemo dep')
ok('D6 the appended pipeline filters exist (status / class / agent / affected company)',
  /Status\{' '\}/.test(BOARD) && /Class\{' '\}/.test(BOARD) && /Agent\{' '\}/.test(BOARD)
  && /Affected company/.test(BOARD) && /right=\{/.test(BOARD),
  [/Status\{' '\}/.test(BOARD), /Class\{' '\}/.test(BOARD), /Agent\{' '\}/.test(BOARD), /Affected company/.test(BOARD)])
ok('D7 pick-don\'t-type (RULE THREE) via EntityPicker for the reference inputs',
  /import EntityPicker from '@\/components\/EntityPicker'/.test(BOARD)
  && (BOARD.match(/<EntityPicker/g) || []).length >= 2)

console.log('\nE. The $ caveat is stated on-page, never hidden (design §2e)')
ok('E1 the blended-rate explanation is rendered', /How the \$ is worked out/.test(BOARD))
ok('E2 …with the "rates are data, edit them" affordance', /Rates are data, not code/.test(BOARD))
ok('E3 an unpriced row shows the reason rather than a fabricated number',
  /unpriced \(no rate for that model\)/.test(BOARD) && /n == null \? '—'/.test(BOARD))
ok('E4 the per-row cost basis is inspectable', /How this \$ was calculated/.test(BOARD))

console.log('\nF. TRACEBACK now rendered in BOTH surfaces (the design §2a gap)')
ok('F1 the board detail view renders the traceback in a <pre>',
  /Technical detail \/ traceback/.test(BOARD) && /\{t\.traceback\}<\/pre>/.test(BOARD))
ok('F2 /failures grows a per-row technical-detail expander', /Technical detail\{detailRef/.test(FAILURES))
ok('F3 …rendering detail.traceback', /detailTrace\(r\.detail\)/.test(FAILURES)
  && /\{detailTrace\(r\.detail\)\}\s*<\/pre>/.test(FAILURES))
ok('F4 …and the reference code the user was shown, so a report can be matched without SQL',
  /const detailRef = /.test(FAILURES) && /ref \$\{detailRef\(r\.detail\)\}/.test(FAILURES))
ok('F5 no OTHER detail field is silently dropped (everything except the trace is listed)',
  /function detailRest/.test(FAILURES) && /k !== 'traceback'/.test(FAILURES))
ok('F6 /failures behaviour is otherwise unchanged (still one bulk-review POST + the config PUT)',
  (FAILURES.match(/method: 'POST'/g) || []).length === 2 && (FAILURES.match(/method: 'PUT'/g) || []).length === 1,
  [FAILURES.match(/method: 'POST'/g), FAILURES.match(/method: 'PUT'/g)])

console.log('\nG. Super-admin gate on the page (mirrors /admin/tenants)')
ok('G1 gates on user.super_admin from auth-context', /const isSuper = !!user\?\.super_admin/.test(BOARD))
ok('G2 a non-super-admin gets an explainer, not a blank page or a crash',
  /if \(!isSuper\) return \(/.test(BOARD) && /platform super-admins<\/b> only/.test(BOARD))
ok('G3 …and is pointed at the surface they CAN use', /href="\/failures"/.test(BOARD))
ok('G4 nothing loads before the gate passes', /if \(isSuper\) \{ load\(\); loadFeed\(\); loadRates\(\) \}/.test(BOARD))

console.log('\nH. FIXED + "what YOU must do" (mig 719, owner directive 2026-07-30)')
ok('H1 a pushed fix reads as FIXED, not as pipeline jargon — everywhere, incl. exports',
  /pushed: 'FIXED'/.test(BOARD) && /STATUS_LABEL\[r\.status\]/.test(BOARD))
ok('H2 …and pushed is the GREEN status colour (unchanged from 718)', /pushed: '#16a34a'/.test(BOARD))
ok('H3 an outstanding step shows an amber "Action required (N)" badge with the COUNT',
  /Action required \(\{pendingOf\(sel\)\}\)/.test(BOARD)
  && /⚠️ Action required \(\$\{pendingOf\(r\)\}\)/.test(BOARD))
ok('H4 the derived flags come from the SERVER, with a local fallback (never a UI-only truth)',
  /r\.pending_actions \?\? /.test(BOARD) && /r\.action_required \?\? /.test(BOARD))
ok('H5 ONE checklist component is shared by the top panel and the row detail (they cannot drift)',
  /function ActionChecklist\(/.test(BOARD)
  && (BOARD.match(/<ActionChecklist /g) || []).length === 2)
ok('H6 a `sql` action renders in a copyable <pre> block (the owner pastes it into Supabase)',
  /isSql \?/.test(BOARD) && /<pre style=\{\{ fontSize: 11\.5[\s\S]{0,200}\{a\.instruction\}<\/pre>/.test(BOARD)
  && /clipboard\?\.writeText\(a\.instruction\)/.test(BOARD))
ok('H7 every action carries a kind BADGE from a fixed vocabulary matching the backend',
  /const KIND: Record<string/.test(BOARD)
  && ['sql', 'env', 'config', 'data', 'other'].every(k => new RegExp(`\\n  ${k}: \\{ label:`).test(BOARD))
  && /KIND\[k\] \|\| KIND\.other/.test(BOARD))
ok('H8 the checklist is READ + TICK only — the board never authors an action (no free-text kind box, RULE THREE)',
  !/setRateForm\(f => \(\{ \.\.\.f, kind/.test(BOARD) && !/<input[^>]*kind/.test(BOARD)
  && !/user_actions:\s*\[/.test(BOARD))
ok('H9 mark-done is optimistic AND reverts on failure (the server stays the truth)',
  /setRows\(rs => rs\.map\(apply\)\)/.test(BOARD) && /setRows\(before\)/.test(BOARD)
  && /revert/.test(BOARD))
const markCall = patchCalls[0] || ''
ok('H10 the mark-done call carries org_id as a QUERY PARAM from the ROW (never a constant) + the board scope',
  /org_id=\$\{encodeURIComponent\(fix\.org_id\)\}/.test(markCall) && /all_orgs=\$\{allOrgs \? 1 : 0\}/.test(markCall)
  && markCall.startsWith('/api/v1/core/fix-pipeline/'), markCall)
ok('H11 an "Action required only" filter toggle exists and narrows the SAME filtered array that drives '
  + 'the tiles and the exports (RULE FIVE)',
  /Action required only/.test(BOARD) && /if \(onlyAction && !needsAction\(r\)\) return false/.test(BOARD)
  && /\}\), \[rows, status, classification, moduleAgent, tenant, onlyAction, filters\]\)/.test(BOARD))
ok('H12 the export column set gains the actions + "what shipped" (RULE FOUR: what you see, exports)',
  /header: 'Your actions'/.test(BOARD) && /header: 'What shipped'/.test(BOARD))
ok('H13 a shipped-but-blocked fix is surfaced at the TOP of the board, not only inside a row',
  /blockedRows\.length > 0 &&/.test(BOARD) && /shipped but not live yet/.test(BOARD))
ok('H14 …and the row itself is tinted amber in the table', /rowStyle=\{\(r: Fix\) => \(needsAction\(r\)/.test(BOARD))

// Verbatim re-impl of the shipped derivations — the arithmetic the badge/filter/tile all depend on.
const pendingOf = r => r.pending_actions ?? (r.user_actions || []).filter(a => a.status !== 'done').length
const needsAction = r => r.action_required ?? (r.status === 'pushed' && pendingOf(r) > 0)
const A = (s) => ({ id: 'a' + s, kind: 'sql', instruction: 'x', status: s === 1 ? 'done' : 'pending' })
ok('H15 pushed + an outstanding step ⇒ action required',
  needsAction({ status: 'pushed', user_actions: [A(1), A(2)] }) === true
  && pendingOf({ status: 'pushed', user_actions: [A(1), A(2)] }) === 1)
ok('H16 pushed + everything ticked ⇒ clean green',
  needsAction({ status: 'pushed', user_actions: [A(1)] }) === false)
ok('H17 a PARKED fix with steps is never "action required" (its checklist is a plan, not a debt)',
  needsAction({ status: 'gate1_parked', user_actions: [A(2)] }) === false)
ok('H18 a pre-719 row (no user_actions at all) renders safely',
  needsAction({ status: 'pushed' }) === false && pendingOf({ status: 'pushed' }) === 0)
ok('H19 the SERVER value wins over the local fallback when present',
  needsAction({ status: 'pushed', action_required: false, user_actions: [A(2)] }) === false
  && pendingOf({ status: 'pushed', pending_actions: 7, user_actions: [] }) === 7)

console.log(`\n${pass} passed, ${fail} failed${fail ? '' : '  ✅'}`)
process.exit(fail ? 1 : 0)
