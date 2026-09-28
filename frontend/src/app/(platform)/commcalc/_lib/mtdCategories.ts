// THE Exec-MTD pay categories (owner 2026-09-28, index §6n) — read from the backend's ONE list
// (`activation_bucketing.MTD_CATEGORIES`, served by GET /commcalc/commission-mtd/categories), never spelled
// here. Both rate editors (the Incentive Plans editor's Executive MTD rates and the Employee Commission
// Structure's Option 1) render these, so a category the pay path adds — Tablet, Watch — is offered with no
// second copy. harness_exec_mtd_device_rates_lock.py fails the build on a literal list in a page.
import { useCachedApi } from '@/lib/cache'

export type MtdCategory = { key: string; label: string }

export const MTD_CATEGORIES_PATH = '/api/v1/commcalc/commission-mtd/categories'

/** The categories in pay order ([] until the first response). */
export function useMtdCategories(): MtdCategory[] {
  const { data } = useCachedApi<{ categories?: MtdCategory[] }>(MTD_CATEGORIES_PATH)
  return data?.categories || []
}
