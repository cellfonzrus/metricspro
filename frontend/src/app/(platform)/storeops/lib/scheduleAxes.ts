// THE TWO AXES OF SCHEDULED HOURS ARE ONE FACT — the client-side home (owner report 2026-10-12,
// index §STOREOPS-01).
//
// THE DEFECT, as a class. The schedule grid derived "hours by store" and "hours by employee"
// SEPARATELY. The store axis kept every shift (it grouped on `store_code`, a column); the employee
// axis was an INNER JOIN of `shift.employee_name` against the roster's `name` — string equality on a
// denormalized copy that renaming an employee leaves stale. So a renamed employee's existing shifts
// were still counted by store and silently dropped by employee, the footer totals differed by
// exactly the orphaned hours, and nothing said so. Measured live on 2026-10-12 (UPS centre 7710):
// 208h by store vs 164h by employee for one week — one person, one employee record, two spellings.
//
// THIS MODULE IS THE ONE PLACE the page, its footer, its per-row totals and its export agree on who
// a shift belongs to and what the two axes add up to. It MIRRORS, deliberately and line for line,
// `backend/app/modules/storeops/schedule_hours.scheduled_axes` +
// `payroll_identity.build_shift_attributor`; `backend/harness_schedule_identity_lock.py` fails the
// build if a page re-derives either axis on its own, and `harness_schedule_axes.py` proves the same
// fixtures (including that live 208-vs-164 week) against the Python side.
//
// THE INVARIANT, which is the whole point:  sum(byStore) === sum(byEmployee) === total.
// A shift that names nobody on the roster is NOT dropped — it rides the explicit `__unattributed__`
// bucket with its reason, so "we do not know whose hours these are" is a visible answer.
//
// No runtime imports, so it is testable without React or a browser.

export const UNATTRIBUTED_KEY = '__unattributed__'
export const UNATTRIBUTED_LABEL = 'Not matched to an employee record'

export type MatchBasis = 'employee_id' | 'own_name_copy' | 'name_ambiguous' | 'unmatched'

export const MATCH_REASONS: Record<MatchBasis, string> = {
  employee_id: "The row carried this org's own employee id.",
  own_name_copy:
    'The row carries no usable employee id, but the name stored on it is exactly one roster ' +
    "employee's name, so it binds.",
  name_ambiguous:
    'The name on this shift matches more than one employee on the roster, so it names nobody. ' +
    'Reported rather than resolved to the first hit.',
  unmatched:
    'Nothing on this shift binds to the employee roster — not an employee id, not a name. These ' +
    'hours cannot be attributed to anybody until someone says who this is.',
}

export const BINDING_BASES: MatchBasis[] = ['employee_id', 'own_name_copy']

export interface AxesShift {
  id?: number | string
  employee_id?: string | number | null
  employee_name?: string | null
  store_code?: string | null
  shift_date?: string
  start_time?: string
  end_time?: string
  scheduled_hours?: number | null
  is_deleted?: boolean | null
}

export interface AxesEmployee {
  id?: number | string
  employee_id?: string | null
  name?: string | null
}

export interface Attribution {
  key: string
  employee_id: string | null
  name: string
  basis: MatchBasis
  binds: boolean
  reason: string
  shift_name: string | null
}

// ── hours math — the SAME rule as backend schedule_hours.shift_hours ─────────────────────────────
// end < start ⇒ overnight, add 24h. end === start ⇒ 0 (a blank row, never a spurious 24h).
export function shiftHours(start?: string | null, end?: string | null): number {
  const mins = (t?: string | null) => {
    const p = String(t ?? '').trim().split(':')
    const h = parseInt(p[0], 10)
    const m = parseInt(p[1], 10)
    if (!Number.isFinite(h) || !Number.isFinite(m)) return NaN
    return h * 60 + m
  }
  const s = mins(start)
  const e = mins(end)
  if (!Number.isFinite(s) || !Number.isFinite(e)) return 0
  if (e === s) return 0
  let d = e - s
  if (d < 0) d += 24 * 60
  return Math.round((d / 60) * 10000) / 10000
}

export function shiftHrs(s: AxesShift): number {
  if (s.start_time && s.end_time) return shiftHours(s.start_time, s.end_time)
  return Number(s.scheduled_hours || 0)
}

