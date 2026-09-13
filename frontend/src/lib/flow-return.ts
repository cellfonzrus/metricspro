/**
 * THE WAY BACK — "if a module hands you off, it has to bring you home".
 *
 * Owner, setting up a new tenant (2026-09-13): *"now since it brought me from the implementation
 * wizard once im done here it shoudl take me back there, if im a new tenant i dont know how to
 * navaagte … the modules if they take you to the next module then they shoudl have the option of
 * continuing with thier original work."*
 *
 * The trail: implementation wizard → setup wizard → "connect your feed" → email auto-import. Three
 * hops, and at the end nothing on screen refers to the flow that sent you. `pos/layout.tsx` already
 * solves this for ONE module — an amber bar with "← Back to setup" — but only because the POS module
 * has a layout wrapping every one of its pages AND a gate endpoint telling it what is outstanding.
 * Neither of those exists for a hand-off that LEAVES the module, which is the case being reported.
 *
 * THIS IS THE SAME PATTERN, GENERALISED, AND IT ADDS NO SECOND NAVIGATION MECHANISM. It does not
 * touch NAV, it does not register routes, it invents no destination, and no link site has to opt in:
 * the platform layout already knows the current path on every render, so the flow you came from is
 * simply REMEMBERED as you leave it. One insertion point covers every hop, present and future.
 *
 * WHAT COUNTS AS A FLOW IS A SHAPE, NOT A LIST. A hardcoded set of six wizard routes would go stale
 * the first time a seventh shipped — and the owner has been explicit that nothing should be
 * hardcoded. A setup flow is recognised by how its path ENDS, so a new flow is covered the day it
 * exists without anyone remembering to register it.
 *
 * Everything here is PURE and storage-free so `frontend/prove_flow_return.mjs` can execute the real
 * rules; the caller owns the sessionStorage read/write.
 */

/** The path endings that mark a setup flow. Shape, not identity — a seventh wizard needs no edit. */
export const FLOW_SUFFIXES = ['/onboarding', '/implementation', '/wizard', '/setup'] as const

/** What is remembered about the flow a person left. `org` pins the company it was started for. */
export type FlowTrail = { path: string; org: string | null } | null

const clean = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

/** Strip a query string / fragment and any trailing slash, so `/x/wizard?step=2` reads as `/x/wizard`. */
export function normalizePath(path: string | null | undefined): string {
  const p = clean(path).split('#')[0].split('?')[0]
  return p.length > 1 && p.endsWith('/') ? p.slice(0, -1) : p
}

/** Is this path a setup flow — somewhere a person could be sent OUT of and want to get back to? */
export function isFlowPath(path: string | null | undefined): boolean {
  const p = normalizePath(path).toLowerCase()
  if (!p || p === '/') return false
  return FLOW_SUFFIXES.some(s => p.endsWith(s))
}

/**
 * The trail after navigating to `path`, given what was already remembered. A PURE reducer — the
 * caller persists whatever comes back.
 *
 * Landing ON a flow remembers it (you may be sent away from here at any moment). Landing anywhere
 * else keeps what was remembered, because the whole point is to survive the hops in between. Nothing
 * is ever invented: with no flow yet visited this returns null and the banner never renders.
 */
export function nextTrail(trail: FlowTrail, path: string | null | undefined, org: string | null | undefined): FlowTrail {
  const p = normalizePath(path)
  if (isFlowPath(p)) return { path: p, org: clean(org) || null }
  return trail
}

/** What the banner offers, or null for "say nothing". */
export type ReturnOffer = { href: string; label: string; note: string } | null

/**
 * The way back from `path`, given the remembered trail and the company currently being acted as.
 *
 * Renders NOTHING when: no flow was visited, or the person is already standing on the flow — a
 * control that goes where you already are is noise, and the reason this is decided here rather than
 * per page is that only one place needs to get it right.
 *
 * THE CROSS-COMPANY CASE. A flow is started FOR a company. If the acting company changed in between
 * (the header switcher reloads the whole app, so the trail outlives the switch), sending someone
 * "back to setup" would drop them into one company's setup flow while the app acts as another — the
 * exact confusion §28 exists to prevent. So the offer stands down and says why, rather than
 * pretending the trail is still good. Compare with `tenant-scope.flowTenantMismatch`, which is the
 * same question asked from the destination page's side.
 */
export function returnOffer(
  trail: FlowTrail, path: string | null | undefined, activeOrg: string | null | undefined,
): ReturnOffer {
  if (!trail || !trail.path) return null
  const here = normalizePath(path)
  const back = normalizePath(trail.path)
  if (!back || back === here) return null
  const org = clean(activeOrg)
  if (trail.org && org && trail.org !== org) {
    return { href: back, label: 'Setup was for another company',
             note: `You started this setup in a different company. Switch back before continuing at ${back}.` }
  }
  return { href: back, label: '← Back to setup',
           note: `Pick up where you left off at ${back}.` }
}

/** Whether the offer is the stood-down, cross-company one (the caller styles it as a warning). */
export function offerIsStale(offer: ReturnOffer): boolean {
  return !!offer && offer.label !== '← Back to setup'
}
