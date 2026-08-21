// Unified Approvals inbox — the PURE decisions behind what the inbox panel says.
//
// THE BUG THIS EXISTS TO KEEP DEAD (live incident, masked ref 881ae411). Every GET /api/v1/approvals
// answered 500. The page caught that, put it in a small banner, and left `rows` at its initial [] —
// so directly underneath the error it drew the header "Waiting on you" and the body "Nothing waiting.
// 🎉". The company owner was told, confidently and in the same breath as an error, that there was
// nothing for him to approve. He had no way to know whether the queue was empty or unreadable.
//
// A failed load does not produce an empty list. It produces an UNKNOWN list, and the two must never
// render the same. Nothing here talks to the network or to React — the panel's whole decision is one
// function so it can be proven offline (prove_approvals_empty_state.mjs) and cannot drift back.

export type Tab = 'pending' | 'decided'
export type PanelKind = 'loading' | 'error' | 'empty' | 'rows'

export interface PanelState {
  kind: PanelKind
  /** What the panel body says. '' for `rows` — the table speaks for itself. */
  message: string
  /** Whether the header may append "· N". A count is a claim about the data; on an unknown list
   *  there is no honest number to print, and "· 0" is the same lie in smaller type. */
  showCount: boolean
}

/** Shown only when the list really did load and really is empty. */
export const EMPTY_PENDING = 'Nothing waiting. 🎉'
export const EMPTY_DECIDED = 'No decisions yet.'
/** Shown in place of either of the above when the load failed. Says "unknown", never "none". */
export const UNKNOWN_BODY =
  'This list could not be loaded, so what is waiting on you is unknown — not zero. Retry, and if it '
  + 'keeps failing quote the reference in the message above to support.'

/** The message text of a thrown value, without `any`. api() throws an Error whose message is the
 *  server's own `detail` — for a masked 500 that is "A system error occurred. Reference: 881ae411",
 *  and carrying it through verbatim is what makes the incident traceable from a screenshot. */
export function errText(e: unknown): string {
  if (e instanceof Error) return e.message
  if (typeof e === 'string') return e
  if (e && typeof e === 'object' && typeof (e as { message?: unknown }).message === 'string') {
    return (e as { message: string }).message
  }
  return 'Unknown error'
}

/** The banner sentence for a load that failed. Leads with the consequence (the list is not
 *  trustworthy), then the server's own words, so the reference id stays visible. */
export function loadErrorText(e: unknown): string {
  return `Approvals could not be loaded — ${errText(e)}`
}

/**
 * What the inbox panel shows. PRECEDENCE, and why it is this order:
 *
 *   error   first — the invariant. If a failure is on record, no other state may render, because
 *                   every other state is a positive claim about data we do not have. Callers clear
 *                   the error when a retry starts, so a spinner still appears on a retry; if the two
 *                   ever coexist through a caller bug, the failure is what the user sees. Fail loud.
 *   loading next  — an in-flight load is not an empty list either.
 *   rows / empty  — only now, with a load that actually succeeded, may we say "none".
 */
export function panelState(s: { loading: boolean; error: string; count: number; tab: Tab }): PanelState {
  if (s.error) return { kind: 'error', message: UNKNOWN_BODY, showCount: false }
  if (s.loading) return { kind: 'loading', message: '', showCount: false }
  if (s.count > 0) return { kind: 'rows', message: '', showCount: true }
  return { kind: 'empty', message: s.tab === 'pending' ? EMPTY_PENDING : EMPTY_DECIDED, showCount: true }
}
