import { CHECKOUT_TAX_METHOD_UNPRICEABLE, type CartLine } from './checkout-contract'

// ── THE phone register's wire body (index §POS-20) ───────────────────────────────────────────────
//
// WHY THIS MODULE EXISTS. On 2026-11-12 the owner tried to ring a sale on the phone and the register
// refused it: "This register does not know a tender called “(blank)”." The checkout screen was
// sending `{ method: 'cash' }` while `pos/router.checkout` and the `pos.checkout` RPC both read
// `payment_method`. Three more keys were wrong the same way — `quantity` where the RPC reads `qty`,
// `short_name` where it reads `description`, and no money keys at all.
//
// THE REFUSAL WAS THE ONLY THING THAT SAVED THE NUMBERS. `pos.checkout` COALESCEs a missing `qty`
// to 1 and every missing money key to 0, so a correctly-spelled tender would have stored every
// line at quantity one for $0.00 — a wrong sale that nothing afterwards could tell from a real one.
//
// So the body is built HERE, in one pure function, instead of inline in the screen: a pure builder
// can be asserted key-by-key by the app's own tests (checkout-body.test.ts) and by
// backend/harness_pos_checkout_contract.py, neither of which needs a phone. The shape it emits is
// the one declared in backend/app/modules/pos/checkout_contract.py.
//
// THE MONEY RULE IS THE WEB REGISTER'S, DELIBERATELY. It mirrors `recalcItem` and the totals in
// frontend/src/app/(platform)/pos/sales/page.tsx so two tills cannot price one sale differently:
//
//     taxBase       = unit_price - discount        (the phone has no per-line discount, so = unit_price)
//     tax_value     = round(taxBase * tax_rate, 2) per UNIT, capped if the org caps it
//     extended_price= round((unit_price - discount + tax_value) * qty, 2)
//     subtotal      = sum((unit_price - discount) * qty)
//     tax_total     = sum(tax_value * qty)
//     total         = subtotal + tax_total
//
// WHAT IT REFUSES RATHER THAN GUESSES. A cost-based tax method ("never tax below the product's
// cost") cannot be computed here, because the phone's cart carries no `cost`. Guessing would make
// the phone price a sale differently from the web — which is the defect this module exists to
// prevent — so it returns a refusal instead. Nothing declared means nothing identified, and the
// surface says so.
//
// RULE TWO: no carrier, tenant or product name appears here.

/** The rate and basis the SERVER resolved — never the phone's own opinion. `taxRate` is a FRACTION
 *  (0.08875), read from `GET /pos/tax-codes/resolve`, and `null` means no rate applies at this
 *  store, which the screen must say out loud rather than charge a silent $0. */
export type TaxContext = {
  taxRate: number | null
  /** `pos_settings.tax_method` as resolved for this store. */
  taxMethod: string
  capEnabled: boolean
  /** Per-unit cap in currency; 0 or disabled means no cap. */
  capAmount: number
}

export type CheckoutItem = {
  product_id: string
  description: string | null
  qty: number
  unit_price: number
  list_price: number
  discount: number
  tax_rate: number
  tax_value: number
  extended_price: number
}

export type CheckoutSale = {
  store_code: string
  customer_id: string | null
  receipt_type: string
  subtotal: number
  discount_total: number
  tax_total: number
  total: number
  balance: number
}

export type CheckoutPayment = {
  payment_method: string
  amount: number
  terms_days: number | null
}

export type CheckoutWireBody = {
  sale: CheckoutSale
  items: CheckoutItem[]
  payments: CheckoutPayment[]
}

/** A body, or a refusal — never a half-built body. `refusal` is a sentence for the cashier. */
export type BuildResult =
  | { body: CheckoutWireBody; refusal: null }
  | { body: null; refusal: string }

const money = (n: number) => Math.round((Number.isFinite(n) ? n : 0) * 100) / 100

/** The tax ONE unit of a line carries, by the web register's rule. */
export function unitTax(line: CartLine, tax: TaxContext): number {
  const taxable = line.is_taxable ?? true
  const rate = tax.taxRate ?? 0
  if (!taxable || rate <= 0) return 0
  // The phone carries no per-line discount, so pre_discount and post_discount coincide here. When
  // a discount is added to this cart, read `tax_applied_on` and choose the base, as the web does.
  const base = line.unit_price
  let value = money(base * rate)
  const cap = tax.capAmount
  if (tax.capEnabled && cap > 0 && value > cap) value = money(cap)
  return value
}

/** Build the wire body, or refuse. Pure: no state, no network, no clock. */
export function buildCheckoutBody(input: {
  storeCode: string
  lines: CartLine[]
  tender: string
  tax: TaxContext
  customerId?: string | null
}): BuildResult {
  const { storeCode, lines, tender, tax } = input

  if (!storeCode.trim()) return { body: null, refusal: 'Choose which store this sale is for.' }
  if (lines.length === 0) return { body: null, refusal: 'The cart is empty.' }
  if (!tender.trim()) return { body: null, refusal: 'Choose how the customer is paying.' }
  // The one thing this builder will not guess at.
  if (tax.taxMethod === 'cost_based') return { body: null, refusal: CHECKOUT_TAX_METHOD_UNPRICEABLE }

  const items: CheckoutItem[] = lines.map((line) => {
    const taxValue = unitTax(line, tax)
    const net = line.unit_price
    return {
      product_id: line.product_id,
      // The RPC reads `description`; the phone's cart calls the same fact `short_name`. The
      // translation happens HERE, once, and nowhere else.
      description: line.short_name ?? null,
      qty: line.quantity,
      unit_price: money(line.unit_price),
      list_price: money(line.list_price ?? line.unit_price),
      discount: 0,
      tax_rate: (line.is_taxable ?? true) ? (tax.taxRate ?? 0) : 0,
      tax_value: taxValue,
      extended_price: money((net + taxValue) * line.quantity),
    }
  })

  const subtotal = money(items.reduce((s, i) => s + (i.unit_price - i.discount) * i.qty, 0))
  const taxTotal = money(items.reduce((s, i) => s + i.tax_value * i.qty, 0))
  const total = money(subtotal + taxTotal)

  return {
    refusal: null,
    body: {
      sale: {
        store_code: storeCode,
        customer_id: input.customerId ?? null,
        receipt_type: 'sale',
        subtotal,
        discount_total: 0,
        tax_total: taxTotal,
        total,
        // What is owed. The server re-derives this from the tender it approved and discards the
        // figure sent here, so a settling tender is 0 and a terms sale is corrected server-side.
        balance: 0,
      },
      items,
      payments: [{ payment_method: tender, amount: total, terms_days: null }],
    },
  }
}
