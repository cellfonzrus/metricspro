// THE ONE HOME for "what will this save do to the payroll?" — the Hours Approval confirmation.
//
// OWNER REPORT 2026-10-26, verbatim: *"there is a lag in saving andf updating the hours when a change
// is done m teh ssystem shoudl confirm that the new hours will be this after teh change and to save it
// every time , if multiple hours are adjusted it shoudl show mnultiple hours before saving what the
// neew payroll will be"*.
//
// THE CLASS, not the instance. The board had no statement of its own intent. A reviewer typed a
// figure into a cell and clicked; what reached the server, and what it would cost, were both
// invisible until after the fact. So "did that save?" could only be answered by reading the board
// again — and when the answer was no, there was nothing to compare against to notice.
//
// This module answers it BEFORE the write, from the rows already on screen, and it is the SAME
// object the dialog renders and the request is built from: the confirmation cannot describe a save
// that differs from the one that goes, because there is only one derivation of it.
//
// PURE: no React, no fetch, no clock, no DOM. No tenant / carrier / store / employee name (RULE TWO)
// and no rate or threshold of its own — every figure comes from the row it was read off. Proved by
// `frontend/prove_hours_approval_preview.mjs`.

/** A board row, narrowed to the fields the preview arithmetic actually reads. */
export type PreviewRow = {
  employee_id: string
  name?: string | null
  store?: string | null
  /** The computed figure: worked − lunch, BEFORE any adjustment. The baseline a correction is against. */
  hours_source?: number | null
  /** The correction currently stored for this row, or null when there is none. */
  hours_approved?: number | null
  /** The adjustment currently stored for this row. */
  adjustment_hours?: number | null
  pay_rate?: number | null
}

/** What a reviewer has typed into one row but not yet sent. Strings, exactly as the inputs hold them. */
export type PreviewEdit = { hours?: string; reason?: string; adj?: string; adjReason?: string }

/** One line of the confirmation: what moves on this row, from what to what, and what it costs. */
export type PreviewLine = {
  employee_id: string
  name: string
  store: string
  /** Payable hours as the board shows them now. */
  hours_before: number
  /** Payable hours once this save lands. */
  hours_after: number
  /** hours_after − hours_before. Signed; 0 is possible and is still a line when a reason changed. */
  hours_delta: number
  pay_before: number
  pay_after: number
  pay_delta: number
  /** Which of the two fields the reviewer touched. Both can be true. */
  changed_hours: boolean
  changed_adjustment: boolean
  /** The corrected figure being sent, or null when this save clears the correction. */
  hours_typed: number | null
  /** True when the typed hours box was emptied over a row that HAS a stored correction. */
  clears_correction: boolean
  adjustment_typed: number | null
  /** Human-visible blockers. A line with any of these cannot be sent. */
  problems: string[]
  reason: string
  adjustment_reason: string
}

export type PreviewTotals = {
  rows: number
  hours_before: number
  hours_after: number
  hours_delta: number
  pay_before: number
  pay_after: number
  pay_delta: number
}

export type Preview = {
  lines: PreviewLine[]
  totals: PreviewTotals
  /** Every line's blockers, flattened, in row order. Empty ⇒ the save can go. */
  problems: string[]
  /** True when nothing was typed — the dialog says so instead of confirming an empty write. */
  empty: boolean
}

/** 2dp, and never `-0`. The board shows money and hours; a negative zero in either reads as a defect. */
export function round2(n: number): number {
  const r = Math.round((Number(n) || 0) * 100) / 100
  return r === 0 ? 0 : r
}

const num = (v: unknown): number => {
  const n = typeof v === 'number' ? v : parseFloat(String(v ?? ''))
  return Number.isFinite(n) ? n : 0
}

/** Is this typed box a real entry? An empty / whitespace-only box is "not mentioned", not zero. */
export function isTyped(v: string | undefined | null): boolean {
  return typeof v === 'string' && v.trim() !== ''
}

/**
 * Payable hours for a row as the board shows them NOW.
 *
 * A stored correction OUTRANKS the computed figure — the same precedence the server applies when it
 * resolves `hours_effective` (`payroll_approval.list_approvals`: `approved if approved is not None
 * else payable`). This function exists so the dialog cannot disagree with that resolution; it is the
 * one restatement of it on this side, not a second rule.
 */
export function payableNow(row: PreviewRow): number {
  if (row.hours_approved !== null && row.hours_approved !== undefined) return round2(num(row.hours_approved))
  return round2(num(row.hours_source) + num(row.adjustment_hours))
}

/**
 * Payable hours for a row AFTER the typed edit lands, under the server's own precedence.
 *
 * Order, stated once: a typed correction wins outright; otherwise the computed figure plus whichever
 * adjustment applies (typed if typed, else stored); and a cleared correction falls back to exactly
 * that, which is what makes the clear visible in the dialog as a real move.
 */
export function payableAfter(row: PreviewRow, edit: PreviewEdit | undefined): number {
  const typedHours = isTyped(edit?.hours)
  const typedAdj = isTyped(edit?.adj)
  if (typedHours) return round2(num(edit?.hours))
  const adj = typedAdj ? num(edit?.adj) : num(row.adjustment_hours)
  // An emptied hours box over a stored correction CLEARS it, so the figure reverts to the computation.
  const clearing = edit?.hours === '' && row.hours_approved !== null && row.hours_approved !== undefined
  if (clearing || row.hours_approved === null || row.hours_approved === undefined) {
    return round2(num(row.hours_source) + adj)
  }
  // A correction stands and only the adjustment moved: the correction still outranks it.
  return round2(num(row.hours_approved))
}