// ── identity folding — mirrors payroll_identity._fold_person / name_tokens / person_key ──────────
// THE ACCENT FOLD IS WHY THIS EXISTS: `'í'.toLowerCase()` is still `'í'` and every plain fold in
// this codebase kept it, so `Martínez` compared unequal to `Martinez`. NFD decomposes a precomposed
// letter into base + combining mark; the marks are then dropped.
function foldAccents(v: unknown): string {
  return String(v ?? '').normalize('NFD').replace(/[̀-ͯ]/g, '')
}

function foldPerson(v: unknown): string {
  return foldAccents(v)
    .toLowerCase()
    .replace(/[^a-z0-9]/g, '')
}

export function nameTokens(v: unknown): string[] {
  return String(v ?? '')
    .split(/[,\s]+/)
    .map(foldPerson)
    .filter(Boolean)
}

/** The order-free identity key for a person spelling — `"Sohail, Hajra"` and `"Hajra Sohail"` share
 *  one key. A dropped middle initial does NOT, because that is a judgement a human makes. */
export function personKey(v: unknown): string {
  return nameTokens(v).sort().join('|')
}

/** `{numeric pk: business employee_id}` for employees whose two id forms differ — mirrors
 *  `payroll_identity.business_id_alias_map`, INCLUDING its ambiguity guard: a numeric that is itself
 *  somebody else's real business id is never aliased, because relabelling it would move that
 *  person's hours under someone else's name. */
export function businessIdAliasMap(employees: AxesEmployee[]): Record<string, string> {
  const all = new Set(
    employees.map(e => String(e.employee_id ?? '').trim()).filter(Boolean))
  const alias: Record<string, string> = {}
  for (const e of employees) {
    const biz = String(e.employee_id ?? '').trim()
    const num = String(e.id ?? '').trim()
    if (!biz || !num || num === biz) continue
    if (all.has(num)) continue
    alias[num] = biz
  }
  return alias
}

/** `attribute(employee_id, employee_name) -> Attribution`. Precedence, first hit wins:
 *  business employee id → numeric pk that aliases to one → a name naming exactly one employee →
 *  ambiguous → unmatched. `name` is always the ROSTER spelling; `shift_name` carries what the row
 *  itself said so a divergence can be shown rather than silently corrected. */
export function buildShiftAttributor(
  employees: AxesEmployee[] | null | undefined,
): (employeeId?: string | number | null, employeeName?: string | null) => Attribution {
  const emps = (employees || []).filter(e => String(e.employee_id ?? '').trim())
  const alias = businessIdAliasMap(emps)
  const byId: Record<string, AxesEmployee> = {}
  const byKey: Record<string, Set<string>> = {}
  for (const e of emps) {
    const biz = String(e.employee_id ?? '').trim()
    byId[biz] = e
    const k = personKey(e.name)
    if (k) (byKey[k] ||= new Set()).add(biz)
  }
  const ans = (
    key: string, employee_id: string | null, name: string, basis: MatchBasis,
    shiftName?: string | null,
  ): Attribution => ({
    key, employee_id, name, basis,
    binds: BINDING_BASES.includes(basis),
    reason: MATCH_REASONS[basis],
    shift_name: String(shiftName ?? '').trim() || null,
  })
  return (employeeId, employeeName) => {
    const raw = String(employeeId ?? '').trim()
    if (raw) {
      const biz = byId[raw] ? raw : alias[raw]
      if (biz && byId[biz]) {
        return ans(biz, biz, String(byId[biz].name ?? ''), 'employee_id', employeeName)
      }
    }
    const hits = Array.from(byKey[personKey(employeeName)] || [])
    if (hits.length === 1) {
      return ans(hits[0], hits[0], String(byId[hits[0]].name ?? ''), 'own_name_copy', employeeName)
    }
    const label = String(employeeName ?? '').trim() || raw || UNATTRIBUTED_LABEL
    return ans(UNATTRIBUTED_KEY, null, label,
               hits.length > 1 ? 'name_ambiguous' : 'unmatched', employeeName)
  }
}

export interface EmployeeBucket {
  key: string
  employee_id: string | null
  name: string
  hours: number
  shifts: AxesShift[]
  stored_names: string[]
  basis: MatchBasis
  binds: boolean
  reason: string
}

export interface NameDivergence {
  employee_id: string | null
  roster_name: string
  stored_name: string
  rows: number
  hours: number
}

