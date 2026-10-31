/**
 * THE browser's reader of the device condition and unit status lists (owner ask 2026-10-31,
 * index §POS-18).
 *
 * Owner: *"cpo is certified pre owned is adifferent device than new , imnei is unique"*.
 *
 * WHY THIS FILE EXISTS. The inventory screen carried both vocabularies as literal arrays, two
 * lines apart, while the same facts were also written as `CHECK` constraints on
 * `pos.inventory_serial` (mig 725, widened by mig 1018). Adding a condition therefore meant
 * editing a page and a constraint with nothing checking that the two agreed — the duplicate
 * defect CLAUDE.md forbids, and the reason a refurbished handset could read as new.
 *
 * Each list now has ONE home on the server — `pos/device_condition.py` and
 * `pos/inventory_integrity.UNIT_STATUSES` — served by `GET /pos/device-conditions`. This file
 * holds only what the browser legitimately decides: keeping a selection valid when the list
 * changes. No condition or status word is written here.
 */

/** One condition as the server describes it. */
export type DeviceCondition = {
  key: string
  label: string
  /** true for the one condition a device carrying no condition marker is taken to be */
  unmarked: boolean
}

/** One unit status as the server describes it. */
export type UnitStatus = { key: string; label: string }

export type DeviceVocabulary = {
  conditions: DeviceCondition[]
  statuses: UnitStatus[]
  unmarked_condition: string
}

/**
 * What the screen has before the server answers. Deliberately EMPTY: a guessed list would be the
 * third copy of the vocabulary this file exists to remove, and an empty dropdown for the
 * half-second before the real list lands is honest, where a wrong list is not.
 */
export const EMPTY_VOCABULARY: DeviceVocabulary = {
  conditions: [], statuses: [], unmarked_condition: '',
}

/** Load both lists. On any failure the caller keeps whatever it had. */
export async function loadDeviceVocabulary(
  fetcher: (url: string) => Promise<Partial<DeviceVocabulary> | null>,
): Promise<DeviceVocabulary> {
  const r = await fetcher('/api/v1/pos/device-conditions')
  return {
    conditions: (r?.conditions || []) as DeviceCondition[],
    statuses: (r?.statuses || []) as UnitStatus[],
    unmarked_condition: String(r?.unmarked_condition || ''),
  }
}

/**
 * Keep a chosen key valid when the list arrives or changes. The current choice wins if it is still
 * offered; otherwise the list's own default — the unmarked condition, or the first status — rather
 * than a word this file would have to name.
 */
export function keepValid(chosen: string, offered: { key: string }[], fallback = ''): string {
  if (offered.some(o => o.key === chosen)) return chosen
  if (fallback && offered.some(o => o.key === fallback)) return fallback
  return offered.length ? offered[0].key : chosen
}
