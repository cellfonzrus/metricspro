// ── Run Commission (recalculate a period) — the SHARED state machine ─────────────────────────────
// OWNER DIRECTIVE 2026-08-05: "run commission button should also be on the commission plan payout and
// multi month page so when the commission structure is updated it should be easily accessible."
//
// One implementation, several mounts. This file is the PURE controller (no React, no DOM) so the
// behaviour that matters for money can be driven and asserted directly by
// `frontend/tools/run-commission-proof.mjs`. `RunCommissionButton.tsx` is a thin shell over it.
//
// The three rules this encodes — every one of them is a documented trap, not a preference:
//
//  1. CONFIRM BEFORE ANYTHING IS SENT. `request()` only opens the confirm; ONLY `confirm()` may issue
//     the POST, and it is a hard no-op from any other phase. A mis-click on a config page must not be
//     able to replace a month of stored payout numbers.
//  2. 409 IS NOT AN ERROR. The single-flight guard (backend `_calc_guard_acquire`) returns 409 with the
//     running-since timestamp when a recompute for this (org, period) is already in flight. That is
//     "wait", not "failed".
//  3. NEVER RE-FIRE. A recompute over ~300s dies at the Railway gateway (502) but COMPLETES
//     server-side; re-firing it re-enters the delete-then-insert and is how a month ends up at $0
//     ([[recompute-gateway-timeout]]). So: exactly ONE POST per confirmed run, ever — on ANY failure
//     we poll `/calc-status` instead of retrying. `state.posts` exists so the proof can assert it.

export type RunPhase =
  | 'idle'      // nothing has happened
  | 'confirm'   // confirm dialog open — NOTHING has been sent yet
  | 'starting'  // the single POST is in flight
  | 'running'   // the calc is running (accepted, or the gateway timed out on a run that IS running)
  | 'busy'      // 409 — a recompute for this period is already running; this press started nothing
  | 'done'      // finished; totals below are the real, post-run numbers
  | 'failed'    // the calculation itself reported an error, or we could not confirm completion

export type RunState = {
  phase: RunPhase
  period: string          // the period this run targets — always the one shown next to the button
  tenant: string          // tenant display name, for the confirm text
  message: string
  before: number | null   // total payout stored for the period BEFORE the run
  after: number | null    // ... and after, so the operator sees the effect of the structure change
  runningSince: string | null
  posts: number           // POST /calculate calls issued by this controller. MUST never exceed 1.
  polls: number
  gatewayTimeout: boolean // the request died at the gateway; the run continues server-side
  uncertain: boolean      // the POST failed in a way we cannot classify — poll, never re-fire
  reps: number | null
}

export type RunApi = (path: string, init?: { method?: string }) => Promise<unknown>

type Json = Record<string, unknown>
const asObj = (v: unknown): Json => (v && typeof v === 'object' && !Array.isArray(v) ? v as Json : {})
const errText = (e: unknown): string =>
  String((e && typeof e === 'object' && 'message' in e ? (e as { message?: unknown }).message : e) ?? '')

export type RunDeps = {
  api: RunApi
  orgId: string
  tenant?: string
  onChange: (s: RunState) => void
  sleep?: (ms: number) => Promise<void>
  pollEveryMs?: number
  maxPollMs?: number
}

export const DEFAULT_POLL_MS = 6000
export const DEFAULT_MAX_POLL_MS = 20 * 60 * 1000   // matches the guard's stale-run window

// ── error classification ─────────────────────────────────────────────────────────────────────────
// `api()` throws a bare Error carrying the server's `detail` (the HTTP status is not preserved), so the
// classification is on the message. The busy text is authored by backend `_calc_busy_message`.
const BUSY_RE = /already running|calculation .* in progress/i
const GATEWAY_RE =
  /bad gateway|gateway ?time-?out|service unavailable|\b50[234]\b|failed to fetch|load failed|networkerror|network error|timed? ?out|connection (closed|reset|terminated)|terminated/i

