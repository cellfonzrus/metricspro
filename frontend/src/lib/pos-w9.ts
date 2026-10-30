/**
 * THE browser's reader of a wholesale customer's W-9 state (owner ask 2026-10-30, index §POS-16).
 *
 * Owner: *"a customer type as wholesale where the option to not charge tax will be by default if
 * the user confirms they received the filled out w9 and block the user to make a sale before the
 * system has an updated w9, only the admin can go past without an updated w9"*.
 *
 * WHAT THE BROWSER MAY AND MAY NOT DECIDE. The server's one home (`pos/wholesale_w9.py`) decides
 * whether a certificate is current, whether a sale is blocked and whether it is tax-free; the
 * checkout asks it again and refuses regardless of what this screen allowed. So nothing here
 * re-derives any of that — it holds the empty state, the words the panel shows, and the ONE rule
 * the register needs locally: an exempt sale charges no tax, so the rate every line is taxed at is
 * zero. That rule lives here because the cart is priced in the browser, and `taxRateFor` is the
 * only place it is applied.
 *
 * The customer types are not listed here either: the customer form asks the server for them, so a
 * fourth spelling of "Personal / Business / Wholesale" cannot appear.
 */

export type W9State = {
  wholesale: boolean
  blocked: boolean
  refusal: string | null
  tax_exempt: boolean
  exempt_basis: string | null
  w9_status: 'current' | 'expired' | 'missing' | null
  override_available: boolean
  needs_w9_request: boolean
  form_url: string
  accepted_formats: string[]
  requested_to_email?: string | null
  requested_at?: string | null
  submitted_at?: string | null
  received_at?: string | null
  link_live?: boolean
  available?: boolean
  setup_note?: string | null
}

/**
 * What the register assumes before it has asked, and for a customer it has not got. Deliberately
 * NOT blocked and NOT exempt: a screen that blocked on no information would stop every sale while
 * a request was in flight, and one that assumed the exemption would sell tax-free on nothing.
 */
export const W9_UNKNOWN: W9State = {
  wholesale: false, blocked: false, refusal: null, tax_exempt: false, exempt_basis: null,
  w9_status: null, override_available: false, needs_w9_request: false,
  form_url: '', accepted_formats: [],
}

export async function loadW9(
  fetcher: (url: string) => Promise<Partial<W9State> | null>,
  customerId: string | null, storeCode: string,
): Promise<W9State> {
  if (!customerId) return W9_UNKNOWN
  const q = storeCode ? `?store_code=${encodeURIComponent(storeCode)}` : ''
  const r = await fetcher(`/api/v1/pos/customers/${encodeURIComponent(customerId)}/w9${q}`)
  return { ...W9_UNKNOWN, ...(r || {}) }
}

/**
 * THE one place an exemption meets a tax rate in the browser: the store's resolved rate, or zero
 * on an exempt sale. The server refuses a sale whose lines carry tax against an exemption, so a
 * second opinion here would only produce a refusal the cashier cannot act on.
 */
export function taxRateFor(storeRate: number | null, w9: W9State): number {
  if (w9.tax_exempt) return 0
  return storeRate ?? 0
}

/** What the panel says about this customer, and how loudly. */
export function panelWords(w9: W9State): { tone: 'ok' | 'warn' | 'block' | 'none'; text: string } {
  if (!w9.wholesale) return { tone: 'none', text: '' }
  if (w9.setup_note) return { tone: 'warn', text: w9.setup_note }
  if (w9.w9_status === 'current') {
    return {
      tone: 'ok',
      text: w9.tax_exempt
        ? `Signed W-9 on file${w9.received_at ? ` (${String(w9.received_at).slice(0, 10)})` : ''} — this sale is tax-free.`
        : `Signed W-9 on file${w9.received_at ? ` (${String(w9.received_at).slice(0, 10)})` : ''}. Sales tax still applies for this company.`,
    }
  }
  if (w9.refusal) return { tone: 'block', text: w9.refusal }
  if (w9.w9_status === 'expired') {
    return { tone: 'warn', text: 'The W-9 on file is out of date — send a new one when you can.' }
  }
  return { tone: 'warn', text: 'No signed W-9 on file. Sales tax applies until one arrives.' }
}

/** Whether the cashier should be offered the "send the form" button, and what it should say. */
export function requestLabel(w9: W9State): string {
  if (w9.link_live) return '✉ Send the W-9 again'
  if (w9.submitted_at && w9.w9_status !== 'current') return '✉ Send the W-9 again'
  return '✉ Email the W-9 to the customer'
}

/** The one sentence a cashier sees when a customer has sent something back that nobody has checked. */
export function awaitingReview(w9: W9State): string | null {
  if (w9.submitted_at && w9.w9_status !== 'current') {
    return 'This customer has sent a W-9 back. It needs checking before their sales are tax-free.'
  }
  return null
}
