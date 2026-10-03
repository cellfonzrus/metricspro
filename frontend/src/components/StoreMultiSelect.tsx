'use client'
import { CheckboxDropdown, type CheckboxOption } from '@/components/CheckboxDropdown'

// ── Picking WHICH STORES a setting applies to ───────────────────────────────────────────────────
// OWNER 2026-10-03: *"in store setup to assign the store it should be a drop down list to select
// multiple stores"* — and the fleet already had the answer: `components/CheckboxDropdown` is THE
// checkbox-under-a-dropdown multi-select built for the 2026-08-04 directive ("the store picker needs
// to have check box under the drop down to pick multiple stores"), which that directive called
// fleet-wide and retroactive. Store Setup never got it: its screens hand-rolled flat checkbox walls.
//
// THE CLASS, NOT THE INSTANCE (CLAUDE.md, "A fix is a DESIGN fix"): the wrong general fact was not
// "the insurance policy screen lists stores badly" — it is that *every* "which stores does this apply
// to" control re-invented its own picker, so they drift (one has a filter, one does not, one shows
// inactive stores, one does not). This is the ONE adapter: it turns a store roster into the shared
// dropdown's options, and every setup surface dereferences it instead of mapping stores itself.
// `harness_closing_source_lock.py` FAILS THE BUILD if a setup screen grows its own store checkbox.
export function storePickerOptions(stores: any[]): CheckboxOption[] {
  return (stores || [])
    .filter((s: any) => String(s?.store_code || '').trim())
    .map((s: any) => ({
      id: String(s.store_code).trim(),
      label: String(s.store_code).trim(),
      // The address is how an admin recognises a store, and "(inactive)" has to stay visible: a
      // policy or a setting may legitimately still cover a store that is closed.
      sublabel: [s.address || '', s.is_active === false ? '(inactive)' : ''].filter(Boolean).join(' '),
    }))
}

export function StoreMultiSelect({ stores, value, onChange, width = 260, placeholder = 'Select stores…', disabled = false }:
  { stores: any[]; value: string[]; onChange: (codes: string[]) => void; width?: number | string; placeholder?: string; disabled?: boolean }) {
  return (
    <CheckboxDropdown options={storePickerOptions(stores)} value={value || []} onChange={onChange}
      placeholder={placeholder} width={width} ariaLabel="Stores" disabled={disabled} />
  )
}
