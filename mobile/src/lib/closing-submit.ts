/**
 * Shaping a daily closing submission — pure, so what a rep's typing turns into is provable with no
 * network and no database.
 *
 * WHAT THIS MODULE REFUSES TO DECIDE, and why each refusal is the point:
 *
 *  · **WHICH TENDERS EXIST.** The tenant declares them (`GET /closing/tender-config` → `defs`), and
 *    `configured` means "this company has its own list". A configured-but-all-off list therefore
 *    answers with NOTHING rather than falling back to the house seven: falling back would put one
 *    tenant's vocabulary on another tenant's form, which is exactly what the owner directive of
 *    2026-10-30 forbids. The house seven are a TEMPLATE the SERVER sends (`standard`), offered only
 *    to a tenant that has declared nothing — they are never a constant in this file.
 *
 *  · **WHICH PAYLOAD SLOT A TENDER TAKES.** Seven tender keys are physical `daily_closing` columns
 *    and anything else is JSONB (mig 111). The server already answers which is which, in the same
 *    response (`standard[].tender_key`), so this module DEREFERENCES that list. The web form carries
 *    its own `STD_KEYS` copy; this one does not, because the copy is the divergence.
 *
 *  · **WHERE A DECLARED CATEGORY IS STORED.** `closing/metric_catalog.storage_for` is the ONE rule
 *    (`harness_metric_catalog_lock.py` §A8: *nobody but the home decides where a declaration is
 *    stored*), and it is already resolved into each catalog row's `storage`. So this module reads
 *    `storage` and never looks at `value_kind` or `declared_by`. A category whose storage it cannot
 *    read is REPORTED as unknown and routed nowhere — guessing is how a per-person money declaration
 *    ends up in a store-level tally, and nothing on screen would say so.
 */

export type TenderDef = { tender_key: string; label?: string; is_standard?: boolean }
export type TenderConfig = { defs?: unknown; standard?: unknown; configured?: unknown }
export type CountDef = { field_key: string; label?: string }
export type Metric = { key: string; label?: string; storage?: string; [k: string]: unknown }
export type ExpenseLine = {
  category_id: string; amount: string; description: string
  employee_id?: string; employee_name?: string
}
export type MetricLineIn = {
  metric_key: string; employee_name?: string; units: string; amount: string
}

/** The physical tender columns are the standard tender_key under one prefix. Stated once. */
export const STD_PREFIX = 't_'

/** The storage value that means "this declaration rides the counts map", as the home spells it. */
export const COUNTS_STORAGE = 'counts_jsonb'

function list<T>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : []
}

function num(v: unknown): number {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? '').replace(/[^0-9.\-]/g, ''))
  return Number.isFinite(n) ? n : 0
}

function text(v: unknown): string {
  return typeof v === 'string' ? v.trim() : ''
}

/** `{tender_key: physical column}` for the keys the SERVER called standard — and no others. */
export function tenderSlots(standard: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  for (const d of list<TenderDef>(standard)) {
    const k = text(d?.tender_key)
    if (k) out[k] = `${STD_PREFIX}${k}`
  }
  return out
}

/** The tenders to put on the form: this tenant's own declaration, or — only when it has declared
 *  nothing at all — the template the server offered. Never a list held in this file. */
export function declaredTenders(cfg: TenderConfig | null | undefined): TenderDef[] {
  if (!cfg || typeof cfg !== 'object') return []
  const defs = list<TenderDef>((cfg as TenderConfig).defs)
  if ((cfg as TenderConfig).configured) return defs      // its own list, even when that list is empty
  return defs.length ? defs : list<TenderDef>((cfg as TenderConfig).standard)
}

/** The sum of the amounts it is handed. It decides nothing about which amounts are tenders — the
 *  caller passes the tender values, because accessory revenue is declared separately and is not a
 *  tender (and a function that quietly re-decided that would move the collected total). */
export function totalCollected(values: Record<string, unknown> | null | undefined): number {
  if (!values || typeof values !== 'object') return 0
  const sum = Object.values(values as Record<string, unknown>)
    .reduce<number>((a, v) => a + num(v), 0)
  return Math.round(sum * 100) / 100
}

/** Split the tenant's declared count fields the way the CATALOG says they are stored.
 *  `counts` → the mig-501 map; `lineMetrics` → the categories that need their own per-person or
 *  money inputs; `unknown` → declared fields whose storage could not be read, reported so the screen
 *  can say so rather than dropping them in silence. */
