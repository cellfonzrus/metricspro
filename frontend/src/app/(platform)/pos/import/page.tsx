'use client'
// POS module — Phase 2: CSV data import (ported from the standalone pos-system app/import page).
// Scope changes vs the standalone app:
//   • 5 entities (customers, products, vendors, inventory, activations) — Store Locations dropped:
//     stores are owned by MetricsPro storeops. Inventory's `location` column became `store_code`.
//   • Dedupe + inserts moved server-side: the page validates/coerces client-side, then POSTs rows to
//     POST /api/v1/pos/import/{entity} (max 5000 rows per request, chunked) and merges the server's
//     {inserted, skipped, errors} response into the per-row results.
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/lib/client'
import { apiCached, LOOKUP } from '@/lib/cache'
import { parseCsv, serializeCsv, type CsvCell } from '@/lib/pos-csv'

// ---------------------------------------------------------------------------
// Entity / field definitions
// ---------------------------------------------------------------------------

type EntityKey = 'customers' | 'products' | 'vendors' | 'inventory' | 'activations'
type FieldType = 'string' | 'number' | 'int' | 'date' | 'boolean'

interface FieldSpec {
  key: string            // canonical column name (template header)
  type: FieldType
  synonyms?: string[]    // normalized alternates used for header auto-matching
  oneOf?: string[]       // allowed values (case-insensitive; canonical casing kept)
  blank?: unknown        // value used when the cell is blank / column unmapped
}

interface EntityDef {
  key: EntityKey
  title: string
  icon: string
  blurb: string
  note?: string
  fields: FieldSpec[]
  examples: string[][]
}

const EXCEL_HINT = 'Excel users: File → Save As → CSV (Comma delimited) (*.csv)'
const CHUNK_SIZE = 5000 // server limit per POST /api/v1/pos/import/{entity} request

