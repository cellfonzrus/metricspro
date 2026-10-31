// WHAT THE REP INCENTIVE REPORT DECLARES SORTABLE (owner ask 2026-10-31, index §CORE-28).
//
// Owner, verbatim: *"in the Rep Incentive Report the month range shows , i need it to be sorted by
// employee store etc , add this to our standard filter bar and update the index"*.
//
// THIS FILE IS A DECLARATION, NOT A MECHANISM. "How two cells compare" is `lib/table-sort` (one home
// since the 2026-08-10 header-sort directive) and "the sort the bar shows and the page applies" is
// `lib/standard-filters` (§CORE-28). All that lives here is which of THIS report's columns can be
// ordered, what the user reads them as, and which cell to compare — because the shared bar must be
// HANDED those, never guess them: a default column list is one report's vocabulary standing in for
// every other report's, the same shape as the tenant-vocabulary default the 2026-10-30 directive
// bans. Omit the declaration and the bar renders no sort control at all.
//
// A rep's displayed name is `storeops_name || epay_salesperson` (two feeds, one person). Sorting
// reads the SAME expression the table prints — ordering by the other one would order the rows by a
// string the user cannot see, and a sort nobody can check is not a sort. The comparison is
// case-insensitive in `table-sort`, so 'ALI R' files under A and not before every lowercase name.
import type { SortFieldDecl, SortRow } from '@/lib/standard-filters'

/** The name this report shows for a person, wherever it shows one. */
export const repDisplayName = (r: SortRow): string =>
  String(r?.storeops_name || r?.epay_salesperson || '').trim()

/** REP BREAKDOWN / COMPENSATION tables — the single-month grain (one row per person per store). */
export const REP_SORT_FIELDS: SortFieldDecl[] = [
  { field: 'rep', label: 'Employee', get: repDisplayName },
  { field: 'store', label: 'Store', get: r => r?.store },
  { field: 'market', label: 'Market', get: r => r?.market },
  { field: 'tier', label: 'Tier', get: r => r?.tier, kind: 'number' },
  { field: 'kpis_met', label: 'KPIs met', get: r => r?.kpis_met, kind: 'number' },
  { field: 'subtotal', label: 'Subtotal', get: r => r?.subtotal, kind: 'number' },
  { field: 'total_payout', label: 'Payout', get: r => r?.total_payout, kind: 'number' },
]

/** MONTH RANGE table — the rep-MONTH grain. No Month field on purpose: the table is already
 *  sectioned by month and the sort orders the rows INSIDE each section, so a Month option would
 *  reorder nothing and read as broken. Market is absent for the same honesty reason: the range
 *  endpoint's rows do not carry one. */
export const RANGE_SORT_FIELDS: SortFieldDecl[] = [
  { field: 'rep', label: 'Employee', get: repDisplayName },
  { field: 'store', label: 'Store', get: r => r?.store },
  { field: 'total_payout', label: 'Payout', get: r => r?.total_payout, kind: 'number' },
  { field: 'final_payout', label: 'Final', get: r => r?.final_payout ?? r?.total_payout, kind: 'number' },
]
