import { api } from './client'

// ── Daily closing SUBMISSION (phone) ─────────────────────────────────────────────────────────────
// The store's own side of the day. `src/api/dm.ts` already covers the DM's SIGN-OFF
// (GET /closing/summary, POST /closing/verify); this module is the rep's submission, and the two are
// deliberately separate files because they are separate jobs with separate gates — not because the
// endpoints differ.
//
// NO NEW ENDPOINT. Every route here is one the web `<ClosingSubmitForm>` already calls, with the
// same contract: the tenant's declared tenders, its declared count fields, the declared-metric
// catalog and its expense categories, then POST /closing/row and PUT /closing/metric-lines.
// A mobile-only submit route would be a second answer to "what did this store declare today", and
// the two would drift on the first config change.
//
// THE CONFIG IS NOT COSMETIC. What the rep is asked for is the tenant's own declaration; the house
// seven tenders live on the SERVER as a template (`standard`), and this module passes the response
// through untouched so `lib/closing-submit.declaredTenders` can apply the one fallback rule. No
// default is applied here.
const BASE = '/api/v1/closing'
const enc = encodeURIComponent
type Dict = Record<string, unknown>

export type StoreOpt = {
  sfid?: string; store_code?: string; store_name?: string; store_address?: string
  // `closing_source` (mig 1035) — who produces this store's daily closing. The server REFUSES a
  // manual submission for a feed-derived store, so the screen reads this before the rep counts an
  // envelope and types seven figures, rather than after.
  closing_source?: string
  [k: string]: unknown
}

export type TenderConfigRes = {
  defs?: { tender_key: string; label?: string; recon_class?: string }[]
  standard?: { tender_key: string; label?: string; is_standard?: boolean }[]
  configured?: boolean
  [k: string]: unknown
}

export type CountConfigRes = {
  defs?: { field_key: string; label?: string }[]
  configured?: boolean
  [k: string]: unknown
}

export type MetricCatalogRes = {
  metrics?: { key: string; label?: string; storage?: string; value_kind?: string; declared_by?: string }[]
  configured?: boolean
  [k: string]: unknown
}

export type ExpenseCategory = { id: string; name: string; kind?: string }

export type RowResult = {
  id?: string | number
  accepted?: boolean
  retry?: { message?: string }
  recon?: { status?: string; flags?: string[]; auto_accepted?: boolean; [k: string]: unknown }
  [k: string]: unknown
}

export function getClosingStores() {
  return api.get<{ stores?: StoreOpt[] } | StoreOpt[]>(`${BASE}/stores`)
}

export function getTenderConfig() {
  return api.get<TenderConfigRes>(`${BASE}/tender-config`)
}

export function getCountConfig() {
  return api.get<CountConfigRes>(`${BASE}/count-config`)
}

export function getMetricCatalog() {
  return api.get<MetricCatalogRes>(`${BASE}/metric-catalog`)
}

export function getExpenseCategories() {
  return api.get<{ categories?: ExpenseCategory[] }>(`${BASE}/expense-categories`)
}

/** Whether this store-day already carries a submission, and the rows already in. Read so a rep is
 *  told "you already closed today" instead of being allowed to file a second one and discover the
 *  duplicate later from a manager. */
export function getDay(close_date: string) {
  return api.get<Dict[] | Dict>(`${BASE}/days?date=${enc(close_date)}`)
}

/** Submit the day. May answer `accepted: false` with a `retry` message — a RECOUNT prompt, which
 *  gives the direction and never the amount, so the rep recounts rather than copies. */
export function submitRow(payload: Dict) {
  return api.post<RowResult>(`${BASE}/row`, payload)
}

/** The richer per-category declarations (money, or a person's name against them). Sent AFTER the row
 *  is accepted, so a refused closing never leaves half a day's declarations behind — and a failure
 *  here must be SAID, never swallowed: the incentive is computed from these lines, so losing them
 *  quietly means somebody is paid less than they earned. */
export function putMetricLines(body: {
  store_code: string; close_date: string; closing_row_id?: string | number
  lines: { metric_key: string; employee_name?: string; units?: string | number; amount?: string | number }[]
}) {
  return api.put<Dict>(`${BASE}/metric-lines`, body)
}

/** Upload the envelope photo and get back its stored path. The screen keeps the local image too and
 *  sends it on submit if this never completed, because the photo must not depend on a pre-upload. */
export function uploadEnvelopePhoto(image: string) {
  return api.post<{ path?: string }>(`${BASE}/envelope-photo`, { image })
}

export function getEnvelopeConfig(store_code?: string) {
  const qs = store_code ? `?store_code=${enc(store_code)}` : ''
  return api.get<{ require_photo_if_cash?: boolean; [k: string]: unknown }>(
    `${BASE}/envelope-config${qs}`)
}
