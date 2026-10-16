// THE client home for "the register's suspended cart" (owner 2026-10-16: "if a person leaves the
// cash register it gets blank if you come back it shoudl automatically store for the next session
// in suspensed state").
//
// WHY A FILE OF ITS OWN. Suspending is not one call — it is a debounced autosave, a restore on
// arrival, and a clear on checkout or discard, all keyed on the same (store, register) terminal.
// Spread across a page component those three drift: the save sends one shape, the restore expects
// another, and the clear forgets the register number so a checked-out sale stays suspended. So the
// shape of the hold, the debounce, and the three calls live HERE, and the register dereferences
// them. The server side has the matching one home (backend/app/modules/pos/held_sale.py).
//
// NOT browser storage. localStorage answers "what did THIS browser have"; a tablet swapped between
// staff, a second browser or a cleared profile all lose the cart, and no manager could ever see
// that a sale is part-rung. The hold belongs to the register, so it lives on the server
// (pos.held_sales, mig 1087).
import { api } from '@/lib/client'

/** A suspended cart line — exactly the register's own cart-line fields, which are the fields
 *  POST /pos/sales/checkout consumes. Mirrors backend `held_sale.LINE_FIELDS`. */
export interface HeldLine {
  product_id: string
  description?: string
  product_type?: string
  serial_number?: string
  qty: number
  unit_price: number
  list_price: number
  cost: number
  discount: number
  tax_rate: number
  tax_value: number
  extended_price: number
  is_taxable?: boolean
}

export interface HeldSale {
  store_code: string
  register_number: number
  cart: HeldLine[]
  line_count: number
  cart_total: number
  customer_id: string | null
  is_activation_sale: boolean
  note: string | null
  held_by: string | null
  /** When the sale was FIRST suspended — an autosave keeps it, so this is the age of the sale and
   *  not of the last keystroke. The screen shows it because the prices are that old. */
  held_at: string | null
  updated_at: string | null
}

export interface HeldSaleResult {
  held: HeldSale | null
  /** false until migration 1087 is applied — the register then behaves exactly as it did before
   *  the feature existed (a cart lost on reload) rather than showing an error. */
  available: boolean
  message?: string
}

const UNAVAILABLE: HeldSaleResult = { held: null, available: false }

function qs(storeCode: string, registerNumber: number): string {
  return `store_code=${encodeURIComponent(storeCode)}&register_number=${registerNumber}`
}

/** The cart left on this register, or null. Never throws: a register must open even when the hold
 *  cannot be read, and a failed read degrades to "no suspended sale" rather than a blocked till. */
export async function fetchHeldSale(storeCode: string, registerNumber: number): Promise<HeldSaleResult> {
  if (!storeCode) return UNAVAILABLE
  try {
    const r = await api(`/api/v1/pos/held-sale?${qs(storeCode, registerNumber)}`)
    return { held: (r?.held as HeldSale | null) || null, available: r?.available !== false, message: r?.message }
  } catch { return UNAVAILABLE }
}

/** Suspend this register's cart. An EMPTY cart with no customer attached clears the hold instead of
 *  storing a row — the server decides that, so an idle register cannot leave a trail either way. */
export async function suspendSale(storeCode: string, registerNumber: number, body: {
  cart: HeldLine[]; customer_id?: string | null; is_activation_sale?: boolean; note?: string | null
}): Promise<HeldSaleResult> {
  if (!storeCode) return UNAVAILABLE
  try {
    const r = await api('/api/v1/pos/held-sale', {
      method: 'PUT',
      body: JSON.stringify({ store_code: storeCode, register_number: registerNumber, ...body }),
    })
    return { held: (r?.held as HeldSale | null) || null, available: r?.available !== false, message: r?.message }
  } catch { return UNAVAILABLE }
}

/** Drop this register's suspended cart — the cashier discarded it, or the sale checked out. */
export async function clearHeldSale(storeCode: string, registerNumber: number): Promise<void> {
  if (!storeCode) return
  try { await api(`/api/v1/pos/held-sale?${qs(storeCode, registerNumber)}`, { method: 'DELETE' }) }
  catch { /* the next autosave or checkout will clear it */ }
}

/** How long the register waits after the last change before suspending. Long enough that typing a
 *  price is one save and not one per keystroke, short enough that walking away mid-sale is caught. */
export const AUTOSAVE_MS = 1200

/** One line for the banner when a cart comes back, so resuming is a decision and not a surprise.
 *  It never claims the prices are current — only what was left and how long ago. */
export function heldSaleBanner(held: HeldSale | null): string {
  if (!held) return ''
  const n = held.line_count || 0
  const lines = `${n} line${n === 1 ? '' : 's'}`
  const total = `$${Number(held.cart_total || 0).toFixed(2)}`
  const when = held.held_at ? ` · suspended ${timeAgo(held.held_at)}` : ''
  const who = held.held_by ? ` by ${held.held_by}` : ''
  return `Resumed a suspended sale from this register — ${lines}, ${total}${when}${who}. These are the prices that were on the lines when it was suspended.`
}

/** "4 minutes ago" from an ISO stamp. Blank-safe; an unparseable stamp says nothing rather than
 *  rendering "NaN minutes ago". */
export function timeAgo(iso: string): string {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return ''
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`
  const days = Math.round(hrs / 24)
  return `${days} day${days === 1 ? '' : 's'} ago`
}
