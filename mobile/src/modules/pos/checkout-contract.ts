// The phone's half of the checkout wire contract (index §POS-20). The declaration of record is
// backend/app/modules/pos/checkout_contract.py; these are the sentences and the cart shape the
// phone needs to honour it, kept here so the builder stays pure and importable by a test.

/** A line of the phone's own cart. Its field names are the PHONE's, not the wire's — translating
 *  between the two is `buildCheckoutBody`'s single job, which is why it is the only place that
 *  knows both. */
export type CartLine = {
  product_id: string
  short_name: string
  quantity: number
  unit_price: number
  list_price?: number
  is_taxable?: boolean
}

/** Why the phone will not ring a sale it cannot price the way the web register would. */
export const CHECKOUT_TAX_METHOD_UNPRICEABLE =
  'This store taxes on product cost, and the app does not carry each product’s cost — so it ' +
  'cannot work out the tax the way the till does. Ring this sale on the computer till.'

/** Said when the server resolved no tax rate for the store. A taxable sale with no rate charges ' +
 *  nothing, and that is not recoverable after the customer leaves — so the screen says it. */
export const CHECKOUT_NO_TAX_RATE =
  'No sales-tax rate is set for this store, so this sale will be rung with no tax.'
