'use client'
// SETUP NOTICE — the ONE home for "this feature's setup isn't finished" on every page (owner 2026-09-29,
// index §19.36). Owner: Display Labels said "Needs migration 068_ui_label_override.sql" — "this migration
// should not be mentioned in customer facing".
//
// A customer sees ONE plain sentence (SETUP_NOTICE). The technical detail — which migration, which table —
// is shown ONLY to the platform super admin (rbac.isPlatformAdmin, the same `user.super_admin` the server's
// `core.router._require_super_admin` answers for /me), passed in as `detail`. The backend's twin,
// backend/app/core/setup_notice.py, does the same at the API boundary for every response; the two
// sentences are LOCKED equal by backend/harness_carrier_vocab_guard.py §SETUP, which also fails the build
// on migration / SQL-editor wording in any page's rendered copy outside this file (a `detail=` prop or a
// `detail:` argument is the one sanctioned carrier of a migration name).
import type { CSSProperties } from 'react'
import { useAuth } from './auth-context'
import { isPlatformAdmin } from './rbac'

export const SETUP_NOTICE = "This feature isn't switched on for your company yet. Contact support to enable it."

/** A failed action whose likeliest cause is unfinished setup — for a toast / message string when the server
 *  sent no words of its own. Never names the migration (a string has no viewer to check). */
export function setupFailed(action: string): string {
  return `${action}. If this keeps happening, this feature may not be switched on for your company yet — contact support.`
}

/** Is the viewer the platform super admin (the only one who may see the technical detail)? */
export function useSetupDetailVisible(): boolean {
  const { user } = useAuth()
  return isPlatformAdmin(user)
}

/** A purely TECHNICAL note for the platform team on a page a tenant can also open — the route to point a
 *  scheduler at, the header a job sends. It is NOT a setup notice: the feature IS switched on, so there is
 *  no customer sentence to show and a non-super-admin sees nothing at all. Same gate as `SetupNotice`, so
 *  "who may read an internal name" stays one answer in one file. */
export function PlatformDetail({ detail, style }: {
  detail: string
  style?: CSSProperties
}) {
  if (!useSetupDetailVisible()) return null
  return (
    <span style={style} title="Visible to the platform team only">
      <code style={{ fontSize: '0.85em', opacity: 0.75 }}>[setup: {detail}]</code>
    </span>
  )
}

/** The notice, inline. `detail` (a migration file / number, a table) renders only for the platform super
 *  admin; everyone else sees the sentence alone. `lead` is the page's own customer-meaningful consequence
 *  ("Showing the built-in defaults.") placed before the sentence. */
export function SetupNotice({ detail, lead, style }: {
  detail?: string | number | (string | number)[] | null
  lead?: string
  style?: CSSProperties
}) {
  const showDetail = useSetupDetailVisible()
  const d = Array.isArray(detail) ? detail.filter(x => x != null && x !== '').join(', ') : detail
  return (
    <span style={style}>
      {lead ? lead + ' ' : ''}{SETUP_NOTICE}
      {showDetail && d != null && d !== '' && (
        <code style={{ marginLeft: 6, fontSize: '0.85em', opacity: 0.75 }} title="Visible to the platform team only">
          [setup: {String(d)}]
        </code>
      )}
    </span>
  )
}
