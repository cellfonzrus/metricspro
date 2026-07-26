// Static-source regression proof (2026-07-25, payroll time-range picker) — NOT committed, scratch
// only, same convention as prove_people_rule5_wave1.mjs / the luxelink-parity purge_app_user guard.
// Confirms both range-picker pages actually wire start/end through to the API call, the export
// filename, and ReportShell's `rows` (RULE FOUR §3c: "what you see is what exports"), and that the
// StandardFilterBar "Clear filters" guard we added can't silently regress.
// Run: node scratchpad/prove_payroll_range_exports.mjs (from frontend/)
import { readFileSync } from 'fs'

let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

const PAGES = {
  'storeops/reports (Hours & Payroll)': 'src/app/(platform)/storeops/reports/page.tsx',
  'storeops/payroll (Payroll Report)': 'src/app/(platform)/storeops/payroll/page.tsx',
}

for (const [label, path] of Object.entries(PAGES)) {
  const src = readFileSync(path, 'utf8')

  ok(`${label}: fetches /payroll with start=/end= (not the old bare month=)`,
    /storeops\/payroll\?start=\$\{[^}]+\}&end=\$\{[^}]+\}/.test(src))
  ok(`${label}: no leftover payroll?month= fetch call`,
    !/storeops\/payroll\?month=\$\{/.test(src))

  ok(`${label}: export filename reflects the active range (filt.period AND filt.periodTo)`,
    /const filename = `[^`]*\$\{filt\.period[^}]*\}[^`]*\$\{filt\.periodTo[^}]*\}/.test(src))

  ok(`${label}: default range sourced from the existing GET /tenant-settings via currentPeriodFromSettingsResponse (no reimplemented period math)`,
    /currentPeriodFromSettingsResponse\(/.test(src) && /core\/tenant-settings/.test(src))

  ok(`${label}: prev/next stepping uses the shared stepPeriod() helper (not ad hoc date math)`,
    /stepPeriod\(\{ start: filt\.period, end: filt\.periodTo \}, ppSettings, dir\)/.test(src))

  ok(`${label}: "Clear filters" guard wired (onFilterChange, not a bare setFilt, passed to StandardFilterBar onChange)`,
    /onChange=\{onFilterChange\}/.test(src) && /function onFilterChange/.test(src))

  ok(`${label}: ReportShell receives the FILTERED rows (visibleRows / byStore), never the raw unfiltered load`,
    /<ReportShell[\s\S]{0,400}?rows=\{(view === 'employee' \? visibleRows : \(byStore as any\[\]\)|visibleRows)\}/.test(src))
}

// ── Gate-1 MINOR-A1 (2026-07-26): the Payroll Report's month-keyed chargebacks/PTO note must fire
// whenever the range ISN'T exactly a full calendar month — not only when it straddles two months —
// so the DEFAULT weekly-inside-one-month case (panels cover the whole month, Net Pay deducts a full
// month's chargebacks from one week's pay) is never silently un-noted.
{
  const src = readFileSync('src/app/(platform)/storeops/payroll/page.tsx', 'utf8')
  ok('payroll page: chargebacks/PTO note condition uses isFullCalendarMonth (not a two-month-straddle-only check)',
    /!isFullCalendarMonth\(filt\.period \|\| '', filt\.periodTo \|\| ''\)/.test(src))
  ok('payroll page: isFullCalendarMonth is imported from the shared pay-period lib (one source of truth, shared with rangeLabel)',
    /import \{[^}]*isFullCalendarMonth[^}]*\} from '\.\.\/lib\/pay-period'/.test(src))
  ok('payroll page: no leftover slice(0,7)-based month-straddle check for the note',
    !/panelMonth !== \(filt\.period \|\| ''\)\.slice\(0, 7\)/.test(src))
}

// ── Functional verbatim re-impl of isFullCalendarMonth (same convention as prove_standard_filters.mjs)
// — proves the fixed LOGIC itself, not just that the page calls some function by that name.
function isFullCalendarMonth(start, end) {
  if (!start || !end) return false
  const s = new Date(start + 'T00:00:00'), e = new Date(end + 'T00:00:00')
  return s.getDate() === 1 && s.getMonth() === e.getMonth() && s.getFullYear() === e.getFullYear()
    && e.getDate() === new Date(e.getFullYear(), e.getMonth() + 1, 0).getDate()
}
ok('isFullCalendarMonth: a full month (07-01..07-31) is true', isFullCalendarMonth('2026-07-01', '2026-07-31'))
ok('isFullCalendarMonth: a full 28-day Feb is true', isFullCalendarMonth('2026-02-01', '2026-02-28'))
ok('isFullCalendarMonth: the DEFAULT WEEKLY period entirely inside one month is FALSE (the exact bug '
  + 'reported — the note must fire here, unlike the old two-month-straddle-only check)',
  !isFullCalendarMonth('2026-07-06', '2026-07-12'))
ok('isFullCalendarMonth: a range spanning two months is FALSE', !isFullCalendarMonth('2026-07-20', '2026-08-05'))
ok('isFullCalendarMonth: a month missing its last day (07-01..07-30) is FALSE', !isFullCalendarMonth('2026-07-01', '2026-07-30'))
ok('isFullCalendarMonth: a month starting on the 2nd is FALSE', !isFullCalendarMonth('2026-07-02', '2026-07-31'))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('ALL GREEN')
