// PROOF HARNESS (frontend twin) — export formula injection (H7) + stored-XSS hrefs (H6).
// 2026-08-05 security register, package `agent/platform-core/export-xss-upload-hardening`.
//
// Nothing here re-implements the fix. Each section transpiles the REAL `src/lib/*.ts` with the
// project's own TypeScript compiler and executes it; the .xlsx sections drive the REAL SheetJS build
// (xlsx@0.18.5, the pinned version) exactly as `export.tsx` does. If a source file stops compiling,
// or an anchor moves, the harness fails loudly instead of testing a stale copy.
//
// It reads the SAME attack/regression corpus as the Python harness
// (`backend/harness_vectors_export_xss.json`), so the two runtimes can never disagree about what is
// dangerous — a vector added on one side immediately binds the other.
//
// SECTIONS
//   A. MEASURED TRUTH        — what SheetJS 0.18.5 actually does with a leading "=". This is where
//                              the register is CORRECTED: `aoa_to_sheet` writes text cells, so the
//                              browser .xlsx path was never the live hole; CSV is.
//   B. pinSheetCellTypes     — the guarantee that replaces that accident, incl. a formula cell
//                              injected by hand (what a lib upgrade would produce).
//   C. isFormulaRisky        — the classifier, over the whole shared corpus, with PARITY against the
//                              Python side's expectations.
//   D. csvField / toCsv      — the live CSV hole, neutralised; ANTI-REGRESSION on money and dates.
//   E. ANTI-REGRESSION, xlsx — a realistic money report exports byte-identically to base @6aadb14.
//   F. safeHref              — the H6 allow-list, over the same corpus.
//   G. UnsafeLinkGuard       — the app-wide click net: blocks script URLs, and CANNOT break the
//                              blob:/data:text/csv download anchors this app really uses.
//   H. WIRING                — export.tsx, TourRunner, tours.ts and the root layout are actually
//                              hooked up (source-level, anchored).
//
// Run:  node frontend/prove_export_xss_upload.mjs      (no network, no DB, no browser)

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'
import { createRequire } from 'node:module'
import { execFileSync } from 'node:child_process'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..')
const BASE_REF = '6aadb14'
const require_ = createRequire(import.meta.url)
const ts = require_('typescript')

let pass = 0, fail = 0
const ck = (label, cond, extra = '') => {
  if (cond) { pass++; console.log(`  ok   ${label}`) }
  else { fail++; console.error(`  FAIL ${label} ${extra}`) }
}
const must = (cond, msg) => { if (!cond) { console.error(`FATAL: ${msg}`); process.exit(2) } }
const section = t => console.log(`\n── ${t} ${'─'.repeat(Math.max(0, 88 - t.length))}`)

const TMP = mkdtempSync(join(tmpdir(), 'mp-xss-proof-'))

function loadTs(relPath, name, rewrite = s => s) {
  const src = readFileSync(join(HERE, relPath), 'utf8')
  const out = ts.transpileModule(rewrite(src), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
    reportDiagnostics: true,
  })
  must(!(out.diagnostics || []).length, `${relPath} failed to transpile: ` +
    (out.diagnostics || []).map(d => ts.flattenDiagnosticMessageText(d.messageText, ' ')).join('; '))
  const file = join(TMP, `${name}.mjs`)
  writeFileSync(file, out.outputText)
  return import(pathToFileURL(file).href)
}

const V = JSON.parse(readFileSync(join(REPO, 'backend/harness_vectors_export_xss.json'), 'utf8'))
const RISKY = V.formula_risky.vectors
const SAFE = V.formula_safe.vectors
const BAD_HREF = V.href_unsafe.vectors
const GOOD_HREF = V.href_safe.vectors
const NAV_BLOCKED = V.navigation_blocked.vectors
const NAV_ALLOWED = V.navigation_allowed.vectors

const CS = await loadTs('src/lib/cell-safety.ts', 'cell-safety')
const SU = await loadTs('src/lib/safe-url.ts', 'safe-url')
// UnsafeLinkGuard imports React; strip the component and keep the pure predicate under test.
const GUARD = await loadTs('src/components/UnsafeLinkGuard.tsx', 'guard', s =>
  s.split('export default function UnsafeLinkGuard')[0]
    .replace("import { useEffect } from 'react'\n", '')
    .replace("import { canonicalizeForScheme } from '@/lib/safe-url'", `import { canonicalizeForScheme } from '${pathToFileURL(join(TMP, 'safe-url.mjs')).href}'`))

