/*
 * FRONTEND half of the upload-tiles-honesty package (2026-07-30), proven against the REAL shipped
 * source — `next build` cannot run in this environment (a known toolchain limitation; the canonical
 * repo fails the same Turbopack build on origin/main), so the real _lib/lastUpload.tsx is transpiled
 * with the repo's own TypeScript and its exports are executed against the EXACT payloads
 * backend/harness_upload_last.py produced from the real handler.
 *
 * Proves:
 *   1. the "Last upload" line reads the ingest that LANDED rows, and a NEWER refused attempt renders as
 *      its own amber warning — so a tile can never imply fresh data arrived when the last file was
 *      refused (the whole point of the endpoint);
 *   2. coverage text handles a period label, a single day, a multi-day span, and a multi-period
 *      historical load — and 'YYYY-MM-DD' is formatted WITHOUT `new Date("YYYY-MM-DD")` (the documented
 *      UTC off-by-one in this codebase), verified by forcing a US timezone;
 *   3. a never-uploaded report says so explicitly, and an UNTRACKED module upload renders NOTHING
 *      (rather than the lie "no data uploaded yet");
 *   4. the API call carries the explicit /api/v1 prefix — a bare path 404s in the app while passing a
 *      curl check (curl-verified != UI-verified) — and an org_id query param;
 *   5. upload/page.tsx: every FILE_TYPES id and every MODULE_UPLOADS traceKey is asked for; the button
 *      verb follows the report's real write semantics; and the mode map matches the backend's keying.
 *
 * Run:  TZ=America/Los_Angeles node backend/scratchpad/upload_last_ui_proof.js
 */
const fs = require('fs');
const path = require('path');

const REPO = path.join(__dirname, '..', '..');
const ts = (() => {
  for (const p of [path.join(REPO, 'frontend', 'node_modules', 'typescript'),
                   '/workspaces/metricspro/frontend/node_modules/typescript']) {
    try { return require(p); } catch (e) { /* next */ }
  }
  throw new Error('typescript not found');
})();

const MOD = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', '_lib', 'lastUpload.tsx');
const PAGE = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', 'upload', 'page.tsx');
const MA_PAGE = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', 'ma-upload', 'page.tsx');

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + '   ' + JSON.stringify(extra)); }
};

// Flatten a fake-React element tree to its text, so the rendered LINE is asserted, not just a helper.
function text(node) {
  if (node == null || node === false) return '';
  if (Array.isArray(node)) return node.map(text).join('');
  if (typeof node === 'object' && node.c) return text(node.c);
  return String(node);
}

function load(src) {
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  const module = { exports: {} };
  const apiCalls = [];
  const fakeReact = {
    createElement: (t, p, ...c) => ({ t, p, c }),
    Fragment: 'Fragment',
    useState: (v) => [v, () => {}],
    useEffect: () => {},
    useCallback: (f) => f,
  };
  const req = (name) => {
    if (name === 'react') return fakeReact;
    if (name === '@/lib/client') {
      return { ORG_ID: 'ORG-UNDER-TEST', api: (u) => { apiCalls.push(u); return Promise.resolve({ reports: {} }); } };
    }
    return {};
  };
  new Function('module', 'exports', 'require', 'React', js)(module, module.exports, req, fakeReact);
  return { M: module.exports, apiCalls };
}

const { M, apiCalls } = load(fs.readFileSync(MOD, 'utf8'));

// ── payloads verbatim from harness_upload_last.py ────────────────────────────────────────────────
const LANDED_WITH_REFUSAL = {
  key: 'daily_sales', last_at: '2026-07-14T09:00:00+00:00', rows_saved: 4533, status: 'ok',
  origin: 'upload_trace', source: 'email_sweep', source_label: 'email feed', filename: 'f.csv',
  period: 'July 2026', periods: { 'July 2026': 4533 }, span: ['2026-07-01', '2026-07-14'], days: 3,
  latest_attempt: { at: '2026-07-14T15:00:00+00:00', rows_saved: 0, status: 'skipped',
                    skipped: 'price_guard', source: 'email_sweep', source_label: 'email feed' },
};
const CLEAN = { ...LANDED_WITH_REFUSAL, latest_attempt: null };
const MULTI = { key: 'ma_commission', last_at: '2026-07-27T10:00:00+00:00', rows_saved: 3100,
                source: 'manual', source_label: 'manual upload',
                periods: { 'May 2026': 1000, 'June 2026': 2100 }, period: null,
                span: ['2026-05-02', '2026-06-11'], days: 2, latest_attempt: null };
const NEVER = { key: 'catalog', last_at: null, rows_saved: null, latest_attempt: null };

