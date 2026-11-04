import { api } from './client'

// ── Schedule API (phone) ─────────────────────────────────────────────────────────────────────────
// Backend: storeops router `/shifts`, `/time-off`, `/shift-swaps`. The native twin of what the web
// scheduling pages call — NO new endpoint and no second read path, because the question "which
// shifts may this person see" already has one answer on the server and a mobile-only route would be
// a second one free to disagree with it.
//
// WHO SEES WHAT IS NOT DECIDED HERE. `GET /storeops/shifts` runs `gate_person_rows` (§14w, owner
// directive 2026-10-05): a rep gets their own week wherever they worked it, a manager gets theirs
// plus the people who work under them. `/time-off` is gated the same way, and a rep may not read who
// else is off. So this module sends a week and renders what comes back; it never filters by
// employee id to "hide" anything, because a client-side filter is not a permission.
//
// WHY `mine()` STILL EXISTS. The screen shows the signed-in person their OWN week by default even
// though a manager's response legitimately contains other people. That is a VIEW, applied after the
// server has already decided what may be seen — never a substitute for it.
const BASE = '/api/v1/storeops'

export type Shift = {
  id?: string
  employee_id?: string | null
  employee_name?: string | null
  store_code?: string | null
  shift_date?: string | null
  start_time?: string | null
  end_time?: string | null
  scheduled_hours?: number | null
  actual_hours?: number | null
  role?: string | null
  notes?: string | null
  [k: string]: unknown
}

export type TimeOffRequest = {
  id?: string
  employee_id?: string | null
  employee_name?: string | null
  start_date?: string | null
  end_date?: string | null
  reason?: string | null
  kind?: string | null
  status?: string | null
  [k: string]: unknown
}

export type ShiftSwap = {
  id?: string
  requester_id?: string | null
  requester_name?: string | null
  target_id?: string | null
  target_name?: string | null
  shift_id?: string | null
  target_shift_id?: string | null
  notes?: string | null
  status?: string | null
  [k: string]: unknown
}

function rows<T>(payload: unknown): T[] {
  // The gated endpoints answer with a bare array; `gate_person_rows` has also been seen to wrap in
  // `{rows: […]}`. Tolerate both rather than rendering an empty week when a shape changes — an empty
  // week and "we could not read the week" look identical to a rep, and only one of them is true.
  if (Array.isArray(payload)) return payload as T[]
  const r = (payload as { rows?: unknown })?.rows
  return Array.isArray(r) ? (r as T[]) : []
}

export async function getShifts(weekStart: string, weekEnd: string): Promise<Shift[]> {
  const qs = new URLSearchParams({ week_start: weekStart, week_end: weekEnd })
  return rows<Shift>(await api.get<unknown>(`${BASE}/shifts?${qs}`))
}

export async function getTimeOff(): Promise<TimeOffRequest[]> {
  // No `employee_id` is sent ON PURPOSE: the server already answers with what this caller may see,
  // and passing an id would let a client ask about somebody else.
  return rows<TimeOffRequest>(await api.get<unknown>(`${BASE}/time-off`))
}

export type TimeOffIn = {
  employee_id: string
  start_date: string
  end_date: string
  reason?: string
  kind?: string
}

export async function requestTimeOff(body: TimeOffIn): Promise<TimeOffRequest> {
  // The backend canonicalises `employee_id` to the business id itself (its Gate-1 N1 fix), so a
  // client cannot create a row keyed on a numeric id. Status is forced to `pending` server-side.
  return await api.post<TimeOffRequest>(`${BASE}/time-off`, body)
}

export async function getShiftSwaps(): Promise<ShiftSwap[]> {
  return rows<ShiftSwap>(await api.get<unknown>(`${BASE}/shift-swaps`))
}

export type ShiftSwapIn = {
  requester_id: string
  shift_id?: string
  target_id?: string
  notes?: string
}

export async function requestShiftSwap(body: ShiftSwapIn): Promise<ShiftSwap> {
  return await api.post<ShiftSwap>(`${BASE}/shift-swaps`, body)
}