const XLSX = require_('xlsx')

// ══ A. MEASURED TRUTH — what SheetJS actually does ══════════════════════════════════════════════
section('A. What SheetJS 0.18.5 actually does (the register correction, measured)')

const aoa = [['Header'], ...RISKY.map(v => [v])]
const wsRaw = XLSX.utils.aoa_to_sheet(aoa)
const cellsRaw = RISKY.map((_, i) => wsRaw[XLSX.utils.encode_cell({ r: i + 1, c: 0 })])
ck(`A1  aoa_to_sheet types all ${RISKY.length} attack vectors as TEXT cells (t:'s'), not formulas`,
  cellsRaw.every(c => c && c.t === 's'), JSON.stringify(cellsRaw.find(c => !c || c.t !== 's')))
ck('A1b no cell carries a formula (.f) — the browser .xlsx path was NOT the live hole',
  cellsRaw.every(c => !('f' in c)))
ck('A1c pinned lib version is the one measured', require_('xlsx/package.json').version === '0.18.5',
  require_('xlsx/package.json').version)

const csvRaw = XLSX.utils.sheet_to_csv(wsRaw)
ck('A2  LIVE HOLE, CONFIRMED: the CSV of the same sheet emits the DDE payload raw and executable',
  csvRaw.includes("=cmd|'/C calc'!A0"))
ck('A2b ...and every one of the = + - @ leads survives into CSV unescaped',
  ['=1+1', '+1+1', '-2+3', '@import'].every(p => csvRaw.includes(p.slice(0, 2))))

// ══ B. pinSheetCellTypes ════════════════════════════════════════════════════════════════════════
section('B. pinSheetCellTypes — turning that accident into a guarantee')

const wsF = XLSX.utils.aoa_to_sheet([['h'], ['x']])
wsF.A2 = { t: 'n', f: "cmd|'/C calc'!A0", v: 0 }          // what a formula-detecting lib would emit
const wsF2 = XLSX.utils.aoa_to_sheet([['h'], ["=cmd|'/C calc'!A0"]])
wsF2.A2.f = "cmd|'/C calc'!A0"
CS.pinSheetCellTypes(wsF); CS.pinSheetCellTypes(wsF2)
ck('B1  an injected formula cell has its .f stripped', !('f' in wsF.A2) && !('f' in wsF2.A2))
ck('B1b a risky string cell is explicitly typed as text', wsF2.A2.t === 's')
ck('B1c the displayed value is NOT rewritten', wsF2.A2.v === "=cmd|'/C calc'!A0")

const wsMoney = XLSX.utils.aoa_to_sheet([['Amount'], [-1234.56], [124043.34], ['-1234.56'], ['2026-08-06']])
const beforeMoney = JSON.stringify(['A2', 'A3', 'A4', 'A5'].map(r => wsMoney[r]))
CS.pinSheetCellTypes(wsMoney)
ck('B2  ANTI-REGRESSION: numbers, dates and numeric strings are left completely untouched',
  JSON.stringify(['A2', 'A3', 'A4', 'A5'].map(r => wsMoney[r])) === beforeMoney)
ck('B2b negative currency is still a NUMBER cell', wsMoney.A2.t === 'n' && wsMoney.A2.v === -1234.56)
ck('B3  the sheet metadata keys (!ref/!cols) are never treated as cells',
  CS.pinSheetCellTypes({ '!ref': 'A1:A2', '!cols': [{ wch: 5 }] })['!ref'] === 'A1:A2')
ck('B4  a null/undefined worksheet is a no-op, never a throw',
  CS.pinSheetCellTypes(null) === null && CS.pinSheetCellTypes(undefined) === undefined)

// ══ C. isFormulaRisky — parity with the Python classifier ═══════════════════════════════════════
section('C. isFormulaRisky — the shared corpus, both runtimes')

ck(`C1  flags all ${RISKY.length} attack vectors`, RISKY.every(v => CS.isFormulaRisky(v)),
  JSON.stringify(RISKY.filter(v => !CS.isFormulaRisky(v)).slice(0, 3)))
