// POS Configuration registry + inheritance resolver (ported from the standalone
// pos-system app's lib/posConfig.ts; data access rewired to the FastAPI /pos router).
//
// Storage model (pos.pos_settings table): one jsonb value per key, at either the
// org level (store_code null = the default for every store) or the store
// level (an override). Effective value = store override ?? org default ??
// the code-side default declared here.
//
// The registry drives both the POS Settings UI (labels, grouping, input types,
// inheritance badges) and the register (loadEffectivePosConfig).
import { api } from '@/lib/client'

export type PosSettingType = 'boolean' | 'number' | 'currency' | 'select' | 'text' | 'textarea'

export interface PosSettingDef {
  key: string
  label: string
  hint: string
  type: PosSettingType
  default: boolean | number | string
  /** for type 'select' */
  options?: { value: string; label: string }[]
  /** shown when a toggle is stored but not yet enforced anywhere */
  notYetEnforced?: string
}

export interface PosSettingSection {
  id: string
  title: string
  icon: string
  subtitle: string
  settings: PosSettingDef[]
}

export const POS_SETTING_SECTIONS: PosSettingSection[] = [
  {
    id: 'sales_rules',
    title: 'Cash Register & Transaction Rules',
    icon: '🛒',
    subtitle: 'What the register requires before a sale can be completed',
    settings: [
      {
        key: 'require_customer',
        label: 'Do not allow to sell without a customer',
        hint: 'A customer profile must be attached before checkout can complete.',
        type: 'boolean', default: false,
      },
      {
        key: 'referral_required',
        label: 'Referral required',
        hint: 'The attached customer must have a referral source on file before checkout.',
        type: 'boolean', default: false,
      },
      {
        key: 'allow_negative_inventory',
        label: 'Allow sale when product quantity is less than 1',
        hint: 'On: backorder/negative-stock sales go through (count discrepancies surface in inventory). Off: the register blocks selling below zero — enforced in the database.',
        type: 'boolean', default: true,
      },
      {
        key: 'only_opener_transacts',
        label: 'Only the person who opened the register can make transactions',
        hint: 'While a drawer session is open, only the employee who opened it can complete sales at that store.',
        type: 'boolean', default: false,
      },
      {
        key: 'enable_register_lock',
        label: 'Enable cash register lock',
        hint: 'Adds a Lock button to the register; unlocking requires the signed-in user’s password.',
        type: 'boolean', default: false,
      },
      {
        key: 'register_lock_minutes',
        label: 'Auto-lock after inactivity (minutes)',
        hint: '0 = manual lock only. Requires the register lock to be enabled.',
        type: 'number', default: 0,
      },
      {
        key: 'commission_splitting',
        label: 'Enable employee commission splitting',
        hint: 'Allows attributing one sales ticket to multiple employees.',
        type: 'boolean', default: false,
        notYetEnforced: 'Takes effect with the CommCalc feed (Phase 3).',
      },
    ],
  },
  {
    id: 'tax_rules',
    title: 'Tax Calculation Rules',
    icon: '🧮',
    subtitle: 'How the register computes line tax (tax codes themselves are managed in POS Settings → Sales Tax)',
    settings: [
      {
        key: 'tax_applied_on',
        label: 'Tax is applied on',
        hint: 'Whether line tax is computed before or after the line discount (state-dependent rule). Was org_settings.tax_applied_on in the standalone app.',
        type: 'select', default: 'post_discount',
        options: [
          { value: 'post_discount', label: 'Price after discount' },
          { value: 'pre_discount', label: 'Price before discount' },
        ],
      },
      {
        key: 'tax_method',
        label: 'Method of tax calculation',
        hint: 'Cost based: when the taxable sale price is below product cost, tax is computed on the cost instead. The rebate and bundled methods need equipment-rebate/MSRP data (parked in QUESTIONS.md) and currently behave like the standard method.',
        type: 'select', default: 'sale_price',
        options: [
          { value: 'sale_price', label: 'Tax on sale price (including discount)' },
          { value: 'sale_price_rebate', label: 'Tax on sale price (including equipment rebate)' },
          { value: 'cost_based', label: 'Cost based' },
          { value: 'bundled_msrp', label: 'Bundled with service equipment (MSRP)' },
        ],
      },
      {
        key: 'tax_cap_enabled',
        label: 'Enable tax cap',
        hint: 'Caps the tax charged per item unit at the amount below.',
        type: 'boolean', default: false,
      },
      {
        key: 'tax_cap_amount',
        label: 'Tax cap per item ($)',
        hint: 'Maximum tax per item unit when the cap is enabled.',
        type: 'currency', default: 0,
      },
    ],
  },
  {
    id: 'cash_drawer',
    title: 'POS Opening / Closing & Cash Count',
    icon: '💰',
    subtitle: 'Drawer sessions, denomination counts, float, and cash-variance controls',
    settings: [
      {
        key: 'enforce_drawer_sessions',
        label: 'Cash sales require an open register',
        hint: 'On: the drawer must be opened (with a float count) before cash transactions are accepted.',
        type: 'boolean', default: false,
      },
      {
        key: 'denomination_count_required',
        label: 'Denomination count required',
        hint: 'Opening and closing the drawer requires counting individual bills and coins (not just typing a total).',
        type: 'boolean', default: false,
      },
      {
        key: 'show_two_dollar_bill',
        label: 'Display $2.00 banknote',
        hint: 'Include a $2 bill row on the denomination count screen.',
        type: 'boolean', default: false,
      },
      {
        key: 'use_default_float',
        label: 'Use default float amount',
        hint: 'Pre-fill the opening cash float with the amount below.',
        type: 'boolean', default: false,
      },
      {
        key: 'default_float_amount',
        label: 'Default float amount ($)',
        hint: 'The opening drawer balance suggested when the drawer is opened.',
        type: 'currency', default: 0,
      },
      {
        key: 'variance_alert_threshold',
        label: 'Alert when open/close cash variance is over ($)',
        hint: '0 = no alert. A close whose counted-vs-expected difference exceeds this shows a prominent variance warning.',
        type: 'currency', default: 0,
      },
      {
        key: 'max_cash_in_drawer',
        label: 'Block cash sales when drawer cash is over ($)',
        hint: '0 = no limit. Cash transactions are blocked until a cash drop once the drawer holds more than this.',
        type: 'currency', default: 0,
      },
    ],
  },
  {
    id: 'payments',
    title: 'Payment Processing & Discounts',
    icon: '💳',
    subtitle: 'Card handling and the availability of discounts at the register',
    settings: [
      {
        key: 'suspend_card_processing',
        label: 'Suspend credit/debit card processing',
        hint: 'Hides the integrated card buttons; card payments are recorded as taken on an external terminal.',
        type: 'boolean', default: false,
      },
      {
        key: 'require_billing_info',
        label: 'Card holder billing info is required',
        hint: 'Address and postal code must be entered on card payments.',
        type: 'boolean', default: false,
        notYetEnforced: 'Applies when an integrated card gateway is connected.',
      },
      {
        key: 'require_cvc',
        label: 'CVV2/CVC code is required',
        hint: 'Card verification code must be entered for card processing.',
        type: 'boolean', default: false,
        notYetEnforced: 'Applies when an integrated card gateway is connected.',
      },
      {
        key: 'coupon_discount_enabled',
        label: 'Coupon/Discount mode is enabled',
        hint: 'Off: the discount field is removed from the register entirely (regardless of employee permissions).',
        type: 'boolean', default: true,
      },
    ],
  },
  {
    id: 'receipt_rules',
    title: 'Receipt Content & Printing',
    icon: '🧾',
    subtitle: 'What prints on receipts and how printing/email prompts behave',
    settings: [
      {
        key: 'receipt_customer_short_name',
        label: 'Print customer as first name + last initial',
        hint: 'Receipts show "John S." instead of the full name.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_coupon_in_retail_price',
        label: 'Embed coupon/discount in the retail price',
        hint: 'Receipt lines print the already-discounted price instead of a separate discount line.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_auto_print',
        label: 'Auto-print receipt on sale completion',
        hint: 'The print dialog opens automatically when a sale completes.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_mandatory_print',
        label: 'Receipt printing is mandatory',
        hint: 'The completion screen cannot be dismissed until the receipt has been printed.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_prompt_email',
        label: 'Prompt for customer email',
        hint: 'After a sale with a customer who has no email on file, ask for one and save it to the profile.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_print_cash_pickup_slip',
        label: 'Print cash pickup slip',
        hint: 'Offer to print a summary slip (expected/counted/variance) when the drawer is closed.',
        type: 'boolean', default: false,
      },
      {
        key: 'receipt_show_plan_fees',
        label: 'Show plan minutes / monthly fees on receipts',
        hint: 'Activation receipts include the service plan’s minutes and monthly fee.',
        type: 'boolean', default: false,
        notYetEnforced: 'Prints once the service-plan catalog details are seeded (owner to provide).',
      },
      {
        key: 'receipt_show_plan_option_fees',
        label: 'Show plan option fees on receipts',
        hint: 'Activation receipts include fees of selected plan options/add-ons.',
        type: 'boolean', default: false,
        notYetEnforced: 'Prints once the service-plan catalog details are seeded (owner to provide).',
      },
    ],
  },
  {
    id: 'receipt_branding',
    title: 'Receipt Branding & Header',
    icon: '🏬',
    subtitle: 'Company identity printed on receipts — set org-wide, override per store (e.g. each store’s address)',
    settings: [
      { key: 'brand_company_name', label: 'Company name', hint: 'Overrides the store display name on receipt headers when set.', type: 'text', default: '' },
      { key: 'brand_address_line1', label: 'Address line 1', hint: '', type: 'text', default: '' },
      { key: 'brand_address_line2', label: 'Address line 2', hint: '', type: 'text', default: '' },
      { key: 'brand_address_line3', label: 'Address line 3', hint: '', type: 'text', default: '' },
      { key: 'brand_address_line4', label: 'Address line 4', hint: '', type: 'text', default: '' },
      { key: 'brand_address_line5', label: 'Address line 5', hint: '', type: 'text', default: '' },
      { key: 'brand_license_tax_ids', label: 'License / Tax IDs', hint: 'Printed under the address on receipts.', type: 'text', default: '' },
      {
        key: 'brand_logo_pos',
        label: 'Print logo on POS receipts',
        hint: '',
        type: 'boolean', default: false,
        notYetEnforced: 'Needs a logo upload (owner to provide the file).',
      },
      {
        key: 'brand_logo_a4',
        label: 'Print logo on A4 invoices',
        hint: '',
        type: 'boolean', default: false,
        notYetEnforced: 'Needs a logo upload (owner to provide the file).',
      },
      {
        key: 'brand_logo_kiosk',
        label: 'Print logo on kiosk receipts',
        hint: '',
        type: 'boolean', default: false,
        notYetEnforced: 'Needs a logo upload; no kiosk mode exists yet.',
      },
    ],
  },
  {
    id: 'disclaimers',
    title: 'Legal Disclaimers & Signatures',
    icon: '⚖️',
    subtitle: 'Up to two disclaimer blocks printed on receipts/invoices, with optional signature lines',
    settings: [
      { key: 'disclaimer1_text', label: 'Disclaimer 1 — text', hint: 'e.g. your return policy.', type: 'textarea', default: '' },
      {
        key: 'disclaimer1_target', label: 'Disclaimer 1 — prints on', hint: '',
        type: 'select', default: 'none',
        options: [
          { value: 'none', label: 'None (off)' },
          { value: 'pos', label: 'POS receipts' },
          { value: 'invoice', label: 'Invoices' },
          { value: 'both', label: 'Both' },
        ],
      },
      { key: 'disclaimer1_signature', label: 'Disclaimer 1 — customer signature line', hint: 'Adds an X____ signature line under the disclaimer.', type: 'boolean', default: false },
      { key: 'disclaimer2_text', label: 'Disclaimer 2 — text', hint: 'e.g. service contract terms.', type: 'textarea', default: '' },
      {
        key: 'disclaimer2_target', label: 'Disclaimer 2 — prints on', hint: '',
        type: 'select', default: 'none',
        options: [
          { value: 'none', label: 'None (off)' },
          { value: 'pos', label: 'POS receipts' },
          { value: 'invoice', label: 'Invoices' },
          { value: 'both', label: 'Both' },
        ],
      },
      { key: 'disclaimer2_signature', label: 'Disclaimer 2 — customer signature line', hint: 'Adds an X____ signature line under the disclaimer.', type: 'boolean', default: false },
    ],
  },
  {
    id: 'agreements',
    title: 'Email & Agreement Templates',
    icon: '✉️',
    subtitle: 'Templates for emailed receipts and card agreements',
    settings: [
      {
        key: 'email_receipt_subject', label: 'Email receipt — subject', hint: '', type: 'text', default: '',
        notYetEnforced: 'Sends once an email provider is configured (receipts share via mail client today).',
      },
      {
        key: 'email_receipt_body', label: 'Email receipt — body', hint: '', type: 'textarea', default: '',
        notYetEnforced: 'Sends once an email provider is configured (receipts share via mail client today).',
      },
      {
        key: 'cc_auth_agreement', label: 'Credit-card authorization agreement', hint: '', type: 'textarea', default: '',
        notYetEnforced: 'Prints with card payments once an integrated card gateway is connected.',
      },
      {
        key: 'chargeback_token_agreement', label: 'Chargeback token agreement', hint: '', type: 'textarea', default: '',
        notYetEnforced: 'Prints with card payments once an integrated card gateway is connected.',
      },
    ],
  },
  {
    id: 'returns',
    title: 'Returns & Exchanges',
    icon: '↩️',
    subtitle: 'Return/exchange policy rules — stored now, enforced when the Returns module is built (see HANDOFF questions)',
    settings: [
      {
        key: 'return_serial_unrecorded_action',
        label: 'Returning a serial item not recorded as sold',
        hint: '',
        type: 'select', default: 'warn_allow',
        options: [
          { value: 'block', label: 'Block the return' },
          { value: 'warn_allow', label: 'Warn, but allow to process' },
          { value: 'allow', label: 'Allow silently' },
        ],
        notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_allow_no_receipt', label: 'Allow returns without a receipt', hint: '',
        type: 'boolean', default: false, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_refund_method',
        label: 'Refund method',
        hint: '',
        type: 'select', default: 'cash_or_account',
        options: [
          { value: 'cash_or_account', label: 'Cash or store account' },
          { value: 'cash_only', label: 'Cash only' },
          { value: 'account_only', label: 'Store account only' },
        ],
        notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_cross_store', label: 'Allow cross-store returns', hint: 'Return at a different store than the sale.',
        type: 'boolean', default: false, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_allow_future_cancellation', label: 'Allow future cancellation dates', hint: '',
        type: 'boolean', default: false, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_phone_serial_exchange', label: 'Allow phone/serial exchanges', hint: '',
        type: 'boolean', default: true, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'return_window_days', label: 'Default return window (days)', hint: '',
        type: 'number', default: 30, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'fru_service_fee', label: 'FRU service fee ($)', hint: 'Field Replacement Unit fee.',
        type: 'currency', default: 0, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
      {
        key: 'fru_account_age_days', label: 'FRU fee waived under account age (days)', hint: '0 = fee always applies.',
        type: 'number', default: 0, notYetEnforced: 'Returns module pending owner go-ahead.',
      },
    ],
  },
  {
    id: 'activations_cfg',
    title: 'Activations & Carrier Rules',
    icon: '📱',
    subtitle: 'Activation-flow rules and carrier credit-check behavior (ported with the Activations module in Phase 2)',
    settings: [
      {
        key: 'activation_dealer_code_warning',
        label: 'Warn when no dealer code is selected',
        hint: 'Saving an activation without a dealer code shows a confirmation warning.',
        type: 'boolean', default: true,
      },
      {
        key: 'credit_check_standalone',
        label: 'Standalone carrier credit checks',
        hint: 'Shows a Credit Check button (carrier portal links) without opening an activation.',
        type: 'boolean', default: false,
      },
      {
        key: 'activation_exchange_on_cancel',
        label: 'Serial & SIM exchange on cancelled activations',
        hint: 'Guided device/SIM exchange when an activation is cancelled.',
        type: 'boolean', default: false,
        notYetEnforced: 'Guided exchange flow pending (cancellation today just sets the status).',
      },
      {
        key: 'commission_zero_same_month_deactivation',
        label: 'Zero out commission on same-month deactivations',
        hint: '',
        type: 'boolean', default: false,
        notYetEnforced: 'Takes effect with the CommCalc feed (Phase 3).',
      },
      {
        key: 'equipment_rebate_discount',
        label: 'Apply POS equipment rebate discount',
        hint: '',
        type: 'boolean', default: false,
        notYetEnforced: 'Needs equipment-rebate data (parked in QUESTIONS.md).',
      },
    ],
  },
]

export const POS_SETTING_DEFS: Record<string, PosSettingDef> = Object.fromEntries(
  POS_SETTING_SECTIONS.flatMap(s => s.settings).map(d => [d.key, d])
)

export type PosConfigValues = Record<string, boolean | number | string>

export interface PosSettingRow {
  store_code: string | null
  key: string
  value: unknown
}

function coerce(def: PosSettingDef, raw: unknown): boolean | number | string {
  if (raw === null || raw === undefined) return def.default
  switch (def.type) {
    case 'boolean':
      return typeof raw === 'boolean' ? raw : def.default
    case 'number':
    case 'currency': {
      const n = typeof raw === 'number' ? raw : Number(raw)
      return Number.isFinite(n) ? n : def.default
    }
    case 'select': {
      const s = String(raw)
      return def.options?.some(o => o.value === s) ? s : def.default
    }
    case 'text':
    case 'textarea':
      return typeof raw === 'string' ? raw : def.default
  }
}

export type PosValueSource = 'override' | 'org' | 'default'

/**
 * Resolve effective values (+ where each came from) for one store scope from
 * raw pos_settings rows. Pass storeCode=null for the org-defaults scope.
 */
export function resolvePosConfig(rows: PosSettingRow[], storeCode: string | null): {
  values: PosConfigValues
  sources: Record<string, PosValueSource>
} {
  const org = new Map<string, unknown>()
  const store = new Map<string, unknown>()
  for (const r of rows) {
    if (r.store_code === null) org.set(r.key, r.value)
    else if (storeCode && r.store_code === storeCode) store.set(r.key, r.value)
  }
  const values: PosConfigValues = {}
  const sources: Record<string, PosValueSource> = {}
  for (const def of Object.values(POS_SETTING_DEFS)) {
    if (storeCode && store.has(def.key)) {
      values[def.key] = coerce(def, store.get(def.key))
      sources[def.key] = 'override'
    } else if (org.has(def.key)) {
      values[def.key] = coerce(def, org.get(def.key))
      sources[def.key] = 'org'
    } else {
      values[def.key] = def.default
      sources[def.key] = 'default'
    }
  }
  return { values, sources }
}

/**
 * Effective config for a store (or org defaults when storeCode is null),
 * ready for the register. Falls back to pure defaults if the query fails
 * (the register must never be dead in the water because of settings).
 */
export async function loadEffectivePosConfig(storeCode: string | null): Promise<PosConfigValues> {
  try {
    const r = await api(`/api/v1/pos/settings${storeCode ? `?store_code=${encodeURIComponent(storeCode)}` : ''}`)
    return resolvePosConfig((r.settings || []) as PosSettingRow[], storeCode).values
  } catch {
    return resolvePosConfig([], storeCode).values
  }
}

// ---------------------------------------------------------------------------
// Denomination helpers (drawer open/close count screens)
// ---------------------------------------------------------------------------

export interface Denomination { key: string; label: string; value: number; kind: 'bill' | 'coin' }

export function denominationList(showTwoDollar: boolean): Denomination[] {
  const bills: Denomination[] = [
    { key: '100', label: '$100', value: 100, kind: 'bill' },
    { key: '50', label: '$50', value: 50, kind: 'bill' },
    { key: '20', label: '$20', value: 20, kind: 'bill' },
    { key: '10', label: '$10', value: 10, kind: 'bill' },
    { key: '5', label: '$5', value: 5, kind: 'bill' },
    ...(showTwoDollar ? [{ key: '2', label: '$2', value: 2, kind: 'bill' as const }] : []),
    { key: '1', label: '$1', value: 1, kind: 'bill' },
  ]
  const coins: Denomination[] = [
    { key: '0.25', label: '25¢', value: 0.25, kind: 'coin' },
    { key: '0.1', label: '10¢', value: 0.1, kind: 'coin' },
    { key: '0.05', label: '5¢', value: 0.05, kind: 'coin' },
    { key: '0.01', label: '1¢', value: 0.01, kind: 'coin' },
  ]
  return [...bills, ...coins]
}

/** Total dollars for a {denominationKey: count} map. */
export function denominationTotal(counts: Record<string, number>): number {
  let cents = 0
  for (const [key, count] of Object.entries(counts)) {
    const v = Number(key)
    const c = Number(count)
    if (!Number.isFinite(v) || !Number.isFinite(c) || c < 0) continue
    cents += Math.round(v * 100) * Math.floor(c)
  }
  return cents / 100
}

// ---------------------------------------------------------------------------
// Per-device register number (like the active store, this is a property of
// the physical terminal, not of the user — localStorage).
// ---------------------------------------------------------------------------

const REGISTER_KEY = 'pos_register_number'

export function getRegisterNumber(): number {
  if (typeof window === 'undefined') return 1
  try {
    const n = Number(window.localStorage.getItem(REGISTER_KEY))
    return Number.isInteger(n) && n >= 1 ? n : 1
  } catch {
    return 1
  }
}

export function setRegisterNumber(n: number): void {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(REGISTER_KEY, String(Math.max(1, Math.floor(n))))
  } catch {
    // localStorage unavailable — default of 1 will be used
  }
}
