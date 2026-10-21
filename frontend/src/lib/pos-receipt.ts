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

/** One of the formats or paper widths the server says this store can print. */
export interface PrintOption { value: string | number; label: string }

/** What the print screen asked for FOR ONE PRINT (§POS-03). Blank = whatever the store declared,
 *  else the company template. Nothing here is stored: the next print starts from the store's own
 *  setting again, which is why a counter can print the till slip now and the office format next. */
export interface PrintChoice {
  layout?: string
  paper_width_mm?: string | number
  /** 'dialog' = the operating system's print dialog, as every store has always had. 'direct' = the
   *  receipt printer itself, through the helper on this counter's PC (§POS-04). Blank = whatever
   *  this store set as its default, which is the dialog until someone turns direct printing on. */
  destination?: string
  /** Put the store's pay-by-bank details on THIS receipt (§POS-06). Off unless ticked, every time:
   *  which customer is paying by bank transfer is known at the counter and nowhere else. */
  remit?: boolean
}

/** Where a receipt would go on WhatsApp, and whether it can go at all (§POS-09). The same shape
 *  `ReceiptEmailView` has, because the screen asks the same question of both channels: who is this
 *  going to, whose is it, and is it sendable. `caption` is the one line that rides with the file. */
export interface ReceiptWhatsappView {
  to: string
  to_source: 'typed' | 'customer' | 'none'
  valid: boolean
  from_name: string
  caption: string
}

export interface SaleReceipt {
  html: string
  layout: string
  /** Set when the stored layout choice is not what will actually print (e.g. the tenant asked for
   *  its POS's layout but has declared no POS). Shown rather than silently fallen back on. */
  layout_note: string
  /** Which of the three said so: this print's own request, this store, or the company template. */
  layout_source: string
  paper_width_mm: number
  paper_source: string
  /** The formats and widths this store may ask for — the server's own vocabulary, so the screen
   *  never offers one the renderer cannot produce. */
  layout_options: PrintOption[]
  paper_options: PrintOption[]
  /** The printer this store declared, in its own words (""  = none declared). */
  printer_label: string
  /** Where this copy is going, which of the two said so, and what the screen may offer. */
  destination: string
  destination_source: string
  destination_options: PrintOption[]
  /** Where the till helper listens on this counter's PC. */
  helper_url: string
  /** Whether THIS copy carries the store's pay-by-bank details, and whether it could at all
   *  (§POS-06: false until the register setup holds BOTH numbers). The masked numbers are here so
   *  the screen can say which account the customer will be told to pay into before it prints; the
   *  full numbers are only ever inside `html` / `escpos_base64`, and only when `remit` is true. */
  remit: boolean
  remit_source: string
  remit_available: boolean
  remit_routing_masked: string
  remit_account_masked: string
  /** The printer's own bytes, sent ONLY when the destination is the printer. The register carries
   *  them to the helper and never looks inside: the receipt is rendered on the server, in one
   *  place, for the paper copy and the cut slip alike. */
  escpos_base64: string
  email: ReceiptEmailView
  whatsapp: ReceiptWhatsappView
  company_name: string
}

/** The choice as query string / body fields. One home, so the print path and the email path cannot
 *  spell it differently and send a customer a copy of a receipt nobody printed. */
function choiceParams(choice?: PrintChoice): Record<string, string> {
  const out: Record<string, string> = {}
  const layout = String(choice?.layout || '').trim()
  const paper = String(choice?.paper_width_mm ?? '').trim()
  const destination = String(choice?.destination || '').trim()
  if (layout) out.layout = layout
  if (paper) out.paper_width_mm = paper
  if (destination) out.destination = destination
  // Sent only when it is ON: an absent parameter and "0" mean the same thing to the server, and a
  // receipt that carries nobody's bank details is the ordinary case.
  if (choice?.remit) out.remit = '1'
  return out
}

/** The receipt of one sale. Throws with the server's own sentence, which already says what to do
 *  (a sale with no stored receipt, for instance, says only register sales carry one). */
export async function fetchSaleReceipt(saleId: string, choice?: PrintChoice): Promise<SaleReceipt> {
  const qs = new URLSearchParams(choiceParams(choice)).toString()
  const r = await api(`/api/v1/pos/sales/${saleId}/receipt${qs ? `?${qs}` : ''}`)
  const rec = (r?.receipt || {}) as Partial<SaleReceipt>
  return {
    html: String(rec.html || ''),
    layout: String(rec.layout || 'thermal'),
    layout_note: String(rec.layout_note || ''),
    layout_source: String(rec.layout_source || 'org'),
    paper_width_mm: Number(rec.paper_width_mm || 80),
    paper_source: String(rec.paper_source || 'org'),
    layout_options: Array.isArray(rec.layout_options) ? rec.layout_options : [],
    paper_options: Array.isArray(rec.paper_options) ? rec.paper_options : [],
    printer_label: String(rec.printer_label || ''),
    destination: String(rec.destination || 'dialog'),
    destination_source: String(rec.destination_source || 'store'),
    destination_options: Array.isArray(rec.destination_options) ? rec.destination_options : [],
    helper_url: String(rec.helper_url || ''),
    escpos_base64: String(rec.escpos_base64 || ''),
    remit: rec.remit === true,
    remit_source: String(rec.remit_source || 'off'),
    remit_available: rec.remit_available === true,
    remit_routing_masked: String(rec.remit_routing_masked || ''),
    remit_account_masked: String(rec.remit_account_masked || ''),
    email: {
      to: String(rec.email?.to || ''),
      to_source: (rec.email?.to_source || 'none') as ReceiptEmailView['to_source'],
      valid: rec.email?.valid === true,
      from_name: String(rec.email?.from_name || ''),
      subject: String(rec.email?.subject || ''),
    },
    whatsapp: {
      to: String(rec.whatsapp?.to || ''),
      to_source: (rec.whatsapp?.to_source || 'none') as ReceiptWhatsappView['to_source'],
      valid: rec.whatsapp?.valid === true,
      from_name: String(rec.whatsapp?.from_name || ''),
      caption: String(rec.whatsapp?.caption || ''),
    },
    company_name: String(rec.company_name || ''),
  }
}

