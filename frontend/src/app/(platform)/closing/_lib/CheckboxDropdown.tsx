// PROMOTED to `@/components/CheckboxDropdown` (2026-08-10) — see the note in `./market-store-cascade`.
// Re-export only, so the closing pages keep their existing import path and there is still exactly ONE
// implementation of the checkbox multi-select. New code should import from `@/components/CheckboxDropdown`.
export { CheckboxDropdown, default } from '@/components/CheckboxDropdown'
export type { CheckboxOption } from '@/components/CheckboxDropdown'
