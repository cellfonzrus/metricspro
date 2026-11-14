// THE ONE FRONTEND HOME for "is this store / this person still active", and for hiding the ones that
// are not. Twin of `backend/app/modules/core/roster_visibility.py` — same rule, same NULL posture.
//
// OWNER DIRECTIVE 2026-11-14: *"once the store or employee is made inactive their data should [not] be
// showing on any module unless the checkbox is clicked for show inactive stores"*.
//
// WHY A HOME AND NOT SEVENTY TESTS. `is_active !== false` was written out by hand in ~70 places, so
// nothing stopped the next screen spelling it differently — and some already did: `is_active === true`
// hides every row whose flag was never set (the column is `BOOLEAN DEFAULT true` and NULLABLE, so a
// row older than the column carries NULL and is LIVE). One fact, one home, dereferenced.
//
// TWO RULES, STATED ONCE:
//  1. NULL / missing means ACTIVE. Only an EXPLICIT false is inactive.
//  2. The STRING 'false' is TRUTHY in JavaScript. A flag that came back through JSON, a CSV import or
//     a form post can be 'false' or 0, so a bare truthiness test reads an inactive row as active at
//     exactly the one screen whose value came off the wire.
//
// HIDING IS NEVER SILENT: `inactiveCount` exists so a surface that hides rows can SAY what it hid. A
// money report that quietly drops a closed store's September earnings is a report that lies about
// September — those surfaces keep the rows and name the count instead (see `splitRoster`).
//
// `backend/harness_roster_visibility.py` fails the build if a screen hand-rolls the test again.

/** The column every roster relation spells this fact in. */
export const ACTIVE_FIELD = 'is_active'

/** THE ONE WORDING of the control, so two screens cannot label the same checkbox differently. */
export const INACTIVE_LABEL = 'Show inactive'

/** THE ONE SPELLING of the server-side opt-in (`?include_inactive=1`), matching the backend home. */
export const INCLUDE_INACTIVE_PARAM = 'include_inactive'

const FALSE_WORDS = new Set(['false', 'f', '0', 'no', 'n', 'off'])

/** True unless the row says, EXPLICITLY, that it is not active. See rules 1 and 2 above. */
export function rosterIsActive(row: unknown, field: string = ACTIVE_FIELD): boolean {
  if (row == null || typeof row !== 'object') return true
  const v = (row as Record<string, unknown>)[field]
  if (v == null) return true
  if (typeof v === 'boolean') return v
  if (typeof v === 'number') return v !== 0
  return !FALSE_WORDS.has(String(v).trim().toLowerCase())
}

/** How the caller decides a row is active: the default flag, a different column, or a predicate. */
export type ActiveTest<T> = string | ((row: T) => boolean) | undefined

function testOf<T>(active: ActiveTest<T>): (row: T) => boolean {
  if (typeof active === 'function') return active
  const field = active || ACTIVE_FIELD
  return (row: T) => rosterIsActive(row, field)
}

/** The rows a surface should show: active-only unless `showInactive`. Returns the SAME array
 *  reference when nothing is hidden, so a report keeps its own order at no cost. */
export function visibleRoster<T>(rows: T[] | null | undefined, showInactive: boolean,
                                 active?: ActiveTest<T>): T[] {
  const all = rows || []
  if (showInactive) return all as T[]
  const ok = testOf(active)
  const out = all.filter(ok)
  return out.length === all.length ? (all as T[]) : out
}

/** How many rows `visibleRoster` would hide — what a surface needs in order to say so out loud. */
export function inactiveCount<T>(rows: T[] | null | undefined, active?: ActiveTest<T>): number {
  const ok = testOf(active)
  return (rows || []).reduce((n, r) => (ok(r) ? n : n + 1), 0)
}

/** `[active, inactive]`, order preserved — for a surface that renders the inactive rows apart
 *  (greyed, in their own section) rather than dropping them, which is what a historical money
 *  report must do: its past totals include a store that has since closed. */
export function splitRoster<T>(rows: T[] | null | undefined, active?: ActiveTest<T>): [T[], T[]] {
  const ok = testOf(active)
  const yes: T[] = [], no: T[] = []
  for (const r of rows || []) (ok(r) ? yes : no).push(r)
  return [yes, no]
}

/** THE OPT-IN, ADDED TO A ROSTER URL — one place, so no caller hand-assembles the query string and
 *  no caller spells the parameter differently from the backend home. Returns the url unchanged when
 *  inactive rows are not wanted, so the common case keeps its existing cache key. */
export function withIncludeInactive(url: string, showInactive: boolean): string {
  if (!showInactive || !url) return url
  if (new RegExp(`[?&]${INCLUDE_INACTIVE_PARAM}=`).test(url)) return url
  return `${url}${url.includes('?') ? '&' : '?'}${INCLUDE_INACTIVE_PARAM}=1`
}
