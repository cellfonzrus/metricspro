// The PURE half of the one URL-tab reader (index §19.40): what a `?tab=` value means and how a tab's URL is
// built. No React, no Next — so frontend/prove_nav_deep_link.mjs drives the real functions under plain Node.
// The hook that reads the live URL is `lib/useUrlTab.ts`; pages call that, never this directly.

export const TAB_PARAM = 'tab'

/** The tab a raw `?tab=` value names, or the default when it names nothing the page declares. Pure. */
export function parseTab<T extends string>(raw: string | null | undefined, keys: readonly T[], fallback: T): T {
  return raw != null && (keys as readonly string[]).includes(raw) ? (raw as T) : fallback
}

/** The URL for `key` on `pathname`, keeping every other query parameter. The default tab drops `?tab=`. Pure. */
export function tabHref(pathname: string, search: string, key: string, fallback: string): string {
  const q = new URLSearchParams(search)
  if (key === fallback) q.delete(TAB_PARAM)
  else q.set(TAB_PARAM, key)
  const s = q.toString()
  return s ? `${pathname}?${s}` : pathname
}
