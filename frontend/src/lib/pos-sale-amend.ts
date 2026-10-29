// THE client home for "editing a sale that is already rung" (index §POS-12).
//
// Owner 2026-10-29: "im not able to edit the receipt ot add more tiems to teh same rec3ipt".
// Offered re-ringing under a new number, a second sale, or editing in place, he chose editing in
// place, keeping the number.
//
// WHY A FILE OF ITS OWN, and not three `api()` calls in the register page. Editing is a
// three-step conversation — ask whether this sale may be edited and get its lines, let the cashier
// change them, save — and the two ends have to agree on one shape. Spread across the page
// component they drift: the load reads one field name and the save sends another, and the cashier
// finds out by losing a line. The server side has the matching one home
// (backend/app/modules/pos/sale_amendment.py), which is also where every refusal SENTENCE is
// written — this file never composes its own, so the Sale Log and the till cannot word one refusal
// two ways.
//
// NO ARITHMETIC HERE, DELIBERATELY. The register prices a cart for display, but what gets STORED is
// re-derived server-side from the agreed figures (`sale_amendment.line_money` / `totals`) and
// whatever this client sends for `extended_price` / `tax_value` / the totals is discarded. An edit
// exists to move those figures, so the one place that owns them has to be the server. Sending them
// anyway is harmless and keeps the line shape identical to the cart's.
import { api } from '@/lib/client'
import type { HeldLine } from '@/lib/pos-held-sale'

// A line being edited is EXACTLY a cart line — the same shape the register already holds and that
// POST /pos/sales/checkout consumes (backend `held_sale.LINE_FIELDS`). Reusing `HeldLine` rather
// than declaring a second line type is what keeps a line loaded from a sale and a line restored
// from a suspended cart interchangeable.
export type AmendLine = HeldLine

/** What the server says about editing one sale, before anything is written. */
export interface AmendPlan {
  sale_id: string
  transaction_id: number | null
  /** Which revision the sale is on NOW; the edit would save it as this plus one. */
  revision: number
  allowed: boolean
  /** The server's own sentence for why not, shown verbatim. Null when allowed. */
  reason: string | null
  cart: AmendLine[]
  customer_id: string | null
  is_activation_sale: boolean
  store_code: string | null
  /** False until migration 1103 is applied — the Sale Log offers no button on a database that
   *  cannot keep the version the edit would supersede. */
  available: boolean
}

export interface AmendSaved {
  revision: number
  totals: { subtotal: number; discount_total: number; tax_total: number; total: number }
  /** "1 line added, 1 changed" — the server's summary, as filed in the audit row. */
  changed: string
  /** The sentence to show the cashier, including the reminder to print a fresh receipt. */
  message: string
}

export interface AmendRevision {
  revision: number
  change_summary: string | null
  total_before: number | null
  total_after: number | null
  reason: string | null
  amended_by: string | null
  created_at: string
}

/** May this sale be edited, and with what lines. Writes nothing. */
export async function fetchAmendPlan(saleId: string): Promise<AmendPlan> {
  const r = await api(`/api/v1/pos/sales/${saleId}/amend-plan`)
  return {
    sale_id: saleId,
    transaction_id: r?.transaction_id ?? null,
    revision: Number(r?.revision) || 1,
    allowed: r?.allowed === true,
    reason: r?.reason ?? null,
    cart: Array.isArray(r?.cart) ? r.cart : [],
    customer_id: r?.customer_id ?? null,
    is_activation_sale: r?.is_activation_sale === true,
    store_code: r?.store_code ?? null,
    // Absent on an older server reads as available; the save still fails closed with a 503, so the
    // worst case is a button that explains itself rather than a button that is wrongly hidden.
    available: r?.available !== false,
  }
}

/** Save the edited lines against the SAME transaction number. */
export async function saveAmendedSale(
  saleId: string, items: AmendLine[], opts?: { reason?: string; notes?: string },
): Promise<AmendSaved> {
  const r = await api(`/api/v1/pos/sales/${saleId}/items`, {
    method: 'PUT',
    body: JSON.stringify({
      items,
      reason: opts?.reason || undefined,
      notes: opts?.notes || undefined,
    }),
  })
  return {
    revision: Number(r?.revision) || 0,
    totals: r?.totals || { subtotal: 0, discount_total: 0, tax_total: 0, total: 0 },
    changed: r?.changed || '',
    message: r?.message || '',
  }
}

/** Every version this sale has passed through, newest first. */
export async function fetchAmendHistory(saleId: string): Promise<AmendRevision[]> {
  const r = await api(`/api/v1/pos/sales/${saleId}/amendments`)
  return Array.isArray(r?.revisions) ? r.revisions : []
}