/** Email it. `to` blank = the address the server already showed (the customer's own); `choice`
 *  blank = the store's own setting. Passing the SAME choice the screen printed with is what keeps
 *  the emailed copy identical to the paper one. */
export async function emailSaleReceipt(
  saleId: string, to?: string, choice?: PrintChoice,
): Promise<{ to: string; from_name: string; attached: boolean; message: string }> {
  const r = await api(`/api/v1/pos/sales/${saleId}/email-receipt`, {
    method: 'POST',
    body: JSON.stringify({ to: (to || '').trim() || null, ...choiceParams(choice) }),
  })
  // `attached` says whether the PDF rode along (§POS-08). A mail that went with the body alone is
  // still a sent receipt, so the screen says which happened rather than claiming the file.
  return { to: String(r?.to || ''), from_name: String(r?.from_name || ''),
    attached: r?.attached === true, message: String(r?.message || '') }
}

/** Send it over WhatsApp (§POS-09). `to` blank = the number the server already showed (the
 *  customer's own); `choice` blank = the store's own setting. The receipt itself is the PDF the
 *  server builds, so the file the customer keeps is the receipt that was handed over. Throws the
 *  server's own sentence, which already says what to do about it. */
export async function whatsappSaleReceipt(
  saleId: string, to?: string, choice?: PrintChoice,
): Promise<{ to: string; attached: boolean; message: string }> {
  const r = await api(`/api/v1/pos/sales/${saleId}/whatsapp-receipt`, {
    method: 'POST',
    body: JSON.stringify({ to: (to || '').trim() || null, ...choiceParams(choice) }),
  })
  return { to: String(r?.to || ''), attached: r?.attached === true, message: String(r?.message || '') }
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

/** What the register setup holds for "pay by bank transfer" — masked, which is all a screen ever
 *  sees of it. `encryption_enabled` is false on a server with no field-encryption key, and the
 *  editor says so rather than implying a protection that is not there. */
export interface ReceiptRemitView {
  routing_masked: string
  account_masked: string
  available: boolean
  encryption_enabled: boolean
}

/** Declare (or clear) the account a customer is told to pay into (§POS-06). THE only client home
 *  for this write: the generic POS-settings save must never touch these two keys, because what it
 *  holds for them is the mask. A blank number clears it. Throws the server's own sentence — it
 *  already says what is wrong with the number that was typed. */
export async function saveReceiptRemit(
  body: { routing_number?: string; account_number?: string; store_code?: string },
): Promise<ReceiptRemitView> {
  const r = await api('/api/v1/pos/receipt-remit', { method: 'PUT', body: JSON.stringify(body) })
  const v = r?.remit || {}
  return {
    routing_masked: String(v.routing_masked || ''),
    account_masked: String(v.account_masked || ''),
    available: v.available === true,
    encryption_enabled: r?.encryption_enabled === true,
  }
}

/** Hand the printer's own bytes to the helper on this counter's PC (§POS-04).
 *
 *  THE HELPER IS THE ONLY THING THAT CAN DO THIS. A browser cannot speak to a USB till printer, so
 *  a receipt that prints with no dialog has to go through a small program running beside it. This
 *  throws a sentence a cashier can act on — the caller prints through the dialog instead, so a
 *  helper that is not running costs a customer nothing. */
export async function printViaHelper(helperUrl: string, escposBase64: string): Promise<void> {
  if (!helperUrl) throw new Error('No till helper address is set for this store.')
  if (!escposBase64) throw new Error('The server did not send printer commands for this receipt.')
  let res: Response
  try {
    res = await fetch(helperUrl, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ escpos_base64: escposBase64 }),
    })
  } catch {
    // A refused connection is the ordinary case: the helper window is closed.
    throw new Error('Could not reach the receipt printer helper on this PC. Is it running?')
  }
  let answer: { ok?: boolean; error?: string } = {}
  try { answer = await res.json() } catch { answer = {} }
  if (!res.ok || answer.ok !== true) {
    throw new Error(answer.error || `The helper answered ${res.status}.`)
  }
}
