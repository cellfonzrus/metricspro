// Proof for the 2026-07-27 Time Clock date-filter bug fix (mod-people, Deliverable 1).
//
// OWNER REPORT: "filter based on dates 07/9-07/22 it shows all time clock for today also and when
// u scroll down and back up it shows and then does not show the data for the date range selected."
//
// ROOT CAUSE (code-read, not guessed): the OLD page kept THREE independent trigger states
// (start/end/employee filter), each re-firing its own `load()` via a shared useEffect, with NO guard
// against out-of-order network responses. Editing "From" alone fired a fetch with the STILL-STALE
// "To" (often "today"); if THAT wide, stale response resolved AFTER a subsequently narrower one, it
// silently overwrote the correct filtered view — classic last-response-wins. Grepped the ENTIRE
// frontend (`grep -rn "IntersectionObserver\|onScroll\|scrollTop"`) for any scroll-triggered
// pagination/refetch that could independently explain "scrolling shows/hides it" — NONE exists
// anywhere in this codebase (confirmed, not asserted); the flicker IS the race, observed while
// scrolling the table, not caused by it.
//
// FIX: (a) only the DATE RANGE re-fetches now (store/market/rep became client-side filters over the
// already-loaded rows, matching every sibling report page's established pattern — eliminates a whole
// class of unnecessary/racy fetches), and (b) a monotonic request-id guard discards any response
// that isn't from the MOST RECENTLY issued request before it's ever applied to state.
//
// Run: node scratchpad/prove_timeclock_filter_race.mjs (from frontend/)
import { readFileSync } from 'fs'

let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

const PAGE = 'src/app/(platform)/storeops/timeclock/page.tsx'
const src = readFileSync(PAGE, 'utf8')

// ── static-source checks (same convention as prove_payroll_range_exports.mjs) ────────────────────
ok('page: a monotonic request-id ref exists (reqIdRef)', /const reqIdRef = useRef\(0\)/.test(src))
ok('page: load() mints a NEW id per invocation (++reqIdRef.current)', /const myReqId = \+\+reqIdRef\.current/.test(src))
ok('page: the punches fetch is GUARDED — a stale response (id mismatch) is discarded before setRows',
  /api\(`\/api\/v1\/storeops\/timeclock\/list\?\$\{qs\}`\)[\s\S]{0,160}if \(reqIdRef\.current !== myReqId\) return; setRows/.test(src))
ok('page: the manual-hours fetch is ALSO guarded (not just punches)',
  /api\(`\/api\/v1\/storeops\/manual-hours\?\$\{qs\}`\)[\s\S]{0,160}if \(reqIdRef\.current !== myReqId\) return; setManual/.test(src))
ok('page: the change-log fetch is ALSO guarded (Deliverable 2 linkage fetch, same race class)',
  /payroll-change-log\?\$\{qs\}`\)[\s\S]{0,220}if \(reqIdRef\.current !== myReqId\) return; setChangeLog/.test(src))

ok('page: load() re-fires ONLY on the date range (filt.period / filt.periodTo) — store/market/rep are '
  + 'client-side filters, not independent re-fetch triggers (eliminates the 3-independent-triggers race class)',
  /\}, \[filt\.period, filt\.periodTo\]\)/.test(src))
ok('page: NO leftover 3-independent-state pattern (old bare start/end/empFilter useState trio)',
  !/const \[start, setStart\] = useState/.test(src) && !/const \[empFilter, setEmpFilter\] = useState/.test(src))

ok('page: adopts the RULE FIVE StandardFilterBar (period/store/market/rep)', /<StandardFilterBar/.test(src))
ok('page: store/market/rep options resolve via storeops.stores.market (the SAME path reports/page.tsx '
  + 'and payroll-change-log/page.tsx already use — never a fresh join)',
  /for \(const s of stores\) if \(s\.store_code\) m\[s\.store_code\]/.test(src))
ok('page: an unresolvable store falls into an explicit "(no market)" bucket, never silently dropped',
  /NO_MARKET = '\(no market\)'/.test(src) && /r\.market === NO_MARKET/.test(src))

ok('page: no new Date("YYYY-MM-DD") UTC-parse bug — fmtTime only ever receives a FULL timestamp, and '
  + 'StandardFilterBar range-mode date inputs are raw strings, never re-parsed', !/new Date\(filt\.period\)/.test(src) && !/new Date\(value\.period\)/.test(src))

ok('page: Deliverable 2 — a punch/manual-hours row touched by a logged change gets a marker '
  + '(punchEdits/manualEdits maps keyed by source_id, not a loose employee+date match)',
  /punchEdits\.get\(String\(r\.id\)\)/.test(src) && /manualEdits\.get\(String\(m\.id\)\)/.test(src))
ok('page: the marker deep-links into the Payroll Change Log pre-filtered to employee+day',
  /gotoChangeLog\(r\.employee_id, r\.work_date\)/.test(src)
  && /router\.push\(`\/storeops\/payroll-change-log\?employee_id=/.test(src))
