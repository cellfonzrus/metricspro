// CONFINEMENT — the ONE home for "the server says this person must fix something first; where do
// they go, and what stays reachable?" (index §39 the setup wizard, §14z the clock-variance gate).
//
// PURE. The (platform) layout's Guard asks it on every navigation. THE SERVER decides whether a gate
// is active and which pages a gated person still needs; this only applies that answer. Presentation
// only: nothing here protects data, every page still gates itself.
//
// WHY ONE FUNCTION AND NOT TWO (CLAUDE.md, "A fix is a DESIGN fix"). The setup wizard was the first
// of these; the clock-variance gate (owner directive 2026-10-10) is the second, and a second copy of
// "is this path allowed, else send them to the one page that clears it" is a future divergence — one
// copy would get the ★ NO LOCKOUT ★ rule right and the other would not. So `confineRedirect` is the
// rule, and every gate is a `Confinement` it is asked about. `harness_confinement_lock.py` fails the
// build if a gate redirects without going through it.
//
// ★ NO LOCKOUT ★ (index §22): a confinement is never a refused sign-in. The person signs in
// normally and is held on the page that clears the block, with the account pages always reachable —
// somebody who cannot sign in cannot fix what blocks them, and an inactive or unreadable gate
// traps nobody at all.

/** A server-declared confinement: where the person must be, and what else stays reachable. */
export type Confinement = { active: boolean; destination: string; allow_paths?: string[] }

export type SetupGate = { active: boolean; wizard_path?: string; allow_paths?: string[] }

// Always reachable while gated: the account pages (password, sign-out flows). A confinement that
// closed these would be the lockout, not the gate.
const ALWAYS_OPEN = ['/account']

/** THE RULE. Where a confined person must go instead of `pathname`, or null when the page may open.
 *  An inactive gate, or one with no destination to send anybody to, confines nobody. */
export function confineRedirect(c: Confinement | null | undefined, pathname: string): string | null {
  if (!c?.active) return null
  const dest = String(c.destination || '').trim()
  if (!dest) return null                 // a gate with nowhere to send them is not a gate
  const open = [dest, ...(c.allow_paths || []), ...ALWAYS_OPEN].filter(Boolean)
  const allowed = open.some(p => pathname === p || pathname.startsWith(p + '/'))
  return allowed ? null : dest
}

/** The setup wizard (index §39, owner 2026-09-27): "on the tenant side the first page which opens up
 *  is the set up wizard which requires the tenant to upload these files". An adapter onto the rule
 *  above — the house default destination is this gate's, not the rule's. */
export function setupRedirect(gate: SetupGate | null | undefined, pathname: string): string | null {
  if (!gate?.active) return null
  return confineRedirect({ active: true, destination: gate.wizard_path || '/commcalc/upload/wizard',
                           allow_paths: gate.allow_paths }, pathname)
}

/** Who is walked to the wizard: a company admin (the `admin` module) — never the platform super admin acting in a
 *  tenant, never a rep or manager who could not upload anyway. */
export function setupGateApplies(isCompanyAdmin: boolean, isPlatformAdmin: boolean): boolean {
  return isCompanyAdmin && !isPlatformAdmin
}

/** The clock-variance gate (owner directive 2026-10-10, index §14z) as a `Confinement`.
 *
 *  `GET /storeops/payroll/clock-gate` answers `{alert, gated, allowed_paths, correction_path}`. It is
 *  the SERVER that knows the weekday, the org's own gate weekday, the role list and whether anything
 *  is still uncorrected — and it deliberately answers `gated: false` both when the feature is off and
 *  when it could not read the board, because "a gate that cannot look does not gate". So an
 *  unreachable backend here confines nobody, which is the direction this gate must fail in.
 */
export function clockGateConfinement(g: { gated?: boolean; allowed_paths?: string[]; correction_path?: string } | null | undefined): Confinement {
  return { active: !!g?.gated, destination: g?.correction_path || '', allow_paths: g?.allowed_paths }
}
