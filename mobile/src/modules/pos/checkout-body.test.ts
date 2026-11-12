import { describe, expect, it } from 'vitest'

import { buildCheckoutBody, unitTax, type TaxContext } from './checkout-body'
import { CHECKOUT_TAX_METHOD_UNPRICEABLE, type CartLine } from './checkout-contract'

// The contract of record: backend/app/modules/pos/checkout_contract.py, which is itself proved
// equal to the `pos.checkout` SQL by backend/harness_pos_checkout_contract.py. Written out here so
// a key renamed on the wire fails in BOTH packages rather than only in Python.
const SALE_KEYS = ['balance', 'customer_id', 'discount_total', 'receipt_type', 'store_code',
                   'subtotal', 'tax_total', 'total']
const ITEM_KEYS = ['description', 'discount', 'extended_price', 'list_price', 'product_id', 'qty',
                   'tax_rate', 'tax_value', 'unit_price']
const PAYMENT_KEYS = ['amount', 'payment_method', 'terms_days']

// Keys the phone used to send, which pos.checkout does not read. Named so a revert is caught.
const NEVER_ON_THE_WIRE = ['method', 'quantity', 'short_name', 'note', 'price', 'line_total']

const line = (over: Partial<CartLine> = {}): CartLine => ({
  product_id: 'p1', short_name: 'Phone A', quantity: 1, unit_price: 100, list_price: 100,
  is_taxable: true, ...over,
})

const tax = (over: Partial<TaxContext> = {}): TaxContext => ({
  taxRate: 0.08875, taxMethod: 'standard', capEnabled: false, capAmount: 0, ...over,
})

const build = (lines: CartLine[], t = tax(), tender = 'cash') =>
  buildCheckoutBody({ storeCode: 'B-1115', lines, tender, tax: t })

describe('the wire body matches the contract pos.checkout actually reads', () => {
  it('emits exactly the declared sale, item and payment keys', () => {
    const { body, refusal } = build([line()])
    expect(refusal).toBeNull()
    expect(Object.keys(body!.sale).sort()).toEqual(SALE_KEYS)
    expect(Object.keys(body!.items[0]).sort()).toEqual(ITEM_KEYS)
    expect(Object.keys(body!.payments[0]).sort()).toEqual(PAYMENT_KEYS)
  })

  // THE REGRESSION. 2026-11-12: the phone sent `method`, so the tender arrived blank and the
  // register refused the sale — "This register does not know a tender called “(blank)”."
  it('names the tender `payment_method`, never `method`', () => {
    const { body } = build([line()], tax(), 'cash')
    expect(body!.payments[0].payment_method).toBe('cash')
    expect(body!.payments[0]).not.toHaveProperty('method')
  })

  // The three that would have stored a WRONG sale rather than refusing one.
  it('names the quantity `qty` and the name `description`', () => {
    const { body } = build([line({ quantity: 3, short_name: 'Phone B' })])
    expect(body!.items[0].qty).toBe(3)
    expect(body!.items[0].description).toBe('Phone B')
    expect(body!.items[0]).not.toHaveProperty('quantity')
    expect(body!.items[0]).not.toHaveProperty('short_name')
  })

  it('sends no key the RPC cannot read', () => {
    const { body } = build([line()])
    const all = [...Object.keys(body!.sale), ...Object.keys(body!.items[0]),
                 ...Object.keys(body!.payments[0])]
    for (const banned of NEVER_ON_THE_WIRE) expect(all).not.toContain(banned)
  })
})

describe('the money is the web register\'s money', () => {
  // Mirrors frontend/src/app/(platform)/pos/sales/page.tsx recalcItem + the totals at lines 948-951.
  it('prices one taxable line the way the web till does', () => {
    const { body } = build([line({ unit_price: 100, quantity: 2 })])
    // per-unit tax: round(100 * 0.08875, 2) = 8.88
    expect(body!.items[0].tax_value).toBe(8.88)
    // extended_price: round((100 + 8.88) * 2, 2) = 217.76
    expect(body!.items[0].extended_price).toBe(217.76)
    expect(body!.sale.subtotal).toBe(200)
    expect(body!.sale.tax_total).toBe(17.76)
    expect(body!.sale.total).toBe(217.76)
    expect(body!.payments[0].amount).toBe(217.76)
  })

  it('charges no tax on a non-taxable line, and says so in tax_rate', () => {
    const { body } = build([line({ is_taxable: false })])
    expect(body!.items[0].tax_value).toBe(0)
    expect(body!.items[0].tax_rate).toBe(0)
    expect(body!.sale.tax_total).toBe(0)
    expect(body!.sale.total).toBe(100)
  })

  it('charges no tax when the server resolved no rate', () => {
    const { body } = build([line()], tax({ taxRate: null }))
    expect(body!.items[0].tax_value).toBe(0)
    expect(body!.sale.total).toBe(100)
  })

  it('honours a per-unit tax cap, like the web till', () => {
    expect(unitTax(line({ unit_price: 1000 }), tax({ capEnabled: true, capAmount: 5 }))).toBe(5)
    // a cap that is off, or zero, is no cap
    expect(unitTax(line({ unit_price: 1000 }), tax({ capEnabled: false, capAmount: 5 }))).toBe(88.75)
    expect(unitTax(line({ unit_price: 1000 }), tax({ capEnabled: true, capAmount: 0 }))).toBe(88.75)
  })

  it('totals several lines', () => {
    const { body } = build([
      line({ product_id: 'a', unit_price: 50, quantity: 2 }),
      line({ product_id: 'b', unit_price: 20, quantity: 1, is_taxable: false }),
    ])
    expect(body!.sale.subtotal).toBe(120)
    expect(body!.sale.tax_total).toBe(8.88)   // 4.44 per unit x 2 units, nothing on the exempt line
    expect(body!.sale.total).toBe(128.88)
  })

  it('leaves the balance to the server', () => {
    const { body } = build([line()])
    expect(body!.sale.balance).toBe(0)
  })
})

describe('it refuses rather than guesses', () => {
  // The honest half: the phone's cart has no `cost`, so a cost-based tax method cannot be priced
  // the way the web till prices it. Guessing would reintroduce exactly the two-tills-disagree
  // defect this module exists to prevent.
  it('refuses a cost-based tax method instead of pricing it differently', () => {
    const { body, refusal } = build([line()], tax({ taxMethod: 'cost_based' }))
    expect(body).toBeNull()
    expect(refusal).toBe(CHECKOUT_TAX_METHOD_UNPRICEABLE)
  })

  it('refuses an empty cart, a missing store and a missing tender', () => {
    expect(build([]).refusal).toMatch(/cart is empty/i)
    expect(buildCheckoutBody({ storeCode: '  ', lines: [line()], tender: 'cash', tax: tax() })
      .refusal).toMatch(/which store/i)
    expect(build([line()], tax(), '  ').refusal).toMatch(/how the customer is paying/i)
  })

  it('never returns a half-built body with a refusal', () => {
    for (const r of [build([]), build([line()], tax({ taxMethod: 'cost_based' }))]) {
      expect(r.refusal).toBeTruthy()
      expect(r.body).toBeNull()
    }
  })
})
