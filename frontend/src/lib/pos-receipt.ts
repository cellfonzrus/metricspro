// THE client home for "this sale's receipt" (owner 2026-10-17: "admin should be able to edit the
// receipt … the email option should show which email to send to and it should be editable, the from
// email address should show the company name").
//
// WHY THE REGISTER NO LONGER BUILDS ONE. The till used to assemble the receipt's HTML itself, which
// made it the only place the template's toggles, the brand block and the disclaimers were applied —
// so an admin's edits reached the till receipt and not the reprint of an imported one, and a receipt
// could not be rendered at all where there is no browser, which is why emailing one had never been
// built. The receipt is now rendered in ONE place on the server (backend pos/sale_receipt.py) and the
// register PRINTS WHAT IT IS GIVEN. This module is the only thing that asks for it.
import { api } from '@/lib/client'

export interface ReceiptEmailView {
  /** The address the receipt would go to — the customer's, or whatever the cashier typed. SHOWN
   *  before anything is sent, because a receipt going somewhere nobody looked at is the complaint. */
  to: string
  to_source: 'typed' | 'customer' | 'none'
  valid: boolean
  /** The name the customer sees it FROM — the tenant's own company name. */
  from_name: string
  subject: string
}

export interface SaleReceipt {
  html: string
  layout: string
  /** Set when the stored layout choice is not what will actually print (e.g. the tenant asked for
   *  its POS's layout but has declared no POS). Shown rather than silently fallen back on. */
  layout_note: string
  email: ReceiptEmailView
  company_name: string
}

/** The receipt of one sale. Throws with the server's own sentence, which already says what to do
 *  (a sale with no stored receipt, for instance, says only register sales carry one). */
export async function fetchSaleReceipt(saleId: string): Promise<SaleReceipt> {
  const r = await api(`/api/v1/pos/sales/${saleId}/receipt`)
  const rec = (r?.receipt || {}) as Partial<SaleReceipt>
  return {
    html: String(rec.html || ''),
    layout: String(rec.layout || 'thermal'),
    layout_note: String(rec.layout_note || ''),
    email: {
      to: String(rec.email?.to || ''),
      to_source: (rec.email?.to_source || 'none') as ReceiptEmailView['to_source'],
      valid: rec.email?.valid === true,
      from_name: String(rec.email?.from_name || ''),
      subject: String(rec.email?.subject || ''),
    },
    company_name: String(rec.company_name || ''),
  }
}

/** Email it. `to` blank = the address the server already showed (the customer's own). */
export async function emailSaleReceipt(saleId: string, to?: string): Promise<{ to: string; from_name: string }> {
  const r = await api(`/api/v1/pos/sales/${saleId}/email-receipt`, {
    method: 'POST',
    body: JSON.stringify({ to: (to || '').trim() || null }),
  })
  return { to: String(r?.to || ''), from_name: String(r?.from_name || '') }
}

/** Print HTML the server rendered, without navigating away from the till. */
export function printReceiptHtml(html: string): void {
  const iframe = document.createElement('iframe')
  iframe.style.position = 'fixed'
  iframe.style.right = '-9999px'
  document.body.appendChild(iframe)
  const doc = iframe.contentWindow?.document
  if (!doc) { iframe.remove(); throw new Error('Could not open the print view') }
  doc.open(); doc.write(html); doc.close()
  iframe.onload = () => {
    iframe.contentWindow?.focus()
    iframe.contentWindow?.print()
    setTimeout(() => iframe.remove(), 2000)
  }
}
