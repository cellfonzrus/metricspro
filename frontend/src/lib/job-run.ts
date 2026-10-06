// THE ONE HOME for "is this background job running, and what may the button do about it"
// (owner report 2026-10-06).
//
// WHY THIS FILE EXISTS — the defect it replaces, stated so nobody re-derives it. The owner pressed
// ⚡ Run Calculation on the CommCalc dashboard for August 2026 and reported:
//
//     "i ran the calculation it does not show it is runnig it shows run calculation again,
//      then when you press it again it says it is already runnig - fix it"
//
// The run was fine: it started 02:27:40 and finished 02:28:36, 56 seconds. Every fault was in the
// surface, and there were three of them:
//
//   1. THE BUTTON NEVER CHANGED. No `disabled`, and a fixed label. The only evidence of a run in
//      flight was a small grey "⏳ Running..." line on another row.
//   2. THE PAGE ASKED ONCE, AFTER TWO SECONDS, AND NEVER AGAIN. `setTimeout(loadData, 2000)` against
//      a job that takes a minute: the page reads a status that is still mid-run and then stops
//      following it, so it can never show the run completing.
//   3. THE SECOND PRESS WAS REFUSED WITH A 409. The server's single-flight guard was right to refuse
//      it; the refusal is the surface blaming the person for a gap the surface left. A press that
//      cannot succeed must not be offered.
//
// THE CLASS, not the instance — this is the general fact that was wrong, and the reason this file is
// shared rather than a fix inside one page: **the UI treated "the request was accepted" as "the job
// has finished."** Four surfaces made that mistake, in two shapes. The CommCalc dashboard had no
// busy state at all. The connectors sweep, the daily-closing sweep and the auto-calc notice set a
// `busy` flag and then cleared it in a `finally` the moment the POST returned — which is the same
// error wearing a hat, because the POST returns when the job STARTS.
//
// THE RULE THIS FILE ENCODES. A press is a REQUEST. A job is running until the SERVER says it is
// not. So:
//
//   • `isRunning` is the one definition of "in flight", and it is a SET of server words, because
//     'running', 'queued', 'started' and 'pending' all mean the same thing to a button.
//   • `buttonState` is the one answer to what the control shows and whether it may be pressed. A
//     running job yields `disabled: true`, so the double-press that earned the 409 cannot be made.
//   • `settleStatus` is the one rule for reconciling a press with a status read. A read taken
//     BEFORE the press landed may not downgrade the press — that race is exactly how a just-started
//     run renders as "not calculated yet" and invites the second press.
//   • `nextPollDelay` is the one schedule for following a job to its end. It is bounded: a surface
//     that polls forever is its own defect, so the schedule returns null and the surface says the
//     run is taking longer than expected rather than spinning silently.
//
// WHAT THIS FILE IS NOT. It holds no endpoint, no period, no org and no tenant or carrier name: it
// is given a status string and returns a verdict, so it cannot grow a per-tenant branch (RULE TWO).
// It performs no I/O and imports nothing, which is what lets the proof harness drive the real
// functions under plain Node with no React and no network.

/** A status string as a server reports it, or null/undefined when nothing is known yet. */
export type JobStatus = string | null | undefined

/** What a surface renders for a job control. */
export interface ButtonState {
  /** Text for the control. */
  label: string
  /** True when a press cannot succeed and must not be offered. */
  disabled: boolean
  /** Short status line for beside the control, or '' when there is nothing to say. */
  badge: string
  /** Machine-readable phase, for a surface that styles the badge. */
  phase: 'idle' | 'running' | 'done' | 'error' | 'overdue'
}

// ── What the server's words mean ───────────────────────────────────────────────
//
// DELIBERATELY A SET, not an equality test against 'running'. The guard in the commission router
// writes 'running'; the auto-calc landing hook reports 'queued'; a POST that has been accepted but
// whose row has not been written yet is 'started'. A button cannot tell these apart and must not
// try — they all mean "do not offer another press".

/** Every server word that means a run is in flight. Lowercased; compare via `isRunning`. */
export const RUNNING_STATES: readonly string[] = ['running', 'queued', 'started', 'pending', 'busy', 'in_progress']

/** Every server word that means a run finished and failed. */
export const ERROR_STATES: readonly string[] = ['error', 'failed', 'refused']

/** Every server word that means a run finished and succeeded. */
export const DONE_STATES: readonly string[] = ['done', 'complete', 'completed', 'ok', 'calculated']

const norm = (s: JobStatus): string => String(s ?? '').trim().toLowerCase()

/** True when the server says a run is in flight. The ONE definition — never re-spell it. */
export function isRunning(status: JobStatus): boolean {
  return RUNNING_STATES.includes(norm(status))
}

