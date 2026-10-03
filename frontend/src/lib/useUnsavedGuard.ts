'use client'
// ONE HOME — "you are about to lose edits you have not saved" (index §19.35). A row editor passes the
// count from `rowSave.ts::pendingRowCount`; while it is > 0:
//   • a reload / tab close / hard navigation raises the browser's own leave prompt (beforeunload);
//   • an in-app link click (the sidebar, a tile) asks first — the listener runs in the CAPTURE phase
//     and, on Cancel, marks the event defaultPrevented, which Next's <Link> honours;
//   • `confirmDiscard()` is for the page's own state resets (switching a tab that reloads the grid).
// Nothing is saved automatically: pay is money, and a half-typed rate must never be written on blur.
import { useCallback, useEffect, useRef } from 'react'

export function unsavedPrompt(count: number): string {
  return `${count} row${count === 1 ? ' has' : 's have'} unsaved changes that will be lost. Leave without saving?`
}

export function useUnsavedGuard(count: number) {
  const n = useRef(count)
  useEffect(() => { n.current = count }, [count])

  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      if (n.current <= 0) return
      e.preventDefault()
      e.returnValue = ''            // required by Chrome to show the prompt
    }
    const onClick = (e: MouseEvent) => {
      if (n.current <= 0 || e.defaultPrevented || e.button !== 0) return
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return        // opens elsewhere; this page stays
      const a = (e.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null
      if (!a || a.target === '_blank' || a.hasAttribute('download')) return
      let url: URL
      try { url = new URL(a.href, window.location.href) } catch { return }
      // Same page AND same query = an in-page anchor; nothing is left. A different query on the same page is a
      // different VIEW now that a tab lives in the URL (`lib/useUrlTab.ts`, index §19.40), so it asks first.
      if (url.origin === window.location.origin && url.pathname === window.location.pathname
          && url.search === window.location.search) return
      if (!window.confirm(unsavedPrompt(n.current))) { e.preventDefault(); e.stopPropagation() }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    document.addEventListener('click', onClick, true)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      document.removeEventListener('click', onClick, true)
    }
  }, [])

  /** true = go ahead (nothing pending, or the person chose to discard). */
  const confirmDiscard = useCallback(() => n.current <= 0 || window.confirm(unsavedPrompt(n.current)), [])
  return { confirmDiscard }
}