export function countsAndLines(
  catalog: unknown,
  countDefs: unknown,
  values: Record<string, string> | null | undefined,
): { counts: Record<string, string>; lineMetrics: Metric[]; unknown: string[] } {
  const metrics = list<Metric>(catalog)
  const byKey = new Map<string, Metric>()
  for (const m of metrics) {
    const k = text(m?.key)
    if (k) byKey.set(k, m)
  }
  const counts: Record<string, string> = {}
  const unknownKeys: string[] = []
  for (const d of list<CountDef>(countDefs)) {
    const k = text(d?.field_key)
    if (!k) continue
    const storage = text(byKey.get(k)?.storage)
    if (!storage) {
      unknownKeys.push(k)                       // declared but unplaceable — reported, never guessed
      continue
    }
    if (storage === COUNTS_STORAGE) counts[k] = (values || {})[k] || '0'
  }
  const lineMetrics = metrics.filter((m) => {
    const storage = text(m?.storage)
    return !!storage && storage !== COUNTS_STORAGE
  })
  return { counts, lineMetrics, unknown: unknownKeys }
}

/** The per-category declarations that are worth sending: a line a person actually filled in. A line
 *  with neither a tally nor an amount is DROPPED rather than declared as a zero nobody typed. */
export function richLines(lines: unknown): MetricLineIn[] {
  return list<MetricLineIn>(lines)
    .map((l) => ({
      metric_key: text(l?.metric_key),
      employee_name: text(l?.employee_name),
      units: text(l?.units),
      amount: text(l?.amount),
    }))
    .filter((l) => l.metric_key && (l.units || l.amount))
}

export type PayloadIn = {
  close_date: string
  store: { sfid?: string; store_code?: string; store_name?: string } | null
  employee_name: string
  standard: unknown
  tenderValues: Record<string, string>
  epay: { epay_on_cash?: string; epay_on_credit?: string; epay_on_acima?: string }
  acc_sale: string
  catalog: unknown
  countDefs: unknown
  countValues: Record<string, string>
  expenseLines: ExpenseLine[]
  envelope_picture?: string
  remarks?: string
}

/** The body of `POST /closing/row`.
 *
 *  Every physical tender column is sent, so a tender the rep left blank reads as a declared zero
 *  rather than as a field the server has to guess about — the server's legacy fallback kicks in only
 *  when NO `t_*` field is present at all, and a half-sent form would silently take that other path.
 */
export function buildPayload(inp: PayloadIn): Record<string, unknown> {
  const slots = tenderSlots(inp.standard)
  const vals = inp.tenderValues || {}

  const tenderFields: Record<string, string> = {}
  for (const col of Object.values(slots)) tenderFields[col] = '0'
  const custom: Record<string, string> = {}
  for (const [key, raw] of Object.entries(vals)) {
    const v = text(raw)
    const col = slots[key]
    if (col) tenderFields[col] = v || '0'
    else if (key && v) custom[key] = v
  }

  const { counts } = countsAndLines(inp.catalog, inp.countDefs, inp.countValues)

  const payload: Record<string, unknown> = {
    close_date: inp.close_date,
    sfid: inp.store?.sfid || '',
    store_code: inp.store?.store_code || '',
    store_name: inp.store?.store_name || '',
    employee_name: text(inp.employee_name),
    ...tenderFields,
    epay_on_cash: text(inp.epay?.epay_on_cash),
    epay_on_credit: text(inp.epay?.epay_on_credit),
    epay_on_acima: text(inp.epay?.epay_on_acima),
    acc_sale: text(inp.acc_sale),
    expense_lines: list<ExpenseLine>(inp.expenseLines)
      .filter((l) => text(l?.category_id) && text(l?.amount))
      .map((l) => ({
        category_id: text(l.category_id),
        amount: text(l.amount),
        description: text(l.description),
        ...(text(l.employee_id) ? { employee_id: text(l.employee_id) } : {}),
        ...(text(l.employee_name) ? { employee_name: text(l.employee_name) } : {}),
      })),
    counts,
    envelope_picture: inp.envelope_picture || '',
    remarks: text(inp.remarks),
  }
  if (Object.keys(custom).length) payload.custom_tenders = custom
  return payload
}