export interface ScheduleAxes {
  total: number
  byStore: Record<string, number>
  byStoreShifts: Record<string, AxesShift[]>
  byEmployee: EmployeeBucket[]
  perDay: Record<string, number>
  unattributed: { hours: number; shifts: AxesShift[] }
  nameDivergence: NameDivergence[]
  invariant: { storeTotal: number; employeeTotal: number; agrees: boolean }
  shiftsFor: (bucket: EmployeeBucket, date?: string) => AxesShift[]
  employeeBucketOf: (s: AxesShift) => EmployeeBucket | undefined
}

const r2 = (n: number) => Math.round(n * 100) / 100

/** BOTH axes of scheduled hours over the rows the page already scoped, from ONE row walk. The
 *  caller never groups shifts by name again — it reads `byStore` / `byEmployee` from here. */
export function scheduledAxes(
  shifts: AxesShift[] | null | undefined,
  employees: AxesEmployee[] | null | undefined,
): ScheduleAxes {
  const attribute = buildShiftAttributor(employees)
  const live = (shifts || []).filter(s => !s.is_deleted)

  const byStore: Record<string, number> = {}
  const byStoreShifts: Record<string, AxesShift[]> = {}
  const perDay: Record<string, number> = {}
  const buckets = new Map<string, EmployeeBucket>()
  const order: string[] = []
  const bucketOf = new Map<AxesShift, EmployeeBucket>()
  const unattributed: AxesShift[] = []
  let unattributedHours = 0
  const div = new Map<string, NameDivergence>()

  for (const s of live) {
    const h = shiftHrs(s)
    const store = String(s.store_code ?? '').trim() || '—'
    byStore[store] = (byStore[store] || 0) + h
    ;(byStoreShifts[store] ||= []).push(s)
    if (s.shift_date) perDay[s.shift_date] = (perDay[s.shift_date] || 0) + h

    const a = attribute(s.employee_id, s.employee_name)
    let b = buckets.get(a.key)
    if (!b) {
      b = { key: a.key, employee_id: a.employee_id, name: a.name, hours: 0, shifts: [],
            stored_names: [], basis: a.basis, binds: a.binds, reason: a.reason }
      buckets.set(a.key, b)
      order.push(a.key)
    }
    b.hours += h
    b.shifts.push(s)
    bucketOf.set(s, b)
    if (a.shift_name && !b.stored_names.includes(a.shift_name)) b.stored_names.push(a.shift_name)
    if (!a.binds) { unattributed.push(s); unattributedHours += h }
    else if (a.shift_name && personKey(a.shift_name) !== personKey(a.name)) {
      const dk = `${a.key}|${a.shift_name}`
      const cell = div.get(dk) || { employee_id: a.employee_id, roster_name: a.name,
                                    stored_name: a.shift_name, rows: 0, hours: 0 }
      cell.rows += 1
      cell.hours = r2(cell.hours + h)
      div.set(dk, cell)
    }
  }

  // Named people first (largest first), the open question last — where it reads as a question.
  const named = order.filter(k => k !== UNATTRIBUTED_KEY).map(k => buckets.get(k)!)
  named.sort((x, y) => y.hours - x.hours || x.name.localeCompare(y.name))
  const rest = order.filter(k => k === UNATTRIBUTED_KEY).map(k => buckets.get(k)!)
  const byEmployee = [...named, ...rest]
  byEmployee.forEach(b => { b.hours = r2(b.hours) })
  Object.keys(byStore).forEach(k => { byStore[k] = r2(byStore[k]) })
  Object.keys(perDay).forEach(k => { perDay[k] = r2(perDay[k]) })

  const storeTotal = r2(Object.values(byStore).reduce((a, v) => a + v, 0))
  const employeeTotal = r2(byEmployee.reduce((a, b) => a + b.hours, 0))

  return {
    total: storeTotal,
    byStore,
    byStoreShifts,
    byEmployee,
    perDay,
    unattributed: { hours: r2(unattributedHours), shifts: unattributed },
    nameDivergence: Array.from(div.values()).sort((a, b) => b.hours - a.hours),
    invariant: { storeTotal, employeeTotal, agrees: Math.abs(storeTotal - employeeTotal) < 0.01 },
    shiftsFor: (bucket, date) =>
      date ? bucket.shifts.filter(s => s.shift_date === date) : bucket.shifts,
    employeeBucketOf: s => bucketOf.get(s),
  }
}
