'use client'
// THE ONE HOME for "this viewport is too narrow for a desk layout" (index §CORE-25).
//
// WHY THIS FILE EXISTS
// Reported 2026-10-30: the POS register "shows no products" on an iPhone. Nothing was wrong with
// the catalog (584 active Vzone products) and nothing was wrong with the fetch. The sale view is
//
//     gridTemplateColumns: '1fr 340px'   …inside   overflow: 'hidden'
//
// so the right-hand Order Summary pane takes a FIXED 340px and the cart pane takes the remainder.
// Measured in a browser at iPhone width: the shell's 248px sidebar leaves the page ~94px, the
// remainder is **0px**, and the cart — its product lines and the "+ Add Product" button — is
// clipped out of existence. At 1440px the same pane measures 802px, which is why nobody saw it.
//
// THE CLASS, NOT THE INSTANCE (owner directive 2026-09-20). The general fact that was wrong is not
// "the register's left pane is too small on a phone"; it is **a pane given a width the viewport
// does not have is not a layout, it is a disappearance**. Seven screens split a page this way. So
// the fix is one shared fact every one of them dereferences, not seven `@media` patches — and
// `tools/narrow-layout-proof.mjs` fails the build if a screen grows its own hard-coded pane width
// back, or if a new one lands that never asked this file.
//
// NO TENANT, CARRIER OR PRODUCT WORD LIVES HERE (RULE TWO). A breakpoint is a fact about glass.
import { useSyncExternalStore } from 'react'

/** The widest viewport that still gets the stacked (phone/small-tablet) layout.
 *  900 rather than 768: the register needs 340px of summary plus a usable cart beside it, and the
 *  shell's own rail is not free. Below this a two-pane screen stacks instead of squeezing. */
export const NARROW_MAX = 900

/** The media query, spelled from NARROW_MAX so CSS and JS can never drift apart. */
export const NARROW_QUERY = `(max-width: ${NARROW_MAX}px)`

/** Pure: is this CSS pixel width a narrow screen? An unknown width (0 / NaN — SSR, a browser that
 *  will not say) reads as a DESK, so the fallback is exactly today's behaviour and never a layout
 *  that silently rearranges itself. */
export function isNarrowWidth(width: number): boolean {
  return Number.isFinite(width) && width > 0 && width <= NARROW_MAX
}

/** THE general form: a grid template that a narrow screen collapses to a single column.
 *  Every screen whose desk layout pins one track beside a flexible one goes through here, so the
 *  rule ("a pinned pane that the viewport cannot afford becomes a stacked row") is written once. */
export function responsiveColumns(deskTemplate: string, narrow: boolean): string {
  return narrow ? '1fr' : deskTemplate
}

/** THE grid template for a two-pane screen: one pane at a fixed `fixedPx`, one taking the rest.
 *  `side` says which end the fixed pane sits on. Narrow ⇒ one column, so the panes stack and
 *  neither is squeezed to nothing. */
export function twoPaneColumns(fixedPx: number, narrow: boolean, side: 'start' | 'end' = 'end'): string {
  return responsiveColumns(side === 'start' ? `${fixedPx}px 1fr` : `1fr ${fixedPx}px`, narrow)
}

/** The whole frame of a two-pane screen that CLIPS its overflow (the register). Stacked, the
 *  viewport-height box and the clipping both have to go: keeping them would cut the second pane
 *  off below the fold, which is the same disappearance in a different direction. */
export function twoPaneFrame(
  fixedPx: number,
  narrow: boolean,
  desk: { height: string; minHeight?: number; side?: 'start' | 'end' },
): { display: 'grid'; gridTemplateColumns: string; overflow: 'hidden' | 'visible'; height: string; minHeight: number | undefined } {
  return {
    display: 'grid',
    gridTemplateColumns: twoPaneColumns(fixedPx, narrow, desk.side || 'end'),
    overflow: narrow ? 'visible' : 'hidden',
    height: narrow ? 'auto' : desk.height,
    minHeight: narrow ? undefined : desk.minHeight,
  }
}

/** Is the viewport narrow, right now, and does it keep answering as the device rotates?
 *  `useSyncExternalStore` rather than state+effect: the browser's media query IS the store, so
 *  there is nothing to mirror and no cascading render on first paint. SSR-safe (false until the
 *  browser says otherwise) and safe in a browser with no matchMedia. */
export function useNarrowViewport(): boolean {
  return useSyncExternalStore(subscribeNarrow, narrowSnapshot, () => false)
}

function query(): MediaQueryList | null {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return null
  return window.matchMedia(NARROW_QUERY)
}

/** The snapshot: a boolean primitive, so React compares it by value and never re-renders in a loop. */
function narrowSnapshot(): boolean {
  return query()?.matches ?? false
}

function subscribeNarrow(onChange: () => void): () => void {
  const mql = query()
  if (!mql) return () => {}
  if (typeof mql.addEventListener === 'function') {
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }
  // `addListener` is the pre-2019 Safari spelling; iOS 13 and older still need it, and an iPad kept
  // at the counter is exactly the device this fix is for.
  const legacy = mql as unknown as { addListener?: (fn: () => void) => void
                                     removeListener?: (fn: () => void) => void }
  legacy.addListener?.(onChange)
  return () => legacy.removeListener?.(onChange)
}
