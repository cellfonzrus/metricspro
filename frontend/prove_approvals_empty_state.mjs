// Proves the Approvals inbox panel's decision, offline.
//
// The bug this exists to keep dead (live incident, masked ref 881ae411): every GET /api/v1/approvals
// answered 500. The page caught it into a banner and left `rows` at [], so the panel underneath the
// error still rendered "Waiting on you" and "Nothing waiting. 🎉". The company owner was told, in the
// same breath as an error, that nothing needed his approval — a confidently wrong answer, which this
// repo treats as worse than an error. A failed load yields an UNKNOWN list, never an empty one, and
// panelState() is the single place that distinction is now made.
import { panelState, loadErrorText, errText, EMPTY_PENDING, EMPTY_DECIDED, UNKNOWN_BODY } from './src/lib/approvals.ts'

let pass = 0, fail = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (ok) pass++
  else { fail++; console.log(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`) }
}
const check = (name, got) => eq(name, !!got, true)
const st = (o) => panelState({ loading: false, error: '', count: 0, tab: 'pending', ...o })

// ── THE REGRESSION. A failed load never renders as an empty one ─────────────────────────────────
const ERR = 'Approvals could not be loaded — A system error occurred. Reference: 881ae411'
eq('load failed -> error panel, not the empty state',
  st({ error: ERR }), { kind: 'error', message: UNKNOWN_BODY, showCount: false })
check('...and the body never says "nothing waiting"', !UNKNOWN_BODY.includes('Nothing waiting'))
// The words matter as much as the branch: the panel must say the list is UNKNOWN, not that it is zero.
check('...it says the queue is unknown', /unknown/i.test(UNKNOWN_BODY))
check('...and tells the user the number is not zero', /not zero/i.test(UNKNOWN_BODY))
check('...and points at the reference for support', /reference/i.test(UNKNOWN_BODY))

// A count is a claim too. "· 0" beside a broken list is the same lie in smaller type.
eq('a failed load prints no count', st({ error: ERR }).showCount, false)
eq('a failed load prints no count even if stale rows are still in state',
  st({ error: ERR, count: 7 }).showCount, false)

// ── PRECEDENCE: the failure outranks every state that would claim knowledge ──────────────────────
// The page clears the error when a retry begins, so a spinner still shows on retry. If the two ever
// coexist through a caller bug, the error must win — fail loud, never quietly optimistic.
eq('error beats loading', st({ error: ERR, loading: true }).kind, 'error')
eq('error beats rows (stale rows are not an answer to the request that just failed)',
  st({ error: ERR, count: 3 }).kind, 'error')
eq('error beats the decided tab too', st({ error: ERR, tab: 'decided' }).kind, 'error')
eq('loading beats empty — an in-flight load is not an empty list',
  st({ loading: true }), { kind: 'loading', message: '', showCount: false })
eq('loading with rows already in state still shows the spinner',
  st({ loading: true, count: 3 }).kind, 'loading')

// ── The honest states still work. Fixing a lie must not break the truth ──────────────────────────
eq('loaded and genuinely empty -> the cheerful empty state, unchanged',
  st({}), { kind: 'empty', message: EMPTY_PENDING, showCount: true })
eq('loaded, empty, decided tab -> its own wording',
  st({ tab: 'decided' }), { kind: 'empty', message: EMPTY_DECIDED, showCount: true })
eq('loaded with rows -> the table, and the count is honest',
  st({ count: 4 }), { kind: 'rows', message: '', showCount: true })
eq('one row is still rows', st({ count: 1 }).kind, 'rows')

// ── errText / loadErrorText: the server's reference id must survive to the screen ────────────────
// The whole point of a masked 500 is the reference. If the UI swallows it into "Unknown error" the
// incident becomes unsearchable in core.failure_log, which is how 881ae411 nearly got lost.
eq('an Error carries its message through',
  errText(new Error('A system error occurred. Reference: 881ae411')),
  'A system error occurred. Reference: 881ae411')
eq('a bare string is a message', errText('boom'), 'boom')
eq('an error-shaped object is a message', errText({ message: 'detail from api()' }), 'detail from api()')
eq('nothing usable never becomes silence', errText(undefined), 'Unknown error')
eq('null never becomes silence', errText(null), 'Unknown error')
eq('an object with no message never becomes silence', errText({ status: 500 }), 'Unknown error')
check('the banner keeps the reference id verbatim',
  loadErrorText(new Error('A system error occurred. Reference: 881ae411')).includes('881ae411'))
check('the banner leads with the consequence, not the raw error',
  loadErrorText(new Error('x')).startsWith('Approvals could not be loaded'))
// An unreadable error still produces a banner — an empty string here would switch the panel back to
// the empty state, reintroducing the exact bug through the back door.
check('even an unusable throw yields non-empty banner text', loadErrorText({}).length > 0)
eq('...and that non-empty text still routes to the error panel',
  st({ error: loadErrorText({}) }).kind, 'error')

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