/**
 * The confirmation for one save: one line per row the reviewer actually touched, plus the totals.
 *
 * `rows` is the board as shown; `edits` is keyed by employee_id; `ids` limits the save to the rows
 * the reviewer selected (omit for "every row with a typed edit"). A row whose typed values match
 * what is already stored produces NO line — confirming a write that changes nothing would train the
 * reviewer to click through the dialog, which is the behaviour this feature exists to prevent.
 */
export function buildPreview(
  rows: PreviewRow[],
  edits: Record<string, PreviewEdit>,
  ids?: string[] | null,
): Preview {
  const want = ids && ids.length ? new Set(ids) : null
  const lines: PreviewLine[] = []

  for (const row of rows || []) {
    const id = row?.employee_id
    if (!id) continue
    if (want && !want.has(id)) continue
    const edit = (edits || {})[id]
    if (!edit) continue

    const typedHours = isTyped(edit.hours)
    const typedAdj = isTyped(edit.adj)
    const storedAdj = round2(num(row.adjustment_hours))
    const typedHoursValue = typedHours ? round2(num(edit.hours)) : null
    const typedAdjValue = typedAdj ? round2(num(edit.adj)) : null
    const clears =
      edit.hours === '' && row.hours_approved !== null && row.hours_approved !== undefined

    // Did anything actually MOVE? A box retyped with the number already in it is not a change.
    // `storedHours` is null when no correction is stored, so ANY typed figure is a move then.
    const storedHours =
      row.hours_approved === null || row.hours_approved === undefined
        ? null
        : round2(num(row.hours_approved))
    const hoursMoved = clears || (typedHours && typedHoursValue !== storedHours)
    const adjMoved = typedAdj && typedAdjValue !== storedAdj
    if (!hoursMoved && !adjMoved) continue

    const before = payableNow(row)
    const after = payableAfter(row, edit)
    const rate = round2(num(row.pay_rate))
    const reason = (edit.reason || '').trim()
    const adjReason = (edit.adjReason || '').trim()

    // The SAME gates the server applies, said before the request rather than after it. The server
    // remains the authority — these only stop a round trip that was always going to be refused.
    const problems: string[] = []
    if (typedHours && !Number.isFinite(parseFloat(String(edit.hours)))) {
      problems.push('the hours box is not a number')
    } else if (typedHours && round2(num(edit.hours)) < 0) {
      problems.push('hours cannot be negative')
    }
    if (typedAdj && !Number.isFinite(parseFloat(String(edit.adj)))) {
      problems.push('the adjustment box is not a number')
    }
    if (adjMoved && !adjReason && !reason) {
      problems.push('the adjustment needs a reason')
    }
    if (typedHours && hoursMoved && round2(num(edit.hours)) !== round2(num(row.hours_source)) && !reason) {
      problems.push('the corrected hours need a reason')
    }
    if (clears && !reason) problems.push('clearing the correction needs a reason')
    if (adjMoved && round2(num(row.hours_source) + num(typedAdjValue)) < 0) {
      problems.push('that adjustment would make payable hours negative')
    }

    lines.push({
      employee_id: id,
      name: row.name || id,
      store: row.store || '',
      hours_before: before,
      hours_after: after,
      hours_delta: round2(after - before),
      pay_before: round2(before * rate),
      pay_after: round2(after * rate),
      pay_delta: round2(after * rate - before * rate),
      changed_hours: !!hoursMoved,
      changed_adjustment: !!adjMoved,
      hours_typed: clears ? null : typedHoursValue,
      clears_correction: !!clears,
      adjustment_typed: typedAdjValue,
      problems,
      reason,
      adjustment_reason: adjReason,
    })
  }

  const totals: PreviewTotals = {
    rows: lines.length,
    hours_before: round2(lines.reduce((a, l) => a + l.hours_before, 0)),
    hours_after: round2(lines.reduce((a, l) => a + l.hours_after, 0)),
    hours_delta: round2(lines.reduce((a, l) => a + l.hours_delta, 0)),
    pay_before: round2(lines.reduce((a, l) => a + l.pay_before, 0)),
    pay_after: round2(lines.reduce((a, l) => a + l.pay_after, 0)),
    pay_delta: round2(lines.reduce((a, l) => a + l.pay_delta, 0)),
  }

  return {
    lines,
    totals,
    problems: lines.flatMap(l => l.problems.map(p => `${l.name}: ${p}`)),
    empty: lines.length === 0,
  }
}

/**
 * The request rows for a save, derived from the SAME preview the reviewer just confirmed.
 *
 * Built from `preview.lines` and not from `edits` again, deliberately: two derivations of "what is
 * being sent" is the duplicate defect, and the whole value of the dialog is that the thing confirmed
 * and the thing sent are one object. `action` is the decide action — 'save' records and signs
 * nothing, 'approve' records and signs off in the same call.
 */
export function previewToRows(
  preview: Preview,
  action: 'save' | 'approve' | 'send_back' | 'reset',
  note?: string,
): Array<Record<string, unknown>> {
  return preview.lines.map(l => ({
    employee_id: l.employee_id,
    action,
    ...(note ? { note } : {}),
    ...(l.changed_hours
      ? { hours_approved: l.clears_correction ? '' : String(l.hours_typed), reason: l.reason }
      : {}),
    ...(l.changed_adjustment
      ? { adjustment_hours: String(l.adjustment_typed), adjustment_reason: l.adjustment_reason || l.reason }
      : {}),
  }))
}
