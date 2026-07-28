/*
 * FRONTEND half of agent/commission/xreport-upload-honesty (2026-07-28), proven against the REAL
 * shipped source — `next build` cannot run in this environment (a known toolchain limitation; the
 * canonical repo fails the same Turbopack build on origin/main), so the real uploadGuard.tsx is
 * transpiled with the repo's own TypeScript and its exported readUploadOutcome is executed against
 * the EXACT payloads the backend harness (xreport_upload_honesty_proof.py) produced.
 *
 * Proves:
 *   1. the owner's bug — a 0-row X-report is no longer 'ok' ("✅ Saved 0 rows"); it is a 'guard'
 *      outcome whose text names the machine reason and what to do about it;
 *   2. a GOOD X-report finally reads "Saved 3 tender row(s)" (it printed "Saved 0 rows" before,
 *      because the response carried `tenders` and readUploadOutcome only ever looked at `saved`);
 *   3. the per-sheet forensics + the unrecognized tender labels reach the banner's detail list;
 *   4. every pre-existing branch (price_guard / price_guard_partial / inventory_* / shrink / clean)
 *      is byte-identical to origin/main's interpreter over the same payloads (no collateral drift);
 *   5. upload/page.tsx actually renders <UploadGuardBanner> and passes the x_report unit.
 *
 * Run:  node backend/scratchpad/xreport_upload_honesty_ui_proof.js
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const ts = require('/workspaces/metricspro/frontend/node_modules/typescript');

const REPO = path.join(__dirname, '..', '..');
const REL = path.join('frontend', 'src', 'app', '(platform)', 'commcalc', '_lib', 'uploadGuard.tsx');
const GUARD = path.join(REPO, REL);
const PAGE = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', 'upload', 'page.tsx');

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + '   ' + JSON.stringify(extra)); }
};

/** Transpile a .tsx module's source text and return its exported readUploadOutcome. */
function loadInterpreter(src) {
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  const module = { exports: {} };
  // the file's only import is `import type { CSSProperties }` (erased) — no runtime deps but React's
  // JSX factory, which is never CALLED by readUploadOutcome.
  new Function('module', 'exports', 'require', 'React', js)(
    module, module.exports, () => ({}), { createElement: () => null });
  return module.exports;
}

const NEW = loadInterpreter(fs.readFileSync(GUARD, 'utf8'));
const OLD = loadInterpreter(
  execFileSync('git', ['-C', REPO, 'show', 'origin/main:' + REL.split(path.sep).join('/')]).toString());