const ENTITIES: EntityDef[] = [
  {
    key: 'customers',
    title: 'Customers',
    icon: '👥',
    blurb: 'Customer address book: names, contact info, addresses, credit settings.',
    note: 'The carrier account PIN cannot be imported — enter it per customer in the app. SSN and driver-licence details are not held by the platform at all, so there is nothing to import.',
    fields: [
      { key: 'account_type', type: 'string', oneOf: ['Personal', 'Business'], blank: 'Personal', synonyms: ['type', 'customer_type'] },
      { key: 'company_name', type: 'string', synonyms: ['company', 'business_name'] },
      { key: 'first_name', type: 'string', synonyms: ['firstname', 'first', 'given_name'] },
      { key: 'last_name', type: 'string', synonyms: ['lastname', 'last', 'surname', 'family_name'] },
      { key: 'middle_initial', type: 'string', synonyms: ['mi', 'middle'] },
      { key: 'dob', type: 'date', synonyms: ['date_of_birth', 'birthdate', 'birth_date'] },
      { key: 'email', type: 'string', synonyms: ['e_mail', 'email_address'] },
      { key: 'phone_primary', type: 'string', synonyms: ['phone', 'phone_number', 'primary_phone', 'mobile', 'cell'] },
      { key: 'phone_secondary', type: 'string', synonyms: ['phone_2', 'phone2', 'secondary_phone', 'alt_phone'] },
      { key: 'address_1', type: 'string', synonyms: ['address', 'street', 'street_1', 'address_line_1'] },
      { key: 'address_2', type: 'string', synonyms: ['street_2', 'address_line_2', 'apt', 'suite'] },
      { key: 'city', type: 'string' },
      { key: 'state', type: 'string', synonyms: ['province'] },
      { key: 'zip', type: 'string', synonyms: ['zip_code', 'zipcode', 'postal_code'] },
      { key: 'credit_limit', type: 'number', synonyms: ['credit'] },
      { key: 'accept_checks', type: 'boolean', blank: true, synonyms: ['accepts_checks', 'checks'] },
      { key: 'referral_source', type: 'string', synonyms: ['referral', 'source', 'how_heard'] },
      { key: 'is_active', type: 'boolean', blank: true, synonyms: ['active'] },
    ],
    examples: [
      ['Personal', '', 'John', 'Smith', 'A', '1985-04-12', 'NY', 'john.smith@example.com', '555-201-1234', '', '123 Main St', 'Apt 4B', 'Brooklyn', 'NY', '11201', '500', 'yes', 'Walk In Customer', 'yes'],
      ['Business', 'Acme Wireless LLC', 'Maria', 'Lopez', '', '11/03/1979', 'NJ', 'accounts@acmewireless.com', '555-987-6543', '555-987-6544', '88 Commerce Way', '', 'Newark', 'NJ', '07102', '2500', 'no', 'Internet', 'yes'],
    ],
  },
  {
    key: 'products',
    title: 'Products',
    icon: '🏷️',
    blurb: 'Product catalog: UPCs, names, department/category (auto-created by name), cost and retail pricing.',
    note: 'Department and category are matched by NAME (case-insensitive) and created automatically if missing — send the names, the server resolves them. Product # is assigned by the system.',
    fields: [
      { key: 'upc', type: 'string', synonyms: ['barcode', 'upc_code', 'upc_barcode', 'sku'] },
      { key: 'short_name', type: 'string', synonyms: ['name', 'product_name', 'short_description', 'description', 'title'] },
      { key: 'full_name', type: 'string', synonyms: ['full_description', 'long_name', 'long_description'] },
      { key: 'department', type: 'string', synonyms: ['department_name', 'dept'] },
      { key: 'category', type: 'string', synonyms: ['category_name', 'cat'] },
      { key: 'system_category', type: 'string', oneOf: ['Accessory', 'Cell Phone', 'Regular', 'Service'], blank: 'Regular', synonyms: ['sys_category'] },
      { key: 'inventory_type', type: 'string', oneOf: ['standard', 'serial'], blank: 'standard', synonyms: ['tracking', 'tracking_type'] },
      { key: 'cost', type: 'number', blank: 0, synonyms: ['unit_cost', 'cost_price', 'wholesale'] },
      { key: 'retail_price', type: 'number', blank: 0, synonyms: ['price', 'retail', 'sale_price', 'sell_price'] },
      { key: 'is_taxable', type: 'boolean', blank: true, synonyms: ['taxable', 'tax'] },
      { key: 'calculate_as_profit', type: 'boolean', blank: true, synonyms: ['profit'] },
      { key: 'is_active', type: 'boolean', blank: true, synonyms: ['active'] },
    ],
    examples: [
      ['885909950805', 'iPhone 15 Case', 'iPhone 15 Silicone Case - Black', 'Accessories', 'Cases', 'Accessory', 'standard', '4.50', '19.99', 'yes', 'yes', 'yes'],
      ['012345678905', 'Tempered Glass SP', 'Tempered Glass Screen Protector 2-Pack', 'Accessories', 'Screen Protection', 'Accessory', 'standard', '1.25', '14.99', 'yes', 'yes', 'yes'],
    ],
  },
  {
    key: 'vendors',
    title: 'Vendors',
    icon: '🏭',
    blurb: 'Business address book: vendors, manufacturers, master dealers and shippers.',
    fields: [
      { key: 'legal_name', type: 'string', synonyms: ['name', 'company', 'company_name', 'vendor_name', 'vendor'] },
      { key: 'short_name', type: 'string', synonyms: ['nickname'] },
      { key: 'business_type', type: 'string', oneOf: ['Vendor', 'Manufacturer', 'Master Dealer', 'Shipper', 'ePay carrier'], blank: 'Vendor', synonyms: ['type'] },
      { key: 'ban', type: 'string', synonyms: ['account_number', 'billing_account'] },
      { key: 'contact_name', type: 'string', synonyms: ['contact'] },
      { key: 'phone', type: 'string', synonyms: ['phone_number', 'phone_primary'] },
      { key: 'fax', type: 'string' },
      { key: 'email', type: 'string', synonyms: ['e_mail', 'email_address'] },
      { key: 'website', type: 'string', synonyms: ['url', 'web'] },
      { key: 'street_one', type: 'string', synonyms: ['address', 'address_1', 'street', 'street_1'] },
      { key: 'street_two', type: 'string', synonyms: ['address_2', 'street_2', 'suite'] },
      { key: 'city', type: 'string' },
      { key: 'state', type: 'string', synonyms: ['province'] },
      { key: 'zip', type: 'string', synonyms: ['zip_code', 'zipcode', 'postal_code'] },
      { key: 'country', type: 'string', blank: 'USA' },
      { key: 'tax_id', type: 'string', synonyms: ['ein', 'tax'] },
      { key: 'is_active', type: 'boolean', blank: true, synonyms: ['active'] },
    ],
    examples: [
      ['Mobile Distributors Inc', 'MobileDist', 'Vendor', 'BAN-10021', 'Dan Reyes', '555-300-2000', '', 'orders@mobiledist.example.com', 'https://mobiledist.example.com', '400 Industrial Pkwy', 'Suite 12', 'Edison', 'NJ', '08817', 'USA', '22-1234567', 'yes'],
      ['Samsung Electronics America', 'Samsung', 'Manufacturer', '', 'Support Desk', '555-410-8800', '', 'b2b@samsung.example.com', '', '85 Challenger Rd', '', 'Ridgefield Park', 'NJ', '07660', 'USA', '', 'yes'],
    ],
  },
  {
    key: 'inventory',
    title: 'Inventory quantities',
    icon: '📦',
    blurb: 'Stock levels for standard (qty-tracked) products, by store.',
    note: 'Rows are matched to products by UPC (fallback: product name) — import PRODUCTS first. store_code must match a MetricsPro store code (Workforce → Stores).',
    fields: [
      { key: 'upc', type: 'string', synonyms: ['barcode', 'upc_code', 'sku'] },
      { key: 'product_name', type: 'string', synonyms: ['product', 'name', 'short_name', 'description'] },
      { key: 'store_code', type: 'string', synonyms: ['store', 'store_number', 'store_no', 'location', 'location_name', 'store_name', 'site'] },
      { key: 'qty_on_hand', type: 'int', synonyms: ['qty', 'quantity', 'on_hand', 'stock', 'qty_in_stock'] },
      { key: 'qty_on_order', type: 'int', blank: 0, synonyms: ['on_order'] },
      { key: 'qty_reserved', type: 'int', blank: 0, synonyms: ['reserved'] },
      { key: 'bin_location', type: 'string', synonyms: ['bin', 'shelf'] },
    ],
    examples: [
      ['885909950805', 'iPhone 15 Case', '101', '25', '10', '0', 'A-04'],
      ['012345678905', 'Tempered Glass SP', '102', '140', '0', '2', 'B-11'],
    ],
  },
  {
    key: 'activations',
    title: 'Activations',
    icon: '📱',
    blurb: 'Carrier activation history: lines, plans, devices and fees.',
    note: 'Rows are linked to customers by phone, email, or name (in that order) — import CUSTOMERS first. Rows with all customer columns blank import unlinked; rows whose customer info matches nothing (or matches more than one customer) are skipped and reported. Activation # is assigned by the system.',
    fields: [
      { key: 'customer_phone', type: 'string', synonyms: ['phone', 'phone_primary', 'customer_phone_number'] },
      { key: 'customer_email', type: 'string', synonyms: ['email', 'e_mail'] },
      { key: 'customer_name', type: 'string', synonyms: ['customer', 'customer_full_name'] },
      { key: 'carrier', type: 'string', synonyms: ['network'] },
      { key: 'activation_date', type: 'date', synonyms: ['date', 'activated_on'] },
      { key: 'cell_number', type: 'string', synonyms: ['cell', 'mobile_number', 'mdn', 'line_number', 'line'] },
      { key: 'phone_model', type: 'string', synonyms: ['model', 'device'] },
      { key: 'phone_serial', type: 'string', synonyms: ['serial', 'imei', 'esn'] },
      { key: 'sim_card', type: 'string', synonyms: ['sim', 'iccid'] },
      { key: 'plan_code', type: 'string' },
      { key: 'plan_description', type: 'string', synonyms: ['plan', 'plan_name'] },
      { key: 'monthly_fee', type: 'number', synonyms: ['fee', 'monthly'] },
      { key: 'included_minutes', type: 'int', synonyms: ['minutes'] },
      { key: 'service_area', type: 'string' },
      { key: 'contract_type', type: 'string' },
      { key: 'contract_terms', type: 'string', synonyms: ['terms'] },
      { key: 'dealer_code', type: 'string' },
      { key: 'account_number', type: 'string', synonyms: ['account_no', 'ban'] },
      { key: 'deposit_amount', type: 'number', synonyms: ['deposit'] },
      { key: 'memo', type: 'string', synonyms: ['note'] },
      { key: 'notes', type: 'string', synonyms: ['comments'] },
      { key: 'status', type: 'string' },
    ],
    examples: [
      ['555-201-1234', 'john.smith@example.com', '', 'Verizon', '2024-06-15', '917-555-0142', 'iPhone 15', '356789104563218', '8901410123456789012', 'UNL55', 'Unlimited 5G Start', '55.00', '0', 'National', 'Postpaid', '24 months', 'D-1188', 'VZ-88213377', '0', 'Ported from AT&T', '', 'active'],
      ['', '', 'Acme Wireless LLC', 'T-Mobile', '03/02/2025', '646-555-0177', 'Galaxy S24', '359881106754321', '', 'BIZ-UL', 'Business Unlimited', '42.50', '0', 'National', 'Postpaid', '12 months', '', '', '25', '', 'Second business line', 'active'],
    ],
  },
]

