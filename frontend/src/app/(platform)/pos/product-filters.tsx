'use client'
// THE product filters — one component for every POS screen that lists products (owner 2026-09-24:
// "create product filters on the pos page"). The register's product picker (pos/sales/page.tsx) and the
// catalog (pos/products/page.tsx) both render THIS and build their query with productFilterParams, so the
// two screens can never offer different filters or spell a param differently. The filtering itself is
// server-side — GET /pos/products → backend pos/product_filters.py, the one filter helper — so it reaches
// past the 500-row list cap. The option lists are the org's own departments / categories (GET
// /pos/catalog), system categories (GET /pos/system-categories, mig 745) and manufacturers (GET
// /pos/products/manufacturers). Locked by backend/harness_pos_product_filters_lock.py.
import type { CSSProperties } from 'react'
import { api } from '@/lib/client'

export interface FilterDepartment { id: string; short_name: string }
export interface FilterCategory { id: string; name: string; department_id: string | null }
export interface FilterSystemCategory { id: string; name: string; is_active: boolean }

export interface ProductFilterOptions {
  departments: FilterDepartment[]
  categories: FilterCategory[]
  systemCategories: FilterSystemCategory[]
  manufacturers: string[]
}

export interface ProductFilterValue {
  department_id: string
  category_id: string
  system_category: string
  manufacturer: string
  inventory_type: '' | 'standard' | 'serial'
  in_stock: boolean
}

export const EMPTY_PRODUCT_FILTERS: ProductFilterValue = {
  department_id: '', category_id: '', system_category: '', manufacturer: '', inventory_type: '', in_stock: false,
}

export const EMPTY_FILTER_OPTIONS: ProductFilterOptions = { departments: [], categories: [], systemCategories: [], manufacturers: [] }

export function hasProductFilters(v: ProductFilterValue): boolean {
  return !!(v.department_id || v.category_id || v.system_category || v.manufacturer || v.inventory_type || v.in_stock)
}

/** Adds the filters in force to `params` — THE one place a product-filter query string is spelled.
 *  `storeCode` narrows "in stock" to that store (the register's active store); omitted = any store. */
export function productFilterParams(v: ProductFilterValue, params: URLSearchParams, storeCode?: string | null): URLSearchParams {
  if (v.department_id) params.set('department_id', v.department_id)
  if (v.category_id) params.set('category_id', v.category_id)
  if (v.system_category) params.set('system_category', v.system_category)
  if (v.manufacturer) params.set('manufacturer', v.manufacturer)
  if (v.inventory_type) params.set('inventory_type', v.inventory_type)
  if (v.in_stock) {
    params.set('in_stock', 'true')
    if (storeCode) params.set('store_code', storeCode)
  }
  return params
}

export async function fetchManufacturers(): Promise<string[]> {
  try {
    const r = await api('/api/v1/pos/products/manufacturers')
    return r.manufacturers || []
  } catch { return [] }
}

/** All four option lists, fetched in parallel. Each failure degrades to an empty list, never a crash. */
export async function fetchProductFilterOptions(): Promise<ProductFilterOptions> {
  const [cat, sys, manufacturers] = await Promise.all([
    api('/api/v1/pos/catalog').catch(() => ({})),
    api('/api/v1/pos/system-categories').catch(() => ({})),
    fetchManufacturers(),
  ])
  return {
    departments: cat?.departments || [],
    categories: cat?.categories || [],
    systemCategories: sys?.system_categories || [],
    manufacturers,
  }
}

const sel: CSSProperties = {
  padding: '7px 10px', borderRadius: 7, border: '1px solid var(--border)', fontSize: 13,
  background: 'var(--surface)', color: 'var(--text)', outline: 'none', minWidth: 0,
}

export default function ProductFilters({
  value, onChange, options, inStockLabel = 'In stock only', includeInactiveSystemCategories = false, style,
}: {
  value: ProductFilterValue
  onChange: (v: ProductFilterValue) => void
  options: ProductFilterOptions
  inStockLabel?: string
  /** The catalog page may still need to find products left on a switched-off system category (they are
   *  never silently recategorised), so it lists those too, marked "(off)". The register offers only
   *  ACTIVE ones — the rule the product form's picker uses. */
  includeInactiveSystemCategories?: boolean
  style?: CSSProperties
}) {
  const set = (patch: Partial<ProductFilterValue>) => onChange({ ...value, ...patch })
  // Category narrowed by the chosen department (the product form's rule); no department = every category.
  const cats = value.department_id
    ? options.categories.filter(c => c.department_id === value.department_id)
    : options.categories
  const sysCats = options.systemCategories.filter(s => s.is_active || includeInactiveSystemCategories)

  function pickDepartment(department_id: string) {
    const keep = !value.category_id
      || !department_id
      || options.categories.some(c => c.id === value.category_id && c.department_id === department_id)
    set({ department_id, category_id: keep ? value.category_id : '' })
  }

  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', ...style }}>
      <select aria-label="Department" value={value.department_id} onChange={e => pickDepartment(e.target.value)} style={{ ...sel, width: 160 }}>
        <option value="">Department: any</option>
        {options.departments.map(d => <option key={d.id} value={d.id}>{d.short_name}</option>)}
      </select>
      <select aria-label="Category" value={value.category_id} onChange={e => set({ category_id: e.target.value })} style={{ ...sel, width: 160 }}>
        <option value="">Category: any</option>
        {cats.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <select aria-label="System category" value={value.system_category} onChange={e => set({ system_category: e.target.value })} style={{ ...sel, width: 180 }}>
        <option value="">System category: any</option>
        {sysCats.map(c => <option key={c.id} value={c.name}>{c.name}{c.is_active ? '' : ' (off)'}</option>)}
      </select>
      <select aria-label="Manufacturer" value={value.manufacturer} onChange={e => set({ manufacturer: e.target.value })} style={{ ...sel, width: 170 }}>
        <option value="">Manufacturer: any</option>
        {options.manufacturers.map(m => <option key={m} value={m}>{m}</option>)}
      </select>
      <select aria-label="Inventory type" value={value.inventory_type} onChange={e => set({ inventory_type: e.target.value as ProductFilterValue['inventory_type'] })} style={{ ...sel, width: 150 }}>
        <option value="">Type: any</option>
        <option value="serial">📱 Serial</option>
        <option value="standard">📦 Standard</option>
      </select>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, cursor: 'pointer', whiteSpace: 'nowrap' }}>
        <input type="checkbox" checked={value.in_stock} onChange={e => set({ in_stock: e.target.checked })} />
        {inStockLabel}
      </label>
      <button type="button" className="btn btn-secondary" disabled={!hasProductFilters(value)}
        onClick={() => onChange({ ...EMPTY_PRODUCT_FILTERS })}>Clear filters</button>
    </div>
  )
}
