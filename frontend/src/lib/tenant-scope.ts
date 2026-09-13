/**
 * WHICH COMPANY AM I IN — the one rule the header indicator and the company switcher share.
 *
 * WHY THIS EXISTS (live incident, `ss@1313global.us`, 2026-09-13, from `core.access_log`):
 *
 *     20:27:50  Vzone   200  /commcalc/email-sweep/accounts     ← setting up the new tenant
 *     20:48:25  HOUSE   200  /commcalc/email-sweep/accounts     ← the house mailbox, on Vzone's screen
 *     20:48:41  HOUSE   200  /core/employees, /core/roles       ← ~5 minutes inside another company
 *     20:53:18  Vzone   200  /core/employees                    ← back again
 *
 * Nothing was broken server-side: that login is a member of all three companies, so every one of
 * those 200s was correct. The defect is that the person could not TELL. The acting company was
 * shown only by a bare, unlabelled `<select>` among a dozen header controls, and the screen it was
 * sitting on (a tenant's onboarding flow) said nothing about which company's data it was rendering.
 *
 * Three rules, all PURE so `frontend/prove_tenant_scope.mjs` can execute the real ones:
 *
 *   1. `actingCompany` NEVER GUESSES. An active org that is not one of this login's memberships —
 *      or no choice at all — resolves to `resolved: false`, not to "probably the first one".
 *   2. `switcherOptions` PREPENDS A PLACEHOLDER whenever the acting company is unresolved. A
 *      `<select value={x}>` whose `x` matches no `<option>` does not render blank: the browser
 *      displays the FIRST option. Memberships arrive ordered by `created_at`, so for every one of
 *      the four multi-company logins on this platform the first option is the HOUSE org — i.e. the
 *      control would silently claim "Cellfonz R Us" while the session acted as Vzone.
 *   3. `switchConfirmText` NAMES BOTH COMPANIES. Changing company reloads the whole app and
 *      re-points every read and every write; it is not a view filter.
 *
 * NO CARRIER, TENANT OR PRODUCT NAME APPEARS HERE (RULE TWO) — every name is passed in.
 */

/** One membership, as `/core/my-tenants` returns it. Extra fields are ignored. */
export type TenantOption = { org_id: string; name?: string | null }

/** What the header may display, and why. `resolved: false` ⇒ show the placeholder, never a name. */
export type ActingCompany = {
  org_id: string | null
  name: string | null
  resolved: boolean
  reason: 'ok' | 'none_chosen' | 'not_a_membership'
}

/** Shown instead of a company name whenever the acting company cannot be resolved. */
export const CHOOSE_COMPANY = '— choose a company —'

/** Shown for a membership whose tenant row carries no name (`_my_tenants_payload` sends 'Tenant'). */
export const UNNAMED_COMPANY = 'Unnamed company'

const clean = (s: unknown): string => (typeof s === 'string' ? s.trim() : '')

/**
 * The company this session is acting as — resolved ONLY from a real membership.
 *
 * `activeOrg` is what the browser holds (localStorage `mp_active_org`), which is also what it sends
 * as `x-active-org`. The backend honours that header only when it names one of the login's own
 * memberships, so anything else is a value the UI must not dress up as a company.
 */
export function actingCompany(
  tenants: readonly TenantOption[] | null | undefined,
  activeOrg: string | null | undefined,
): ActingCompany {
  const list = Array.isArray(tenants) ? tenants : []
  const id = clean(activeOrg)
  if (!id) return { org_id: null, name: null, resolved: false, reason: 'none_chosen' }
  const hit = list.find(t => t && clean(t.org_id) === id)
  if (!hit) return { org_id: id, name: null, resolved: false, reason: 'not_a_membership' }
  return { org_id: clean(hit.org_id), name: clean(hit.name) || UNNAMED_COMPANY, resolved: true, reason: 'ok' }
}

/** One entry of the header switcher. `placeholder` entries are not selectable destinations. */
export type SwitcherOption = { value: string; label: string; placeholder: boolean }

/**
 * The options a `<select value={activeOrg || ''}>` must render.
 *
 * When the acting company is unresolved a placeholder with `value: ''` is PREPENDED, so the value
 * the control is bound to always matches an option and the browser cannot fall through to
 * displaying the first membership. When it IS resolved the list is exactly the memberships, in the
 * order given — byte-identical to what shipped before this module for every resolved session.
 */
export function switcherOptions(
  tenants: readonly TenantOption[] | null | undefined,
  activeOrg: string | null | undefined,
): SwitcherOption[] {
  const list = (Array.isArray(tenants) ? tenants : []).filter(t => t && clean(t.org_id))
  const opts: SwitcherOption[] = list.map(t => ({
    value: clean(t.org_id),
    label: clean(t.name) || UNNAMED_COMPANY,
    placeholder: false,
  }))
  if (actingCompany(list, activeOrg).resolved) return opts
  return [{ value: '', label: CHOOSE_COMPANY, placeholder: true }, ...opts]
}

/**
 * Whether the header switcher should be offered at all.
 *
 * Only a login with more than one membership can switch. An impersonated session cannot: the acting
 * company is pinned by the grant, so a control there would visibly do nothing.
 */
export function switcherVisible(
  tenants: readonly TenantOption[] | null | undefined,
  impersonating: boolean,
): boolean {
  return !impersonating && (Array.isArray(tenants) ? tenants : []).length > 1
}

/**
 * The confirmation shown before leaving the company you are in. Returns '' when no confirmation is
 * warranted (same company, or no company resolved to leave), so the caller can treat '' as "just go".
 */
export function switchConfirmText(fromName: string | null | undefined, toName: string | null | undefined): string {
  const from = clean(fromName)
  const to = clean(toName)
  if (!from || !to || from === to) return ''
  return `Leave ${from} and start working in ${to}?\n\n`
       + `Every report, upload and setting on every page will be ${to}'s from now on, `
       + `not ${from}'s. You can switch back from the same menu.`
}

/**
 * Does a flow that PINNED a company still match the company being acted as?
 *
 * A setup flow hands off to other modules (onboarding → feeds → email auto-import) and each hop is a
 * fresh page load, so the flow cannot hold the answer in React state. It records the company it was
 * started for and asks this on every load. True ⇒ the page is about to render one company's data
 * under another company's flow, and must say so instead.
 */
export function flowTenantMismatch(
  pinnedOrg: string | null | undefined,
  activeOrg: string | null | undefined,
): boolean {
  const pinned = clean(pinnedOrg)
  const active = clean(activeOrg)
  if (!pinned || !active) return false   // nothing pinned, or nothing resolved ⇒ nothing to contradict
  return pinned !== active
}
