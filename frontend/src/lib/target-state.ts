// ── THE TARGET VERDICT — one vocabulary for "how is this scope doing against its goal" ───────────
// Owner bug report, 2026-09-10: "on the employe dashboad under my tagrets it shows 0 achieved with on
// target, that needs to be fixed".
//
// THE DEFECT. Every screen decided the verdict the same way: `need > 0 ? 'to go' : 'target met'`.
// `need` is `max(0, monthly - achieved)` (commcalc/targets_engine.compute_scope), so a category with
// NO TARGET SET — monthly 0, which is also what a missing target row collapses to — produces need 0
// and rendered as a green pass. A rep with no goals and no sales was told "✓ on track". The one state
// that means "nobody has given you a number" was displayed as the state that means "you hit it".
//
// That is the silent-zero rule (index §23e/§23p) in its most damaging form: a zero standing in for an
// absent measurement is bad in a money column, but here it does not merely mislead — it tells a
// person their performance is fine while nothing about it is being measured at all.
//
// FOUR STATES, and the distinction between the first two is the whole point:
//   unknown — no figure at all (the scope was never computed). Not zero, not fine.
//   unset   — computed, but there IS no goal (monthly ≤ 0). You cannot be on track against nothing.
//   met     — a real goal, and it is reached.
//   short   — a real goal, and there is a gap; the caller renders the gap in its own units.
//
// WHY IT IS SHARED. Six render sites carried the same one-line conditional (the employee dashboard's
// "My Targets", both Action Plan boxes, the Targets table, the Targets detail card, and My Targets).
// The owner reported one of them. Fixing that one and leaving five to keep saying it is not a fix —
// and six copies of a verdict is exactly how they drifted into disagreeing in the first place.
export type TargetVerdict = 'unknown' | 'unset' | 'met' | 'short'

export type TargetState = {
  kind: TargetVerdict
  /** Ready-to-render phrase for the states that have no number of their own ('' for `short`). */
  label: string
  /** Colour for the verdict text, or for the `need` figure when a site shows only the number. */
  color: string
  /** True only for a REAL, reached goal — never for the absence of one. */
  passing: boolean
}

const NEUTRAL = 'var(--text3)'
const AMBER = '#b45309'
const GREEN = 'var(--green, #16794a)'

function num(x: unknown): number | null {
  if (x == null || x === '') return null
  const n = Number(x)
  return Number.isFinite(n) ? n : null
}

/**
 * The verdict for one category, from its monthly goal and its remaining `need`.
 *
 * `achieved` is deliberately NOT a parameter: `need` already carries it
 * (`need = max(0, monthly - achieved)`), and taking both would let a caller hand in a pair that
 * disagrees — a second way to compute the same answer, which is how this drifted before.
 */
export function targetState(monthly: unknown, need: unknown): TargetState {
  const m = num(monthly)
  const n = num(need)
  if (m == null || n == null) {
    return { kind: 'unknown', label: 'not measured', color: NEUTRAL, passing: false }
  }
  if (m <= 0) {
    return { kind: 'unset', label: 'no target set', color: NEUTRAL, passing: false }
  }
  if (n > 0) {
    return { kind: 'short', label: '', color: AMBER, passing: false }
  }
  return { kind: 'met', label: '✓ target met', color: GREEN, passing: true }
}