export function isBusyError(e: unknown): boolean {
  return BUSY_RE.test(errText(e))
}
export function isGatewayError(e: unknown): boolean {
  const m = errText(e)
  return !BUSY_RE.test(m) && GATEWAY_RE.test(m)
}
// "started 2026-08-05T02:14:07Z" → the timestamp, for the friendly wait message.
export function runningSinceFrom(msg: string): string | null {
  const m = /started\s+([^)]+?)\s*\)/i.exec(String(msg || ''))
  return m ? m[1].trim() : null
}

export function humanSince(iso: string | null, now: number = Date.now()): string {
  if (!iso) return ''
  const t = Date.parse(iso)
  if (!isFinite(t)) return iso
  const mins = Math.max(0, Math.round((now - t) / 60000))
  if (mins < 1) return 'less than a minute ago'
  if (mins === 1) return '1 minute ago'
  if (mins < 60) return `${mins} minutes ago`
  const h = Math.floor(mins / 60)
  return h === 1 ? '1 hour ago' : `${h} hours ago`
}

export const fmtUsd = (n: number) =>
  new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n || 0)

export function sumPayout(rows: unknown): { total: number; reps: number } {
  const list: unknown[] = Array.isArray(rows) ? rows : []
  let total = 0
  for (const raw of list) {
    const r = asObj(raw)
    total += Number(r.final_payout ?? r.total_payout ?? 0) || 0
  }
  return { total: Math.round(total * 100) / 100, reps: list.length }
}

// ── the endpoints, in ONE place, so every mount hits the identical URLs ───────────────────────────
// org_id rides as a QUERY PARAM (AGENT_CONTRACT §2) — the tenant middleware rewrites it from the JWT.
export const paths = {
  calculate: (period: string, orgId: string) =>
    `/api/v1/commcalc/calculate/${encodeURIComponent(period)}?org_id=${encodeURIComponent(orgId)}`,
  commissions: (period: string, orgId: string) =>
    `/api/v1/commcalc/commissions/${encodeURIComponent(period)}?org_id=${encodeURIComponent(orgId)}`,
  calcStatus: (period: string, orgId: string) =>
    `/api/v1/commcalc/calc-status/${encodeURIComponent(period)}?org_id=${encodeURIComponent(orgId)}`,
}

export function confirmText(period: string, tenant: string): string {
  const who = (tenant || '').trim()
  return `Recalculate commissions for ${period}${who ? ` (${who})` : ''}? ` +
    `This replaces the stored payout numbers for that month.`
}

export function initialState(period: string, tenant: string): RunState {
  return {
    phase: 'idle', period, tenant, message: '', before: null, after: null,
    runningSince: null, posts: 0, polls: 0, gatewayTimeout: false, uncertain: false, reps: null,
  }
}

export type RunController = {
  get: () => RunState
  /** Open the confirm. Sends NOTHING. */
  request: (period: string, tenant?: string) => void
  /** Close the confirm without running. */
  cancel: () => void
  /** Clear a finished/busy/failed result back to idle. */
  dismiss: () => void
  /** The ONLY path that issues POST /calculate. A no-op unless the confirm is open. */
  confirm: () => Promise<RunState>
}