/** True when the server says a run finished and failed. */
export function isError(status: JobStatus): boolean {
  return ERROR_STATES.includes(norm(status))
}

/** True when the server says a run finished and succeeded. */
export function isDone(status: JobStatus): boolean {
  return DONE_STATES.includes(norm(status))
}

// ── Reconciling a press with a status read ────────────────────────────────────
//
// THE RACE THAT CAUSED THE REPORT. The surface sets 'running' optimistically on press, then reads
// the status. If that read was issued before the server wrote its claim — or returns a row from an
// earlier, finished run — the surface overwrites its own correct optimistic state with 'done' and
// the button looks pressable again. The press is newer evidence than a read that predates it, so a
// read may only downgrade a press once it is demonstrably later than it.

/**
 * The status a surface should hold, given what it believes and what a read returned.
 *
 * `pressedAt` / `readAt` are epoch milliseconds. A read that is not strictly later than the press
 * cannot contradict it, so the optimistic 'running' survives until a genuinely later read says the
 * run has ended. A read that reports a run IN FLIGHT is always accepted — it agrees with the press
 * and carries the server's own start time.
 */
export function settleStatus(
  read: JobStatus,
  opts?: { pressedAt?: number | null; readAt?: number | null },
): string {
  const r = norm(read)
  const pressedAt = opts?.pressedAt ?? null
  const readAt = opts?.readAt ?? null
  if (pressedAt === null) return r || 'not_run'
  if (isRunning(r)) return r
  // The press stands unless the read is strictly later than it.
  if (readAt === null || readAt <= pressedAt) return 'running'
  return r || 'not_run'
}

// ── Following a job to its end ────────────────────────────────────────────────
//
// BOUNDED ON PURPOSE. Two seconds was too early for a 56-second job; polling every two seconds
// forever would be a different defect (a page that hammers an endpoint and never admits defeat).
// The schedule starts quick, because most runs are short, then backs off, and then STOPS — and the
// surface says the run is taking longer than expected, which is a true statement it can act on.

/** Poll offsets from the press, in milliseconds. */
export const POLL_SCHEDULE: readonly number[] = [
  2_000, 5_000, 9_000, 14_000, 20_000, 30_000, 45_000, 60_000,
  90_000, 120_000, 180_000, 240_000, 300_000,
]

/** How long a run may be in flight before a surface stops following it (ms). */
export const POLL_GIVE_UP_MS = POLL_SCHEDULE[POLL_SCHEDULE.length - 1]

/**
 * Milliseconds to wait before the next status read, given how long ago the press was.
 * Returns null when the surface should stop polling and say the run is overdue.
 */
export function nextPollDelay(elapsedMs: number): number | null {
  const e = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0
  for (const at of POLL_SCHEDULE) {
    if (at > e) return at - e
  }
  return null
}

/** True when a run has been in flight longer than the surface will follow it. */
export function isOverdue(elapsedMs: number): boolean {
  return Number.isFinite(elapsedMs) && elapsedMs > POLL_GIVE_UP_MS
}

// ── What the control shows ────────────────────────────────────────────────────

/**
 * The ONE answer to what a job control renders and whether it may be pressed.
 *
 * `idleLabel` is the surface's own wording for the action ("⚡ Run Calculation", "Run sweep now").
 * Everything else is derived here so two surfaces cannot disagree about what "running" looks like.
 */
export function buttonState(opts: {
  status: JobStatus
  idleLabel: string
  /** Milliseconds since the press, when this surface started the run. */
  elapsedMs?: number | null
  /** True while the POST itself is in flight — before any status is known. */
  submitting?: boolean
}): ButtonState {
  const idle = opts.idleLabel
  const elapsed = opts.elapsedMs ?? null

  if (opts.submitting) {
    return { label: 'Starting…', disabled: true, badge: '⏳ Starting…', phase: 'running' }
  }
  if (isRunning(opts.status)) {
    if (elapsed !== null && isOverdue(elapsed)) {
      // STILL DISABLED. An overdue run is one we have stopped watching, not one we know has died —
      // the server's stale-takeover window decides that, and offering a press we expect to be
      // refused would reproduce the reported defect.
      return {
        label: 'Running…',
        disabled: true,
        badge: '⚠ Still running — taking longer than expected. Reload to re-check.',
        phase: 'overdue',
      }
    }
    return { label: 'Running…', disabled: true, badge: '⏳ Running…', phase: 'running' }
  }
  if (isError(opts.status)) {
    return { label: idle, disabled: false, badge: '⚠ Last run failed', phase: 'error' }
  }
  if (isDone(opts.status)) {
    return { label: idle, disabled: false, badge: '✓ Calculated', phase: 'done' }
  }
  return { label: idle, disabled: false, badge: 'Not calculated yet', phase: 'idle' }
}
