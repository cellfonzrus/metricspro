/**
 * THE browser's reader of the register's tender list (owner ask 2026-10-30, index §POS-15).
 *
 * Owner: *"also add another tender type as Terms . 15/30/60"*.
 *
 * WHY THIS FILE EXISTS. The register carried its tender buttons as a literal array, and the rule
 * for swapping the integrated credit/debit pair for the external-terminal button lived beside it.
 * The same facts were also written in a `CHECK` constraint on `pos.sale_payments` (mig 725). Three
 * copies of one fact, so adding a tender type meant editing a screen, a condition and a
 * constraint — with nothing checking that the three agreed.
 *
 * The vocabulary now has ONE home, on the server: `backend/app/modules/pos/tender_vocabulary.py`,
 * served by `GET /pos/tender-types`. This file holds only what the browser legitimately decides —
 * keeping a selection valid when the list changes, and what the cashier must type before a Terms
 * sale may be rung. Those checks are a COURTESY, not the gate: the endpoint asks the same one home
 * again and refuses regardless of what the screen allowed (that is what the integration tests pin).
 *
 * No tender word, label or day count is written here.
 */

/** One tender as the server describes it. */
export type Tender = {
  key: string
  label: string
  icon?: string
  settles_now: boolean
  needs_amount_tendered: boolean
  /** present on a tender that is paid later — the day counts this org declared */
  day_options?: number[]
}

/**
 * What the screen shows before the server has answered. Deliberately cash ALONE: a guessed list
 * would be a fourth copy of the vocabulary, and offering a tender this org may not have is worse
 * than offering one button for the half-second before the real list lands.
 */
export const TENDER_FALLBACK: Tender[] = [
  { key: 'cash', label: 'Cash', icon: '💵', settles_now: true, needs_amount_tendered: true },
]

/** Load the list for this store. On any failure the caller keeps the fallback. */
export async function loadTenders(
  fetcher: (url: string) => Promise<{ tenders?: Tender[] } | null>, storeCode: string,
): Promise<Tender[]> {
  const q = storeCode ? `?store_code=${encodeURIComponent(storeCode)}` : ''
  const r = await fetcher(`/api/v1/pos/tender-types${q}`)
  const list = (r?.tenders || []) as Tender[]
  return list.length ? list : TENDER_FALLBACK
}

/** The tender for a key, or undefined when this register is not offering it. */
export function tenderFor(key: string, tenders: Tender[]): Tender | undefined {
  return tenders.find(t => t.key === key)
}

/**
 * Keep a selected tender valid when the list changes (a store switch, or a manager suspending card
 * processing mid-shift). The first offered tender wins, and cash is the answer when nothing is
 * offered at all — the screen is never left with no tender selected.
 */
export function keepSelectionValid(selected: string, tenders: Tender[]): string {
  if (tenders.some(t => t.key === selected)) return selected
  return tenders[0]?.key || TENDER_FALLBACK[0].key
}

/** What is still missing before a sale on terms may be rung. Each entry is a sentence. */
export function termsBlockers(
  tender: Tender | undefined, state: { customerId: string | null; termsDays: number | null },
): string[] {
  if (!tender || tender.settles_now) return []
  const out: string[] = []
  if (!state.customerId) {
    out.push('A sale on terms has to be in a customer’s name — pick or add the customer first.')
  }
  const options = tender.day_options || []
  if (state.termsDays === null || !options.includes(state.termsDays)) {
    out.push(`Choose a payment term — ${options.join(' / ')} days.`)
  }
  return out
}

/** When a terms sale falls due: the sale's own date plus the day count, as an ISO date. */
export function dueDateFor(saleDate: Date, days: number): string {
  const d = new Date(saleDate.getTime())
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

/**
 * The money line the cashier sees on the confirmation. A tender that did not settle is a BALANCE,
 * and saying "Paid" there is the same false statement the receipt renderer stopped making.
 */
export function paymentLineFor(
  tender: Tender | undefined, total: number, dueDate: string | null,
): { label: string; value: number } {
  if (tender && !tender.settles_now) {
    return { label: dueDate ? `Balance due ${dueDate}` : 'Balance due', value: total }
  }
  return { label: `Paid (${tender?.label || ''})`.replace(' ()', ''), value: total }
}