console.log('\n── 1. the honesty line: a refused newer attempt never becomes "last upload" ───────────');
const t1 = text(M.LastUploadLine({ rec: LANDED_WITH_REFUSAL, loaded: true }));
check('the landed ingest is what "Last upload" reports (4,533 rows)',
  /Last upload/.test(t1) && /4,533 rows/.test(t1), t1);
check('the newer 0-row attempt renders as its OWN amber warning',
  /Newer attempt/.test(t1) && /saved no rows/.test(t1), t1);
check('the refusal reason is humanized (price_guard → "price guard")',
  /\(price guard\)/.test(t1), t1);
check('it points at the existing forensics surface ("Where are my rows?")',
  /Where are my rows\?/.test(t1), t1);
check('a clean record shows NO warning line (no phantom alarm)',
  !/Newer attempt/.test(text(M.LastUploadLine({ rec: CLEAN, loaded: true }))));
check('the ingest PATH is named so a tile says where the data came from',
  /via email feed/.test(t1), t1);

console.log('\n── 2. coverage text: period / single day / span / multi-period ────────────────────────');
check('a multi-day span reads as a range with a day count',
  M.coverageText(LANDED_WITH_REFUSAL) === `${M.fmtDay('2026-07-01')} – ${M.fmtDay('2026-07-14')} (3 days)`,
  M.coverageText(LANDED_WITH_REFUSAL));
check('a single-day file reads as one day, not a range',
  M.coverageText({ span: ['2026-07-14', '2026-07-14'], days: 1 }) === M.fmtDay('2026-07-14'));
check('a period-grain report reads its period label', M.coverageText({ period: 'June 2026' }) === 'June 2026');
// The span is the MORE precise statement, so it leads — but a load that touched two months must not
// read as one, so the period count is appended.
check('a multi-month historical load shows its day span AND that it touched 2 periods',
  /May 2 – Jun 11 \(2 days\) · 2 periods/.test(M.coverageText(MULTI)), M.coverageText(MULTI));
check('a single-period load does NOT get a redundant "1 periods" suffix',
  !/periods/.test(M.coverageText(LANDED_WITH_REFUSAL)), M.coverageText(LANDED_WITH_REFUSAL));
check('with NO span, a multi-period load still names the periods',
  /2 periods: May 2026, June 2026/.test(M.coverageText({ ...MULTI, span: null, days: null })),
  M.coverageText({ ...MULTI, span: null, days: null }));
check('no coverage info → empty string (renders nothing, invents nothing)',
  M.coverageText({}) === '');
check(`fmtDay('2026-07-01') does NOT slip a day west of Greenwich (TZ=${process.env.TZ || 'system'})`,
  /Jul/.test(M.fmtDay('2026-07-01')) && /1\b/.test(M.fmtDay('2026-07-01')), M.fmtDay('2026-07-01'));
check('a malformed day passes through instead of rendering "Invalid Date"',
  M.fmtDay('nonsense') === 'nonsense' && M.fmtStamp('nonsense') === 'nonsense');
check('fmtStamp on an empty value renders nothing', M.fmtStamp(null) === '' && M.fmtStamp('') === '');

console.log('\n── 3. never-uploaded vs UNTRACKED are different statements ────────────────────────────');
check('a report with no ingest says so explicitly',
  /No data uploaded yet/.test(text(M.LastUploadLine({ rec: NEVER, loaded: true }))));
check('an UNTRACKED module upload renders NOTHING (asset ledger / daily closing write no journal)',
  text(M.LastUploadLine({ rec: null, loaded: true, tracked: false })) === '');
check('nothing renders before the fetch lands (no "No data" flash)',
  text(M.LastUploadLine({ rec: NEVER, loaded: false })) === '');

console.log('\n── 4. the API call is UI-valid, not just curl-valid ──────────────────────────────────');
const hook = M.useLastUploads(['sales', 'daily_sales']);
hook.reload();
check('the request carries the explicit /api/v1 prefix (a bare path 404s in the app)',
  apiCalls.length === 1 && apiCalls[0].startsWith('/api/v1/commcalc/upload/last?'), apiCalls);
check('...and an org_id query param (RULE ONE: middleware rewrites the PARAM)',
  /[?&]org_id=/.test(apiCalls[0]), apiCalls[0]);
check('...and the report keys it was asked for', /types=sales%2Cdaily_sales/.test(apiCalls[0]), apiCalls[0]);

