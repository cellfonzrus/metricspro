// PLATFORM OPERATOR CONSOLE — shared client types + the capability mirror.
//
// Owner directive 2026-09-05: "Need to separate the super admin access … make a separate view for
// the super admin but the option for the super admin to log in to any tenant from it".
//
// MIRROR OF backend/app/modules/core/operator.py — KEEP IN SYNC. Everything here is a CLIENT-SIDE
// CONVENIENCE for page affordances only: every console endpoint resolves the caller's authority
// server-side through `operator_api._authority` (which itself calls the ONE existing gate,
// `core.router._require_super_admin`) and 403s independently of anything decided here.
//
// WHY THIS IS NOT `rbac.ts`. `rbac.isSuperAdmin(perms)` answers "does this login hold the ADMIN
// MODULE inside the tenant they are acting as" — it is a TENANT-ADMIN test (`perms.modules.admin`)
// and it is what gates the tenant app's Configuration menu. Platform authority is a different
// question with a different answer (`TenantMembership.super_admin`, and after the cutover a
// `core.platform_operator` row). Conflating them is exactly the coupling this work exists to break,
// so the operator console asks the SERVER who it is talking to rather than inferring it from tenant
// permissions.

import { api } from '@/lib/client'

export type OperatorCapability =
  | 'tenant.read' | 'tenant.enter' | 'tenant.lifecycle'
  | 'billing.read' | 'billing.write'
  | 'operator.read' | 'operator.write'
  | 'audit.read' | 'notice.write' | 'control_box.read'
  | 'security.write' | 'policy.write'

export type OperatorSection = {
  href: string; label: string; icon: string
  capability: OperatorCapability | null; description: string
}

export type EntryBanner = {
  session_id: string; org_id: string; tenant_name: string; actor_email: string
  reason: string; expires_at: string; seconds_remaining: number
  grants: string[]; note: string
}

export type OperatorMe = {
  auth_id: string; email: string; is_operator: true
  operator_role: string | null
  // WHY they are authorized. 'legacy' = still riding on a tenant membership flag;
  // 'registry' = a platform identity of their own. The console shows this verbatim so the owner can
  // watch the separation happen rather than guess at it.
  sources: ('legacy' | 'registry' | 'house_bootstrap')[]
  capabilities: OperatorCapability[]
  legacy_honored: boolean
  policy: Record<string, any>
  sections: OperatorSection[]
  active_registry_operators: number
  entry: EntryBanner | null
}

export function can(me: OperatorMe | null, cap: OperatorCapability): boolean {
  return !!me?.capabilities?.includes(cap)
}

export function loadOperatorMe(): Promise<OperatorMe> {
  return api('/api/v1/core/operator/me') as Promise<OperatorMe>
}

// Shared visual language for the console. Deliberately DIFFERENT chrome from the tenant app — the
// separation has to be legible at a glance, so an operator never mistakes "I am running the
// platform" for "I am inside a company's books". Slate/amber, not the tenant blue.
export const OPS = {
  bg: '#0f172a', panel: '#1e293b', panelSoft: '#243449', border: '#334155',
  text: '#e2e8f0', text2: '#94a3b8', text3: '#64748b',
  accent: '#f59e0b', accentSoft: 'rgba(245,158,11,0.14)',
  good: '#22c55e', warn: '#f59e0b', bad: '#ef4444', unknown: '#a855f7',
}

export const LAMP_COLOR: Record<string, string> = {
  green: OPS.good, amber: OPS.warn, red: OPS.bad, unknown: OPS.unknown, unmonitored: OPS.text3,
}

export function fmtWhen(v: string | null | undefined): string {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(+d) ? String(v) : d.toLocaleString()
}

export function countdown(seconds: number): string {
  if (seconds <= 0) return 'expired'
  const m = Math.floor(seconds / 60), s = seconds % 60
  return m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}
