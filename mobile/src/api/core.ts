import { api } from './client'
import { companyLabel } from '@/lib/company-label'

// ── Identity / bootstrap ─────────────────────────────────────────────────────────────────────────
// GET /core/me returns the signed-in user's profile + resolved role permissions for the active
// tenant. `permissions` is the roles JSONB (e.g. { scope, modules, pos_view_pii, ... }); the module
// registry reads it to decide which tabs to show.
export type MeUser = {
  id?: string
  email?: string
  name?: string | null
  employee_id?: string | null
  role?: string | null
  role_display?: string | null
  org_id?: string | null
  org_name?: string | null
  [k: string]: unknown
}

export type MePayload = {
  provisioned: boolean
  user: MeUser | null
  permissions: Record<string, unknown>
}

export type TenantMembership = {
  org_id: string
  // /core/my-tenants (and /bootstrap) return the company display name under `name`. `org_name` is
  // kept as a tolerated alias so a future/other payload shape still resolves a name, never a raw id.
  name?: string | null
  org_name?: string | null
  slug?: string | null
  role?: string | null
  role_display?: string | null
  is_default?: boolean
}

// WHAT A HUMAN PICKS THIS COMPANY BY — dereferenced from the one home (`lib/company-label`), not
// spelled here. It used to end `|| t.org_id`, and that last fallback is what put four raw UUIDs on
// the owner's "Choose a company" screen (2026-10-30). The rule lives in a module that imports
// nothing so CI can prove it without a device; `tenantName` is the name the two screens call it by.
export { GENERIC_COMPANY_LABEL, looksLikeOrgId } from '@/lib/company-label'
export const tenantName = companyLabel

export function getMe() {
  return api.get<MePayload>('/api/v1/core/me')
}

// A login can belong to more than one tenant; the picker uses this. Endpoint tolerated-absent
// (older backends) → caller treats a failure as "single tenant".
export function getMyTenants() {
  return api.get<{ tenants: TenantMembership[] }>('/api/v1/core/my-tenants')
}
