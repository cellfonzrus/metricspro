// THE client home for "editing a sale that is already rung" (index §POS-12).
//
// Owner 2026-10-29: "im not able to edit the receipt ot add more tiems to teh same rec3ipt".
// Offered re-ringing under a new number, a second sale, or editing in place, he chose editing in
// place, keeping the number.
//
// WHY A FILE OF ITS OWN, and not three `api()` calls in the register page. Editing is a
// three-step conversation — ask whether this sale may be edited and get its lines, let the cashier
// change them, save — and the two ends have to agree on one shape. Spread across the page
// component they drift: the load reads one field name and the save sends another, and the cashier
// finds out by losing a line. The server side has the matching one home
// (backend/app/modules/pos/sale_amendment.py), which is also where every refusal SENTENCE is
// written — this file never composes its own, so the Sale Log and the till cannot word one refusal
// two ways.
//
// NO ARITHMETIC HERE, DELIBERATELY. The register prices a cart for display, but what gets STORED is
// re-derived server-side from the agreed figures (`sale_amendment.line_money` / `totals`) and
// whatever this client sends for `extended_price` / `tax_value` / the totals is discarded. An edit
// exists to move those figures, so the one place that owns them has to be the server. Sending them
// anyway is harmless and keeps the line shape identical to the cart's.
import { api } from '@/lib/client'
import type { HeldLine } from '@/lib/pos-held-sale'

// A line being edited is EXACTLY a cart line — the same shape the register already holds and that
// POST /pos/sales/checkout consumes (backend `held_sale.LINE_FIELDS`). Reusing `HeldLine` rather
// than declaring a second line type is what keeps a line loaded from a sale and a line restored
// from a suspended cart interchangeable.
export type AmendLine = HeldLine

// WHAT WAS TENDERED, AND WHAT THE EDIT LEAVES OUTSTANDING (index §POS-17).
//
// Owner 2026-10-30: "create log of the cash or credit mismatch". Editing a sale moves what is OWED;
// it does not move what was TAKEN, so an edit can leave money to collect or refund.
//
// NO ARITHMETIC HERE EITHER, and here it matters more than it does for the cart. The register may
// price a cart for display, but it must never derive the MISMATCH: a browser subtracting a tender
// from a total is a second answer to a money question the server already owns
// (backend/app/modules/pos/sale_settlement.py), and the two would drift on the first rounding
// change. Every figure and every sentence below is the server's, shown verbatim. The preview comes
// from a DRY RUN of the save route — literally the same call that writes, with nothing written — so
// the figure the cashier agrees to cannot disagree with the figure that lands.
export interface AmendTender {
  /** False when one of the two figures was never recorded. NO figure may be shown when false. */
  measured: boolean
  /** True only when measured AND outside the declared band — the key to branch on for a warning. */
  mismatch: boolean
  /** tendered − expected. NEGATIVE is short (money to collect). Null when not measured. */
  variance: number | null
  verdict: 'short' | 'over' | 'ok' | null
  direction: 'collect' | 'refund' | 'settled' | null
  /** The server's sentence for why it could not be measured. Null when it could. */
  reason_label: string | null
  tendered: { present: boolean; total: number | null; methods: string[] }
  expected: { present: boolean; amount: number | null }
  /** Which tender the difference belongs to, or that the sale carried more than one. */
  attribution: { method: string | null; ambiguous: boolean; methods: string[] }
  band: number
}

/** What the server says about editing one sale, before anything is written. */
export interface AmendPlan {
  sale_id: string
  transaction_id: number | null
  /** Which revision the sale is on NOW; the edit would save it as this plus one. */
  revision: number
  allowed: boolean
  /** The server's own sentence for why not, shown verbatim. Null when allowed. */
  reason: string | null
  cart: AmendLine[]
  customer_id: string | null
  is_activation_sale: boolean
  store_code: string | null
  /** False until migration 1103 is applied — the Sale Log offers no button on a database that
   *  cannot keep the version the edit would supersede. */
  available: boolean
  /** How far back THIS caller may reopen a sale, as the server resolved it (§POS-12.9). Null on an
   *  older server; the screen then says nothing rather than inventing a number. */
  window_days: number | null
  /** Whether the caller holds the longer window. Stated, never inferred from a missing key. */
  elevated: boolean
  /** What stands tendered against this sale right now, before any edit. */
  tender: AmendTender | null
  /** False until migration 1108 is applied: an edit that WOULD leave a mismatch is then refused
   *  with a 503 rather than saved unlogged, so the till can warn before the cashier starts. */
  tender_log_available: boolean
}

export interface AmendSaved {
  revision: number
  totals: { subtotal: number; discount_total: number; tax_total: number; total: number }
  /** "1 line added, 1 changed" — the server's summary, as filed in the audit row. */
  changed: string
  /** The sentence to show the cashier, including the reminder to print a fresh receipt. */
  message: string
  /** The cash / credit mismatch this edit leaves behind (§POS-17), as the server measured it. */
  tender: AmendTender | null
  /** The server's sentence about that mismatch. Shown verbatim; never composed here. */
  tender_message: string
}

export interface AmendRevision {
  revision: number
  change_summary: string | null
  total_before: number | null
  total_after: number | null
  reason: string | null
  amended_by: string | null
  created_at: string
}

