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
import { apiCached } from '@/lib/cache'
import { useAuth } from '@/lib/auth-context'
import RegisterDrawer, { RegisterSession } from '@/components/pos/RegisterDrawer'
import RegisterLock from '@/components/pos/RegisterLock'
import { getActiveStore, setActiveStore } from '@/lib/pos-store'
import { PosConfigValues, loadEffectivePosConfig, resolvePosConfig, getRegisterNumber } from '@/lib/pos-config'
import { LinkedText } from '@/components/ScreenLink'
import ProductFilters, {
  EMPTY_FILTER_OPTIONS, EMPTY_PRODUCT_FILTERS, ProductFilterOptions, ProductFilterValue,
  fetchProductFilterOptions, hasProductFilters, productFilterParams,
} from '../product-filters'
// THE product form (../product-form) — the same fields, defaults and payload the catalog's
// New/Edit modal renders, so "create it from the register" cannot define a product differently.
import ProductFormFields, {
  ProductFormValue, productFormFrom, productFormFromEntry, productFormPayload, productFormReady,
} from '../product-form'
// THE suspended cart (lib/pos-held-sale) — restore on arrival, debounced autosave, clear on
// checkout or discard, all keyed on this (store, register) terminal.
import {
  AUTOSAVE_MS, clearHeldSale, fetchHeldSale, heldSaleBanner, suspendSale,
} from '@/lib/pos-held-sale'
// THE receipt (lib/pos-receipt) — rendered on the server so the till copy, the reprint and the
// emailed copy are one thing; this screen prints what it is handed (§POS-02).
import { emailSaleReceipt, fetchSaleReceipt, printReceiptHtml } from '@/lib/pos-receipt'
import type { PrintChoice, SaleReceipt } from '@/lib/pos-receipt'
// THE format picker — one home, shared with the Sale Log reprint (§POS-03).
import { PrintFormatPicker } from '@/components/pos/PrintFormatPicker'

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
  // What the product was LISTED at when it went into the cart. The register may edit unit_price
  // (owner directive 2026-08-11), so a line that stored only the charged price would make a
  // $199 case sold for $99 indistinguishable from a $99 product. An override is exactly
  // list_price !== unit_price, and pos.sale_items.list_price (mig 745) keeps it after checkout.
  list_price: number
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
  // Product picker filters (owner 2026-09-24) — the ONE shared filter set (../product-filters), applied
  // server-side through the same GET /pos/products call as the search, so they reach past the 500 cap.
  const [pickerFilters, setPickerFilters] = useState<ProductFilterValue>(EMPTY_PRODUCT_FILTERS)
  const [filterOptions, setFilterOptions] = useState<ProductFilterOptions | null>(null)
  const pickerTicket = useRef(0)   // a slower, older response must never replace a newer one (index §23k)
  const [pickerPending, setPickerPending] = useState(false)
  const [pickerError, setPickerError] = useState('')
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
  // Create / edit a product from the register (owner 2026-10-16). `mode` 'create' is the
  // not-found prompt; 'edit' fixes the description fields of a product already in the catalog.
  // Both render the SHARED form (../product-form) — the register defines no fields of its own.
  const [productForm, setProductForm] = useState<ProductFormValue | null>(null)
  const [productFormMode, setProductFormMode] = useState<'create' | 'edit'>('create')
  const [productFormId, setProductFormId] = useState<string | null>(null)
  // Opening stock for a product created at the till: a qty for a standard product, the first unit's
  // serial / IMEI for a serial-tracked one. Blank means "created, none on hand yet" — which is
  // legitimate and must not be guessed into a 1.
  const [openingQty, setOpeningQty] = useState('')
  const [openingSerial, setOpeningSerial] = useState('')
  const [openingImei, setOpeningImei] = useState('')
  const [productFormBusy, setProductFormBusy] = useState(false)
  const [productFormError, setProductFormError] = useState('')
  const [productFormNote, setProductFormNote] = useState('')
  // The suspended cart. `heldRestored` gates the autosave: saving before the restore has run would
  // overwrite the cart the last session left with this session's empty one.
  const [heldRestored, setHeldRestored] = useState(false)
  const [heldBanner, setHeldBanner] = useState('')
  // One transient line above the cart: what the last create / restore / discard actually did.
  const [registerNote, setRegisterNote] = useState('')
  const [showPayment, setShowPayment] = useState(false)
  const [showReceipt, setShowReceipt] = useState(false)
  const [paymentMethod, setPaymentMethod] = useState('cash')
  const [amountTendered, setAmountTendered] = useState('')
  const [lastTransactionId, setLastTransactionId] = useState<number | null>(null)
  const [lastReceipt, setLastReceipt] = useState<ReceiptSnapshot | null>(null)
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
  // The emailed receipt (§POS-02): what the server says it would send, where, and from whom — all of
  // it SHOWN before the cashier presses Send, and the address editable right there.
  const [emailTo, setEmailTo] = useState('')
  const [emailFromName, setEmailFromName] = useState('')
  const [emailSending, setEmailSending] = useState(false)
  const [emailSentTo, setEmailSentTo] = useState('')
  // THE PRINT FORMAT (§POS-03, owner 2026-10-19). What the server says this sale can print as and
  // what it resolved to, plus what the cashier asked for FOR THIS PRINT. Nothing is saved: the
  // store's own declared setting is where the next receipt starts from again.
  const [receiptView, setReceiptView] = useState<SaleReceipt | null>(null)
  const [printChoice, setPrintChoice] = useState<PrintChoice>({})
  // The same two, for a Sale Log reprint of a sale rung up on another day or at another counter.
  const [reprintSaleId, setReprintSaleId] = useState<string | null>(null)
  const [reprintView, setReprintView] = useState<SaleReceipt | null>(null)
  const [reprintChoice, setReprintChoice] = useState<PrintChoice>({})

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
    apiCached('/api/v1/storeops/stores').then((r: any) => setStores(Array.isArray(r) ? r : [])).catch(() => {})
    api('/api/v1/pos/tax-codes').then(r => setTaxCodes(r.tax_codes || [])).catch(() => setTaxCodes([]))
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

  // ── THE SUSPENDED SALE (owner 2026-10-16, index §POS-01) ──────────────────────────────────────
  // RESTORE. The cart belongs to the TERMINAL — (store, register) — so the next session on this
  // register is handed back exactly what the last one left, whether that was a reload, a tab
  // closed mid-sale or the end of a shift. Restored VERBATIM: the prices are the ones the cashier
  // agreed with the customer, and the banner says how old they are rather than implying they are
  // current. A cart already being rung on this screen is never overwritten by an older hold.
  useEffect(() => {
    if (!activeStore) return
    let cancelled = false
    fetchHeldSale(activeStore, getRegisterNumber()).then(({ held }) => {
      if (cancelled) return
      if (held && held.cart.length && cart.length === 0) {
        setCart(held.cart.map(l => recalcItem({
          product_id: l.product_id,
          description: l.description || '',
          product_type: l.product_type || 'Regular',
          serial_number: l.serial_number || '',
          qty: l.qty || 1,
          unit_price: l.unit_price || 0,
          list_price: l.list_price ?? l.unit_price ?? 0,
          cost: l.cost || 0,
          discount: l.discount || 0,
          tax_rate: l.tax_rate || 0,
          tax_value: l.tax_value || 0,
          extended_price: l.extended_price || 0,
          is_taxable: l.is_taxable,
        })))
        setIsActivationSale(held.is_activation_sale === true)
        setHeldBanner(heldSaleBanner(held))
        setActiveView('sale')
      }
      setHeldRestored(true)
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStore])

  // AUTOSAVE. Debounced, so typing a price is one save and not one per keystroke. Gated on
  // `heldRestored` — saving before the restore has run would overwrite the last session's cart with
  // this session's empty one, which is the exact loss this feature exists to stop. An empty cart
  // with no customer clears the hold (the server decides that), so an idle register leaves nothing
  // behind. Suspending writes no sale, burns no transaction number and moves no stock, which is why
  // it can happen without asking.
  useEffect(() => {
    if (!activeStore || !heldRestored) return
    const t = setTimeout(() => {
      suspendSale(activeStore, getRegisterNumber(), {
        cart: cart.map(i => ({
          product_id: i.product_id, description: i.description, product_type: i.product_type,
          serial_number: i.serial_number || '', qty: i.qty, unit_price: i.unit_price,
          list_price: i.list_price, cost: i.cost, discount: i.discount, tax_rate: i.tax_rate,
          tax_value: i.tax_value, extended_price: i.extended_price, is_taxable: i.is_taxable,
        })),
        customer_id: selectedCustomer?.id || null,
        is_activation_sale: isActivationSale,
      })
    }, AUTOSAVE_MS)
    return () => clearTimeout(t)
  }, [cart, selectedCustomer, isActivationSale, activeStore, heldRestored])

  /** Empty the cart — and the suspended copy with it, or coming back would restore what the cashier
   *  just threw away. The selected customer stays, which is what Clear Cart has always done. */
  function clearCart() {
    setCart([]); setHeldBanner(''); setRegisterNote('')
    if (activeStore) clearHeldSale(activeStore, getRegisterNumber())
  }

  /** Throw away a RESUMED sale wholesale — the cashier does not want the cart the last session left,
   *  so the customer it was for goes with it. */
  function discardHeldSale() {
    setSelectedCustomer(null); setIsActivationSale(false)
    clearCart()
  }

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

  // THE print path (§POS-02): the receipt is rendered on the server — one home for the till copy,
  // the reprint and the emailed copy — and the register prints what it is handed. It used to build
  // the HTML here, which is why an admin's template edits reached this screen and nothing else.
  async function printSaleReceipt(saleId: string | null, choice?: PrintChoice): Promise<boolean> {
    if (!saleId) { alert('That sale has not been saved yet.'); return false }
    try {
      // The choice rides the SAME request that renders the receipt, so what prints is what was
      // picked — the screen never re-lays anything out itself.
      const rec = await fetchSaleReceipt(saleId, choice)
      printReceiptHtml(rec.html)
      return true
    } catch (err: any) {
      alert(`Could not print the receipt: ${err?.message || err}`)
      return false
    }
  }

  // The completion modal asks the server who the receipt would go to, so the screen shows the real
  // address (the customer's own) rather than an empty box the cashier has to guess at. Called where
  // the modal is OPENED rather than from an effect watching it: opening the modal is the event, and
  // an effect would re-ask on every unrelated re-render while the modal is up.
  async function loadReceiptEmailView(saleId: string, choice?: PrintChoice) {
    setEmailSentTo('')
    setEmailTo('')
    setEmailFromName('')
    try {
      const rec = await fetchSaleReceipt(saleId, choice)
      setEmailTo(rec.email.to)
      setEmailFromName(rec.email.from_name)
      // The same answer carries the formats this store may print in and which one it resolved to,
      // so the modal shows the real choice rather than a guess at it.
      setReceiptView(rec)
    } catch {
      // The receipt box simply stays empty and the format picker stays hidden — the cashier can
      // still type an address and still print (which asks the server for the receipt itself and
      // reports its own failure), on whatever the store declared.
    }
  }

  async function sendReceiptEmail() {
    if (!lastSaleId) return
    setEmailSending(true)
    try {
      // The printed choice goes with it, so the customer's copy is the one that was handed over.
      const res = await emailSaleReceipt(lastSaleId, emailTo, printChoice)
      setEmailSentTo(res.to)
      // An address the customer gave at the till is worth keeping on their profile, which is what
      // the capture prompt (receipt_prompt_email) was asking for anyway.
      if (emailPromptCustomer && emailTo.trim()) {
        try {
          await api(`/api/v1/pos/customers/${emailPromptCustomer}`, { method: 'PATCH', body: JSON.stringify({ email: emailTo.trim() }) })
          setEmailPromptCustomer(null)
        } catch { /* the receipt went out; saving it to the profile is the lesser half */ }
      }
    } catch (err: any) {
      alert(`Could not email the receipt: ${err?.message || err}`)
    }
    setEmailSending(false)
  }

  // receipt_auto_print: when the completion modal opens, fire the same print path as the
  // Print Receipt button — once per sale (the ref guards against re-renders re-firing).
  useEffect(() => {
    if (!showReceipt || !lastReceipt || !lastSaleId || cfg.receipt_auto_print !== true) return
    if (autoPrintedTxn.current === lastReceipt.transactionId) return
    autoPrintedTxn.current = lastReceipt.transactionId
    // No request: an auto-print prints on whatever this store declared, which is the whole
    // point of the store declaration. A cashier's one-off pick belongs to the button.
    printSaleReceipt(lastSaleId)
    setReceiptPrinted(true)
  }, [showReceipt, lastReceipt, lastSaleId, cfg.receipt_auto_print])

  async function printSelectedSale() {
    if (!selectedSale) { alert('Select a transaction in the list first.'); return }
    // A reprint gets the SAME choice of format the till does (owner 2026-10-19): the counter asking
    // for it is often not the one that rang the sale up, and the paper in the printer differs.
    setReprintSaleId(selectedSale.id)
    setReprintChoice({})
    setReprintView(null)
    try {
      setReprintView(await fetchSaleReceipt(selectedSale.id))
    } catch (err) {
      setReprintSaleId(null)
      alert(`Could not read that receipt: ${err instanceof Error ? err.message : String(err)}`)
    }
  }

  async function loadProducts() {
    try {
      const r = await api('/api/v1/pos/products')
      setProducts(r.products || [])
    } catch { setProducts([]) }
  }

  // The filter option lists load the first time the picker opens (not on every register visit).
  useEffect(() => {
    if (!showProductPicker || filterOptions) return
    fetchProductFilterOptions().then(setFilterOptions).catch(() => setFilterOptions(EMPTY_FILTER_OPTIONS))
  }, [showProductPicker, filterOptions])

  // Server-side product search + filters for the picker (the default catalog list caps at 500 rows).
  // "In stock" means on hand at this register's active store.
  useEffect(() => {
    if (!showProductPicker) return
    const q = productSearch.trim()
    const ticket = ++pickerTicket.current
    if (!q && !hasProductFilters(pickerFilters)) { setPickerResults(null); setPickerPending(false); setPickerError(''); return }
    setPickerPending(true); setPickerError('')
    const filtered = hasProductFilters(pickerFilters)
    const t = setTimeout(() => {
      const params = productFilterParams(pickerFilters, new URLSearchParams(), activeStore)
      if (q) params.set('search', q)
      api(`/api/v1/pos/products?${params}`)
        .then(r => { if (ticket === pickerTicket.current) setPickerResults(r.products || []) })
        .catch((err: unknown) => {
          if (ticket !== pickerTicket.current) return
          // A failed FILTERED load shows nothing (and says why) — never the unfiltered list.
          setPickerResults(filtered ? [] : null)
          if (filtered) setPickerError(`Could not load filtered products: ${err instanceof Error ? err.message : String(err)}`)
        })
        .finally(() => { if (ticket === pickerTicket.current) setPickerPending(false) })
    }, 250)
    return () => clearTimeout(t)
  }, [productSearch, showProductPicker, pickerFilters, activeStore])

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
      list_price: product.retail_price,
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

  // Price override. recalcItem() already derives tax and the line total FROM unit_price, so the
  // rest of the sale follows without a second code path. A blank/NaN entry is read as 0 rather
  // than NaN — one NaN in a cart line propagates silently into the sale total.
  function updateUnitPrice(idx: number, unit_price: number) {
    const v = Number.isFinite(unit_price) ? Math.max(0, unit_price) : 0
    setCart(prev => prev.map((it, i) => i === idx ? recalcItem({ ...it, unit_price: v }) : it))
  }

  function updateSerial(idx: number, serial: string) {
    setCart(prev => prev.map((it, i) => i === idx ? { ...it, serial_number: serial } : it))
  }

  // The line's DESCRIPTION — what prints on the receipt and lands in pos.sale_items.description
  // (owner 2026-10-16: "let the user edit the product description fields"). It is the line's own
  // wording, so editing it here changes this sale only; the catalogue product is untouched. To
  // change the product itself, `openProductEdit` opens the shared form.
  function updateDescription(idx: number, description: string) {
    setCart(prev => prev.map((it, i) => i === idx ? { ...it, description } : it))
  }

  function updateProductType(idx: number, product_type: string) {
    setCart(prev => prev.map((it, i) => i === idx ? { ...it, product_type } : it))
  }

  // ── Create / edit the CATALOGUE product from the register ─────────────────────────────────────
  function openProductCreate(entry: string) {
    setProductFormMode('create')
    setProductFormId(null)
    setProductForm(productFormFromEntry(entry))      // a pure barcode fills UPC, anything else the name
    setOpeningQty(''); setOpeningSerial(''); setOpeningImei('')
    setProductFormError(''); setProductFormNote('')
    setShowProductPicker(false)
  }

  function openProductEdit(product: Product) {
    setProductFormMode('edit')
    setProductFormId(product.id)
    setProductForm(productFormFrom(product))
    setOpeningQty(''); setOpeningSerial(''); setOpeningImei('')
    setProductFormError(''); setProductFormNote('')
  }

  function closeProductForm() {
    setProductForm(null); setProductFormId(null); setProductFormError(''); setProductFormNote('')
  }

  /** Save the form. Create: POST the product, put the opening stock on hand, then ring it up —
   *  three separate facts, in that order, each reported. Edit: PATCH the product and refresh the
   *  cart lines that are still showing its old name. */
  async function saveProductForm() {
    if (!productForm || !productFormReady(productForm)) return
    setProductFormBusy(true); setProductFormError(''); setProductFormNote('')
    try {
      const payload = productFormPayload(productForm)
      if (productFormMode === 'edit' && productFormId) {
        const r = await api(`/api/v1/pos/products/${productFormId}`, { method: 'PATCH', body: JSON.stringify(payload) })
        const saved = (r?.product || { ...payload, id: productFormId }) as Product
        // The cart holds a SNAPSHOT of the line (a price override must survive an edit), so only
        // the catalogue-derived description follows the rename — never the agreed price.
        setCart(prev => prev.map(it => it.product_id === productFormId
          ? { ...it, description: saved.short_name || it.description } : it))
        setProducts(prev => prev.map(pp => pp.id === productFormId ? { ...pp, ...saved } : pp))
        setPickerResults(prev => prev ? prev.map(pp => pp.id === productFormId ? { ...pp, ...saved } : pp) : prev)
        closeProductForm()
        return
      }
      const created = (await api('/api/v1/pos/products', { method: 'POST', body: JSON.stringify(payload) }))?.product as Product
      if (!created?.id) throw new Error('the product was not returned by the server')
      let note = ''
      // Opening stock is a SEPARATE fact from the product existing. If it fails, the product still
      // exists and is still rung up — with the reason said out loud rather than a silent zero.
      try {
        if (productForm.inventory_type === 'serial') {
          const serial = openingSerial.trim()
          if (serial) {
            await api('/api/v1/pos/inventory/serial', {
              method: 'POST',
              body: JSON.stringify({ product_id: created.id, store_code: activeStore || null,
                                     serial_number: serial, imei: openingImei.trim() || null,
                                     cost: productForm.cost || null, status: 'in_stock' }),
            })
            note = `Created, and unit ${serial} received into inventory.`
          } else {
            note = 'Created. No unit received — add its IMEI in Inventory when it arrives.'
          }
        } else {
          const qty = parseInt(openingQty, 10)
          if (Number.isFinite(qty) && qty > 0) {
            const r = await api('/api/v1/pos/inventory/standard', {
              method: 'POST',
              body: JSON.stringify({ product_id: created.id, store_code: activeStore || '', qty, mode: 'add' }),
            })
            note = r?.message || `Created, and ${qty} received into inventory.`
          } else {
            note = 'Created. No opening stock recorded.'
          }
        }
      } catch (err: unknown) {
        note = `Created, but the opening stock did not land: ${err instanceof Error ? err.message : String(err)}`
      }
      setProducts(prev => [created, ...prev])
      addToCart(created)                     // this is why the cashier opened the form
      setProductFormNote(note)
      setProductForm(null); setProductFormId(null)
      setRegisterNote(note)
      fetchProductFilterOptions().then(setFilterOptions).catch(() => {})   // a new maker/category becomes filterable
    } catch (err: unknown) {
      setProductFormError(err instanceof Error ? err.message : String(err))
    } finally {
      setProductFormBusy(false)
    }
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
      // The FULL name is recorded; whether a receipt PRINTS it short
      // (receipt_customer_short_name) is a rendering rule and lives with the renderer (§POS-02), so
      // changing that setting changes old reprints too instead of only new sales.
      customerName: selectedCustomer
        ? ([selectedCustomer.first_name, selectedCustomer.last_name].filter(Boolean).join(' ').trim()
           || selectedCustomer.company_name || null)
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
            list_price: item.list_price,
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
    // A fresh sale starts from the store's own declared format again, never the last cashier's pick.
    setPrintChoice({})
    setReceiptView(null)
    setEmailPromptCustomer(
      cfg.receipt_prompt_email === true && selectedCustomer && !(selectedCustomer.email || '').trim()
        ? selectedCustomer.id : null
    )
    setLastReceipt({ ...receiptSnapshot, transactionId: sale.transaction_id })
    // The sale is rung: the suspended copy is finished, so drop it before the cart empties. Leaving
    // it would hand the next session a cart for a sale the customer has already paid for.
    if (activeStore) clearHeldSale(activeStore, getRegisterNumber())
    setHeldBanner('')
    setRegisterNote('')
    setCart([])
    setSelectedCustomer(null)
    setShowPayment(false)
    setShowReceipt(true)
    loadReceiptEmailView(sale.id)
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
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
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
              {/* Customer Special Order — sell an item the store doesn't stock (ships to the store).
                  Neutral by design: the vendor behind the catalog is never named store/customer-side. */}
              <a href="/pos/special-orders" style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', background: 'var(--green)', color: 'white', borderRadius: 7, padding: '12px 20px', fontSize: 13, fontWeight: 700, textDecoration: 'none', minWidth: 180 }}>
                🧾 Customer Special Order
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

            {/* A RESUMED SALE says so. The cashier at the register may not be the one who left the
                cart, and the prices on it are as old as the hold — so the banner names both rather
                than presenting someone else's part-rung sale as this one's work. */}
            {heldBanner && (
              <div style={{ background: 'rgba(217,119,6,0.08)', borderBottom: '1px solid var(--amber, #d97706)', padding: '8px 16px', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ fontSize: 12, color: 'var(--text)', flex: 1, minWidth: 220 }}>⏸ {heldBanner}</span>
                <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px' }} onClick={() => setHeldBanner('')}>Keep it</button>
                <button className="btn btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--red)' }} onClick={discardHeldSale}>Discard it</button>
              </div>
            )}
            {registerNote && (
              <div style={{ borderBottom: '1px solid var(--border)', padding: '8px 16px', display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--text2)', flex: 1 }}>{registerNote}</span>
                <button onClick={() => setRegisterNote('')} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 14, cursor: 'pointer' }}>×</button>
              </div>
            )}

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
                      {/* Product type and description are EDITABLE on the line (owner 2026-10-16).
                          They are the line's own wording — what prints on the receipt and lands in
                          pos.sale_items — so a correction here changes this sale only. The ✎ opens
                          the shared product form to change the CATALOGUE product itself. */}
                      <td style={{ padding: '8px 10px' }}>
                        <input list="pos-product-types" value={item.product_type}
                          onChange={e => updateProductType(idx, e.target.value)}
                          title="Product type on this line — what the receipt and the sale record show"
                          style={{ ...inputStyle, width: 110, padding: '4px 6px', fontSize: 11, color: 'var(--text2)' }} />
                      </td>
                      <td style={{ padding: '8px 10px', maxWidth: 220 }}>
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                          <input value={item.description} onChange={e => updateDescription(idx, e.target.value)}
                            title="Description on this line — edit to change what prints on the receipt"
                            style={{ ...inputStyle, flex: 1, minWidth: 0, padding: '4px 6px', fontSize: 12, fontWeight: 500 }} />
                          <button type="button" title="Edit this product in the catalog (name, price, category…)"
                            onClick={() => {
                              const p = products.find(pp => pp.id === item.product_id)
                                || (pickerResults || []).find(pp => pp.id === item.product_id)
                              if (p) openProductEdit(p)
                              else setRegisterNote('That product is not in the list this screen loaded — open it from Products & Services to edit it.')
                            }}
                            style={{ background: 'none', border: 'none', color: 'var(--accent2)', cursor: 'pointer', fontSize: 12, padding: '0 2px' }}>✎</button>
                        </div>
                      </td>
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
                      <td style={{ padding: '8px 10px' }}>
                        <input type="number" step="0.01" min="0" value={item.unit_price}
                          onChange={e => updateUnitPrice(idx, parseFloat(e.target.value))}
                          title={item.list_price !== item.unit_price
                            ? `List price $${item.list_price.toFixed(2)} — this line is overridden`
                            : 'Price — edit to override for this sale'}
                          style={{ ...inputStyle, width: 74, padding: '4px 6px', fontSize: 11,
                            color: 'var(--green)', fontWeight: 600,
                            borderColor: item.list_price !== item.unit_price ? 'var(--amber, #d97706)' : undefined }} />
                        {item.list_price !== item.unit_price && (
                          <div style={{ fontSize: 10, color: 'var(--text3)', marginTop: 2 }}>
                            was ${item.list_price.toFixed(2)}
                          </div>
                        )}
                      </td>
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
              {/* Clearing the cart drops the SUSPENDED copy too — otherwise coming back would
                  restore what the cashier just threw away. */}
              <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={clearCart}>🗑️ Clear Cart</button>
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
            <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={printSelectedSale}>🖨️ Print Receipt</button>
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
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 900, maxWidth: '100%', maxHeight: '85vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>Select Product</b>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {/* The cashier may always create one, not only when a search came back empty — a
                    product being entered manually for the first time is the ordinary case at a till. */}
                <button className="btn btn-secondary" style={{ fontSize: 12 }} onClick={() => openProductCreate(productSearch)}>+ New product</button>
                <button onClick={() => setShowProductPicker(false)} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
              </div>
            </div>
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)' }}>
              <input value={productSearch} onChange={e => setProductSearch(e.target.value)} placeholder="Search products..." style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} autoFocus />
              <ProductFilters value={pickerFilters} onChange={setPickerFilters} options={filterOptions || EMPTY_FILTER_OPTIONS}
                inStockLabel={activeStore ? `In stock at ${activeStore}` : 'In stock only'} style={{ marginTop: 10 }} />
              {pickerPending && hasProductFilters(pickerFilters) && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>Filtering…</div>
              )}
              {pickerError && <div style={{ fontSize: 11, color: 'var(--red, #dc2626)', marginTop: 8 }}>{pickerError}</div>}
              {!pickerPending && !pickerError && pickerResults && (
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 8 }}>
                  {pickerResults.length === 0 ? 'No products match.' : `${pickerResults.length}${pickerResults.length >= 500 ? '+' : ''} match${pickerResults.length === 1 ? '' : 'es'}${pickerResults.length > 100 ? ' · showing the first 100 — narrow the search or filters' : ''}`}
                </div>
              )}
            </div>
            {/* NOT FOUND → PROMPT TO CREATE (owner 2026-10-16: "prompt the user to create the new
                product if the product is not found"). This is the one place a nothing-matched search
                ends, whether the cashier typed in the box or scanned at the Quick Sale field — a
                scan that matches nothing opens this picker with the scanned text already in it. */}
            {!pickerPending && !pickerError && pickerResults && pickerResults.length === 0 && (
              <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface2)', display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ flex: 1, minWidth: 220, fontSize: 13 }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>
                    {productSearch.trim() ? `“${productSearch.trim()}” is not in the catalog.` : 'Nothing matches these filters.'}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text2)' }}>
                    Create it now and it is added to Products &amp; Services, put into this store&apos;s inventory and rung up on this sale.
                  </div>
                </div>
                <button className="btn btn-primary" style={{ fontSize: 13 }} onClick={() => openProductCreate(productSearch)}>
                  + Create {productSearch.trim() ? `“${productSearch.trim()}”` : 'a product'}
                </button>
              </div>
            )}
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {/* While a FILTERED request is in flight, show nothing rather than the unfiltered list —
                  a click on a row the filters exclude would ring up the wrong product. */}
              {(pickerPending && hasProductFilters(pickerFilters) ? [] : pickerResults ?? products.filter(p => !productSearch || p.short_name.toLowerCase().includes(productSearch.toLowerCase()) || (p.upc && p.upc.includes(productSearch)))).slice(0, 100).map(p => (
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
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--green)' }}>${Number(p.retail_price || 0).toFixed(2)}</div>
                      <div style={{ fontSize: 11, color: 'var(--text3)' }}>Cost: ${Number(p.cost || 0).toFixed(2)}</div>
                    </div>
                    {/* Edit the product's own description fields without leaving the register. */}
                    <button type="button" title="Edit this product (name, description, price, category…)"
                      onClick={e => { e.stopPropagation(); openProductEdit(p) }}
                      style={{ background: 'none', border: '1px solid var(--border)', borderRadius: 6, color: 'var(--accent2)', cursor: 'pointer', fontSize: 12, padding: '4px 8px' }}>✎ Edit</button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* PRODUCT TYPE suggestions for the editable cart line — the org's own system categories,
          already loaded for the picker filters. A datalist SUGGESTS without constraining: the line's
          type is free text in pos.sale_items, and forcing a dropdown would make a one-off wording
          impossible to enter at the till. */}
      <datalist id="pos-product-types">
        {(filterOptions?.systemCategories || []).filter(c => c.is_active).map(c => <option key={c.id} value={c.name} />)}
      </datalist>

      {/* CREATE / EDIT A PRODUCT FROM THE REGISTER (owner 2026-10-16) — the SHARED form
          (../product-form), so the fields are the catalog page's fields. Creating also puts the
          opening stock on hand and rings the product up, which is why the cashier opened it. */}
      {productForm && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 760, maxWidth: '100%', maxHeight: '92vh', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <b style={{ fontSize: 14 }}>
                {productFormMode === 'edit' ? `Edit product — ${productForm.short_name || 'this product'}` : 'New product — not in the catalog yet'}
              </b>
              <button onClick={closeProductForm} style={{ background: 'none', border: 'none', color: 'var(--text2)', fontSize: 20, cursor: 'pointer' }}>×</button>
            </div>
            <div style={{ padding: 20, overflowY: 'auto', flex: 1 }}>
              <ProductFormFields value={productForm} onChange={setProductForm} options={filterOptions || EMPTY_FILTER_OPTIONS} />

              {productFormMode === 'create' && (
                <div style={{ ...panel, marginTop: 14 }}>
                  <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Opening stock{activeStore ? ` at ${activeStore}` : ''}
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 10 }}>
                    Leave blank if you are creating the product but have none on hand yet — the sale still rings up.
                  </div>
                  {productForm.inventory_type === 'serial' ? (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                      <div>
                        <label style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }}>Serial number of the first unit</label>
                        <input value={openingSerial} onChange={e => setOpeningSerial(e.target.value)} placeholder="Scan or type the serial" style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} />
                      </div>
                      <div>
                        <label style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }}>IMEI (optional)</label>
                        <input value={openingImei} onChange={e => setOpeningImei(e.target.value)} placeholder="15 digits" style={{ ...inputStyle, width: '100%', boxSizing: 'border-box' }} />
                      </div>
                      <div style={{ gridColumn: '1 / -1', fontSize: 11, color: 'var(--text3)' }}>
                        A serial-tracked unit is received one unit at a time, through the duplicate-IMEI check — so the
                        same device cannot be received twice by accident.
                      </div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
                      <div>
                        <label style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }}>Quantity received</label>
                        <input type="number" min="0" step="1" value={openingQty} onChange={e => setOpeningQty(e.target.value)} placeholder="0" style={{ ...inputStyle, width: 120 }} />
                      </div>
                      {!activeStore && (
                        <div style={{ fontSize: 11, color: 'var(--amber, #d97706)' }}>
                          Pick this register&apos;s store first — stock is held at a store, so none can be recorded without one.
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              {productFormError && (
                <div style={{ marginTop: 12, fontSize: 12, color: 'var(--red, #dc2626)' }}>{productFormError}</div>
              )}
              {productFormNote && (
                <div style={{ marginTop: 12, fontSize: 12, color: 'var(--text2)' }}>{productFormNote}</div>
              )}
            </div>
            <div style={{ padding: '14px 20px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn btn-secondary" onClick={closeProductForm}>Cancel</button>
              <button className="btn btn-primary" disabled={productFormBusy || !productFormReady(productForm)} onClick={saveProductForm}>
                {productFormBusy
                  ? 'Saving…'
                  : productFormMode === 'edit' ? 'Save changes' : 'Create & add to sale'}
              </button>
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
              {/* EMAIL THE RECEIPT (§POS-02) — the address is shown and editable, and the line under
                  it says what name the customer will see it come from, so nobody has to send one to
                  find out. */}
              <div style={{ background: 'var(--surface2)', border: '1px solid var(--border)', borderRadius: 8, padding: 12, marginBottom: 12, textAlign: 'left' }}>
                <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Email the receipt to:</div>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input type="email" value={emailTo} onChange={e => { setEmailTo(e.target.value); setEmailSentTo('') }}
                    onKeyDown={e => { if (e.key === 'Enter' && !emailSending) sendReceiptEmail() }}
                    placeholder="name@example.com" style={{ ...inputStyle, flex: 1, minWidth: 0 }} />
                  <button className="btn btn-primary" style={{ fontSize: 12, padding: '7px 12px', cursor: emailSending ? 'wait' : 'pointer', opacity: emailSending || !emailTo.trim() ? 0.6 : 1 }}
                    onClick={sendReceiptEmail} disabled={emailSending || !emailTo.trim()}>
                    {emailSending ? 'Sending…' : 'Send'}
                  </button>
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)', marginTop: 6 }}>
                  {emailSentTo
                    ? `Sent to ${emailSentTo}.`
                    : emailFromName
                      ? `The customer sees it from ${emailFromName}.`
                      : 'No company name is set yet, so it arrives with the platform name — set one in POS → Settings → Receipt Template.'}
                </div>
              </div>
              {lastWasActivation && lastSaleId && (
                <a href={`/pos/activations?sale=${lastSaleId}&customer=${lastSaleCustomerId || ''}`}
                  title={mustPrint ? mustPrintTitle : undefined}
                  style={{ display: 'block', width: '100%', boxSizing: 'border-box', background: 'linear-gradient(135deg, var(--accent2), var(--accent))', border: 'none', color: 'white', borderRadius: 8, padding: 14, fontSize: 15, fontWeight: 700, cursor: mustPrint ? 'not-allowed' : 'pointer', marginBottom: 8, textDecoration: 'none', pointerEvents: mustPrint ? 'none' : 'auto', opacity: mustPrint ? 0.5 : 1 }}>
                  📱 Continue to Activation →
                </a>
              )}
              {/* WHAT IT PRINTS AS (§POS-03) — every store, every sale, above the button that
                  prints it. Changing it re-reads the receipt so the note and the resolved width are
                  the ones that will actually come out. */}
              <PrintFormatPicker view={receiptView} choice={printChoice}
                onChange={next => {
                  setPrintChoice(next)
                  if (lastSaleId) loadReceiptEmailView(lastSaleId, next)
                }} />
              <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center', marginBottom: 8 }}
                onClick={async () => { if (await printSaleReceipt(lastSaleId, printChoice)) setReceiptPrinted(true) }}>
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

      {/* THE REPRINT SCREEN (§POS-03) — a Sale Log reprint picks its format the same way the till
          does, because the counter reprinting it may have different paper in the printer. */}
      {reprintSaleId && (
        <div style={modalOverlay}>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 12, width: 420, overflow: 'hidden' }}>
            <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
              <b style={{ fontSize: 14 }}>🖨️ Print receipt</b>
            </div>
            <div style={{ padding: 20 }}>
              {reprintView
                ? <PrintFormatPicker view={reprintView} choice={reprintChoice}
                    onChange={async next => {
                      setReprintChoice(next)
                      try { setReprintView(await fetchSaleReceipt(reprintSaleId, next)) } catch { /* the picker keeps the last answer */ }
                    }} />
                : <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>Reading the receipt…</div>}
              <button className="btn btn-primary" style={{ width: '100%', justifyContent: 'center' }}
                disabled={!reprintView}
                onClick={async () => { if (await printSaleReceipt(reprintSaleId, reprintChoice)) setReprintSaleId(null) }}>
                🖨️ Print
              </button>
              <button className="btn btn-secondary" style={{ width: '100%', justifyContent: 'center', marginTop: 8 }}
                onClick={() => setReprintSaleId(null)}>
                Cancel
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
                  {/* A blocker names where to go ("… in the Employees module") — link it (owner 2026-09-08). */}
                  <LinkedText text={issue} />
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
