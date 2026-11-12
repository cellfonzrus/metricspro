// THE ONE HOME for "what colour is a surface under text" (index §CORE-37).
//
// WHY THIS FILE EXISTS. On 2026-11-12 the Finance Dashboard expense grid showed every other row as a
// white band with no name in it. The row's background was the LITERAL `'white'`; the label's colour was
// the token `var(--text)`, which in the dark theme is near-white. A literal does not flip with the
// theme and a token does, so the pair pulled apart: white ink on a white band. 148 literal light
// backgrounds were written across 80 files, so this was never one screen's bug.
//
// THE CLASS, stated so nobody re-derives it: *a background written as a literal colour is not a
// themed background.* The text above it IS themed, so the two can only agree in one theme. Every
// background in this app therefore reads a token, and the tokens live in `app/globals.css`, which
// redefines each one under `:root[data-theme="dark"]`.
//
// WHAT CALLERS GET HERE. The two surfaces a table row can sit on, and the striping rule itself, so a
// new table cannot invent a third answer to "which colour is row 3". `tools/theme-surface-proof.mjs`
// fails the build if a literal light colour appears in a background position again, or if a striped
// row is hand-rolled from `index % 2` instead of calling `rowStripe`.

/** A card, a panel, a table row that is not striped. Flips to #0d0e11 in the dark theme. */
export const SURFACE = 'var(--surface)'
/** The alternate band: a header cell, a striped row, a pressed tab. Flips to #17181d in the dark theme. */
export const SURFACE2 = 'var(--surface2)'

/**
 * The background of row `index` of a striped table — the ONE answer to that question.
 *
 * Opaque on BOTH rows on purpose. The older hand-rolled form returned `'transparent'` for the even
 * row, which is invisible the moment the row holds a `position: sticky` first column: the scrolled
 * body shows through the gap. An explicit surface token costs nothing and removes that whole class.
 *
 * A non-finite or negative index reads as row 0 rather than throwing — a table drawing itself with a
 * bad index should look plain, not blow up the page.
 */
export function rowStripe(index: number): string {
  if (!Number.isFinite(index)) return SURFACE
  return Math.abs(Math.trunc(index)) % 2 === 1 ? SURFACE2 : SURFACE
}

/**
 * The background of a row that is SELECTED or otherwise called out, falling back to the stripe.
 * `tint` must already be a token (`var(--…)`); passing a literal is the defect this file exists for,
 * and the proof script rejects it at build time rather than at 11pm on a dark screen.
 */
export function rowStripeOr(index: number, tint: string | null | undefined): string {
  return tint ? tint : rowStripe(index)
}