// ONE reader for the tender shape, so the plan, the preview and the save cannot disagree about
// which field means what. A server that does not send it at all (an older deploy) reads as null and
// every surface below simply shows nothing — never a zero, which would read as "it balances".
type TenderBag = Record<string, unknown>
/** An absent or non-numeric figure reads as null, NEVER 0 — a zero would read as "it balances". */
const _num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v))
const _bag = (v: unknown): TenderBag => (v && typeof v === 'object' ? v as TenderBag : {})
const _strs = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : [])
const _str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null)
/** A word the server is not allowed to invent: anything outside the list reads as "no verdict",
 *  never as a guess. The three verdicts are deposit_recon's own, platform-wide. */
const _oneOf = <T extends string>(v: unknown, allowed: readonly T[]): T | null =>
  (typeof v === 'string' && (allowed as readonly string[]).includes(v) ? v as T : null)
const VERDICTS = ['short', 'over', 'ok'] as const
const DIRECTIONS = ['collect', 'refund', 'settled'] as const

function readTender(raw: unknown): AmendTender | null {
  if (!raw || typeof raw !== 'object') return null
  const t = raw as TenderBag
  const tendered = _bag(t.tendered)
  const expected = _bag(t.expected)
  const attribution = _bag(t.attribution)
  return {
    measured: t.measured === true,
    mismatch: t.mismatch === true,
    variance: _num(t.variance),
    verdict: _oneOf(t.verdict, VERDICTS),
    direction: _oneOf(t.direction, DIRECTIONS),
    reason_label: _str(t.reason_label),
    tendered: {
      present: tendered.present === true,
      total: _num(tendered.total),
      methods: _strs(tendered.methods),
    },
    expected: {
      present: expected.present === true,
      amount: _num(expected.amount),
    },
    attribution: {
      method: _str(attribution.method),
      ambiguous: attribution.ambiguous === true,
      methods: _strs(attribution.methods),
    },
    band: Number(t.band) || 0,
  }
}

/** May this sale be edited, and with what lines. Writes nothing. */
export async function fetchAmendPlan(saleId: string): Promise<AmendPlan> {
  const r = await api(`/api/v1/pos/sales/${saleId}/amend-plan`)
  return {
    sale_id: saleId,
    transaction_id: r?.transaction_id ?? null,
    revision: Number(r?.revision) || 1,
    allowed: r?.allowed === true,
    reason: r?.reason ?? null,
    cart: Array.isArray(r?.cart) ? r.cart : [],
    customer_id: r?.customer_id ?? null,
    is_activation_sale: r?.is_activation_sale === true,
    store_code: r?.store_code ?? null,
    // Absent on an older server reads as available; the save still fails closed with a 503, so the
    // worst case is a button that explains itself rather than a button that is wrongly hidden.
    available: r?.available !== false,
    // NOT coerced to 0: an older server that sends nothing must read as "unknown", because 0 is a
    // real and meaningful window here ("the same day only") and would be a false statement.
    window_days: r?.window_days === null || r?.window_days === undefined
      ? null : Number(r.window_days),
    elevated: r?.elevated === true,
    tender: readTender(r?.tender),
    tender_log_available: r?.tender_log_available !== false,
  }
}

/** THE PREVIEW: what this edit would come to and what it would leave outstanding — WRITING NOTHING.
 *
 *  A dry run of the save route, not a route of its own. That is the point: the preview the cashier
 *  agrees to and the save that lands are the same server computation, so they cannot drift. */
export async function previewAmendedSale(
  saleId: string, items: AmendLine[], opts?: { reason?: string; notes?: string },
): Promise<AmendSaved & { dry_run: true }> {
  const r = await api(`/api/v1/pos/sales/${saleId}/items`, {
    method: 'PUT',
    body: JSON.stringify({
      items, dry_run: true,
      reason: opts?.reason || undefined,
      notes: opts?.notes || undefined,
    }),
  })
  return {
    dry_run: true,
    revision: Number(r?.revision) || 0,
    totals: r?.totals || { subtotal: 0, discount_total: 0, tax_total: 0, total: 0 },
    changed: r?.changed || '',
    message: r?.message || '',
    tender: readTender(r?.tender),
    tender_message: r?.tender_message || '',
  }
}

/** Save the edited lines against the SAME transaction number. */
export async function saveAmendedSale(
  saleId: string, items: AmendLine[], opts?: { reason?: string; notes?: string },
): Promise<AmendSaved> {
  const r = await api(`/api/v1/pos/sales/${saleId}/items`, {
    method: 'PUT',
    body: JSON.stringify({
      items,
      reason: opts?.reason || undefined,
      notes: opts?.notes || undefined,
    }),
  })
  return {
    revision: Number(r?.revision) || 0,
    totals: r?.totals || { subtotal: 0, discount_total: 0, tax_total: 0, total: 0 },
    changed: r?.changed || '',
    message: r?.message || '',
    tender: readTender(r?.tender),
    tender_message: r?.tender_message || '',
  }
}

/** Every version this sale has passed through, newest first. */
export async function fetchAmendHistory(saleId: string): Promise<AmendRevision[]> {
  const r = await api(`/api/v1/pos/sales/${saleId}/amendments`)
  return Array.isArray(r?.revisions) ? r.revisions : []
}
