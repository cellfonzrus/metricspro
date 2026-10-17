'use client'
// THE product form — one component for every POS screen that creates or edits a product
// (owner 2026-10-16: "in the pos menu, let the user edit the product description fields and add a
// product to the inventory if a new product is being entered manually, prompt the user to create
// the new product if the product is not found").
//
// WHY IT EXISTS. The catalog page (pos/products/page.tsx) already carried the whole form inline.
// The register now needs the SAME form in two more places — "this product is not in the catalog,
// create it" from the picker, and "fix this product's description" from a cart line. Copying the
// field list into the register would have been a second definition of what a POS product IS: the
// day someone adds a field to one, the other screen silently stops writing it. So the fields, the
// defaults, the empty form and the shape of the PATCH/POST body live HERE and both screens
// dereference them — the same discipline product-filters.tsx applies to the filters.
//
// What this file does NOT own: the catalog's own department / category / system-category EDITOR
// (renaming and switching those off stays on the catalog page, which is where they are managed),
// and the write itself — a caller still POSTs or PATCHes /pos/products so the register's create
// flow can chain its own "and put it in inventory" step onto the result.
//
// Locked by backend/harness_pos_product_form_lock.py.
import type { CSSProperties } from 'react'
import type { ProductFilterOptions } from './product-filters'

export const BODY_STYLES = ['Not Set', 'Bar', 'Flip', 'Slider', 'Tablet'] as const

/** The description fields of a POS product — the editable ones, in the order the form shows them. */
export interface ProductFormValue {
  upc: string
  short_name: string
  full_name: string
  department_id: string
  category_id: string
  system_category: string
  inventory_type: string
  manufacturer: string
  cost: number
  retail_price: number
  is_taxable: boolean
  calculate_as_profit: boolean
  body_style: string
  is_active: boolean
  end_of_life: boolean
}

export const EMPTY_PRODUCT_FORM: ProductFormValue = {
  upc: '', short_name: '', full_name: '', department_id: '', category_id: '',
  system_category: 'Accessory', inventory_type: 'standard', manufacturer: '',
  cost: 0, retail_price: 0, is_taxable: true, calculate_as_profit: true,
  body_style: 'Not Set', is_active: true, end_of_life: false,
}

/** A product row as the API returns it — every field optional and loosely typed, because the two
 *  screens model the row slightly differently and the mapper below is what reconciles them. */
export type ProductRowLike = { [K in keyof ProductFormValue]?: unknown }

/** A product row (as GET /pos/products returns it) → the form's own value. Every blank is spelled
 *  here once, so no screen has to remember that a null `body_style` shows as "Not Set". */
export function productFormFrom(p: ProductRowLike): ProductFormValue {
  return {
    upc: String(p.upc ?? ''),
    short_name: String(p.short_name ?? ''),
    full_name: String(p.full_name ?? ''),
    department_id: String(p.department_id ?? ''),
    category_id: String(p.category_id ?? ''),
    system_category: String(p.system_category ?? '') || EMPTY_PRODUCT_FORM.system_category,
    inventory_type: String(p.inventory_type ?? '') || 'standard',
    manufacturer: String(p.manufacturer ?? ''),
    cost: Number(p.cost ?? 0) || 0,
    retail_price: Number(p.retail_price ?? 0) || 0,
    is_taxable: p.is_taxable !== false,
    calculate_as_profit: p.calculate_as_profit !== false,
    body_style: String(p.body_style ?? '') || 'Not Set',
    is_active: p.is_active !== false,
    end_of_life: p.end_of_life === true,
  }
}

/** THE body a POST / PATCH /pos/products is sent. "Not Set" is the form's way of saying "no body
 *  style"; the backend's own `_clean` turns it into NULL, and spelling it here too means a screen
 *  that forgets cannot send the literal string. */
export function productFormPayload(v: ProductFormValue): Record<string, unknown> {
  return { ...v, body_style: v.body_style === 'Not Set' ? null : v.body_style }
}

/** Is this form complete enough to save? The ONE answer, so no screen enables its Save button on a
 *  looser rule than the backend's (`short_name required`). */
export function productFormReady(v: ProductFormValue): boolean {
  return !!v.short_name.trim()
}

/** What a scanned / typed entry that matched nothing should become. A pure barcode goes in the UPC
 *  field with the name left for the cashier to type; anything else is read as the name. Keeping
 *  this here means the register and any later caller split "is this a barcode" the same way. */
export function productFormFromEntry(entry: string): ProductFormValue {
  const q = (entry || '').trim()
  const isBarcode = /^[0-9]{6,}$/.test(q)
  return { ...EMPTY_PRODUCT_FORM, upc: isBarcode ? q : '', short_name: isBarcode ? '' : q }
}

const input: CSSProperties = {
  padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13,
  background: 'var(--surface)', color: 'var(--text)', width: '100%', outline: 'none',
}
const label: CSSProperties = { fontSize: 12, color: 'var(--text2)', marginBottom: 3, display: 'block' }
const panel: CSSProperties = { background: 'var(--surface2)', borderRadius: 8, padding: 14, border: '1px solid var(--border)' }

