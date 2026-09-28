// WHO DID THIS — the one display rule for an actor column (updated_by / changed_by / appealed_by /
// recorded_by) written by the backend's `_caller_uid` (index §19.34).
//
// The backend stores the signed-in user's id, or NULL when no signed-in user resolved (RBAC off,
// automation, agents, the auto-calc poller). NULL is the database's own "unknown"; it is never a
// sentinel string any more. Rows written before 2026-09-28 may still carry the retired sentinel 'web',
// which meant exactly the same thing — so both read as "system".
const RETIRED_SENTINELS = new Set(['web'])

export function actorLabel(v: string | null | undefined): string {
  const s = (v ?? '').trim()
  if (!s || RETIRED_SENTINELS.has(s.toLowerCase())) return 'system'
  return s
}