ck(`C1b clears all ${SAFE.length} legitimate vectors`, !SAFE.some(v => CS.isFormulaRisky(v)),
  JSON.stringify(SAFE.filter(v => CS.isFormulaRisky(v)).slice(0, 3)))
ck('C2  non-strings can never be risky (number/boolean/null/Date)',
  ![0, -1234.56, 1, true, false, null, undefined, new Date()].some(v => CS.isFormulaRisky(v)))
// PARITY: the Python side must classify the corpus identically. Run it and compare.
const pyOut = execFileSync('python3', ['-c', `
import json,sys
sys.path.insert(0, "${join(REPO, 'backend')}")
from app.modules.notify.render import _is_formula_risky as f
V = json.load(open("${join(REPO, 'backend/harness_vectors_export_xss.json')}"))
print(json.dumps({"r":[f(v) for v in V["formula_risky"]["vectors"]],
                  "s":[f(v) for v in V["formula_safe"]["vectors"]]}))
`], { encoding: 'utf8' })
const py = JSON.parse(pyOut)
ck('C3  PARITY: Python and TypeScript classify every corpus vector identically',
  JSON.stringify(py.r) === JSON.stringify(RISKY.map(v => CS.isFormulaRisky(v))) &&
  JSON.stringify(py.s) === JSON.stringify(SAFE.map(v => CS.isFormulaRisky(v))))

// ══ D. csvField — the path that IS live ═════════════════════════════════════════════════════════
section('D. csvField / toCsv — the CSV hole closed, money and dates untouched')

ck('D1  every attack vector is neutralised in CSV (prefixed, never evaluated)',
  RISKY.every(v => { const f = CS.csvField(v); return f.startsWith("'") || f.startsWith('"\'') }),
  JSON.stringify(RISKY.filter(v => { const f = CS.csvField(v); return !(f.startsWith("'") || f.startsWith('"\'')) }).slice(0, 3)))