/**
 * The product's description fields. Both the catalog's New/Edit modal and the register's
 * create-this-product modal render THIS, so the two can never offer different fields.
 *
 * `options` are the org's own department / category / system-category lists — the same
 * ProductFilterOptions the filters take, fetched once per page by fetchProductFilterOptions().
 */
export default function ProductFormFields({
  value, onChange, options, includeInactiveSystemCategories = false,
}: {
  value: ProductFormValue
  onChange: (v: ProductFormValue) => void
  options: ProductFilterOptions
  /** The catalog keeps a switched-off system category selectable so a product already on one is
   *  not silently re-stamped; the register offers only active ones. */
  includeInactiveSystemCategories?: boolean
}) {
  const set = (patch: Partial<ProductFormValue>) => onChange({ ...value, ...patch })
  // Category narrowed by the chosen department (the filters' own rule); no department = every category.
  const cats = value.department_id
    ? options.categories.filter(c => c.department_id === value.department_id)
    : options.categories
  const sysCats = options.systemCategories.filter(s => s.is_active || includeInactiveSystemCategories)
  const margin = value.cost > 0 && value.retail_price > 0
    ? `$${(value.retail_price - value.cost).toFixed(2)} (${(((value.retail_price - value.cost) / value.retail_price) * 100).toFixed(1)}%)`
    : ''

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <label style={label}>Short Name *</label>
        <input value={value.short_name} onChange={e => set({ short_name: e.target.value })} style={input} placeholder="e.g. iPhone 15 Pro" />
      </div>
      <div>
        <label style={label}>Full Name</label>
        <input value={value.full_name} onChange={e => set({ full_name: e.target.value })} style={input} placeholder="Full description" />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div>
          <label style={label}>UPC / Barcode</label>
          <input value={value.upc} onChange={e => set({ upc: e.target.value })} style={input} placeholder="Scan or type UPC" />
        </div>
        <div>
          <label style={label}>Manufacturer / Provider</label>
          <input value={value.manufacturer} onChange={e => set({ manufacturer: e.target.value })} style={input} placeholder="e.g. Apple" />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <div>
          <label style={label}>Department</label>
          <select value={value.department_id} onChange={e => set({ department_id: e.target.value, category_id: '' })} style={input}>
            <option value="">-- Select --</option>
            {options.departments.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
          </select>
        </div>
        <div>
          <label style={label}>Category</label>
          <select value={value.category_id} onChange={e => set({ category_id: e.target.value })} style={input}>
            <option value="">-- Select --</option>
            {cats.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10 }}>
        <div>
          <label style={label}>System Category</label>
          <select value={value.system_category} onChange={e => set({ system_category: e.target.value })} style={input}>
            {sysCats.map(c => <option key={c.id}>{c.name}</option>)}
            {/* Keep an inactive value the product already carries selectable, or opening the form
                would silently re-stamp it to the first option. */}
            {value.system_category && !sysCats.some(c => c.name === value.system_category) && (
              <option key="current">{value.system_category}</option>
            )}
          </select>
        </div>
        <div>
          <label style={label}>Inventory Type</label>
          <select value={value.inventory_type} onChange={e => set({ inventory_type: e.target.value })} style={input}>
            <option value="standard">Standard (qty-based)</option>
            <option value="serial">Serial Tracked (IMEI)</option>
          </select>
        </div>
        <div>
          <label style={label}>Body Style</label>
          <select value={value.body_style} onChange={e => set({ body_style: e.target.value })} style={input}>
            {BODY_STYLES.map(s => <option key={s}>{s}</option>)}
          </select>
        </div>
      </div>
      <div style={panel}>
        <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--text2)', marginBottom: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Pricing</div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <div>
            <label style={label}>Cost</label>
            <input type="number" step="0.01" value={value.cost} onChange={e => set({ cost: parseFloat(e.target.value) || 0 })} style={input} />
          </div>
          <div>
            <label style={label}>Retail Price</label>
            <input type="number" step="0.01" value={value.retail_price} onChange={e => set({ retail_price: parseFloat(e.target.value) || 0 })} style={input} />
          </div>
        </div>
        {margin && <div style={{ marginTop: 8, fontSize: 12, color: 'var(--text2)' }}>Margin: {margin}</div>}
      </div>
      <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
          <input type="checkbox" checked={value.is_taxable} onChange={e => set({ is_taxable: e.target.checked })} />
          Taxable
        </label>
        {([['Calculate as profit', 'calculate_as_profit'], ['Active', 'is_active'], ['End of life', 'end_of_life']] as const).map(([lbl, key]) => (
          <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer' }}>
            <input type="checkbox" checked={value[key]} onChange={e => set({ [key]: e.target.checked } as Partial<ProductFormValue>)} />
            {lbl}
          </label>
        ))}
      </div>
    </div>
  )
}