ok('page: reads the REVERSE deep-link (?employee_id=&start=&end=) from the Payroll Change Log',
  /sp\.get\('employee_id'\)/.test(src) && /sp\.get\('start'\)/.test(src))

ok('page: Deliverable 3 — the lunch deduction is an EXPLICIT line (its own text under Hours), never '
  + 'folded silently into the punch hours cell', /lunch \(auto\)/.test(src) && /r\.hours != null \? Number\(r\.hours\)\.toFixed\(2\)/.test(src))

const clPage = readFileSync('src/app/(platform)/storeops/payroll-change-log/page.tsx', 'utf8')
ok('payroll-change-log page: reads the deep-link (?employee_id=&start=&end=) from Time Clock',
  /urlEmp = sp\.get\('employee_id'\)/.test(clPage))
ok('payroll-change-log page: row click navigates BACK to Time Clock at that employee+day (reciprocal deep-link)',
  /router\.push\(`\/storeops\/timeclock\?employee_id=/.test(clPage))
ok('payroll-change-log page: the deep-link filter is undoable (Clear button)', /setDeepLinkEmployeeId\(''\)/.test(clPage))

// ── functional proof: the request-id guard's actual RACE-RESISTANCE, verbatim re-impl ────────────
// Re-implements load()'s guard shape exactly (not the whole component — no React needed to prove
// async ordering logic), same convention as prove_payroll_range_exports.mjs's isFullCalendarMonth re-impl.
function makeLoader(fetchImpl) {
  let reqId = 0
  let applied = null
  function load(args) {
    const myReqId = ++reqId
    fetchImpl(args).then(result => {
      if (reqId !== myReqId) return   // STALE — a newer request has since been issued, discard silently
      applied = { args, result }
    })
  }
  return { load, get: () => applied }
}

// Scenario 1 — THE EXACT REPORTED BUG SHAPE: user edits "From" (fires a WIDE, stale request whose "To"
// is still "today"), then edits "To" (fires the CORRECT narrow request) — but the WIDE one resolves
// LATER (classic last-response-wins race). Without the guard this would show "today"'s data; WITH the
// guard, the correct narrower result must win regardless of arrival order.
async function scenario1() {
  const timeline = []
  const loader = makeLoader(args => new Promise(resolve => {
    // The STALE "From changed, To still = today" request is deliberately the SLOW one.
    const delay = args.range === 'wide-stale-today' ? 30 : 5
    setTimeout(() => { timeline.push(args.range); resolve({ rows: args.range }) }, delay)
  }))
  loader.load({ range: 'wide-stale-today' })   // fired first (user edits "From"), resolves LAST
  await new Promise(r => setTimeout(r, 1))     // simulate the tiny gap before the user finishes "To"
  loader.load({ range: 'narrow-correct' })     // fired second (user edits "To"), resolves FIRST
  await new Promise(r => setTimeout(r, 60))    // let both settle
  return { applied: loader.get(), timeline }
}
const s1 = await scenario1()
ok('RACE PROOF: the stale wide ("today"-inclusive) response resolves AFTER the correct narrow one, '
  + 'but the guard keeps the CORRECT narrow result applied (the exact reported bug, fixed)',
  s1.applied.args.range === 'narrow-correct')
ok('RACE PROOF: both requests genuinely raced (stale one really did resolve later, proving this is a '
  + 'real ordering test and not a no-op)', s1.timeline[0] === 'narrow-correct' && s1.timeline[1] === 'wide-stale-today')

// Scenario 2 — ordinary (non-racing) sequential edits still work correctly (no regression).
async function scenario2() {
  const loader = makeLoader(args => new Promise(resolve => setTimeout(() => resolve({ rows: args.range }), 5)))
  loader.load({ range: 'a' })
  await new Promise(r => setTimeout(r, 20))
  loader.load({ range: 'b' })
  await new Promise(r => setTimeout(r, 20))
  return loader.get()
}
const s2 = await scenario2()
ok('sequential (non-overlapping) loads are unaffected — the latest one always applies', s2.args.range === 'b')

// Scenario 3 — MANY rapid overlapping requests (e.g. typing a date digit-by-digit on a mobile date
// picker that fires onChange per keystroke) — only the LAST one issued ever gets applied, regardless
// of how the middle ones resolve.
async function scenario3() {
  const resolveOrder = [4, 1, 3, 0, 2]   // deliberately scrambled arrival order
  const loader = makeLoader(args => new Promise(resolve => {
    const delay = resolveOrder.indexOf(args.i) * 5
    setTimeout(() => resolve({ rows: args.i }), delay)
  }))
  for (let i = 0; i < 5; i++) loader.load({ i })
  await new Promise(r => setTimeout(r, 50))
  return loader.get()
}
const s3 = await scenario3()
ok('5 rapid overlapping requests, scrambled resolution order — only request #4 (the LAST issued) is '
  + 'ever applied, no matter which one settles first', s3.args.i === 4)

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
