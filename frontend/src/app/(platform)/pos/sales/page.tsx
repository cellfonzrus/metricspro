'use client'

// POS module — Phase 1: Sales Register (ported from the standalone pos-system app's
// app/sales/page.tsx; data access rewired from direct Supabase to the FastAPI /pos router).
//
// Intended behavior changes vs the standalone source:
//   * Checkout is ONE atomic call (POST /pos/sales/checkout) — sale + items + payments commit or
//     roll back together, so the old "sale created but records failed" partial-failure path is gone.
//   * The receipt snapshot the source kept only in React state is now persisted on the sale row
//     (sale.receipt) so Sale Log reprints are exact.
//   * Identity: stores are store_code TEXT, employees are employee_id TEXT; the BACKEND stamps the
//     sale's employee_id from the login (never trusted from the client).

import { useEffect, useMemo, useRef, useState } from 'react'
import { api, addDays, localToday } from '@/lib/client'
import { useAuth } from '@/lib/auth-context'
import RegisterDrawer, { RegisterSession } from '@/components/pos/RegisterDrawer'
import RegisterLock from '@/components/pos/RegisterLock'
import { getActiveStore, setActiveStore } from '@/lib/pos-store'
import { PosConfigValues, loadEffectivePosConfig, resolvePosConfig, getRegisterNumber } from '@/lib/pos-config'

interface Product {
  id: string
  product_code?: number
  short_name: string
  upc: string | null
  retail_price: number
  cost: number
  is_taxable: boolean
  inventory_type: string
  system_category: string | null
}

interface CartItem {
  product_id: string
  description: string
  product_type: string
  serial_number: string
  qty: number
  unit_price: number
  cost: number
  discount: number
  tax_rate: number      // FRACTION (e.g. 0.08875)
  tax_value: number     // PER-UNIT tax; × qty at checkout
  extended_price: number
  // Stamped from the product at add time so re-taxing (store/tax-code changes) doesn't
  // depend on the item still being present in the default 500-row catalog list.
  is_taxable?: boolean
}

interface Customer {
  id: string
  cust_number: number
  first_name: string | null
  last_name: string | null
  company_name: string | null
  account_type: string
  email: string | null
  phone_primary: string | null
  referral_source?: string | null
}

interface ReceiptItem { description: string; serial: string | null; qty: number; unit_price: number; discount: number; line_total: number }
interface ReceiptSnapshot {
  transactionId: number | null
  date: string
  customerName: string | null
  items: ReceiptItem[]
  subtotal: number
  discountTotal: number
  taxTotal: number
  total: number
  method: string
  tendered: number | null
  change: number
}

interface Sale {
  id: string
  transaction_id: number
  created_at: string
  status: string
  receipt_type: string
  store_code: string | null
  employee_id: string | null
  customer_id: string | null
  customer_name?: string | null
  total: number
  subtotal: number
  tax_total: number
  discount_total: number
  balance: number | null
  voided_at: string | null
  is_activation_sale?: boolean
  receipt?: ReceiptSnapshot | null
}

interface TaxCode { id: string; name: string; rate: number; store_code: string | null; is_active: boolean }
interface Store { store_code: string; address?: string | null; market?: string | null }

interface ReceiptTemplate {
  header_text: string | null
  footer_text: string | null
  show_store_name: boolean
  show_customer: boolean
  show_serials: boolean
  show_tax_breakdown: boolean
  show_discounts: boolean
  paper_width_mm: number
  font_size_px: number
}

const inputStyle: React.CSSProperties = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 7, padding: '7px 10px', fontSize: 13, color: 'var(--text)', outline: 'none' }
const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const modalOverlay: React.CSSProperties = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 200, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }
const thStyle: React.CSSProperties = { padding: '8px 10px', textAlign: 'left', color: 'var(--text2)', fontWeight: 600, whiteSpace: 'nowrap', fontSize: 11, textTransform: 'uppercase' }

// Local calendar day (YYYY-MM-DD) → real instants, so filters compare correctly
// against UTC created_at timestamps.
const dayStartIso = (ymd: string) => { const [y, m, d] = ymd.split('-').map(Number); return new Date(y, m - 1, d).toISOString() }
const dayEndIso = (ymd: string) => { const [y, m, d] = ymd.split('-').map(Number); return new Date(y, m - 1, d, 23, 59, 59).toISOString() }