// ── payloads: verbatim shapes the backend harness produced ──────────────────────────────────────
const HAPPY = {
  success: true, file_type: 'x_report', tenders: 3, saved: 3, stores: 2, date: '2026-07-27',
  format: 'multi-sheet', parser_path: 'multi_sheet', rows_read: 3, save_failures: 0, first_error: null,
  xreport_diag: {
    parser_path: 'multi_sheet', sheets_read: 2, headers_found: 2, tender_rows_matched: 3,
    tender_rows_skipped: 0, unmatched_labels: [], config_label_count: 0, upsert_attempts: 3,
    save_failures: 0, first_error: null,
    sheets: [{ sheet: '3 Palisade Ave', rows: 9, outcome: 'rows', header_row: 3,
               header_wording: 'Tender Types + Net + Refunds', matched: 2, skipped: 0, skipped_labels: [] }],
    flat: null,
  },
};
const ALL_UNMATCHED = {
  success: false, file_type: 'x_report', tenders: 0, saved: 0, stores: 0, parser_path: 'neither',
  skipped: 'all_labels_unmatched',
  note: "Found the tender header on 1 sheet(s), but recognized NONE of the 2 tender label(s) on them: " +
        "Klarna, Dish SmartPay. Nothing was written. Map these labels under Closing → Tender Config " +
        "(report 'x_report') and re-upload — 0 tenant label(s) are mapped today.",
  xreport_diag: {
    parser_path: 'neither', sheets_read: 1, headers_found: 1, tender_rows_matched: 0,
    tender_rows_skipped: 2, unmatched_labels: ['Klarna', 'Dish SmartPay'], config_label_count: 0,
    upsert_attempts: 0, save_failures: 0,
    sheets: [{ sheet: '3 Palisade Ave', rows: 9, outcome: 'no_labels_matched', header_row: 3,
               header_wording: 'Tender Types + Net + Refunds', matched: 0, skipped: 2,
               skipped_labels: ['Klarna', 'Dish SmartPay'] }],
    flat: { columns: ['X-Report', 'Unnamed: 1'], rows: 8, store_col: null, tender_col: null, amount_col: null },
  },
};
const HEADER_MISS = {
  success: false, file_type: 'x_report', tenders: 0, saved: 0, stores: 0, parser_path: 'neither',
  skipped: 'header_not_found',
  note: "Read 1 sheet(s) (3 Palisade Ave) — none carried a 'Tender Types … Net … Refunds/Sub Net' header row.",
  xreport_diag: {
    parser_path: 'neither', sheets_read: 1, headers_found: 0, unmatched_labels: [], config_label_count: 0,
    upsert_attempts: 0, save_failures: 0,
    sheets: [{ sheet: '3 Palisade Ave', rows: 8, outcome: 'header_not_found',
               closest_row: { row: 2, cells: ['Tendered Amounts'], score: 3,
                              others: [{ row: 3, cells: ['Payment Media', 'Gross Sales', 'Returned', 'Net Total'], score: 2 }] } }],
    flat: { columns: ['X-Report'], rows: 7, store_col: null, tender_col: null, amount_col: null },
  },
};
const UPSERTS_FAILED = {
  success: false, file_type: 'x_report', tenders: 0, saved: 0, parser_path: 'multi_sheet',
  skipped: 'all_upserts_failed', save_failures: 3,
  first_error: 'duplicate key value violates unique constraint',
  note: 'Parsed 3 tender row(s) but EVERY database write failed — nothing was saved. First error: ' +
        'duplicate key value violates unique constraint. (commcalc.pos_tender_summary needs migration 062…)',
  xreport_diag: { parser_path: 'multi_sheet', sheets_read: 2, headers_found: 2, unmatched_labels: [],
                  upsert_attempts: 3, save_failures: 3,
                  first_error: 'duplicate key value violates unique constraint', sheets: [], flat: null },
};
const UNMAPPED_CAVEAT = {
  success: true, file_type: 'x_report', tenders: 2, saved: 2, parser_path: 'multi_sheet',
  skipped: 'x_report_unmapped_labels',
  note: 'Saved 2 tender row(s) across 1 store(s). 1 tender label(s) were NOT recognized and were ' +
        'skipped — their amounts are missing from the recon: Zelle. …',
  xreport_diag: { parser_path: 'multi_sheet', sheets_read: 1, headers_found: 1,
                  unmatched_labels: ['Zelle'], config_label_count: 0, upsert_attempts: 2,
                  save_failures: 0, sheets: [], flat: null },
};

// ── 1/2/3. the X-report branch ──────────────────────────────────────────────────────────────────
console.log('\n=== 1. THE OWNER BUG: a 0-row X-report can no longer render green ===');
const oldZero = OLD.readUploadOutcome(ALL_UNMATCHED, 'rows');
check('1a BEFORE: origin/main returned tone "ok" + "Saved 0 rows." — the green lie',
  oldZero.tone === 'ok' && oldZero.text === 'Saved 0 rows.', oldZero);
const newZero = NEW.readUploadOutcome(ALL_UNMATCHED, 'tender row(s)');
check('1b AFTER: tone "guard" (amber banner, not a green tick)', newZero.tone === 'guard', newZero);
check('1c the machine reason is in the message', newZero.text.includes('all_labels_unmatched'), newZero.text);
check('1d the unrecognized labels are named', newZero.reason.includes('Klarna'), newZero.reason);
check('1e the message says WHERE to fix it (Tender Config, x_report leg)',
  newZero.reason.includes('Tender Config'), newZero.reason);
check('1f the banner heading is honest (a parse miss, NOT "existing data protected")',
  newZero.title.startsWith('X-Report saved 0 tender rows'), newZero.title);

console.log('\n=== 2. A GOOD X-report finally reports its real count ===');
// the payload origin/main ACTUALLY returned for a successful multi-sheet X-report: no `saved` key.
const HAPPY_OLD_SHAPE = { success: true, file_type: 'x_report', tenders: 3, stores: 2,
                          date: '2026-07-27', format: 'multi-sheet' };
const oldGood = OLD.readUploadOutcome(HAPPY_OLD_SHAPE, 'rows');
check('2a BEFORE: a 3-row SUCCESS also printed "Saved 0 rows." (the response carried `tenders`; the ' +
      'interpreter only ever read `saved`)', oldGood.tone === 'ok' && oldGood.text === 'Saved 0 rows.',
  oldGood);
check('2a2 …and the NEW interpreter reads that SAME old payload as 3',
  NEW.readUploadOutcome(HAPPY_OLD_SHAPE, 'tender row(s)').text === 'Saved 3 tender row(s).',
  NEW.readUploadOutcome(HAPPY_OLD_SHAPE, 'tender row(s)'));