// ---------------------------------------------------------------------------
// Parsing / coercion helpers
// ---------------------------------------------------------------------------

function normHeader(h: string): string {
  return h.toLowerCase().trim().replace(/[\s\-\/.]+/g, '_').replace(/_+/g, '_')
}

function parseDateCell(raw: string): string | null {
  let y = 0, m = 0, d = 0
  let match = raw.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/)
  if (match) { y = +match[1]; m = +match[2]; d = +match[3] }
  else {
    match = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/)
    if (match) { m = +match[1]; d = +match[2]; y = +match[3] }
    else return null
  }
  if (m < 1 || m > 12 || d < 1 || d > 31 || y < 1900 || y > 2100) return null
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

const TRUTHY = ['y', 'yes', 'true', 't', '1']
const FALSY = ['n', 'no', 'false', 'f', '0']

function coerce(spec: FieldSpec, rawIn: string): { value: unknown; error?: string } {
  const raw = rawIn.trim()
  if (raw === '') return { value: spec.blank !== undefined ? spec.blank : null }
  switch (spec.type) {
    case 'string': {
      if (spec.oneOf) {
        const hit = spec.oneOf.find(o => o.toLowerCase() === raw.toLowerCase())
        return hit !== undefined
          ? { value: hit }
          : { value: null, error: `${spec.key} must be one of: ${spec.oneOf.join(', ')} (got "${raw}")` }
      }
      return { value: raw }
    }
    case 'number': {
      const n = parseFloat(raw.replace(/[$,\s]/g, ''))
      return Number.isFinite(n) ? { value: n } : { value: null, error: `${spec.key} is not a number: "${raw}"` }
    }
    case 'int': {
      const n = Number(raw.replace(/[$,\s]/g, ''))
      return Number.isInteger(n) ? { value: n } : { value: null, error: `${spec.key} must be a whole number: "${raw}"` }
    }
    case 'date': {
      const iso = parseDateCell(raw)
      return iso ? { value: iso } : { value: null, error: `${spec.key} must be YYYY-MM-DD or MM/DD/YYYY: "${raw}"` }
    }
    case 'boolean': {
      const l = raw.toLowerCase()
      if (TRUTHY.includes(l)) return { value: true }
      if (FALSY.includes(l)) return { value: false }
      return { value: null, error: `${spec.key} must be yes/no/true/false/1/0 (got "${raw}")` }
    }
  }
}