export default function PosSalesPage() {
  const { user, loading: authLoading } = useAuth()

  const [activeView, setActiveView] = useState<'home' | 'sale' | 'salelog'>('home')
  const [cart, setCart] = useState<CartItem[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null)
  const [products, setProducts] = useState<Product[]>([])                 // default catalog (quick-scan matching)
  const [pickerResults, setPickerResults] = useState<Product[] | null>(null) // server search results while typing
  const [sales, setSales] = useState<Sale[]>([])
  const [selectedSale, setSelectedSale] = useState<Sale | null>(null)
  const [loading, setLoading] = useState(false)
  const [quickScanInput, setQuickScanInput] = useState('')
  const [productSearch, setProductSearch] = useState('')
  const [customerSearch, setCustomerSearch] = useState('')
  const [showCustomerPicker, setShowCustomerPicker] = useState(false)
  const [showNewCustomer, setShowNewCustomer] = useState(false)
  const [newCust, setNewCust] = useState({ first_name: '', last_name: '', phone_primary: '', email: '' })
  const [creatingCustomer, setCreatingCustomer] = useState(false)
  const [showProductPicker, setShowProductPicker] = useState(false)
  const [showPayment, setShowPayment] = useState(false)
  const [showReceipt, setShowReceipt] = useState(false)
  const [paymentMethod, setPaymentMethod] = useState('cash')
  const [amountTendered, setAmountTendered] = useState('')
  const [lastTransactionId, setLastTransactionId] = useState<number | null>(null)
  const [lastReceipt, setLastReceipt] = useState<ReceiptSnapshot | null>(null)
  const [receiptTemplate, setReceiptTemplate] = useState<ReceiptTemplate | null>(null)
  const [saving, setSaving] = useState(false)
  const [voiding, setVoiding] = useState(false)

  // Sale Log filters
  const [slDateFrom, setSlDateFrom] = useState(addDays(localToday(), -30))
  const [slDateTo, setSlDateTo] = useState(localToday())
  const [slStore, setSlStore] = useState('')
  const [slCustomerSearch, setSlCustomerSearch] = useState('')
  const [slSerial, setSlSerial] = useState('')
  const [slActivationOnly, setSlActivationOnly] = useState(false)
  const [saleItems, setSaleItems] = useState<any[]>([])

  // Sale attribution + tax configuration
  const [stores, setStores] = useState<Store[]>([])
  // The device's active store_code (localStorage) — read in an effect so SSR markup matches first paint.
  const [activeStore, setActiveStoreState] = useState<string | null>(null)
  const [taxCodes, setTaxCodes] = useState<TaxCode[]>([])
  const [isActivationSale, setIsActivationSale] = useState(false)
  const [lastSaleId, setLastSaleId] = useState<string | null>(null)
  const [lastSaleCustomerId, setLastSaleCustomerId] = useState<string | null>(null)
  const [lastWasActivation, setLastWasActivation] = useState(false)
  const [blockIssues, setBlockIssues] = useState<string[] | null>(null)
  // Receipt printing rules: tracks whether the print path ran for the sale currently in the
  // completion modal (receipt_mandatory_print), and which transaction auto-print already fired
  // for (receipt_auto_print must fire once per sale, not per re-render).
  const [receiptPrinted, setReceiptPrinted] = useState(false)
  const autoPrintedTxn = useRef<number | null>(null)
  // receipt_prompt_email: customer id to offer an email capture for in the completion modal
  const [emailPromptCustomer, setEmailPromptCustomer] = useState<string | null>(null)
  const [emailPromptValue, setEmailPromptValue] = useState('')
  const [savingEmail, setSavingEmail] = useState(false)

  // POS Configuration — start from the code-side defaults so nothing flashes
  // blocked/hidden before the effective config loads.
  const [cfg, setCfg] = useState<PosConfigValues>(() => resolvePosConfig([], null).values)
  // This device's open cash-drawer session at the active store (null = closed)
  const [drawerSession, setDrawerSession] = useState<RegisterSession | null>(null)

  // The register no longer resolves an employee row — the backend stamps the sale's
  // employee_id from the login; the client only needs it for the opener rule + blockers.
  const employeeId = user?.employee_id || null
  const employeeResolved = !authLoading

  useEffect(() => {
    setActiveStoreState(getActiveStore())
    loadProducts()
    api('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/pos/tax-codes').then(r => setTaxCodes(r.tax_codes || [])).catch(() => setTaxCodes([]))
    api('/api/v1/pos/receipt-template').then(r => setReceiptTemplate(r.template || null)).catch(() => {})
  }, [])

  // No store chosen on this device yet → fall back to the login's own store grant, then the
  // first store (fallback is not persisted; only an explicit pick is).
  useEffect(() => {
    if (activeStore) return
    const fallback = user?.store_code || stores[0]?.store_code || null
    if (fallback) setActiveStoreState(fallback)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, stores])

  function pickStore(code: string) {
    setActiveStore(code || null)
    setActiveStoreState(code || null)
  }

  const storeName = activeStore || ''

  // Effective POS configuration (store override ?? org default ?? code default)
  // + this device's open drawer session, reloaded whenever the store changes.
  useEffect(() => {
    let cancelled = false
    loadEffectivePosConfig(activeStore).then(values => { if (!cancelled) setCfg(values) })
    if (activeStore) {
      api(`/api/v1/pos/register/session?store_code=${encodeURIComponent(activeStore)}&register_number=${getRegisterNumber()}`)
        .then(r => { if (!cancelled) setDrawerSession((r.session as RegisterSession | null) || null) })
        .catch(() => { if (!cancelled) setDrawerSession(null) })
    } else {
      setDrawerSession(null)
    }
    return () => { cancelled = true }
  }, [activeStore])

  // When integrated card processing is suspended, credit/debit collapse into a
  // single external-terminal option — keep the selected method valid either way.
  useEffect(() => {
    if (cfg.suspend_card_processing && (paymentMethod === 'credit_card' || paymentMethod === 'debit_card')) {
      setPaymentMethod('card_external')
    } else if (!cfg.suspend_card_processing && paymentMethod === 'card_external') {
      setPaymentMethod('cash')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg.suspend_card_processing])

  // Effective tax rate as a FRACTION (e.g. 0.08875):
  //   1) an active tax code for the active store
  //   2) fallback: an active org-wide tax code (store_code null)
  //   3) fallback: none configured → null (0 tax + amber chip)
  const activeTaxRate = useMemo<number | null>(() => {
    const active = taxCodes.filter(t => t.is_active)
    if (activeStore) {
      const s = active.find(t => t.store_code === activeStore)
      if (s && Number.isFinite(Number(s.rate))) return Number(s.rate) / 100 // rate is a PERCENT (8.875)
    }
    const org = active.find(t => t.store_code === null)
    return org && Number.isFinite(Number(org.rate)) ? Number(org.rate) / 100 : null
  }, [taxCodes, activeStore])

  // Tax basis — was org_settings.tax_applied_on in the standalone app; now a config-registry key.
  const taxAppliedOn: 'pre_discount' | 'post_discount' =
    cfg.tax_applied_on === 'pre_discount' ? 'pre_discount' : 'post_discount'

  function escapeHtml(s: string) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }

  // Customer name as it goes onto the receipt. receipt_customer_short_name
  // (POS Configuration): print "First L." instead of the full name — customers
  // with only a company name on file keep the company name as is.
  function receiptCustomerName(first: string | null, last: string | null, company: string | null): string | null {
    const full = `${first || ''} ${last || ''}`.trim() || company
    if (cfg.receipt_customer_short_name !== true || !full) return full
    const f = (first || '').trim()
    const l = (last || '').trim()
    if (!f && !l) return full // company name only — leave as is
    if (!l) return f
    return `${f ? f + ' ' : ''}${l[0].toUpperCase()}.`
  }

  function buildReceiptHtml(r: ReceiptSnapshot) {
    // Tenant-customizable format (GET /pos/receipt-template); safe defaults when absent.
    const t = receiptTemplate
    const showSerials = t?.show_serials ?? true
    const showDiscounts = t?.show_discounts ?? true
    const showTax = t?.show_tax_breakdown ?? true
    const widthPx = (t?.paper_width_mm ?? 80) === 58 ? 220 : 300
    const fontPx = t?.font_size_px ?? 12
    const smallPx = Math.max(9, fontPx - 2)
    // receipt_coupon_in_retail_price (POS Configuration): fold the line discount into the printed
    // unit price — no separate −$x amounts and no Discounts subtotal row.
    const foldDiscount = cfg.receipt_coupon_in_retail_price === true
    // r.subtotal is NET of line discounts. When a separate Discounts row prints, the Subtotal
    // line must show the GROSS amount so Subtotal − Discounts + Tax = TOTAL adds up exactly.
    const showDiscountRow = !foldDiscount && showDiscounts && r.discountTotal > 0
    const displaySubtotal = showDiscountRow ? r.subtotal + r.discountTotal : r.subtotal
    // Receipt branding (POS Configuration): a configured company name replaces
    // the store name; address lines + license/tax IDs print under it.
    const brandName = String(cfg.brand_company_name || '').trim()
    const brandAddress = [cfg.brand_address_line1, cfg.brand_address_line2, cfg.brand_address_line3, cfg.brand_address_line4, cfg.brand_address_line5]
      .map(l => String(l || '').trim()).filter(l => l !== '')
    const brandIds = String(cfg.brand_license_tax_ids || '').trim()
    // Legal disclaimers (POS Configuration): blocks targeted at POS receipts
    // print after the footer, each with an optional customer-signature line.
    const disclaimerHtml = [1, 2].map(n => ({
      text: String(cfg[`disclaimer${n}_text`] || '').trim(),
      target: String(cfg[`disclaimer${n}_target`] || 'none'),
      signature: cfg[`disclaimer${n}_signature`] === true,
    })).filter(d => d.text !== '' && (d.target === 'pos' || d.target === 'both')).map(d =>
      `<hr><div class="pre" style="font-size:${smallPx}px">${escapeHtml(d.text)}</div>` +
      (d.signature ? `<div style="margin-top:16px">X ______________________<br><span style="font-size:${smallPx}px">Customer signature</span></div>` : '')
    ).join('')
    const rows = r.items.map(i => `
      <tr><td colspan="3" class="desc">${escapeHtml(i.description)}${showSerials && i.serial ? `<br><span class="serial">S/N: ${escapeHtml(i.serial)}</span>` : ''}</td></tr>
      <tr class="nums"><td>${i.qty} × $${(foldDiscount ? i.unit_price - i.discount : i.unit_price).toFixed(2)}${!foldDiscount && showDiscounts && i.discount > 0 ? ` −$${i.discount.toFixed(2)}` : ''}</td><td></td><td class="r">$${i.line_total.toFixed(2)}</td></tr>
    `).join('')
    return `<!doctype html><html><head><title>Receipt #${r.transactionId ?? ''}</title><style>
      * { margin: 0; padding: 0; box-sizing: border-box; }
      body { font-family: 'Courier New', monospace; font-size: ${fontPx}px; color: #000; width: ${widthPx}px; padding: 12px; }
      .center { text-align: center; }
      .store { font-size: ${fontPx + 2}px; font-weight: bold; }
      .pre { white-space: pre-wrap; }
      hr { border: none; border-top: 1px dashed #000; margin: 8px 0; }
      table { width: 100%; border-collapse: collapse; }
      td { padding: 1px 0; vertical-align: top; }
      .r { text-align: right; }
      .desc { font-weight: bold; }
      .serial { font-weight: normal; font-size: ${Math.max(9, fontPx - 2)}px; }
      .nums td { color: #333; }
      .grand { font-size: ${fontPx + 2}px; font-weight: bold; }
      @media print { body { width: auto; } }
    </style></head><body>
      <div class="center">
        ${((t?.show_store_name ?? true) || brandName) ? `<div class="store">${escapeHtml(brandName || storeName || 'POS System')}</div>` : ''}${brandAddress.map(l => `<div>${escapeHtml(l)}</div>`).join('')}${brandIds ? `<div>${escapeHtml(brandIds)}</div>` : ''}
        ${t?.header_text ? `<div class="pre">${escapeHtml(t.header_text)}</div>` : ''}
        <div>Transaction #${r.transactionId ?? ''}</div>
        <div>${escapeHtml(r.date)}</div>
        ${(t?.show_customer ?? true) && r.customerName ? `<div>Customer: ${escapeHtml(r.customerName)}</div>` : ''}
      </div>
      <hr><table>${rows}</table><hr>
      <table>
        <tr><td>Subtotal</td><td class="r">$${displaySubtotal.toFixed(2)}</td></tr>
        ${showDiscountRow ? `<tr><td>Discounts</td><td class="r">−$${r.discountTotal.toFixed(2)}</td></tr>` : ''}
        ${showTax ? `<tr><td>Tax</td><td class="r">$${r.taxTotal.toFixed(2)}</td></tr>` : ''}
        <tr class="grand"><td>TOTAL</td><td class="r">$${r.total.toFixed(2)}</td></tr>
      </table><hr>
      <table>
        <tr><td>Paid (${escapeHtml(r.method)})</td><td class="r">$${(r.tendered ?? r.total).toFixed(2)}</td></tr>
        ${r.change > 0 ? `<tr><td>Change</td><td class="r">$${r.change.toFixed(2)}</td></tr>` : ''}
      </table>
      <hr><div class="center pre">${escapeHtml(t?.footer_text || 'Thank you for your business!')}</div>${disclaimerHtml}
    </body></html>`
  }

  function printHtml(html: string) {
    const iframe = document.createElement('iframe')
    iframe.style.position = 'fixed'
    iframe.style.right = '-9999px'
    document.body.appendChild(iframe)
    const doc = iframe.contentWindow?.document
    if (!doc) { alert('Could not open print view'); iframe.remove(); return }
    doc.open(); doc.write(html); doc.close()
    iframe.onload = () => {
      iframe.contentWindow?.focus()
      iframe.contentWindow?.print()
      setTimeout(() => iframe.remove(), 2000)
    }
  }

  // receipt_auto_print: when the completion modal opens, fire the same print path as the
  // Print Receipt button — once per sale (the ref guards against re-renders re-firing).
  useEffect(() => {
    if (!showReceipt || !lastReceipt || cfg.receipt_auto_print !== true) return
    if (autoPrintedTxn.current === lastReceipt.transactionId) return
    autoPrintedTxn.current = lastReceipt.transactionId
    printHtml(buildReceiptHtml(lastReceipt))
    setReceiptPrinted(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showReceipt, lastReceipt, cfg.receipt_auto_print])

  // receipt_prompt_email: save the email typed in the completion modal onto the customer's profile.
  async function saveCustomerEmail() {
    if (!emailPromptCustomer) return
    const email = emailPromptValue.trim()
    if (!email) { alert('Enter an email address, or click Skip.'); return }
    setSavingEmail(true)
    try {
      await api(`/api/v1/pos/customers/${emailPromptCustomer}`, { method: 'PATCH', body: JSON.stringify({ email }) })
      setEmailPromptCustomer(null)
    } catch (err: any) {
      alert(`Could not save email: ${err?.message || err}`)
    }
    setSavingEmail(false)
  }

  async function printSelectedSale() {
    if (!selectedSale) { alert('Select a transaction in the list first.'); return }
    try {
      const r = await api(`/api/v1/pos/sales/${selectedSale.id}`)
      const s = r.sale
      // Prefer the receipt snapshot persisted at checkout — reprints are then exact.
      const snap = s?.receipt as ReceiptSnapshot | null
      if (snap && Array.isArray(snap.items)) {
        printHtml(buildReceiptHtml({ ...snap, transactionId: snap.transactionId ?? s.transaction_id }))
        return
      }
      // Older/imported sales without a snapshot: rebuild from the rows like the source did.
      printHtml(buildReceiptHtml({
        transactionId: s.transaction_id,
        date: new Date(s.created_at).toLocaleString(),
        customerName: selectedSale.customer_name || null,
        items: (s.items || []).map((i: any) => ({
          description: i.description || '',
          serial: i.serial_number,
          qty: i.qty,
          unit_price: i.unit_price || 0,
          discount: i.discount || 0,
          line_total: i.extended_price || 0,
        })),
        subtotal: s.subtotal || 0,
        discountTotal: s.discount_total || 0,
        taxTotal: s.tax_total || 0,
        total: s.total || 0,
        method: s.payments?.[0]?.payment_method || '—',
        tendered: null,
        change: 0,
      }))
    } catch (err: any) {
      alert(`Could not load the receipt: ${err?.message || err}`)
    }
  }

  async function loadProducts() {
    try {
      const r = await api('/api/v1/pos/products')
      setProducts(r.products || [])
    } catch { setProducts([]) }
  }

  // Server-side product search for the picker (the default catalog list caps at 500 rows).
  useEffect(() => {
    if (!showProductPicker) return
    const q = productSearch.trim()
    if (!q) { setPickerResults(null); return }
    const t = setTimeout(() => {
      api(`/api/v1/pos/products?search=${encodeURIComponent(q)}`)
        .then(r => setPickerResults(r.products || []))
        .catch(() => setPickerResults(null))
    }, 250)
    return () => clearTimeout(t)
  }, [productSearch, showProductPicker])

  async function loadSales() {
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (slDateFrom) params.set('date_from', dayStartIso(slDateFrom))
      if (slDateTo) params.set('date_to', dayEndIso(slDateTo))
      if (slStore) params.set('store_code', slStore)
      const r = await api(`/api/v1/pos/sales?${params}`)
      let rows: Sale[] = r.sales || []
      // Client-side refinements the list endpoint doesn't take:
      const cust = slCustomerSearch.trim().toLowerCase()
      if (cust) rows = rows.filter(s => (s.customer_name || '').toLowerCase().includes(cust))
      if (slActivationOnly) rows = rows.filter(s => !!s.is_activation_sale)
      const serial = slSerial.trim().toLowerCase()
      if (serial) {
        // Serial lookup scans the persisted receipt snapshots (sales checked out here always have one).
        rows = rows.filter(s => (s.receipt?.items || []).some(i => (i.serial || '').toLowerCase().includes(serial)))
      }
      setSales(rows)
    } catch (err: any) {
      alert(`Could not load sales: ${err?.message || err}`)
    }
    setSelectedSale(null)
    setSaleItems([])
    setLoading(false)
  }

  async function searchCustomers() {
    if (!customerSearch.trim()) return
    try {
      const r = await api(`/api/v1/pos/customers?search=${encodeURIComponent(customerSearch.trim())}`)
      setCustomers(r.customers || [])
    } catch { setCustomers([]) }
  }

  async function createQuickCustomer() {
    if (!newCust.first_name.trim() && !newCust.last_name.trim()) {
      alert('Enter at least a first or last name')
      return
    }
    setCreatingCustomer(true)
    try {
      const r = await api('/api/v1/pos/customers', {
        method: 'POST',
        body: JSON.stringify({
          first_name: newCust.first_name.trim() || null,
          last_name: newCust.last_name.trim() || null,
          phone_primary: newCust.phone_primary.trim() || null,
          email: newCust.email.trim() || null,
          account_type: 'Personal',
          referral_source: 'Walk In Customer',
          is_active: true,
        }),
      })
      if (!r.customer) throw new Error('unknown error')
      setSelectedCustomer(r.customer as Customer)
      setShowNewCustomer(false)
      setShowCustomerPicker(false)
      setNewCust({ first_name: '', last_name: '', phone_primary: '', email: '' })
    } catch (err: any) {
      alert(`Could not create customer: ${err?.message || err}`)
    }
    setCreatingCustomer(false)
  }

  async function loadSaleItems(saleId: string) {
    try {
      const r = await api(`/api/v1/pos/sales/${saleId}`)
      setSaleItems(r.sale?.items || [])
    } catch { setSaleItems([]) }
  }

  // Recompute per-unit tax and line total from unit_price/discount/tax_rate/qty.
  // Returns a NEW object — cart state is never mutated in place.
  function recalcItem(item: CartItem): CartItem {
    const discountedPrice = item.unit_price - item.discount
    // Tax basis: pre_discount taxes the full unit price, post_discount (default)
    // taxes the price after the line discount.
    let taxBase = taxAppliedOn === 'pre_discount' ? item.unit_price : discountedPrice
    // Cost-based method (POS Configuration): never tax below the product's cost.
    if (cfg.tax_method === 'cost_based' && taxBase < item.cost) taxBase = item.cost
    let taxValue = item.tax_rate > 0 ? parseFloat((taxBase * item.tax_rate).toFixed(2)) : 0
    // Per-unit tax cap (POS Configuration): 0 / disabled = no cap.
    const taxCap = Number(cfg.tax_cap_amount)
    if (cfg.tax_cap_enabled && taxCap > 0 && taxValue > taxCap) taxValue = parseFloat(taxCap.toFixed(2))
    return {
      ...item,
      tax_value: taxValue,
      extended_price: parseFloat(((discountedPrice + taxValue) * item.qty).toFixed(2)),
    }
  }

  // Re-tax cart lines when the effective rate or tax basis changes (e.g. store switched mid-sale).
  // Non-taxable products stay at 0.
  useEffect(() => {
    setCart(prev => prev.length === 0 ? prev : prev.map(it => {
      // is_taxable is stamped on the item at add time; the catalog lookup is only a
      // fallback for items that somehow predate the stamp.
      const taxable = it.is_taxable !== undefined
        ? it.is_taxable
        : (products.find(pp => pp.id === it.product_id)?.is_taxable ?? it.tax_rate > 0)
      return recalcItem({ ...it, tax_rate: taxable ? (activeTaxRate ?? 0) : 0 })
    }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTaxRate, taxAppliedOn, cfg.tax_method, cfg.tax_cap_enabled, cfg.tax_cap_amount])

  function addToCart(product: Product, serial = '') {
    const newItem: CartItem = recalcItem({
      product_id: product.id,
      description: product.short_name,
      product_type: product.system_category || 'Regular',
      serial_number: serial,
      qty: 1,
      unit_price: product.retail_price,
      cost: product.cost,
      discount: 0,
      tax_rate: product.is_taxable ? (activeTaxRate ?? 0) : 0,
      tax_value: 0,
      extended_price: 0,
      is_taxable: product.is_taxable,
    })
    setCart(prev => {
      if (!serial) {
        // Only merge into an existing non-serialized line for the same product
        const existing = prev.findIndex(i => i.product_id === product.id && !i.serial_number)
        if (existing >= 0) {
          return prev.map((it, i) => i === existing ? recalcItem({ ...it, qty: it.qty + 1 }) : it)
        }
      }
      return [...prev, newItem]
    })
    setShowProductPicker(false)
    setQuickScanInput('')
  }

  function handleQuickScan(e: React.KeyboardEvent) {
    if (e.key !== 'Enter') return
    const q = quickScanInput.trim()
    if (!q) return
    const match = products.find(p => p.upc === q || p.short_name.toLowerCase().includes(q.toLowerCase()))
    if (match) addToCart(match)
    else { setProductSearch(q); setShowProductPicker(true) }
  }

  function removeFromCart(idx: number) { setCart(prev => prev.filter((_, i) => i !== idx)) }

  function updateQty(idx: number, qty: number) {
    setCart(prev => qty <= 0
      ? prev.filter((_, i) => i !== idx)
      : prev.map((it, i) => i === idx ? recalcItem({ ...it, qty }) : it))
  }

  function updateDiscount(idx: number, discount: number) {
    setCart(prev => prev.map((it, i) => i === idx ? recalcItem({ ...it, discount }) : it))
  }

  function updateSerial(idx: number, serial: string) {
    setCart(prev => prev.map((it, i) => i === idx ? { ...it, serial_number: serial } : it))
  }

  const subtotal = cart.reduce((s, i) => s + (i.unit_price - i.discount) * i.qty, 0)
  const taxTotal = cart.reduce((s, i) => s + i.tax_value * i.qty, 0)
  const discountTotal = cart.reduce((s, i) => s + i.discount * i.qty, 0)
  const total = subtotal + taxTotal
  const change = parseFloat(amountTendered || '0') - total

  // Attribution requirements — a sale may not be rung up without a store (inventory moves at the
  // sale's store_code) and a login linked to an employee (the backend stamps employee_id from it).
  // atPayment: the cash-specific drawer rule only applies once the payment method is final (in
  // completeSale) — the pre-modal check must not block a customer about to pay by card.
  function getCheckoutBlockers(atPayment = false): string[] {
    const issues: string[] = []
    if (!activeStore) {
      issues.push('No active store is selected. Pick a store in the 🏪 store selector in the page header — inventory is deducted at the sale\'s store, so every sale must be attributed to one.')
    }
    if (!employeeResolved) {
      issues.push('Still verifying which employee is linked to your login — wait a moment and try again.')
    } else if (!employeeId) {
      issues.push('Your login is not linked to an employee record. Ask an admin to link your login to an employee record in the Employees module.')
    }
    // POS Configuration rules
    if (cfg.require_customer && !selectedCustomer) {
      issues.push('This store requires a customer on every sale — attach a customer before checkout.')
    }
    if (cfg.referral_required && (!selectedCustomer || !selectedCustomer.referral_source)) {
      issues.push('A referral source is required — the attached customer has none on file (edit the customer or pick another).')
    }
    if (cfg.only_opener_transacts && drawerSession && drawerSession.opened_by && employeeId && drawerSession.opened_by !== employeeId) {
      issues.push('The register was opened by another employee — only the opener can transact while this drawer is open.')
    }
    if (atPayment && cfg.enforce_drawer_sessions && paymentMethod === 'cash' && !drawerSession) {
      issues.push('Cash sales require an open register — open the drawer first (top of the register).')
    }
    return issues
  }

  async function completeSale() {
    if (cart.length === 0) return
    const blockers = getCheckoutBlockers(true)
    if (blockers.length > 0) {
      setShowPayment(false)
      setBlockIssues(blockers)
      return
    }
    const saleTotal = parseFloat(total.toFixed(2))

    // Cash tender: a blank amount means exact payment; an amount below the total blocks the
    // sale. The resolved tendered/change land on the receipt snapshot (never 0/0 for cash).
    let cashTendered: number | null = null
    let cashChange = 0
    if (paymentMethod === 'cash') {
      const raw = amountTendered.trim()
      const parsed = raw === '' ? NaN : parseFloat(raw)
      if (!Number.isFinite(parsed)) {
        cashTendered = saleTotal // exact payment
      } else if (parsed < saleTotal) {
        alert(`Amount tendered ($${parsed.toFixed(2)}) is less than the total ($${saleTotal.toFixed(2)}).`)
        return
      } else {
        cashTendered = parsed
        cashChange = parseFloat((parsed - saleTotal).toFixed(2))
      }
    }
    setSaving(true)

    // Drawer cash limit (POS Configuration): with an open session and a cash payment, block the
    // sale if float + cash taken since open + this sale would exceed the configured maximum
    // (a cash drop is required first). The server computes the drawer cash.
    const maxCash = Number(cfg.max_cash_in_drawer)
    if (paymentMethod === 'cash' && maxCash > 0 && drawerSession) {
      let drawerCash = 0
      try {
        const r = await api(`/api/v1/pos/register/drawer-cash?session_id=${encodeURIComponent(drawerSession.id)}`)
        drawerCash = Number(r.cash) || 0
      } catch (err: any) {
        alert(`Could not verify the drawer cash total against the limit in POS Configuration: ${err?.message || err}`)
        setSaving(false)
        return
      }
      if (drawerCash + saleTotal > maxCash) {
        alert(`Drawer cash would exceed the $${maxCash.toFixed(2)} limit set in POS Configuration — perform a cash drop (close & reopen the drawer) before more cash sales.`)
        setSaving(false)
        return
      }
    }

    // Receipt snapshot — persisted on the sale row so Sale Log reprints are exact.
    // transactionId is generated at insert; reprints merge it back in from the sale row.
    const receiptSnapshot: ReceiptSnapshot = {
      transactionId: null,
      date: new Date().toLocaleString(),
      customerName: selectedCustomer
        ? receiptCustomerName(selectedCustomer.first_name, selectedCustomer.last_name, selectedCustomer.company_name)
        : null,
      items: cart.map(i => ({
        description: i.description,
        serial: i.serial_number || null,
        qty: i.qty,
        unit_price: i.unit_price,
        discount: i.discount,
        line_total: i.extended_price,
      })),
      subtotal: parseFloat(subtotal.toFixed(2)),
      discountTotal: parseFloat(discountTotal.toFixed(2)),
      taxTotal: parseFloat(taxTotal.toFixed(2)),
      total: saleTotal,
      method: paymentMethod,
      tendered: paymentMethod === 'cash' ? cashTendered : null,
      change: paymentMethod === 'cash' ? cashChange : 0,
    }

    // ONE atomic call: sale + items + payments commit or roll back together. A stock block
    // (allow_negative_inventory off) rolls the WHOLE sale back — the server message says why.
    let sale: Sale
    try {
      const r = await api('/api/v1/pos/sales/checkout', {
        method: 'POST',
        body: JSON.stringify({
          sale: {
            store_code: activeStore,
            customer_id: selectedCustomer?.id || null,
            receipt_type: 'sale',
            subtotal: parseFloat(subtotal.toFixed(2)),
            discount_total: parseFloat(discountTotal.toFixed(2)),
            tax_total: parseFloat(taxTotal.toFixed(2)),
            total: saleTotal,
            balance: 0,
            is_activation_sale: isActivationSale,
            receipt: receiptSnapshot,
          },
          items: cart.map(item => ({
            product_id: item.product_id,
            product_type: item.product_type,
            description: item.description,
            serial_number: item.serial_number || null,
            qty: item.qty,
            unit_price: item.unit_price,
            cost: item.cost,
            discount: item.discount,
            tax_rate: item.tax_rate,
            // line-level tax, consistent with extended_price being the line total
            tax_value: parseFloat((item.tax_value * item.qty).toFixed(2)),
            extended_price: item.extended_price,
          })),
          payments: [{ payment_method: paymentMethod, amount: saleTotal }],
        }),
      })
      sale = r.sale as Sale
      if (!sale?.id) throw new Error('unknown error')
    } catch (err: any) {
      alert(`Could not save sale: ${err?.message || 'unknown error'}`)
      setSaving(false)
      return
    }

    setLastTransactionId(sale.transaction_id)
    // Captured for the receipt modal's 'Continue to Activation' hand-off
    setLastSaleId(sale.id)
    setLastSaleCustomerId(selectedCustomer?.id || null)
    setLastWasActivation(isActivationSale)
    // Fresh sale → the print-tracking flag resets (receipt_mandatory_print), and a customer with
    // no email on file triggers the capture prompt (receipt_prompt_email).
    setReceiptPrinted(false)
    setEmailPromptCustomer(
      cfg.receipt_prompt_email === true && selectedCustomer && !(selectedCustomer.email || '').trim()
        ? selectedCustomer.id : null
    )
    setEmailPromptValue('')
    setLastReceipt({ ...receiptSnapshot, transactionId: sale.transaction_id })
    setCart([])
    setSelectedCustomer(null)
    setShowPayment(false)
    setShowReceipt(true)
    setAmountTendered('')
    setIsActivationSale(false)
    setSaving(false)
  }

  async function voidSale() {
    if (!selectedSale) { alert('Select a transaction in the list first.'); return }
    if (selectedSale.status === 'voided' || selectedSale.voided_at) { alert(`Transaction #${selectedSale.transaction_id} is already voided.`); return }
    if (!confirm(`Void transaction #${selectedSale.transaction_id} for $${selectedSale.total?.toFixed(2)}?`)) return
    setVoiding(true)
    try {
      await api(`/api/v1/pos/sales/${selectedSale.id}/void`, { method: 'POST' })
      setSelectedSale(null)
      loadSales()
    } catch (err: any) {
      const m = String(err?.message || '')
      alert(/does not allow|pos_void/i.test(m) ? 'Your role does not allow voiding sales.' : m || 'Void failed')
    }
    setVoiding(false)
  }

  // Discounts: the org toggle removes the field entirely; fine-grained void/discount
  // permissions are enforced server-side by the /pos router.
  const discountsEnabled = cfg.coupon_discount_enabled !== false
  const canDiscount = discountsEnabled
  // receipt_mandatory_print: the completion modal's close/continue controls stay
  // disabled until the print path has run.
  const mustPrint = cfg.receipt_mandatory_print === true && !receiptPrinted
  const mustPrintTitle = 'Receipt printing is mandatory — print first'

  return (
    <div>
      {/* Header — title, store selector, drawer chip, register lock */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14, flexWrap: 'wrap', gap: 10 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>💰 POS — Cash Register</h1>
          <p style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Sales register · {user?.email || ''}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ fontSize: 12, color: 'var(--text2)' }}>🏪 Store:</label>
          <select value={activeStore || ''} onChange={e => pickStore(e.target.value)} style={{ ...inputStyle, width: 170 }}>
            <option value="">— select store —</option>
            {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}</option>)}
          </select>
          <RegisterDrawer activeStore={activeStore} storeName={storeName} cfg={cfg} session={drawerSession} onSessionChange={setDrawerSession} />
          {cfg.enable_register_lock === true && (
            <RegisterLock autoLockMinutes={Number(cfg.register_lock_minutes) || 0} />
          )}
          <button className="btn btn-secondary" onClick={() => { setActiveView('salelog') }}>🔍 Sale Log</button>
        </div>
      </div>

      {/* ===== HOME VIEW ===== */}
      {activeView === 'home' && (
        <div>
          <div style={{ ...panel, marginBottom: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10, paddingBottom: 4, borderBottom: '1px solid var(--border)' }}>Sales</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button onClick={() => setActiveView('sale')} style={{ background: 'var(--green)', border: 'none', color: 'white', borderRadius: 7, padding: '12px 20px', fontSize: 13, fontWeight: 700, cursor: 'pointer', minWidth: 140 }}>
                💵 Customer Sale
              </button>
              <a href="/pos/activations" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--green)', color: 'white', borderRadius: 7, padding: '12px 20px', fontSize: 13, fontWeight: 700, textDecoration: 'none', minWidth: 160 }}>
                📋 Manual Activation
              </a>
              <button onClick={() => setActiveView('salelog')} className="btn btn-secondary" style={{ minWidth: 120, justifyContent: 'center' }}>
                🔍 Look Up
              </button>
            </div>
          </div>

          {/* Quick Sale scan — part of the New Sale flow */}
          <div style={{ ...panel, maxWidth: 500, margin: '0 auto 14px' }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text2)', marginBottom: 10, textAlign: 'center' }}>Quick Sale:</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <input
                value={quickScanInput}
                onChange={e => setQuickScanInput(e.target.value)}
                onKeyDown={handleQuickScan}
                placeholder="<Scan product here>"
                style={{ ...inputStyle, flex: 1, textAlign: 'center' }}
                autoFocus
              />
              <button className="btn btn-primary" onClick={() => setShowProductPicker(true)}>🔍</button>
            </div>
          </div>

          <div style={{ textAlign: 'center', fontSize: 12, color: 'var(--text3)', marginTop: 16 }}>
            Register {getRegisterNumber()}: <span style={{ color: 'var(--accent2)' }}>Home</span> » <span style={{ color: 'var(--accent2)' }}>Sale</span> » <span style={{ color: 'var(--accent2)' }}>Customer Sale</span>
          </div>
        </div>
      )}

      {/* ===== SALE VIEW ===== */}
      {activeView === 'sale' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', border: '1px solid var(--border)', borderRadius: 10, overflow: 'hidden', minHeight: 480, height: 'calc(100vh - 220px)' }}>

          {/* LEFT — Cart */}
          <div style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--surface)' }}>
            {/* Customer bar */}
            <div style={{ background: 'var(--surface2)', borderBottom: '1px solid var(--border)', padding: '10px 16px', display: 'flex', gap: 10, alignItems: 'center' }}>
              <span style={{ fontSize: 12, color: 'var(--text2)' }}>Customer:</span>
              {selectedCustomer ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
                  <span style={{ fontSize: 13, fontWeight: 600 }}>
                    {selectedCustomer.first_name} {selectedCustomer.last_name} (#{selectedCustomer.cust_number})
                  </span>
                  <button onClick={() => setSelectedCustomer(null)} style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', fontSize: 14 }}>×</button>
                </div>
              ) : (
                <button className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 12px' }} onClick={() => setShowCustomerPicker(true)}>
                  + Select Customer
                </button>
              )}
              <button className="btn btn-secondary" style={{ fontSize: 11, padding: '5px 10px', marginLeft: 'auto' }} onClick={() => setActiveView('home')}>← Back</button>
            </div>

            {/* Cart items table */}
            <div style={{ flex: 1, overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead style={{ position: 'sticky', top: 0, zIndex: 10 }}>
                  <tr style={{ background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
                    {['Product Type', 'Product', 'Serial #', 'Qty', 'Unit Price', 'Discount', 'Tax', 'Extended Price', 'Tax Value', ''].map(h => (
                      <th key={h} style={thStyle}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {cart.length === 0 ? (
                    <tr>
                      <td colSpan={10} style={{ padding: 60, textAlign: 'center', color: 'var(--text3)' }}>
                        <div style={{ fontSize: 32, marginBottom: 12 }}>🛒</div>
                        <div style={{ fontSize: 14 }}>Cart is empty</div>
                        <div style={{ fontSize: 12, marginTop: 6 }}>Scan a product or click + Add Product</div>
                      </td>
                    </tr>
                  ) : cart.map((item, idx) => (
                    <tr key={idx} style={{ borderBottom: '1px solid var(--border)', background: idx % 2 === 0 ? 'transparent' : 'var(--surface2)' }}>
                      <td style={{ padding: '8px 10px', color: 'var(--text2)' }}>{item.product_type}</td>
                      <td style={{ padding: '8px 10px', fontWeight: 500, maxWidth: 180 }}>{item.description}</td>
                      <td style={{ padding: '8px 10px' }}>
                        <input value={item.serial_number} onChange={e => updateSerial(idx, e.target.value)}
                          style={{ ...inputStyle, width: 100, padding: '4px 6px', fontSize: 11 }} placeholder="Serial #" />
                      </td>
                      <td style={{ padding: '8px 10px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                          <button onClick={() => updateQty(idx, item.qty - 1)} style={{ background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)', width: 20, height: 20, borderRadius: 3, cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>-</button>
                          <span style={{ minWidth: 20, textAlign: 'center', fontWeight: 600 }}>{item.qty}</span>
                          <button onClick={() => updateQty(idx, item.qty + 1)} style={{ background: 'var(--surface2)', border: '1px solid var(--border)', color: 'var(--text)', width: 20, height: 20, borderRadius: 3, cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>+</button>
                        </div>
                      </td>
                      <td style={{ padding: '8px 10px', color: 'var(--green)' }}>${item.unit_price.toFixed(2)}</td>
                      <td style={{ padding: '8px 10px' }}>
                        {discountsEnabled ? (
                          <input type="number" step="0.01" value={item.discount} readOnly={!canDiscount}
                            onChange={e => canDiscount && updateDiscount(idx, parseFloat(e.target.value) || 0)}
                            style={{ ...inputStyle, width: 60, padding: '4px 6px', fontSize: 11 }} />
                        ) : (
                          <span title="Discounts are disabled in POS Settings" style={{ color: 'var(--text3)' }}>—</span>
                        )}
                      </td>
                      <td style={{ padding: '8px 10px', color: 'var(--text2)' }}>{item.tax_rate > 0 ? `${(item.tax_rate * 100).toFixed(2)}%` : 'No'}</td>
                      <td style={{ padding: '8px 10px', color: 'var(--green)', fontWeight: 600 }}>${item.extended_price.toFixed(2)}</td>
                      <td style={{ padding: '8px 10px', color: 'var(--text2)' }}>${(item.tax_value * item.qty).toFixed(2)}</td>
                      <td style={{ padding: '8px 10px' }}>
                        <button onClick={() => removeFromCart(idx)} style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', fontSize: 14 }}>×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Bottom action bar */}
            <div style={{ background: 'var(--surface2)', borderTop: '1px solid var(--border)', padding: '10px 16px', display: 'flex', gap: 8 }}>
              <button className="btn btn-primary" style={{ fontSize: 12 }} onClick={() => setShowProductPicker(true)}>+ Add Product</button>
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => setCart([])}>🗑️ Clear Cart</button>
              <div style={{ flex: 1 }}>
                <input value={quickScanInput} onChange={e => setQuickScanInput(e.target.value)} onKeyDown={handleQuickScan} placeholder="Scan product UPC..." style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} />
              </div>
            </div>
          </div>

          {/* RIGHT — Totals & Payment */}
          <div style={{ background: 'var(--surface2)', borderLeft: '1px solid var(--border)', display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
            {/* Totals */}
            <div style={{ padding: 20, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 16 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text2)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Order Summary</div>
                {activeTaxRate === null && (
                  <a href="/pos/settings" title="No active tax code for this store — configure one in POS Settings"
                    style={{ background: 'rgba(217,119,6,0.1)', border: '1px solid var(--amber)', color: 'var(--amber)', borderRadius: 10, padding: '2px 8px', fontSize: 10, fontWeight: 600, textDecoration: 'none', whiteSpace: 'nowrap' }}>
                    ⚠ no tax code configured
                  </a>
                )}
              </div>

              {[
                // `subtotal` is NET of line discounts — show the GROSS amount next to the
                // Discount row so Subtotal − Discount + Tax = TOTAL adds up exactly.
                { label: 'Subtotal', value: `$${(subtotal + discountTotal).toFixed(2)}`, red: false },
                { label: 'Discount', value: discountTotal > 0 ? `-$${discountTotal.toFixed(2)}` : '$0.00', red: discountTotal > 0 },
                { label: `Tax (${((activeTaxRate ?? 0) * 100).toFixed(3).replace(/\.?0+$/, '')}%)`, value: `$${taxTotal.toFixed(2)}`, red: false },
              ].map(row => (
                <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 13 }}>
                  <span style={{ color: 'var(--text2)' }}>{row.label}</span>
                  <span style={{ color: row.red ? 'var(--red)' : 'var(--text)' }}>{row.value}</span>
                </div>
              ))}

              <div style={{ display: 'flex', justifyContent: 'space-between', padding: '14px 0', fontSize: 20, fontWeight: 700 }}>
                <span>TOTAL</span>
                <span style={{ color: 'var(--green)' }}>${total.toFixed(2)}</span>
              </div>

              <div style={{ fontSize: 12, color: 'var(--text3)', marginBottom: 16 }}>
                Items: {cart.reduce((s, i) => s + i.qty, 0)} | {cart.length} line{cart.length !== 1 ? 's' : ''}
              </div>

              {/* Quick amount buttons */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 6 }}>Quick amounts:</div>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {[20, 50, 100, 'Exact'].map(amt => (
                    <button key={amt} className="btn btn-secondary" style={{ fontSize: 12, padding: '5px 10px' }}
                      onClick={() => setAmountTendered(amt === 'Exact' ? total.toFixed(2) : String(amt))}>
                      {amt === 'Exact' ? 'Exact' : `$${amt}`}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Payment section */}
            <div style={{ padding: 16, borderTop: '1px solid var(--border)' }}>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 8 }}>Payment Method:</div>
              <div style={{ display: 'flex', gap: 6, marginBottom: 10, flexWrap: 'wrap' }}>
                {(cfg.suspend_card_processing
                  ? [
                      { value: 'cash', label: '💵 Cash' },
                      { value: 'card_external', label: '💳 Card (external terminal)' },
                      { value: 'check', label: '📝 Check' },
                    ]
                  : [
                      { value: 'cash', label: '💵 Cash' },
                      { value: 'credit_card', label: '💳 Credit' },
                      { value: 'debit_card', label: '🏧 Debit' },
                      { value: 'check', label: '📝 Check' },
                    ]
                ).map(m => (
                  <button key={m.value} onClick={() => setPaymentMethod(m.value)}
                    style={{ background: paymentMethod === m.value ? 'var(--accent)' : 'var(--surface)', border: `1px solid ${paymentMethod === m.value ? 'var(--accent2)' : 'var(--border)'}`, color: paymentMethod === m.value ? 'white' : 'var(--text)', borderRadius: 6, padding: '6px 10px', fontSize: 11, cursor: 'pointer', fontWeight: paymentMethod === m.value ? 700 : 400 }}>
                    {m.label}
                  </button>
                ))}
              </div>
              {cfg.suspend_card_processing === true && (
                <div style={{ fontSize: 11, color: 'var(--amber)', marginBottom: 10 }}>⚠ Integrated card processing is suspended</div>
              )}

              {paymentMethod === 'cash' && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 4 }}>Amount Tendered:</div>
                  <input type="number" value={amountTendered} onChange={e => setAmountTendered(e.target.value)}
                    style={{ ...inputStyle, width: '100%', boxSizing: 'border-box', fontSize: 16, fontWeight: 700 }} placeholder="$0.00" />
                  {parseFloat(amountTendered) > 0 && (
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, fontSize: 13 }}>
                      <span style={{ color: 'var(--text2)' }}>Change:</span>
                      <span style={{ color: change >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>${Math.max(0, change).toFixed(2)}</span>
                    </div>
                  )}
                </div>
              )}

              <button onClick={() => {
                if (cart.length === 0) return
                const issues = getCheckoutBlockers()
                if (issues.length > 0) { setBlockIssues(issues); return }
                setShowPayment(true)
              }} disabled={cart.length === 0 || saving}
                style={{ width: '100%', background: cart.length === 0 ? 'var(--surface)' : 'var(--green)', border: cart.length === 0 ? '1px solid var(--border)' : 'none', color: cart.length === 0 ? 'var(--text3)' : 'white', borderRadius: 8, padding: 14, fontSize: 15, fontWeight: 700, cursor: cart.length === 0 ? 'not-allowed' : 'pointer', opacity: cart.length === 0 ? 0.6 : 1 }}>
                ✅ Process Sale — ${total.toFixed(2)}
              </button>

              <button className="btn btn-secondary" style={{ width: '100%', justifyContent: 'center', marginTop: 8 }} onClick={() => setActiveView('home')}>
                Cancel Sale
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== SALE LOG VIEWER ===== */}
      {activeView === 'salelog' && (
        <div>
          {/* Action bar */}
          <div style={{ ...panel, marginBottom: 14, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={printSelectedSale}>🖨️ Print Preview</button>
            <button onClick={voidSale} disabled={voiding} style={{ background: 'var(--red)', border: 'none', color: 'white', borderRadius: 6, padding: '6px 12px', fontSize: 12, fontWeight: 600, cursor: voiding ? 'wait' : 'pointer', opacity: voiding ? 0.7 : 1 }}>✗ Void Sale Receipt</button>
            <button className="btn btn-secondary" style={{ fontSize: 12, marginLeft: 'auto' }} onClick={() => setActiveView('home')}>← Back to POS</button>
          </div>

          {/* Search parameters */}
          <div style={{ ...panel, marginBottom: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 12, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Search Parameters — Select by Transaction #</div>
            <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Store</div>
                <select value={slStore} onChange={e => setSlStore(e.target.value)} style={{ ...inputStyle, width: 160 }}>
                  <option value="">All stores</option>
                  {stores.map(s => <option key={s.store_code} value={s.store_code}>{s.store_code}</option>)}
                </select>
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Date From</div>
                <input type="date" value={slDateFrom} onChange={e => setSlDateFrom(e.target.value)} style={{ ...inputStyle, width: 140 }} />
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>To</div>
                <input type="date" value={slDateTo} onChange={e => setSlDateTo(e.target.value)} style={{ ...inputStyle, width: 140 }} />
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Customer</div>
                <input value={slCustomerSearch} onChange={e => setSlCustomerSearch(e.target.value)} placeholder="<Any>" style={{ ...inputStyle, width: 160 }} />
              </div>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginBottom: 3 }}>Serial</div>
                <input value={slSerial} onChange={e => setSlSerial(e.target.value)} style={{ ...inputStyle, width: 140 }} />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, color: 'var(--text2)', cursor: 'pointer', marginBottom: 2 }}>
                  <input type="checkbox" checked={slActivationOnly} onChange={e => setSlActivationOnly(e.target.checked)} />
                  Activation only
                </label>
                <button className="btn btn-primary" onClick={loadSales}>Search</button>
              </div>
            </div>
          </div>

          {/* Transactions table */}
          <div className="table-wrapper" style={{ overflowX: 'auto', marginBottom: 14 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 900 }}>
              <thead>
                <tr style={{ background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
                  {['Transaction ID', 'Created Date', 'Status', 'Receipt Type', 'Store', 'Created By', 'Customer Name', 'Total Sale', 'Total Payment', 'Balance', 'Voided', 'Activation'].map(h => (
                    <th key={h} style={{ ...thStyle, padding: '8px 12px' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loading ? (
                  <tr><td colSpan={12} style={{ padding: 30, textAlign: 'center' }}><div className="spinner" style={{ margin: '0 auto' }} /></td></tr>
                ) : sales.length === 0 ? (
                  <tr><td colSpan={12} style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>No transactions found. Click Search to load.</td></tr>
                ) : sales.map(sale => (
                  <tr key={sale.id} onClick={() => { setSelectedSale(sale); loadSaleItems(sale.id) }}
                    style={{ borderBottom: '1px solid var(--border)', cursor: 'pointer', background: selectedSale?.id === sale.id ? 'var(--surface2)' : 'transparent' }}>
                    <td style={{ padding: '7px 12px', color: 'var(--accent2)', fontWeight: 600 }}>{sale.transaction_id}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{new Date(sale.created_at).toLocaleString()}</td>
                    <td style={{ padding: '7px 12px' }}>
                      <span style={{ color: sale.status === 'completed' ? 'var(--green)' : 'var(--red)', fontWeight: 600, textTransform: 'capitalize' }}>{sale.status}</span>
                    </td>
                    <td style={{ padding: '7px 12px', textTransform: 'capitalize', color: 'var(--text2)' }}>{sale.receipt_type}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{sale.store_code || '—'}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{sale.employee_id || '—'}</td>
                    <td style={{ padding: '7px 12px' }}>{sale.customer_name || '—'}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--green)', fontWeight: 600 }}>${sale.total?.toFixed(2)}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--green)' }}>${((sale.total ?? 0) - (sale.balance ?? 0)).toFixed(2)}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>${(sale.balance ?? 0).toFixed(2)}</td>
                    <td style={{ padding: '7px 12px', color: sale.status === 'voided' || sale.voided_at ? 'var(--red)' : 'var(--text2)', fontWeight: sale.status === 'voided' || sale.voided_at ? 600 : 400 }}>{sale.status === 'voided' || sale.voided_at ? 'Yes' : 'No'}</td>
                    <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{sale.is_activation_sale ? 'Yes' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Bottom detail — Items */}
          {selectedSale && (
            <div className="table-wrapper" style={{ overflowX: 'auto' }}>
              <div style={{ padding: '8px 16px', fontSize: 12, fontWeight: 700, background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
                Items — Transaction #{selectedSale.transaction_id}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 800 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    {['Product Type', 'Product ID', 'Description', 'Serial', 'Quantity', 'Unit Price', 'Discount', 'Tax', 'Extended Price', 'Tax Value'].map(h => (
                      <th key={h} style={{ ...thStyle, padding: '7px 12px' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {saleItems.length === 0 ? (
                    <tr><td colSpan={10} style={{ padding: 16, textAlign: 'center', color: 'var(--text3)' }}>No items</td></tr>
                  ) : saleItems.map(item => (
                    <tr key={item.id} style={{ borderBottom: '1px solid var(--border)' }}>
                      <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{item.product_type}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--accent2)' }}>{item.product_id?.slice(0, 8)}</td>
                      <td style={{ padding: '7px 12px' }}>{item.description}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--text2)', fontFamily: 'monospace' }}>{item.serial_number || '—'}</td>
                      <td style={{ padding: '7px 12px' }}>{item.qty}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--green)' }}>${item.unit_price?.toFixed(2)}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>${item.discount?.toFixed(2)}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>{item.tax_rate > 0 ? `${(item.tax_rate * 100).toFixed(2)}%` : '0%'}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--green)', fontWeight: 600 }}>${item.extended_price?.toFixed(2)}</td>
                      <td style={{ padding: '7px 12px', color: 'var(--text2)' }}>${item.tax_value?.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* CUSTOMER PICKER MODAL */}
      {showCustomerPicker && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 600, maxHeight: '80vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Select Customer</b>
              <button onClick={() => setShowCustomerPicker(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 8 }}>
              <input value={customerSearch} onChange={e => setCustomerSearch(e.target.value)} onKeyDown={e => e.key === 'Enter' && searchCustomers()} placeholder="Search by name, phone, account #..." style={{ ...inputStyle, flex: 1 }} />
              <button className="btn btn-primary" onClick={searchCustomers}>Search</button>
              <button className={showNewCustomer ? 'btn btn-secondary' : 'btn btn-primary'} style={{ whiteSpace: 'nowrap', background: showNewCustomer ? undefined : 'var(--green)' }} onClick={() => setShowNewCustomer(v => !v)}>
                {showNewCustomer ? 'Cancel' : '+ New Customer'}
              </button>
            </div>
            {showNewCustomer && (
              <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
                  <input value={newCust.first_name} onChange={e => setNewCust(f => ({ ...f, first_name: e.target.value }))} placeholder="First name" style={inputStyle} autoFocus />
                  <input value={newCust.last_name} onChange={e => setNewCust(f => ({ ...f, last_name: e.target.value }))} placeholder="Last name" style={inputStyle} />
                  <input value={newCust.phone_primary} onChange={e => setNewCust(f => ({ ...f, phone_primary: e.target.value }))} placeholder="Phone" style={inputStyle} />
                  <input value={newCust.email} onChange={e => setNewCust(f => ({ ...f, email: e.target.value }))} placeholder="Email" style={inputStyle} />
                </div>
                <button onClick={createQuickCustomer} disabled={creatingCustomer} style={{ background: 'var(--green)', border: 'none', color: 'white', borderRadius: 7, padding: '8px 16px', fontSize: 12, fontWeight: 600, cursor: creatingCustomer ? 'wait' : 'pointer', width: '100%' }}>
                  {creatingCustomer ? 'Creating…' : 'Create & Select'}
                </button>
                <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 6 }}>Full details (address, ID, etc.) can be added later from the Customers page.</div>
              </div>
            )}
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {customers.map(c => (
                <div key={c.id} onClick={() => { setSelectedCustomer(c); setShowCustomerPicker(false) }}
                  style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{c.first_name} {c.last_name} {c.company_name ? `(${c.company_name})` : ''}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2 }}>{c.phone_primary} · {c.email}</div>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--accent2)' }}>#{c.cust_number}</div>
                </div>
              ))}
              {customers.length === 0 && <div style={{ padding: 30, textAlign: 'center', color: 'var(--text3)' }}>Search for a customer above</div>}
            </div>
          </div>
        </div>
      )}

      {/* PRODUCT PICKER MODAL */}
      {showProductPicker && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 700, maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Select Product</b>
              <button onClick={() => setShowProductPicker(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
              <input value={productSearch} onChange={e => setProductSearch(e.target.value)} placeholder="Search products..." style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} autoFocus />
            </div>
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {(pickerResults ?? products.filter(p => !productSearch || p.short_name.toLowerCase().includes(productSearch.toLowerCase()) || (p.upc && p.upc.includes(productSearch)))).slice(0, 100).map(p => (
                <div key={p.id} onClick={() => addToCart(p)}
                  style={{ padding: '10px 20px', borderBottom: '1px solid var(--border)', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 600 }}>{p.short_name}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 2, display: 'flex', gap: 12 }}>
                      <span>UPC: {p.upc || '—'}</span>
                      <span style={{ color: p.inventory_type === 'serial' ? 'var(--accent2)' : 'var(--green)' }}>{p.inventory_type === 'serial' ? '📱 Serial' : '📦 Standard'}</span>
                      <span>{p.is_taxable ? 'Taxable' : 'No Tax'}</span>
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--green)' }}>${Number(p.retail_price || 0).toFixed(2)}</div>
                    <div style={{ fontSize: 11, color: 'var(--text3)' }}>Cost: ${Number(p.cost || 0).toFixed(2)}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* PAYMENT CONFIRMATION MODAL */}
      {showPayment && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 420, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)' }}>
              <b style={{ fontSize: 14 }}>Confirm Payment</b>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: 16, marginBottom: 16 }}>
                {[
                  { label: 'Subtotal', value: `$${subtotal.toFixed(2)}`, bold: false, big: false },
                  { label: 'Tax', value: `$${taxTotal.toFixed(2)}`, bold: false, big: false },
                  { label: 'TOTAL DUE', value: `$${total.toFixed(2)}`, bold: true, big: true },
                ].map(row => (
                  <div key={row.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '6px 0', borderBottom: row.label !== 'TOTAL DUE' ? '1px solid var(--border)' : 'none' }}>
                    <span style={{ fontSize: row.big ? 15 : 13, fontWeight: row.bold ? 700 : 400, color: row.bold ? 'var(--text)' : 'var(--text2)' }}>{row.label}</span>
                    <span style={{ fontSize: row.big ? 20 : 13, fontWeight: 700, color: row.bold ? 'var(--green)' : 'var(--text)' }}>{row.value}</span>
                  </div>
                ))}
              </div>
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Payment: <strong style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{paymentMethod.replace(/_/g, ' ')}</strong></div>
                {paymentMethod === 'cash' && parseFloat(amountTendered) > 0 && (
                  <div style={{ fontSize: 13, color: 'var(--green)' }}>Change: ${Math.max(0, change).toFixed(2)}</div>
                )}
              </div>
              <div style={{ marginBottom: 14 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: isActivationSale ? 'var(--accent2)' : 'var(--text)', fontWeight: isActivationSale ? 700 : 400, marginBottom: 8, cursor: 'pointer', background: isActivationSale ? 'rgba(46,117,182,0.08)' : 'transparent', border: `1px solid ${isActivationSale ? 'var(--accent2)' : 'var(--border)'}`, borderRadius: 7, padding: '8px 10px' }}>
                  <input type="checkbox" checked={isActivationSale} onChange={e => setIsActivationSale(e.target.checked)} />
                  📱 Activation Sale — continue to activation after payment
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginBottom: 8, cursor: 'pointer' }}>
                  <input type="checkbox" defaultChecked /> Print receipt?
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                  <input type="checkbox" /> Email receipt?
                </label>
                {selectedCustomer?.email && <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 4, marginLeft: 20 }}>{selectedCustomer.email}</div>}
              </div>
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={() => setShowPayment(false)}>Cancel</button>
              <button onClick={completeSale} disabled={saving} style={{ background: 'var(--green)', border: 'none', color: 'white', borderRadius: 7, padding: '10px 24px', fontSize: 14, fontWeight: 700, cursor: saving ? 'wait' : 'pointer', opacity: saving ? 0.7 : 1 }}>
                {saving ? 'Processing...' : '✅ Complete Sale'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* SALE COMPLETE (RECEIPT) MODAL */}
      {showReceipt && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--green)', borderRadius: 12, width: 380, overflow: 'hidden', textAlign: 'center' }}>
            <div style={{ padding: '30px 20px', background: 'rgba(22,163,74,0.08)' }}>
              <div style={{ fontSize: 48, marginBottom: 8 }}>✅</div>
              <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--green)' }}>Sale Complete!</div>
              <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 8 }}>Transaction #{lastTransactionId}</div>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ background: 'var(--surface2)', borderRadius: 8, padding: 14, marginBottom: 16, textAlign: 'left' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ color: 'var(--text2)', fontSize: 13 }}>Total charged</span>
                  <span style={{ color: 'var(--green)', fontWeight: 700, fontSize: 16 }}>${(lastReceipt?.total ?? 0).toFixed(2)}</span>
                </div>
                {lastReceipt?.method === 'cash' && (
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span style={{ color: 'var(--text2)', fontSize: 13 }}>Change given</span>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>${(lastReceipt?.change ?? 0).toFixed(2)}</span>
                  </div>
                )}
              </div>
              {emailPromptCustomer && (
                <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginBottom: 12, textAlign: 'left' }}>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Customer email for receipts:</div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <input type="email" value={emailPromptValue} onChange={e => setEmailPromptValue(e.target.value)}
                      onKeyDown={e => e.key === 'Enter' && saveCustomerEmail()} placeholder="name@example.com" style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
                    <button className="btn btn-primary" style={{ fontSize: 12, padding: '7px 12px', cursor: savingEmail ? 'wait' : 'pointer', opacity: savingEmail ? 0.7 : 1 }} onClick={saveCustomerEmail} disabled={savingEmail}>
                      {savingEmail ? 'Saving…' : 'Save'}
                    </button>
                    <button className="btn btn-secondary" style={{ fontSize: 12, padding: '7px 12px' }} onClick={() => setEmailPromptCustomer(null)}>
                      Skip
                    </button>
                  </div>
                </div>
              )}
              {lastWasActivation && lastSaleId && (
                <a href={`/pos/activations?sale=${lastSaleId}&customer=${lastSaleCustomerId || ''}`}
                  title={mustPrint ? mustPrintTitle : undefined}
                  style={{ display: 'block', width: '100%', boxSizing: 'border-box', background: 'linear-gradient(135deg, var(--accent2), var(--accent))', border: 'none', color: 'white', borderRadius: 8, padding: 14, fontSize: 15, fontWeight: 700, cursor: mustPrint ? 'not-allowed' : 'pointer', marginBottom: 8, textDecoration: 'none', pointerEvents: mustPrint ? 'none' : 'auto', opacity: mustPrint ? 0.5 : 1 }}>
                  📱 Continue to Activation →
                </a>
              )}
              <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginBottom: 8 }}
                onClick={() => { if (lastReceipt) { printHtml(buildReceiptHtml(lastReceipt)); setReceiptPrinted(true) } }}>
                🖨️ Print Receipt
              </button>
              <button onClick={() => { setShowReceipt(false); setActiveView('home') }} disabled={mustPrint} title={mustPrint ? mustPrintTitle : undefined}
                style={{ width: '100%', background: 'var(--green)', border: 'none', color: 'white', borderRadius: 8, padding: 12, fontSize: 14, fontWeight: 700, cursor: mustPrint ? 'not-allowed' : 'pointer', opacity: mustPrint ? 0.5 : 1 }}>
                New Sale
              </button>
              <button className="btn btn-secondary" style={{ width: '100%', justifyContent: 'center', marginTop: 8, cursor: mustPrint ? 'not-allowed' : 'pointer', opacity: mustPrint ? 0.5 : 1 }}
                onClick={() => { setShowReceipt(false); setActiveView('salelog'); loadSales() }} disabled={mustPrint} title={mustPrint ? mustPrintTitle : undefined}>
                View Sale Log
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CHECKOUT BLOCKED MODAL — sale attribution requirements not met */}
      {blockIssues && (
        <div style={{ ...modalOverlay, zIndex: 300 }}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--red)', borderRadius: 12, width: 460, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'rgba(220,38,38,0.06)' }}>
              <b style={{ fontSize: 14, color: 'var(--red)' }}>⛔ Cannot Complete Sale</b>
            </div>
            <div style={{ padding: 20 }}>
              <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
                Every sale must record the store it happened at and the employee who rang it up. Fix the following first:
              </div>
              {blockIssues.map((issue, i) => (
                <div key={i} style={{ background: 'var(--surface2)', borderLeft: '3px solid var(--amber)', borderRadius: 6, padding: '10px 12px', fontSize: 12, marginBottom: 8, lineHeight: 1.5 }}>
                  {issue}
                </div>
              ))}
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', textAlign: 'right' }}>
              <button className="btn btn-secondary" onClick={() => setBlockIssues(null)}>OK</button>
            </div>
          </div>
        </div>
      )}

      {/* STATUS STRIP */}
      <div style={{ marginTop: 14, padding: '6px 4px', display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text3)', flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
          <span>🏪 Store: <strong style={{ color: activeStore ? 'var(--text)' : 'var(--amber)' }}>{storeName || 'No store selected'}</strong></span>
          <span>👤 User: <strong style={{ color: 'var(--text)' }}>{user?.email || '—'}</strong></span>
        </div>
        <span>Register {getRegisterNumber()} | Cart: {cart.length} items | Total: ${total.toFixed(2)}</span>
      </div>
    </div>
  )
}