const newGood = NEW.readUploadOutcome(HAPPY, 'tender row(s)');
check('2b AFTER: "Saved 3 tender row(s)." + tone ok',
  newGood.tone === 'ok' && newGood.text === 'Saved 3 tender row(s).', newGood);
const legacyGood = NEW.readUploadOutcome({ ...HAPPY, saved: undefined }, 'tender row(s)');
check('2c an OLD payload (tenders only, no `saved`) is also read correctly', legacyGood.saved === 3, legacyGood);

console.log('\n=== 3. forensics reach the banner detail list ===');
check('3a header-miss: the closest row(s) are listed VERBATIM, including the real column header',
  NEW.readUploadOutcome(HEADER_MISS).details.some(d => d.includes('Payment Media') && d.includes('Net Total')),
  NEW.readUploadOutcome(HEADER_MISS).details);
check('3b header-miss: the flat fallback columns are listed too',
  NEW.readUploadOutcome(HEADER_MISS).details.some(d => d.includes('tender column = NOT FOUND')),
  NEW.readUploadOutcome(HEADER_MISS).details);
check('3c all-unmatched: per-sheet line names the header row + the skipped labels',
  NEW.readUploadOutcome(ALL_UNMATCHED).details.some(d => d.includes('header at row 3') && d.includes('Klarna')),
  NEW.readUploadOutcome(ALL_UNMATCHED).details);
const uf = NEW.readUploadOutcome(UPSERTS_FAILED);
check('3d all_upserts_failed: the first DB error is shown, tone guard',
  uf.tone === 'guard' && uf.details.some(d => d.includes('duplicate key value')), uf.details);
const um = NEW.readUploadOutcome(UNMAPPED_CAVEAT, 'tender row(s)');
check('3e rows-saved-with-unmapped-labels is amber "warn", NOT a clean green tick',
  um.tone === 'warn' && um.saved === 2, um);
check('3f its heading says the unmapped dollars are missing from the recon',
  um.title.includes('missing from the recon'), um.title);

// ── 4. no collateral drift on every pre-existing branch ─────────────────────────────────────────
console.log('\n=== 4. NO DRIFT on the pre-existing branches (old vs new interpreter) ===');
const LEGACY = {
  clean: { saved: 1234 },
  price_guard: { saved: 0, skipped: 'price_guard', shrink: [{ reason: 'degraded export' }] },
  price_guard_partial: { saved: 10, skipped: 'price_guard_partial', guarded_dates: ['2026-07-01'], shrink: [] },
  inventory_no_stores: { saved: 0, skipped: 'inventory_no_stores', note: 'parsed 0 stores' },
  inventory_devices_only: { saved: 0, devices: 3, skipped: 'inventory_devices_only', note: 'per-device' },
  shrink_only: { saved: 5, shrink: [{ key: '2026-07-01', new: 5, prior: 500 }] },
  empty: {},
  x_report_clean_no_skip: { file_type: 'x_report', saved: 0, tenders: 0 },
};
for (const [name, payload] of Object.entries(LEGACY)) {
  const a = OLD.readUploadOutcome(payload, 'row(s)');
  const b = NEW.readUploadOutcome(payload, 'row(s)');
  const same = a.tone === b.tone && a.text === b.text && a.reason === b.reason &&
    (name === 'x_report_clean_no_skip' ? true : a.saved === b.saved);
  check(`4.${name} identical to origin/main`, same, { a, b });
}

// ── 5. the page really renders the banner ───────────────────────────────────────────────────────
console.log('\n=== 5. upload/page.tsx wiring ===');
const page = fs.readFileSync(PAGE, 'utf8');
check('5a imports UploadGuardBanner + the UploadOutcome type',
  /import \{ readUploadOutcome, UploadGuardBanner, type UploadOutcome \}/.test(page));
check('5b keeps the interpreted outcome per file type', /setOutcomes\(p => \(\{ \.\.\.p, \[fileType\]: o \}\)\)/.test(page));
check('5c renders <UploadGuardBanner outcome={outcomes[id] || null} /> inside the file card',
  /<UploadGuardBanner outcome=\{outcomes\[id\] \|\| null\} \/>/.test(page));
check('5d x_report messages are counted in TENDER rows, not "rows"',
  /fileType === 'x_report' \? 'tender row\(s\)' : 'rows'/.test(page));
check('5e the endpoint still carries the /api/v1 prefix (api() needs it — curl-verified != UI-verified)',
  /apiUpload\(\s*`\/api\/v1\/commcalc\/upload\/\$\{fileType\}/.test(page));

console.log(`\n${'='.repeat(100)}\nRESULT: ${pass} passed, ${fail} failed\n${'='.repeat(100)}`);
process.exit(fail ? 1 : 0);
