import { api } from './client'
import { enqueue } from '@/offline/queue'
import { getOnline } from '@/offline/net'

// ── POS API ──────────────────────────────────────────────────────────────────────────────────────
// Backend: pos router /pos/*. Checkout is a single atomic RPC (sale + items + payments in one
// transaction) — the inventory trigger fires inside it, so an out-of-stock line rolls the whole sale
// back. That atomicity is why a POST we replay from the offline queue is safe: it either fully lands
// or fully fails.
const BASE = '/api/v1/pos'

export type Product = {
  id: string
  product_code?: number
  upc?: string | null
  short_name: string
  full_name?: string | null
  department_id?: string | null
  department_name?: string | null
  category_id?: string | null
  category_name?: string | null
  system_category?: string | null
  inventory_type?: string | null
  cost?: number | null
  retail_price?: number | null
  is_taxable?: boolean
  is_active?: boolean
}

export type CartLine = {
  product_id: string
  short_name: string
  quantity: number
  unit_price: number
  list_price?: number
  is_taxable?: boolean
}

export type Payment = { method: string; amount: number }

export type RegisterSession = {
  id: string
  store_code: string
  register_number: number
  opened_at: string
  opened_by?: string | null
  status: string
} | null

export function searchProducts(params: { search?: string; system_category?: string; active_only?: boolean } = {}) {
  const q = new URLSearchParams()
  if (params.search) q.set('search', params.search)
  if (params.system_category) q.set('system_category', params.system_category)
  q.set('active_only', String(params.active_only ?? true))
  return api.get<{ products: Product[] }>(`${BASE}/products?${q.toString()}`)
}

export function getCatalog() {
  return api.get<{ departments: any[]; categories: any[]; system_categories?: any[] }>(`${BASE}/catalog`)
}

export function getRegisterSession(store_code: string, register_number = 1) {
  const q = new URLSearchParams({ store_code, register_number: String(register_number) })
  return api.get<RegisterSession>(`${BASE}/register/session?${q.toString()}`)
}

export type CheckoutBody = {
  sale: { store_code?: string; register_number?: number; customer_id?: string | null; note?: string | null }
  items: CartLine[]
  payments: Payment[]
}

export function checkout(body: CheckoutBody) {
  return api.post<{ sale: unknown }>(`${BASE}/sales/checkout`, body)
}

/** Durable checkout: replayed from the offline queue if the network is down (atomic RPC → safe). */
export async function checkoutDurable(body: CheckoutBody): Promise<{ queued: true } | { sale: unknown }> {
  if (getOnline()) return checkout(body)
  const total = body.items.reduce((s, l) => s + l.unit_price * l.quantity, 0)
  await enqueue({
    kind: 'pos.checkout',
    label: `Sale ${body.items.length} item(s) · $${total.toFixed(2)}`,
    method: 'POST',
    path: `${BASE}/sales/checkout`,
    body,
  })
  return { queued: true }
}

export function recentSales(params: { store_code?: string; limit?: number } = {}) {
  const q = new URLSearchParams()
  if (params.store_code) q.set('store_code', params.store_code)
  q.set('limit', String(params.limit ?? 25))
  return api.get<{ sales: any[] }>(`${BASE}/sales?${q.toString()}`)
}
