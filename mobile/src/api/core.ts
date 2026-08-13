import { api } from './client'

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
  org_name?: string | null
  role?: string | null
}

export function getMe() {
  return api.get<MePayload>('/api/v1/core/me')
}

// A login can belong to more than one tenant; the picker uses this. Endpoint tolerated-absent
// (older backends) → caller treats a failure as "single tenant".
export function getMyTenants() {
  return api.get<{ tenants: TenantMembership[] }>('/api/v1/core/my-tenants')
}
