// The setup-wizard gate (index §39, owner 2026-09-27): "on the tenant side the first page which opens up is the set up
// wizard which requires the tenant to upload these files". PURE — the (platform) layout's Guard asks it on every
// navigation. The SERVER decides whether the gate is active and which pages a gated admin still needs
// (GET /commcalc/setup-documents/gate → commcalc/setup_documents.gate / allow_paths); this only applies that answer.
// Presentation only: nothing here protects data, every page still gates itself.
export type SetupGate = { active: boolean; wizard_path?: string; allow_paths?: string[] }

// Always reachable while gated: the account pages (password, sign-out flows).
const ALWAYS_OPEN = ['/account']

/** Where a gated admin must go instead of `pathname`, or null when the page may open. */
export function setupRedirect(gate: SetupGate | null | undefined, pathname: string): string | null {
  if (!gate?.active) return null
  const wizard = gate.wizard_path || '/commcalc/upload/wizard'
  const open = [wizard, ...(gate.allow_paths || []), ...ALWAYS_OPEN]
  const allowed = open.some(p => pathname === p || pathname.startsWith(p + '/'))
  return allowed ? null : wizard
}

/** Who is walked to the wizard: a company admin (the `admin` module) — never the platform super admin acting in a
 *  tenant, never a rep or manager who could not upload anyway. */
export function setupGateApplies(isCompanyAdmin: boolean, isPlatformAdmin: boolean): boolean {
  return isCompanyAdmin && !isPlatformAdmin
}
