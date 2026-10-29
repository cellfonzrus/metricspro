// THE ONE HOME for handing a destination to the browser (index §CORE-02.10).
//
// This is the only place in the frontend that hands a destination to the browser. Everywhere else
// navigates with `useRouter().push()`, which is what Next.js wants and what the new lint rule
// `@next/next/no-location-assign-relative-destination` asks for.
//
// WHY THIS FILE IS THE EXCEPTION, AND WHY IT IS NOT A SHORTCUT.
//
// `router.push()` re-renders the route tree but KEEPS the document: every mounted component, every
// cached lookup and every piece of state the previous identity put there survives the navigation.
// That is exactly what must NOT survive when the acting identity changes:
//
//   · an admin starting or stopping "view as employee" — the employee's app would open still
//     holding the admin's rows, and the admin's app would reopen holding the employee's;
//   · a platform operator entering or leaving a tenant — the operator would walk out of a tenant
//     with that tenant's numbers still on screen.
//
// The next request carries a different identity, so the data on screen belongs to nobody. A full
// document load is the thing that throws it away. Five call sites used to spell this out by hand,
// each with its own prose explaining it; the lint rule, aimed at the spelling, could not tell them
// from two ordinary row clicks. The fix is this one home: one navigation, one suppression, one
// reason, dereferenced — see backend/harness_identity_reload_one_home.py, which fails the build if
// a second file starts navigating or if one of the five stops dereferencing this one.
//
// Not `router.refresh()` either: that re-fetches server components and still keeps client state.
// Not `location.reload()`: the destination is part of the point (an operator leaving a tenant lands
// on the tenant list, not back inside the tenant).

/** A path inside this app, always rooted — the only shape this function accepts. */
export type InternalPath = `/${string}`

/**
 * Leave the current document and load `path` fresh, because the acting identity has changed.
 *
 * Use this ONLY on an identity boundary. For ordinary navigation use `useRouter().push()`.
 *
 * No-op during server rendering, so a caller does not need its own `typeof window` guard.
 */
export function reloadAs(path: InternalPath): void {
  if (typeof window === 'undefined') return
  // There is no linter suppression on this line, and that is deliberate rather than lucky. The rule
  // reports a destination it can READ as a literal relative path; this one arrives as a parameter,
  // so there is nothing for it to object to. So the fix buys nothing by silencing anything: the
  // suppression count does not grow, and the rule keeps protecting every other file — which is what
  // backend/harness_navigate_away_one_home.py exists to keep true.
  window.location.assign(path)
}

/**
 * Hand the browser an ABSOLUTE `https:` URL belonging to an external provider — an OAuth consent
 * screen, a hosted payment page — and leave the application.
 *
 * The URL is built by our own backend, not typed by a user, so this is not an open redirect today.
 * It is checked anyway, because the check costs nothing and the failure it prevents is severe: a
 * destination that reached this function having lost its scheme would navigate RELATIVE to the
 * current page (so `evil.test/x` lands on `/vision/settings/evil.test/x`), and one carrying a
 * `javascript:` or `data:` scheme would execute in the app's own origin, with the signed-in
 * session's storage in reach. So: https only, nothing else, and a refusal is loud rather than a
 * silent no-op — the caller shows the operator an error instead of a dead button.
 *
 * @throws if the destination is not a well-formed absolute `https:` URL.
 */
export function redirectToProvider(url: string): void {
  let parsed: URL
  try {
    parsed = new URL(String(url ?? ''))
  } catch {
    throw new Error('That sign-in link came back malformed, so it was not opened.')
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('That sign-in link is not a secure (https) address, so it was not opened.')
  }
  if (typeof window === 'undefined') return
  window.location.assign(parsed.toString())
}
