// Proof for the report TOTAL row's aggregation choice (src/lib/report-totals.ts) and for the one
// thing that made it unreachable: `agg` was documented as a page override but was not on
// ExportColumn, so no page could say it and EVERY right-aligned numeric column was summed — a
// maximum summed across twelve months is a number no row ever carried, and twelve shares summed go
// past 100%. Verbatim re-impl of the decision, plus source checks that the wiring has not come
// undone. Run: node scratchpad/prove_report_totals.mjs
import fs from 'node:fs'
let pass = 0, fail = 0
function ok(name, cond) { if (cond) { pass++ } else { fail++; console.log('  FAIL:', name) } }

// ── verbatim re-impl of the decision ────────────────────────────────────────────────────────────
const AVG_RE = /(^|[^a-z])(avg|average|mean|rate|ratio|percent|pct|margin)([^a-z]|$)|%|\bper\b/i
const isMoneyCol = c => !!c.money || c.type === 'money'
function toNumber(v) {
  if (v == null || v === '') return null
  if (typeof v === 'number') return isFinite(v) ? v : null
  const cleaned = String(v).trim().replace(/[$,%\s]/g, '')
  if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(cleaned)) return null
  const n = Number(cleaned)
  return isNaN(n) ? null : n
}
function resolveAgg(col, rows) {
  if (col.agg) return col.agg
  if (AVG_RE.test(col.header || '')) return 'avg'
  if (isMoneyCol(col)) return 'sum'
  if (col.type === 'text' || col.type === 'date') return 'none'
  if (col.type === 'number' || col.align === 'right') {
    const present = rows.map(r => col.get(r)).filter(v => v != null && v !== '')
    if (present.length === 0) return 'none'
    return present.every(v => toNumber(v) != null) ? 'sum' : 'none'
  }
  return 'none'
}

// Three months of the real shape: a count that ADDS, a maximum that does not, a share that does not.
const MONTHS = [
  { period: 'August 2026', flagged: 4, worst: 3, share: '0.4%' },
  { period: 'September 2026', flagged: 7, worst: 9, share: '0.9%' },
  { period: 'October 2026', flagged: 0, worst: 0, share: '0.0%' },
]
const countCol = { header: 'Accounts over the threshold', get: r => r.flagged, align: 'right' }
const worstCol = { header: 'Worst single account', get: r => r.worst, align: 'right', agg: 'none' }
const shareCol = { header: 'Share of accounts flagged', get: r => r.share, align: 'right', agg: 'none' }
const sumOf = (col) => MONTHS.reduce((s, r) => s + (toNumber(col.get(r)) ?? 0), 0)

ok('a count of accounts ADDS across the months', resolveAgg(countCol, MONTHS) === 'sum' && sumOf(countCol) === 11)
ok('a MAXIMUM does not add — the override is honoured', resolveAgg(worstCol, MONTHS) === 'none')
ok('…and without it the total row would have claimed 12 lines on one account', sumOf(worstCol) === 12)
ok('a SHARE does not add — the override is honoured', resolveAgg(shareCol, MONTHS) === 'none')
ok('…and its header alone would NOT have caught it (no %, no "per", no "rate" in it)',
  !AVG_RE.test(shareCol.header))
ok('an explicit override beats every other rule, including the money rule',
  resolveAgg({ header: 'Cost', get: () => 1, money: true, agg: 'none' }, MONTHS) === 'none')
ok('…and can force a sum a header would otherwise average',
  resolveAgg({ header: 'Rate', get: () => 1, align: 'right', agg: 'sum' }, MONTHS) === 'sum')
ok('with no override the behaviour is unchanged — this is additive, no adopter moves',
  resolveAgg({ header: 'Units', get: r => r.flagged, align: 'right' }, MONTHS) === 'sum'
  && resolveAgg({ header: 'Store', get: r => r.period }, MONTHS) === 'none'
  && resolveAgg({ header: 'Accessory rate', get: () => 1, align: 'right' }, MONTHS) === 'avg')

// ── the wiring, which is the part that can come undone ──────────────────────────────────────────
const read = rel => fs.readFileSync(new URL(rel, import.meta.url), 'utf8')
ok('resolveAgg still lets a page override win first', /if \(col\.agg\) return col\.agg/.test(read('../src/lib/report-totals.ts')))
ok('ExportColumn carries `agg`, so a page can actually say it', /agg\?: 'sum' \| 'avg' \| 'none'/.test(read('../src/lib/export.tsx')))
{
  const page = read("../src/app/(platform)/commcalc/store-takeover/page.tsx")
  ok('the month table marks its maximum and its share as non-adding',
    /Worst single account[\s\S]{0,160}agg: 'none'/.test(page)
    && /Share of accounts flagged[\s\S]{0,220}agg: 'none'/.test(page))
}

console.log(`\nreport-totals: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