console.log('\n── 5. the pages ask for every key and state the real write semantics ─────────────────');
const page = fs.readFileSync(PAGE, 'utf8');
const ma = fs.readFileSync(MA_PAGE, 'utf8');
const MODE_RE = '(additive_daily|additive_keyed|replace_period|replace_all)';
// Scope each scan to its own block — FILE_TYPES declares its mode inline, MODULE_UPLOADS on the next line.
const ftBlock = page.slice(page.indexOf('const FILE_TYPES'), page.indexOf('const PERIODLESS'));
const muBlock = page.slice(page.indexOf('const MODULE_UPLOADS'), page.indexOf('const MODULE_LINKS'));
const ids = [...ftBlock.matchAll(/\{ id: '([a-z_]+)',/g)].map(m => m[1]);
check('every FILE_TYPES tile declares a mode', ids.length >= 14
  && ids.every(id => new RegExp(`id: '${id}',[^\\n]*mode: '${MODE_RE}'`).test(ftBlock)),
  { count: ids.length, missing: ids.filter(id => !new RegExp(`id: '${id}',[^\\n]*mode: '`).test(ftBlock)) });
const mids = [...muBlock.matchAll(/\{ id: '([a-z_]+)',/g)].map(m => m[1]);
check('every MODULE_UPLOADS tile declares a mode too', mids.length === 4
  && mids.every(id => new RegExp(`id: '${id}',[\\s\\S]{0,400}?mode: '${MODE_RE}'`).test(muBlock)),
  { count: mids.length, ids: mids });
check('the module uploads whose endpoints write NO ingest journal are marked untracked',
  /id: 'asset_ledger',[\s\S]{0,400}?tracked: false/.test(muBlock)
  && /id: 'daily_closing',[\s\S]{0,400}?tracked: false/.test(muBlock));
check('and the two that DO record an ingest declare the keys they trace under',
  /id: 'hotsheet',[\s\S]{0,400}?traceKeys: \['hotsheet'\]/.test(muBlock)
  && /id: 'vip_workbook',[\s\S]{0,400}?traceKeys: \['vip_workbook', 'vip_invoices'\]/.test(muBlock));
check('LAST_UPLOAD_KEYS is built from the tiles + module traceKeys (no hand-maintained list)',
  /const LAST_UPLOAD_KEYS = \[\s*\.\.\.FILE_TYPES\.map\(t => t\.id\),\s*\.\.\.MODULE_UPLOADS\.flatMap\(m => m\.traceKeys \|\| \[\]\),/.test(page));
// The four DATE-KEYED reports the backend delete-then-inserts PER DAY must read 'additive_daily'.
for (const id of ['daily_sales', 'ma_commission', 'ma_daily_tx', 'ma_fulfillment']) {
  check(`${id} (backend DATE_KEYED, per-day replace) says "Upload additional file"`,
    new RegExp(`id: '${id}'[^\\n]*mode: 'additive_daily'`).test(page));
}
for (const id of ['sales', 'payment_detail', 'mi_report', 'dlar_rep', 'dlar_store', 'comp_report']) {
  check(`${id} (backend has_period, period delete) says "Replace period file"`,
    new RegExp(`id: '${id}'[^\\n]*mode: 'replace_period'`).test(page));
}
for (const id of ['catalog', 'master_cats']) {
  check(`${id} (backend whole-table wipe) says "Replace all data"`,
    new RegExp(`id: '${id}'[^\\n]*mode: 'replace_all'`).test(page));
}
for (const id of ['x_report', 'inventory_aging']) {
  check(`${id} (backend keyed upsert, clears nothing outside the file) says "additional file"`,
    new RegExp(`id: '${id}'[^\\n]*mode: 'additive_keyed'`).test(page));
}
check('the verb only changes once a report HAS data (first upload still says "Choose File")',
  /const modeVerb = \(mode: UploadMode, prior: boolean\) => \(prior \? MODE_UI\[mode\]\.verb : '📂 Choose File'\)/.test(page));
check('"has data" for the verb = ANY prior ingest, not just the selected period',
  /const everLanded = !!prior \|\| !!lastData\[id\]\?\.last_at/.test(page));
check('an incomplete ingest journal is disclosed instead of reading as "never uploaded"',
  /Last-upload history is incomplete on this deployment/.test(page));
check('the tile renders <LastUploadLine> and the untracked flag',
  /<LastUploadLine rec=\{lastData\[id\]\} loaded=\{lastLoaded\} \/>/.test(page)
  && /tracked=\{entry\.tracked !== false\}/.test(page));
check('the line refreshes after an upload on this page (both upload paths)',
  (page.match(/loadHistory\(\); reloadLast\(\)/g) || []).length === 2);
check('ma-upload asks under the report_key its ingest traces under, and reloads after upload',
  /const keys = useMemo\(\(\) => \[report\.report_key\], \[report\.report_key\]\)/.test(ma)
  && /reloadLast\(\)/.test(ma) && /<LastUploadLine rec=\{last\[report\.report_key\]\}/.test(ma));

console.log('\n══════════════════════════════════════════════════════════════════════════════════════');
console.log(`  ${pass} passed, ${fail} failed`);
console.log('══════════════════════════════════════════════════════════════════════════════════════');
process.exit(fail ? 1 : 0);
