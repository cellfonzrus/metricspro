// Supply Ordering — shared types + the cart the pages hand to each other (index §36, mig 1021).
//
// Nothing here names a vendor: vendors are rows (commcalc.po_vendor) the tenant sets up on /supply/vendors,
// with their own free-shipping threshold and delivery time. The visual tokens are the house module tokens
// (re-exported, not re-declared, so the look cannot drift from the other module pages).
export { panel, input, label, btn, btnPrimary, btnDanger, cell, th, fmtMoney } from '@/lib/marketing'

export interface Attention { code: string; severity: 'warn' | 'info'; text: string }
export interface VendorLogin {
  id: string; label?: string; username?: string | null; portal_url?: string | null
  has_password: boolean; has_session: boolean; enabled?: boolean; frequency?: string | null; hour?: number | null
  auth_status?: string | null; auth_message?: string | null; last_run_at?: string | null; last_status?: string | null
}
export interface Vendor {
  id: string; name: string; contact_name?: string | null; email?: string | null; phone?: string | null
  terms?: string | null; notes?: string | null; is_active: boolean
  portal_url?: string | null; catalog_urls?: string[]; portal_config?: Record<string, any>
  free_shipping_threshold?: number | null; shipping_fee_below_threshold?: number | null
  delivery_days_min?: number | null; delivery_days_max?: number | null
  data_source_id?: string | null; is_price_source: boolean
  recipe?: { level: string; text: string }; recipe_errors?: string[]
  login?: VendorLogin | null; catalog_seen_at?: string | null; attention?: Attention[]
}
export interface Offer {
  row_id: string; name: string; sku?: string; price: number | null; list_price?: number | null
  pack_qty?: number | null; availability: string; stock_qty?: number | null; url?: string; unit_price?: number | null
}
export interface CompareRow {
  product: string; match: string; match_method: string; basis: string
  best_vendor: string | null; best_price: number | null; next_price: number | null
  savings: number | null; savings_pct: number | null; note: string; offers: Record<string, Offer>
}

// ── the cart (per viewer, in this browser — a convenience, never the record; the record is the PO) ──
export interface CartItem { key: string; label: string; qty: number; offer_ids: string[]; basis: string }
const CART_KEY = 'supply_cart_v1'

export function readCart(): CartItem[] {
  try {
    const raw = typeof window !== 'undefined' ? window.localStorage.getItem(CART_KEY) : null
    const v = raw ? JSON.parse(raw) : []
    return Array.isArray(v) ? v : []
  } catch { return [] }
}
export function writeCart(items: CartItem[]) {
  try { window.localStorage.setItem(CART_KEY, JSON.stringify(items)) } catch { /* private window: cart lives in memory */ }
}
export function addToCart(item: CartItem) {
  const cur = readCart()
  const i = cur.findIndex(c => c.key === item.key)
  if (i >= 0) cur[i] = { ...cur[i], qty: cur[i].qty + item.qty }
  else cur.push(item)
  writeCart(cur)
  return cur
}

export const AVAIL_LABEL: Record<string, string> = {
  in_stock: 'In stock', backorder: 'Backorder', out_of_stock: 'Out of stock', unknown: 'Stock not stated',
}
export const AVAIL_COLOR: Record<string, string> = {
  in_stock: '#16a34a', backorder: '#f39c12', out_of_stock: '#dc2626', unknown: '#6b7280',
}
export const PO_STATUS_COLOR: Record<string, string> = {
  draft: '#6b7280', submitted: '#2563eb', partially_received: '#f39c12', received: '#16a34a', closed: '#334155', cancelled: '#dc2626',
}
export function fmtDate(v?: string | null) {
  if (!v) return '—'
  const d = new Date(v)
  return isNaN(d.getTime()) ? String(v) : d.toLocaleString()
}
