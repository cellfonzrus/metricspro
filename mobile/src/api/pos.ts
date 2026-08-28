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

// ── Receipt import (photograph a receipt from a primary POS → a searchable sale) ──────────────────
// Backend: app/modules/pos/receipt_import.py + /pos/receipt-import[s]. Two-step by design: preview
// (dry_run) OCRs the photo and returns the parsed fields WITHOUT writing, so the user confirms before
// a sale is created; import then writes the sale + the (encrypted) receipt ledger row.
export type ReceiptItem = {
  description?: string | null
  imei?: string | null
  qty?: number
  unit_price?: number
  extended?: number
}

export type ParsedReceipt = {
  customer_name?: string | null
  phone?: string | null
  email?: string | null
  items?: ReceiptItem[]
  subtotal?: number | null
  tax?: number | null
  total?: number | null
  sale_date?: string | null
  payment_method?: string | null
  imei?: string | null
  imeis?: string[]
  device_name?: string | null
}

export type ReceiptImport = {
  id: string
  store_code?: string | null
  sale_id?: string | null
  customer_id?: string | null
  status?: string
  imei?: string | null
  phone?: string | null
  customer_name?: string | null
  device_name?: string | null
  total?: number | null
  sale_date?: string | null
  notes?: string | null
  created_at?: string
}

/** OCR a receipt photo WITHOUT writing anything — returns the parsed fields for confirmation. */
export function previewReceipt(image: string, ext: 'jpg' | 'png' = 'jpg') {
  return api.post<{ dry_run: true; parsed: ParsedReceipt; raw_ocr: any }>(
    `${BASE}/receipt-import`,
    { image, ext, dry_run: true },
  )
}

/** Create the sale + ledger row from a receipt photo. Returns imported=false + a message when the
 *  photo couldn't be read (the caller falls back to manual entry). */
export function importReceipt(body: { image: string; ext?: 'jpg' | 'png'; store_code?: string; notes?: string }) {
  return api.post<{
    imported: boolean
    parsed: ParsedReceipt
    message?: string
    import_id?: string
    sale_id?: string
    customer_id?: string
    transaction_id?: string
  }>(`${BASE}/receipt-import`, body)
}

export function listReceiptImports(
  params: { q?: string; imei?: string; phone?: string; customer?: string; store_code?: string; limit?: number } = {},
) {
  const qs = new URLSearchParams()
  for (const k of ['q', 'imei', 'phone', 'customer', 'store_code'] as const) {
    const v = params[k]
    if (v && String(v).trim()) qs.set(k, String(v).trim())
  }
  qs.set('limit', String(params.limit ?? 50))
  return api.get<{ receipt_imports: ReceiptImport[] }>(`${BASE}/receipt-imports?${qs.toString()}`)
}

export function getReceiptImport(id: string) {
  return api.get<{ receipt_import: ReceiptImport & { parsed?: ParsedReceipt; raw_ocr?: any } }>(
    `${BASE}/receipt-imports/${encodeURIComponent(id)}`,
  )
}

// ── Structured, per-POS receipt import (RQ / B2B PDFs → editable + reprintable) ────────────────────
export type ReceiptCol = { key: string; label: string; kind: string; align?: string }
export type ReceiptItemRow = { cells: Record<string, string>; editable: string[] }
export type ReceiptTotal = { key: string; label: string; amount: number | null; editable?: boolean }
export type ReceiptMeta = { key: string; label: string; value: string; editable?: boolean }
export type ReceiptDocument = {
  pos_source?: string
  format_label?: string
  title?: string
  meta?: ReceiptMeta[]
  store?: { lines?: string[]; phone?: string | null; fax?: string | null }
  bill_to?: { lines?: string[]; name?: string }
  ship_to?: { lines?: string[] } | null
  columns?: ReceiptCol[]
  items?: ReceiptItemRow[]
  totals?: ReceiptTotal[]
  payments?: { label: string; amount: number | null }[]
  comments?: string | null
  footer_text?: string | null
  derived?: Record<string, unknown>
}

export function getReceiptFormats() {
  return api.get<{ formats: { source: string; label: string }[]; default_source?: string | null }>(
    `${BASE}/receipt-import/formats`,
  )
}

/** OCR a PDF of a known POS format into an editable Document WITHOUT writing (for the edit screen). */
export function previewStructuredReceipt(pos_source: string, fileBase64: string) {
  return api.post<{ dry_run: true; pos_source: string; document: ReceiptDocument }>(
    `${BASE}/receipt-import/structured`,
    { pos_source, file: fileBase64, dry_run: true },
  )
}

/** Commit the (possibly edited) Document as a stored, reprintable receipt + summary sale. */
export function importStructuredReceipt(body: {
  pos_source: string
  document: ReceiptDocument
  store_code?: string
  notes?: string
}) {
  return api.post<{ imported: boolean; pos_source: string; document: ReceiptDocument; import_id?: string; sale_id?: string }>(
    `${BASE}/receipt-import/structured`,
    body,
  )
}

export function updateReceiptDocument(id: string, document: ReceiptDocument) {
  return api.patch<{ ok: true; document: ReceiptDocument }>(
    `${BASE}/receipt-imports/${encodeURIComponent(id)}/document`,
    { document },
  )
}

/** The reprint route path (fetched as HTML via apiGetText → expo-print). */
export function receiptPrintPath(id: string) {
  return `${BASE}/receipt-imports/${encodeURIComponent(id)}/print`
}