// ---------------------------------------------------------------------------
// Header auto-matching + required-column check
// ---------------------------------------------------------------------------

function autoMap(entity: EntityDef, headers: string[]): (string | null)[] {
  const claimed = new Set<string>()
  return headers.map(h => {
    const n = normHeader(h)
    if (!n) return null
    const f = entity.fields.find(f => f.key === n || (f.synonyms || []).includes(n))
    if (!f || claimed.has(f.key)) return null
    claimed.add(f.key)
    return f.key
  })
}

function missingRequired(entity: EntityDef, mapped: Set<string>): string[] {
  switch (entity.key) {
    case 'customers':
      return mapped.has('first_name') || mapped.has('company_name') ? [] : ['first_name or company_name']
    case 'products':
      return mapped.has('short_name') ? [] : ['short_name']
    case 'vendors':
      return mapped.has('legal_name') ? [] : ['legal_name']
    case 'activations':
      return mapped.has('carrier') || mapped.has('cell_number') ? [] : ['carrier or cell_number']
    case 'inventory': {
      const miss: string[] = []
      if (!mapped.has('upc') && !mapped.has('product_name')) miss.push('upc or product_name')
      if (!mapped.has('qty_on_hand')) miss.push('qty_on_hand')
      return miss
    }
  }
}

// ---------------------------------------------------------------------------
// Validation (client-side: coercion + required-field checks only — dedupe and
// reference resolution happen server-side in POST /api/v1/pos/import/{entity})
// ---------------------------------------------------------------------------

interface ImportRow {
  rowNum: number                       // 1-based data row number (after header)
  raw: Record<string, string>          // canonical field key -> raw cell text
  payload: Record<string, unknown>     // coerced row sent to the server
  errors: string[]
  result?: { status: 'imported' | 'skipped' | 'failed'; reason: string }
}

function validateRows(entity: EntityDef, headerMap: (FieldSpec | null)[], dataRows: { cells: string[]; rowNum: number }[]): ImportRow[] {
  const out: ImportRow[] = []

  for (const { cells, rowNum } of dataRows) {
    const raw: Record<string, string> = {}
    headerMap.forEach((spec, i) => { if (spec) raw[spec.key] = (cells[i] ?? '').trim() })

    const payload: Record<string, unknown> = {}
    const errors: string[] = []
    for (const spec of entity.fields) {
      if (!(spec.key in raw)) {
        if (spec.blank !== undefined) payload[spec.key] = spec.blank
        continue
      }
      const { value, error } = coerce(spec, raw[spec.key])
      if (error) errors.push(error)
      payload[spec.key] = value
    }

    // Per-row required checks (matches the mapping-level requirements).
    if (entity.key === 'customers') {
      if (!(raw.first_name || '').trim() && !(raw.company_name || '').trim()) errors.push('first_name or company_name is required')
    } else if (entity.key === 'products') {
      if (!payload.short_name) errors.push('short_name is required')
    } else if (entity.key === 'vendors') {
      if (!payload.legal_name) errors.push('legal_name is required')
    } else if (entity.key === 'activations') {
      if (!(raw.carrier || '').trim() && !(raw.cell_number || '').trim()) errors.push('carrier or cell_number is required')
    } else {
      // inventory quantities
      if ((raw.qty_on_hand ?? '') === '') errors.push('qty_on_hand is required')
      if (!(raw.upc || '').trim() && !(raw.product_name || '').trim()) errors.push('upc or product_name is required')
    }

    // Drop null values so server-side / DB column defaults apply.
    for (const k of Object.keys(payload)) if (payload[k] === null) delete payload[k]

    out.push({ rowNum, raw, payload, errors })
  }
  return out
}