export function createRunCommission(deps: RunDeps): RunController {
  const sleep = deps.sleep || ((ms: number) => new Promise<void>(r => setTimeout(r, ms)))
  const pollEvery = deps.pollEveryMs ?? DEFAULT_POLL_MS
  const maxPoll = deps.maxPollMs ?? DEFAULT_MAX_POLL_MS
  let s: RunState = initialState('', deps.tenant || '')

  const set = (patch: Partial<RunState>) => { s = { ...s, ...patch }; deps.onChange(s); return s }

  async function totalFor(period: string): Promise<{ total: number; reps: number } | null> {
    try { return sumPayout(await deps.api(paths.commissions(period, deps.orgId))) }
    catch { return null }   // a read that fails must never block or fake the run's outcome
  }

  async function confirm(): Promise<RunState> {
    // RULE 1 — the confirm is the gate. Any other phase (including a double-click that already fired)
    // returns without touching the network.
    if (s.phase !== 'confirm') return s
    const period = s.period
    set({ phase: 'starting', message: `Starting the recalculation for ${period}…`, after: null, reps: null })

    const beforeAgg = await totalFor(period)
    set({ before: beforeAgg ? beforeAgg.total : null })

    // RULE 3 — this is the one and only POST for this run. Nothing below may issue another.
    try {
      s = { ...s, posts: s.posts + 1 }
      await deps.api(paths.calculate(period, deps.orgId), { method: 'POST' })
      set({ phase: 'running', message: `Recalculating ${period}… this can take several minutes.` })
    } catch (e: unknown) {
      const msg = errText(e)
      if (isBusyError(e)) {
        // RULE 2 — not an error. This press started nothing; the earlier run owns the period.
        const since = runningSinceFrom(msg)
        return set({
          phase: 'busy', runningSince: since,
          message: `A recompute for ${period} is already running${since ? ` (started ${humanSince(since)})` : ''}` +
            ` — wait for it to finish. Nothing was started by this press.`,
        })
      }
      const gw = isGatewayError(e)
      set({
        phase: 'running', gatewayTimeout: gw, uncertain: !gw,
        message: gw
          ? `Still running — the request timed out at the gateway but the recalculation completes on the server. ` +
            `This can take several minutes. Do not run it again.`
          : `Could not confirm the request (${msg}). Checking whether the recalculation is running — ` +
            `it will not be sent again.`,
      })
    }

    // Poll for completion. NEVER re-POST — on the 502 path the run is already in flight.
    const deadline = Date.now() + maxPoll
    let last: Json | null = null
    while (Date.now() < deadline) {
      await sleep(pollEvery)
      set({ polls: s.polls + 1 })
      let row: Json
      try { row = asObj(await deps.api(paths.calcStatus(period, deps.orgId))) }
      catch { continue }   // a hiccup on the status read is not a failed calculation
      const cur = String(row.calc_status || '').toLowerCase()
      if (cur === 'done' || cur === 'error') { last = row; break }
    }

    const st = String(last?.calc_status || '').toLowerCase()
    if (st !== 'done' && st !== 'error') {
      return set({
        phase: 'failed',
        message: `Still running after ${Math.round(maxPoll / 60000)} minutes — the recalculation was NOT re-sent. ` +
          `Open the Rep Incentive report for ${period} to see the result when it lands.`,
      })
    }

    const afterAgg = await totalFor(period)
    if (st === 'error') {
      const errs = last?.save_errors
      return set({
        phase: 'failed',
        after: afterAgg ? afterAgg.total : null,
        reps: afterAgg ? afterAgg.reps : null,
        message: `The calculation for ${period} was refused — the last good snapshot was kept. ` +
          (Array.isArray(errs) ? errs.join(' ') : String(errs || '')),
      })
    }
    return set({
      phase: 'done',
      after: afterAgg ? afterAgg.total : null,
      reps: afterAgg ? afterAgg.reps : null,
      message: `${period} recalculated.`,
    })
  }

  return {
    get: () => s,
    request: (period: string, tenant?: string) => {
      set({
        phase: 'confirm', period, tenant: tenant ?? s.tenant,
        message: '', before: null, after: null, reps: null,
        runningSince: null, gatewayTimeout: false, uncertain: false,
      })
    },
    cancel: () => { set({ phase: 'idle', message: '' }) },
    dismiss: () => { set({ phase: 'idle', message: '', gatewayTimeout: false, uncertain: false }) },
    confirm,
  }
}

// The delta the owner is actually looking for after editing a plan/tier/schedule.
export function deltaLabel(s: RunState): string | null {
  if (s.before === null || s.after === null) return null
  const d = Math.round((s.after - s.before) * 100) / 100
  if (d === 0) return 'no change vs before'
  return `${d > 0 ? '+' : '−'}${fmtUsd(Math.abs(d))} vs before (${fmtUsd(s.before)})`
}