ck(`D2  ANTI-REGRESSION: all ${SAFE.length} legitimate values come back byte-identical`,
  SAFE.every(v => CS.csvField(v) === (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)),
  JSON.stringify(SAFE.filter(v => CS.csvField(v) !== v && !/[",\r\n]/.test(v)).slice(0, 3)))
ck('D2b money keeps its exact characters', CS.csvField('-1234.56') === '-1234.56' &&
  CS.csvField('-$1,234.56') === '"-$1,234.56"' && CS.csvField(-1234.56) === '-1234.56')
ck('D2c dates keep their exact characters',
  CS.csvField('2026-08-06') === '2026-08-06' && CS.csvField('08/06/2026') === '08/06/2026')
ck('D3  RFC 4180 quoting still applies (comma / quote / newline)',
  CS.csvField('a,b') === '"a,b"' && CS.csvField('say "hi"') === '"say ""hi"""' &&
  CS.csvField('a\nb') === '"a\nb"')
ck('D3b null/undefined become an empty field', CS.csvField(null) === '' && CS.csvField(undefined) === '')
ck('D4  toCsv builds CRLF rows', CS.toCsv([['a', 'b'], ['=1+1', -1234.56]]) === "a,b\r\n'=1+1,-1234.56")

// ══ E. ANTI-REGRESSION — a real money report exports identically to base ════════════════════════
section('E. ANTI-REGRESSION — the browser .xlsx export vs base @' + BASE_REF)

function baseFile(rel) {
  return execFileSync('git', ['-C', REPO, 'show', `${BASE_REF}:${rel}`], { encoding: 'utf8' })
}
// The base buildWorkbook, minus the pin. Same inputs → the workbook object must be identical.
const MONEY_ROWS = [
  ['Store', 'Rep', 'Note', 'Commission'],
  ['Jamaica Ave', 'A. Rivera', '-1234.56', -1234.56],
  ['Queens Blvd', 'M. Chen', '2026-08-06', 124043.34],
  ['Liberty Ave', 'D. Okafor', '+1 (555) 123-4567', 0],
  ['Great Neck Rd', 'S. Patel', '-Adjustment', -0.01],
  ['Hillside Ave', 'J. Kim', 'M2-M12', 27043.19],
]
const wsBefore = XLSX.utils.aoa_to_sheet(MONEY_ROWS.map(r => r.slice()))
const wsAfter = CS.pinSheetCellTypes(XLSX.utils.aoa_to_sheet(MONEY_ROWS.map(r => r.slice())))
const cellsOf = ws => Object.keys(ws).filter(k => k[0] !== '!').sort()
  .map(k => [k, ws[k].t, ws[k].v])
const before = JSON.stringify(cellsOf(wsBefore))
const after = JSON.stringify(cellsOf(wsAfter))
ck('E1  every cell of a realistic money report is identical after the pin (type + value)',
  // only the '-Adjustment' / phone cells may change TYPE, and they were already 's'
  before === after, `${before}\n   !== ${after}`)
ck('E1b every money figure is still a number cell, unrounded',
  [-1234.56, 124043.34, 0, -0.01, 27043.19].every(n =>
    Object.keys(wsAfter).some(k => k[0] !== '!' && wsAfter[k].t === 'n' && wsAfter[k].v === n)))
const wbAfter = XLSX.utils.book_new()
XLSX.utils.book_append_sheet(wbAfter, wsAfter, 'Detail')
const wbBefore = XLSX.utils.book_new()
XLSX.utils.book_append_sheet(wbBefore, wsBefore, 'Detail')
ck('E2  the written .xlsx bytes are IDENTICAL — no report file changes at all',
  XLSX.write(wbAfter, { type: 'base64', bookType: 'xlsx' }) ===
  XLSX.write(wbBefore, { type: 'base64', bookType: 'xlsx' }))

// ══ F. safeHref ═════════════════════════════════════════════════════════════════════════════════
section('F. safeHref — H6 allow-list')

ck(`F1  rejects all ${BAD_HREF.length} XSS / off-site vectors`, !BAD_HREF.some(v => SU.isSafeHref(v)),
  JSON.stringify(BAD_HREF.filter(v => SU.isSafeHref(v)).slice(0, 3)))
ck(`F1b accepts all ${GOOD_HREF.length} real link shapes`, GOOD_HREF.every(v => SU.isSafeHref(v)),
  JSON.stringify(GOOD_HREF.filter(v => !SU.isSafeHref(v)).slice(0, 3)))
ck('F1c NON-REWRITING: a safe href comes back byte-identical',
  GOOD_HREF.every(v => SU.safeHref(v) === v))
ck('F1d an unsafe href becomes undefined (anchor renders with no href — visible, inert)',
  BAD_HREF.every(v => SU.safeHref(v) === undefined))
ck('F1e the fallback form works for <Link>, which requires a string',
  SU.safeHref('javascript:alert(1)', '#') === '#' && SU.safeHref('/admin/roles', '#') === '/admin/roles')
ck('F2  empty / null / whitespace are not links',
  !['', null, undefined, '   '].some(v => SU.isSafeHref(v)))
// PARITY with the Python allow-list.
const pyHref = JSON.parse(execFileSync('python3', ['-c', `
import json,sys
sys.path.insert(0, "${join(REPO, 'backend')}")
from app.modules.core.safe_href import is_safe_href as f
V = json.load(open("${join(REPO, 'backend/harness_vectors_export_xss.json')}"))
print(json.dumps({"bad":[f(v) for v in V["href_unsafe"]["vectors"]],
                  "good":[f(v) for v in V["href_safe"]["vectors"]]}))
`], { encoding: 'utf8' }))
ck('F3  PARITY: the Python write-side and TS render-side allow-lists agree on every vector',
  JSON.stringify(pyHref.bad) === JSON.stringify(BAD_HREF.map(v => SU.isSafeHref(v))) &&
  JSON.stringify(pyHref.good) === JSON.stringify(GOOD_HREF.map(v => SU.isSafeHref(v))))
ck('F4  isSafeMediaSrc allows a captured PNG chart data URL and a blob:, rejects data:text/html',
  SU.isSafeMediaSrc('data:image/png;base64,iVBORw0KGgo=') &&
  SU.isSafeMediaSrc('blob:https://x/1') && !SU.isSafeMediaSrc('data:text/html,<script>alert(1)</script>') &&
  !SU.isSafeMediaSrc('javascript:alert(1)'))

// ══ G. UnsafeLinkGuard — the app-wide net ═══════════════════════════════════════════════════════
section('G. UnsafeLinkGuard — blocks script URLs, cannot break a real download link')

ck(`G1  blocks all ${NAV_BLOCKED.length} script-URL forms`,
  NAV_BLOCKED.every(v => GUARD.isBlockedNavigation(v)),
  JSON.stringify(NAV_BLOCKED.filter(v => !GUARD.isBlockedNavigation(v))))
ck(`G2  ANTI-REGRESSION: allows all ${NAV_ALLOWED.length} link forms this app really uses ` +
  '(incl. the data:text/csv + blob: download anchors)',
  !NAV_ALLOWED.some(v => GUARD.isBlockedNavigation(v)),
  JSON.stringify(NAV_ALLOWED.filter(v => GUARD.isBlockedNavigation(v))))
ck('G3  every legitimate deep link in the corpus also passes the net',
  !GOOD_HREF.some(v => GUARD.isBlockedNavigation(v)))
ck('G4  null / empty are not navigations',
  !GUARD.isBlockedNavigation(null) && !GUARD.isBlockedNavigation(''))

// ══ H. WIRING ═══════════════════════════════════════════════════════════════════════════════════
section('H. Wiring — the fixes are actually in the shipped paths')

const read = p => readFileSync(join(HERE, p), 'utf8')
const exportSrc = read('src/lib/export.tsx')
ck('H1  export.tsx pins the cell types right after aoa_to_sheet',
  /aoa_to_sheet\(aoa\)[\s\S]{0,600}?pinSheetCellTypes\(ws\)/.test(exportSrc))
ck('H2  printReport escapes + allow-lists the chart image src (it was interpolated raw)',
  exportSrc.includes('.filter(isSafeMediaSrc)') && exportSrc.includes('src="${esc(src)}"'))
const baseExport = baseFile('frontend/src/lib/export.tsx')
ck('H2b NEGATIVE CONTROL: base interpolated that src straight into document.write()',
  baseExport.includes('src="${src}"'))
ck('H3  the export payload path is otherwise untouched (rawCell/displayCell/money identical to base)',
  ['function displayCell', 'function rawCell', 'const money ='].every(anchor => {
    const a = exportSrc.slice(exportSrc.indexOf(anchor)).split('\n').slice(0, 6).join('\n')
    const b = baseExport.slice(baseExport.indexOf(anchor)).split('\n').slice(0, 6).join('\n')
    return a === b
  }))
const tour = read('src/components/TourRunner.tsx')
ck('H4  TourRunner checks isSafeHref BEFORE router.push (the ?tour= auto-fire)',
  tour.indexOf('isSafeHref(step.page_href)') > 0 &&
  tour.indexOf('isSafeHref(step.page_href)') < tour.indexOf('router.push(step.page_href)'))
const baseTour = baseFile('frontend/src/components/TourRunner.tsx')
ck('H4b NEGATIVE CONTROL: base pushed the tenant-supplied href with no check at all',
  baseTour.includes('router.push(step.page_href)') && !baseTour.includes('isSafeHref'))
ck('H5  tours.ts scrubs on arrival, for both the list and the single-tour fetch',
  read('src/lib/tours.ts').includes('sanitizeTourHrefs') &&
  read('src/lib/tours.ts').includes('safeHref(t.start_href)'))
ck('H6  the click net is mounted in the ROOT layout (covers /portal and /onboard/[token] too)',
  read('src/app/layout.tsx').includes('<UnsafeLinkGuard />'))
for (const [f, needle] of [
  ['src/components/AdminAttention.tsx', 'safeHref(n.deep_link'],
  ['src/components/AdminAttention.tsx', 'safeHref(it.deep_link'],
  ['src/components/PortalReports.tsx', 'safeHref(r.href'],
  ['src/app/(platform)/admin/import-health/page.tsx', 'safeHref(i.deep_link'],
  ['src/app/(platform)/admin/import-health/page.tsx', 'safeHref(f.deep_link'],
  ['src/app/(platform)/remediation/page.tsx', 'safeHref(result.approval_url'],
  ['src/app/(platform)/training/page.tsx', 'safeHref(t.start_href'],
]) ck(`H7  sink sanitised: ${f} :: ${needle})`, read(f).includes(needle))

console.log(`\n${'='.repeat(96)}\nRESULT: ${pass} passed, ${fail} failed\n${'='.repeat(96)}`)
process.exit(fail ? 1 : 0)
