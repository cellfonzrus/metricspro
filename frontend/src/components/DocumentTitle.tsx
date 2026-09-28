'use client'
import { useEffect } from 'react'
import { usePathname } from 'next/navigation'
import { documentTitleForPath } from '@/lib/rbac'

// Sets the browser tab title from the route, using the label the SIDEBAR already carries
// (`lib/rbac.NAV`). Renders nothing.
//
// Owner 2026-09-27, "generic page titles": all 328 pages shipped the one root title, so eight open
// tabs were eight identical tabs. 324 of those pages are `'use client'` and a client component
// cannot export `metadata`, so the choice was 328 new server layouts or ONE derivation — and the
// label already exists beside the href for the nav. Rename a nav item and its tab renames itself.
//
// This is runtime-only (it sets `document.title` after hydration rather than emitting a server-side
// <title>), which is exactly right here: every route under this layout is behind auth and must never
// be indexed — see `app/robots.ts`. The root layout keeps the static metadata that the PUBLIC pages
// and link previews need.
export default function DocumentTitle() {
  const pathname = usePathname()
  useEffect(() => {
    const t = documentTitleForPath(pathname || '/')
    if (typeof document !== 'undefined' && document.title !== t) document.title = t
  }, [pathname])
  return null
}