// ---------------------------------------------------------------------------
// Download helpers
// ---------------------------------------------------------------------------

function downloadCsv(filename: string, rows: CsvCell[][]) {
  const blob = new Blob([serializeCsv(rows)], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function downloadTemplate(entity: EntityDef) {
  const header = entity.fields.map(f => f.key)
  const comment = ['# NOTE: delete the two example rows below before uploading. Rows starting with # are ignored. Keep the header row.']
  downloadCsv(`${entity.key}-template.csv`, [header, comment, ...entity.examples])
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type Step = 'choose' | 'map' | 'preview' | 'importing' | 'done'

interface ParsedFile {
  headers: string[]
  dataRows: { cells: string[]; rowNum: number }[]
}

interface ImportResponse {
  inserted: number
  skipped: { index: number; message: string }[]
  errors: { index: number; message: string }[]
  total: number
}

const panel: React.CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }
const input: React.CSSProperties = { padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13, background: 'var(--surface)', color: 'var(--text)', outline: 'none' }
const th: React.CSSProperties = { textAlign: 'left', padding: 8, fontSize: 11, fontWeight: 600, color: 'var(--text2)', textTransform: 'uppercase', whiteSpace: 'nowrap' }
const cell: React.CSSProperties = { padding: '7px 12px', borderBottom: '1px solid var(--border)', whiteSpace: 'nowrap' }
const chip = (color: string): React.CSSProperties => ({ color, border: `1px solid ${color}`, borderRadius: 6, padding: '3px 10px', fontSize: 12, fontWeight: 700 })

const GREEN = '#16a34a'
const RED = '#dc2626'
const AMBER = '#d97706'

export default function PosImportPage() {
  const [step, setStep] = useState<Step>('choose')
  const [entity, setEntity] = useState<EntityDef | null>(null)
  const [fileName, setFileName] = useState('')
  const [fileError, setFileError] = useState('')
  const [busy, setBusy] = useState(false)
  const [parsed, setParsed] = useState<ParsedFile | null>(null)
  const [mapping, setMapping] = useState<(string | null)[]>([])
  const [rows, setRows] = useState<ImportRow[]>([])
  const [matchedFields, setMatchedFields] = useState<string[]>([])
  const [skipErrors, setSkipErrors] = useState(false)
  const [progress, setProgress] = useState({ done: 0, total: 0 })
  const [storeCodes, setStoreCodes] = useState<string[]>([])
  const fileInputRef = useRef<HTMLInputElement>(null)
  const pendingEntityRef = useRef<EntityDef | null>(null)

  // Store codes come from MetricsPro storeops — shown in the inventory hint so
  // users know what store_code values are valid.
  useEffect(() => {
    apiCached('/api/v1/storeops/stores', LOOKUP)
      .then((stores: { store_code?: string | number | null }[]) => {
        const codes = (stores || []).map(s => String(s.store_code ?? '').trim()).filter(Boolean)
        setStoreCodes(codes)
      })
      .catch(() => {})
  }, [])

  const counts = useMemo(() => {
    let valid = 0, errored = 0
    for (const r of rows) {
      if (r.errors.length > 0) errored++
      else valid++
    }
    return { valid, errored, total: rows.length }
  }, [rows])

  const summary = useMemo(() => {
    let imported = 0, skipped = 0, failed = 0
    for (const r of rows) {
      if (r.result?.status === 'imported') imported++
      else if (r.result?.status === 'skipped') skipped++
      else if (r.result?.status === 'failed') failed++
    }
    return { imported, skipped, failed }
  }, [rows])

  const mappedSet = useMemo(() => new Set(mapping.filter((m): m is string => m !== null)), [mapping])
  const mapMissing = entity ? missingRequired(entity, mappedSet) : []

  function reset() {
    setStep('choose')
    setEntity(null)
    setParsed(null)
    setMapping([])
    setRows([])
    setFileName('')
    setFileError('')
    setSkipErrors(false)
    setMatchedFields([])
    setProgress({ done: 0, total: 0 })
  }

  function pickFile(e: EntityDef) {
    pendingEntityRef.current = e
    fileInputRef.current?.click()
  }

  async function handleFile(file: File) {
    const ent = pendingEntityRef.current
    if (!ent) return
    setBusy(true)
    setFileError('')
    setFileName(file.name)
    setSkipErrors(false)
    setRows([])
    try {
      const text = await file.text()
      const grid = parseCsv(text)
      if (grid.length === 0) throw new Error('The file is empty.')
      const headers = grid[0]
      const dataRows = grid.slice(1)
        .map((cells, i) => ({ cells, rowNum: i + 1 }))
        .filter(r => !(r.cells[0] || '').trim().startsWith('#'))       // comment rows
        .filter(r => r.cells.some(c => c.trim() !== ''))               // blank rows
      if (dataRows.length === 0) throw new Error('No data rows found (comment rows starting with # are ignored).')
      setEntity(ent)
      setParsed({ headers, dataRows })
      setMapping(autoMap(ent, headers))
      setStep('map')
    } catch (err) {
      setFileError(err instanceof Error ? err.message : 'Could not read the file.')
      setStep('choose')
    }
    setBusy(false)
  }

  function setColMapping(idx: number, key: string) {
    setMapping(prev => prev.map((m, i) => {
      if (i === idx) return key || null
      return m === key ? null : m // a target field can only be fed by one column
    }))
  }

  function applyMapping() {
    if (!entity || !parsed) return
    const headerMap = mapping.map(k => (k ? entity.fields.find(f => f.key === k) || null : null))
    setMatchedFields(entity.fields.filter(f => mappedSet.has(f.key)).map(f => f.key))
    setRows(validateRows(entity, headerMap, parsed.dataRows))
    setStep('preview')
  }

  async function runImport() {
    if (!entity) return
    setStep('importing')

    const next = rows.map(r => ({ ...r, result: undefined as ImportRow['result'] }))
    // Rows with client-side validation errors are never sent.
    for (const r of next) {
      if (r.errors.length > 0) r.result = { status: 'failed', reason: r.errors.join('; ') }
    }
    const pending = next.filter(r => !r.result)
    setProgress({ done: 0, total: pending.length })

    // The server dedupes and inserts; max 5000 rows per request, so chunk
    // larger files and merge results. Response indexes are relative to each
    // chunk, which slicing the pending list handles naturally.
    for (let start = 0; start < pending.length; start += CHUNK_SIZE) {
      const chunk = pending.slice(start, start + CHUNK_SIZE)
      try {
        const res: ImportResponse = await api(`/api/v1/pos/import/${entity.key}`, {
          method: 'POST',
          body: JSON.stringify({ rows: chunk.map(r => r.payload) }),
        })
        for (const s of res.skipped || []) {
          const row = chunk[s.index]
          if (row) row.result = { status: 'skipped', reason: s.message || 'skipped as duplicate' }
        }
        for (const e of res.errors || []) {
          const row = chunk[e.index]
          if (row) row.result = { status: 'failed', reason: e.message || 'insert failed' }
        }
        for (const r of chunk) if (!r.result) r.result = { status: 'imported', reason: '' }
      } catch (err) {
        const msg = err instanceof Error ? err.message : 'import request failed'
        for (const r of chunk) if (!r.result) r.result = { status: 'failed', reason: msg }
      }
      setProgress({ done: Math.min(start + CHUNK_SIZE, pending.length), total: pending.length })
    }

    setRows(next)
    setStep('done')
  }

  // Error report: original raw values + import_status + reason. Merges the
  // server's skipped/errors with client-side validation failures.
  function downloadErrorReport() {
    if (!entity) return
    const bad = rows.filter(r => r.result && r.result.status !== 'imported')
    const header = [...entity.fields.map(f => f.key), 'import_status', 'reason']
    const body = bad.map(r => [
      ...entity.fields.map(f => r.raw[f.key] ?? ''),
      r.result!.status,
      r.result!.reason,
    ])
    downloadCsv(`${entity.key}-import-errors.csv`, [header, ...body])
  }

  const importDisabled = counts.valid === 0 || (counts.errored > 0 && !skipErrors)

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📥 Data Import</h1>
          <p className="pg-note" style={{ color: 'var(--text2)', fontSize: 14, margin: '4px 0 0' }}>
            Migrate POS data from another system via CSV{entity ? ` · ${entity.title}` : ''}
          </p>
        </div>
        {step !== 'choose' && step !== 'importing' && (
          <button className="btn btn-secondary" onClick={reset}>Start Over</button>
        )}
      </div>

      <input ref={fileInputRef} type="file" accept=".csv,.txt,text/csv,text/plain" style={{ display: 'none' }}
        onChange={e => {
          const f = e.target.files?.[0]
          e.target.value = ''
          if (f) handleFile(f)
        }} />

      {/* STEP 1: choose entity */}
      {step === 'choose' && (
        <>
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 14, color: 'var(--text2)' }}>
              Download a CSV template, fill it with data exported from your old POS, then upload it here — you&apos;ll get to map your columns to ours before anything is imported.
              Recommended order: <strong style={{ color: 'var(--text)' }}>Vendors → Products → Inventory quantities → Customers → Activations</strong>.
              Existing records are never overwritten — duplicates are skipped and reported.
            </div>
            <div style={{ fontSize: 12, color: AMBER, marginTop: 6 }}>{EXCEL_HINT}. Files must be .csv or .txt.</div>
          </div>

          {fileError && (
            <div style={{ ...panel, borderColor: RED, color: RED, fontSize: 13, marginBottom: 14 }}>{fileError}</div>
          )}
          {busy && (
            <div style={{ ...panel, marginBottom: 14, display: 'flex', alignItems: 'center', gap: 10, fontSize: 13, color: 'var(--text2)' }}>
              <div className="spinner" /> Reading file…
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 14 }}>
            {ENTITIES.map(e => (
              <div key={e.key} style={{ ...panel, display: 'flex', flexDirection: 'column', gap: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontSize: 22 }}>{e.icon}</span>
                  <span style={{ fontSize: 15, fontWeight: 700 }}>{e.title}</span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5, flex: 1 }}>
                  {e.blurb}
                  {e.note && (
                    <div style={{ marginTop: 8, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 10px', color: AMBER, fontSize: 11, lineHeight: 1.5 }}>
                      {e.note}
                      {e.key === 'inventory' && storeCodes.length > 0 && (
                        <div style={{ marginTop: 4, color: 'var(--text2)' }}>
                          Your store codes: {storeCodes.join(', ')}
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 11, color: 'var(--text3)' }}>{e.fields.length} columns · {EXCEL_HINT}</div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-secondary" style={{ flex: 1 }} onClick={() => downloadTemplate(e)}>⬇ Download template</button>
                  <button className="btn btn-primary" style={{ flex: 1 }} disabled={busy} onClick={() => pickFile(e)}>⬆ Upload CSV</button>
                </div>
              </div>
            ))}

            {/* Stores are NOT an import entity here — they belong to MetricsPro. */}
            <div style={{ ...panel, display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 22 }}>🏬</span>
                <span style={{ fontSize: 15, fontWeight: 700 }}>Store Locations</span>
              </div>
              <div style={{ fontSize: 12, color: 'var(--text2)', lineHeight: 1.5, flex: 1 }}>
                Stores are managed in MetricsPro (Workforce → Stores) and are not imported here.
                Inventory rows reference them by <code>store_code</code>.
              </div>
            </div>
          </div>
        </>
      )}

      {/* STEP 2: column mapping */}
      {step === 'map' && entity && parsed && (
        <>
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 16, fontWeight: 700 }}>{entity.icon} {entity.title} — map your columns</div>
            <div style={{ fontSize: 13, color: 'var(--text2)', marginTop: 4 }}>
              {fileName} · {parsed.headers.length} columns · {parsed.dataRows.length} data rows.
              Match each column in your file to one of our fields, or skip it — skipped columns are simply ignored.
            </div>
          </div>

          <div style={{ ...panel, padding: 0, overflow: 'hidden', marginBottom: 14 }}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    <th style={th}>Column in your file</th>
                    <th style={th}>Sample (first row)</th>
                    <th style={th}>Imports into</th>
                  </tr>
                </thead>
                <tbody>
                  {parsed.headers.map((h, i) => (
                    <tr key={i}>
                      <td style={{ ...cell, fontWeight: 600 }}>{h.trim() || `(column ${i + 1})`}</td>
                      <td style={{ ...cell, color: 'var(--text2)', maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {(parsed.dataRows[0]?.cells[i] ?? '').trim() || '—'}
                      </td>
                      <td style={cell}>
                        <select value={mapping[i] ?? ''} onChange={e => setColMapping(i, e.target.value)}
                          style={{ ...input, minWidth: 220, color: mapping[i] ? 'var(--text)' : 'var(--text3)' }}>
                          <option value="">— skip this column —</option>
                          {entity.fields.map(f => (
                            <option key={f.key} value={f.key}>{f.key} ({f.type})</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {mapMissing.length > 0 && (
            <div style={{ ...panel, borderColor: RED, color: RED, fontSize: 12, marginBottom: 12 }}>
              Required field(s) not mapped yet: <strong>{mapMissing.join('; ')}</strong>. Map a column to them to continue.
            </div>
          )}

          <div style={{ ...panel, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 12, color: 'var(--text3)' }}>
              {mappedSet.size} of {parsed.headers.length} columns mapped · unmapped columns will be ignored
            </span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-secondary" onClick={reset}>Back</button>
            <button className="btn btn-primary" disabled={mapMissing.length > 0} onClick={applyMapping}>
              Continue to validation →
            </button>
          </div>
        </>
      )}

      {/* STEP 3: preview + validation */}
      {step === 'preview' && entity && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 16, fontWeight: 700 }}>{entity.icon} {entity.title} — {fileName}</span>
            <span style={chip('var(--text2)')}>{counts.total} rows</span>
            <span style={chip(GREEN)}>{counts.valid} valid</span>
            <span style={chip(RED)}>{counts.errored} with errors</span>
          </div>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12 }}>
            Duplicates are detected during import and reported as skipped — existing records are never overwritten.
          </div>

          <div style={{ ...panel, padding: 0, overflow: 'hidden', marginBottom: 14 }}>
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: 12, color: 'var(--text3)' }}>
              Preview — first {Math.min(20, rows.length)} of {rows.length} rows
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ background: 'var(--surface2)' }}>
                    <th style={th}>Row</th>
                    <th style={th}>Status</th>
                    {matchedFields.map(k => <th key={k} style={th}>{k}</th>)}
                    <th style={th}>Problems</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, 20).map(r => {
                    const bad = r.errors.length > 0
                    return (
                      <tr key={r.rowNum}>
                        <td style={{ ...cell, color: 'var(--text3)' }}>{r.rowNum}</td>
                        <td style={{ ...cell, color: bad ? RED : GREEN, fontWeight: 700 }}>{bad ? 'error' : 'valid'}</td>
                        {matchedFields.map(k => (
                          <td key={k} style={{ ...cell, color: 'var(--text2)', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.raw[k] ?? ''}</td>
                        ))}
                        <td style={{ ...cell, color: RED, fontSize: 11, maxWidth: 320, whiteSpace: 'normal' }}>
                          {r.errors.join('; ')}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>

          <div style={{ ...panel, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            {counts.errored > 0 && (
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, cursor: 'pointer' }}>
                <input type="checkbox" checked={skipErrors} onChange={e => setSkipErrors(e.target.checked)} />
                Skip the {counts.errored} errored row{counts.errored === 1 ? '' : 's'} and import the rest
              </label>
            )}
            <div style={{ flex: 1 }} />
            <button className="btn btn-secondary" onClick={() => setStep('map')}>← Back to mapping</button>
            <button className="btn btn-primary" disabled={importDisabled} onClick={runImport}>
              Import {counts.valid} row{counts.valid === 1 ? '' : 's'}
            </button>
          </div>
          {importDisabled && counts.errored > 0 && !skipErrors && (
            <div style={{ fontSize: 12, color: RED, marginTop: 8 }}>
              Fix the errored rows in your CSV and re-upload, or check &quot;skip errored rows&quot; to import only the valid ones.
            </div>
          )}
        </>
      )}

      {/* STEP 4: importing */}
      {step === 'importing' && entity && (
        <div style={{ ...panel, padding: 30, textAlign: 'center' }}>
          <div style={{ fontSize: 24, marginBottom: 10 }}>{entity.icon}</div>
          <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 14 }}>
            Importing {entity.title.toLowerCase()}… {progress.done} / {progress.total}
          </div>
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, height: 10, overflow: 'hidden', maxWidth: 420, margin: '0 auto' }}>
            <div style={{ background: GREEN, height: '100%', width: `${progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%`, transition: 'width 0.2s' }} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--text3)', marginTop: 12 }}>
            Uploading in chunks of up to {CHUNK_SIZE.toLocaleString()} rows — do not close this page.
          </div>
        </div>
      )}

      {/* STEP 5: results */}
      {step === 'done' && entity && (
        <div style={{ ...panel, padding: 30, textAlign: 'center' }}>
          <div style={{ fontSize: 28, marginBottom: 8 }}>{summary.failed === 0 ? '✅' : '⚠️'}</div>
          <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 18 }}>{entity.title} import finished</div>
          <div style={{ display: 'flex', gap: 14, justifyContent: 'center', flexWrap: 'wrap', marginBottom: 22 }}>
            {([
              [summary.imported, 'imported', GREEN],
              [summary.skipped, 'skipped (duplicates)', AMBER],
              [summary.failed, 'failed', RED],
            ] as const).map(([n, label, color]) => (
              <div key={label} style={{ background: 'var(--surface)', border: `1px solid ${color}`, borderRadius: 8, padding: '14px 24px' }}>
                <div style={{ fontSize: 24, fontWeight: 700, color }}>{n}</div>
                <div style={{ fontSize: 12, color: 'var(--text2)' }}>{label}</div>
              </div>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 10, justifyContent: 'center', flexWrap: 'wrap' }}>
            {(summary.skipped > 0 || summary.failed > 0) && (
              <button className="btn btn-secondary" onClick={downloadErrorReport}>⬇ Download error report</button>
            )}
            <button className="btn btn-primary" onClick={reset}>Import more data</button>
          </div>
        </div>
      )}
    </div>
  )
}
