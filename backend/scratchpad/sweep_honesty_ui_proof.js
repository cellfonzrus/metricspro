/*
 * FRONTEND half of the FTP/email sweep honest-zero package (2026-07-30), proven against the REAL
 * shipped source — `next build` cannot run in this environment (a known toolchain limitation; the
 * canonical repo fails the same Turbopack build on origin/main), so the real _lib/sweepOutcome.tsx is
 * transpiled with the repo's own TypeScript and its exports are executed against the EXACT journal
 * rows + run payloads the backend harness (harness_sweep_honesty.py) produced.
 *
 * Proves:
 *   1. every status the backend can record renders a REASON — incl. the two the pages' old
 *      `status === 'ok' ? … : ✕` pair rendered as a bare red ✕: 'empty' and 'ignored';
 *   2. a clean ingest is still a green ✓ with rows, and an ok-with-caveat is still AMBER (the shipped
 *      behaviour of the email page, now shared with the FTP page);
 *   3. a 0-row row can never render without an explanation, even with `detail` null;
 *   4. retry semantics are stated on screen (the operator can tell "will retry" from "terminal");
 *   5. OLD-vs-NEW differential against `origin/main`'s FTP page: its interpreter showed an 'empty' /
 *      'ignored' outcome as `✕ null` (a red X with the word "null"), and an ok-with-caveat as a clean
 *      green tick;
 *   6. both pages actually import + render the shared cell, and the run-summary reports journal-write
 *      failures + retries.
 *
 * Run:  node backend/scratchpad/sweep_honesty_ui_proof.js
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const REPO = path.join(__dirname, '..', '..');
// Resolve TypeScript from THIS checkout's node_modules first, falling back to the canonical repo's —
// a hard-coded absolute worktree path is exactly what broke universal_ingest_proof.py for two weeks.
const ts = (() => {
  for (const p of [path.join(REPO, 'frontend', 'node_modules', 'typescript'),
                   '/workspaces/metricspro/frontend/node_modules/typescript']) {
    try { return require(p); } catch (e) { /* try the next */ }
  }
  throw new Error('typescript not found — run npm i in frontend/ or symlink node_modules');
})();
const REL = path.join('frontend', 'src', 'app', '(platform)', 'commcalc', '_lib', 'sweepOutcome.tsx');
const MOD = path.join(REPO, REL);
const FTP_PAGE = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', 'ftp-imports', 'page.tsx');
const EMAIL_PAGE = path.join(REPO, 'frontend', 'src', 'app', '(platform)', 'commcalc', 'email-imports', 'page.tsx');

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + '   ' + JSON.stringify(extra)); }
};

function load(src) {
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', 'React', js)(
    module, module.exports, () => ({}), { createElement: (t, p, ...c) => ({ t, p, c }) });
  return module.exports;
}

const S = load(fs.readFileSync(MOD, 'utf8'));

// ── journal rows exactly as harness_sweep_honesty.py recorded them ───────────────────────────────
const ROWS = {
  clean:      { status: 'ok', rows_saved: 4533, detail: null, skipped: null },
  caveat:     { status: 'ok', rows_saved: 743, skipped: 'inventory_devices_only',
                detail: '0 stores (no store column found) · 743 device row(s) saved' },
  guard:      { status: 'skipped', rows_saved: 0, skipped: 'price_guard',
                detail: 'kept existing data for 2026-07-13 — a degraded export' },
  xzero:      { status: 'skipped', rows_saved: 0, skipped: 'header_not_found',
                detail: "Read 3 sheet(s) — none carried a 'Tender Types … Net' header row" },
  empty:      { status: 'empty', rows_saved: 0, skipped: null,
                detail: 'the file was read but produced 0 ingestable rows — nothing was saved and existing data was left untouched' },
  emptyNoDet: { status: 'empty', rows_saved: 0, skipped: null, detail: null },
  ignored:    { status: 'ignored', rows_saved: 0, skipped: null,
                detail: "'sales_trend' is a derived report with no importer — ignored." },
  error:      { status: 'error', rows_saved: 0, skipped: null,
                detail: "This doesn't look like the right file for 'sales'" },
  dlfail:     { status: 'download_failed', rows_saved: 0, detail: 'RETR timeout' },
};

console.log('\n── 1. every status renders a reason; none is a bare glyph ────────────────────────────');
for (const [k, row] of Object.entries(ROWS)) {
  const t = S.sweepText(row);
  check(`${k}: renders a non-empty sentence`, typeof t === 'string' && t.trim().length > 8, t);
}
check("a 0-row row NEVER renders without an explanation (even with detail=null)",
  Object.values(ROWS).filter(r => !r.rows_saved).every(r => S.sweepText(r).replace(/^0 rows\s*—?\s*/, '').trim().length > 5),
  Object.values(ROWS).filter(r => !r.rows_saved).map(r => S.sweepText(r)));

console.log('\n── 2. tone: green only for a clean ingest ────────────────────────────────────────────');
check('clean ingest = green ✓', S.sweepTone(ROWS.clean).color === '#16794a' && S.sweepTone(ROWS.clean).glyph === '✓');
check('ok WITH a caveat = amber ⚠ (not a clean tick)', S.sweepTone(ROWS.caveat).color === '#b45309');
check('price-guard refusal = amber ⚠', S.sweepTone(ROWS.guard).color === '#b45309');
check("read-but-empty = amber ∅ (was a red ✕)", S.sweepTone(ROWS.empty).glyph === '∅' && S.sweepTone(ROWS.empty).color === '#b45309');
check("no-importer = muted – (not an error)", S.sweepTone(ROWS.ignored).glyph === '–' && S.sweepTone(ROWS.ignored).color === 'var(--text3)');
check('error = red ✕', S.sweepTone(ROWS.error).color === '#dc2626');
check('no green tone is EVER produced for a 0-row outcome',
  Object.values(ROWS).filter(r => !r.rows_saved).every(r => S.sweepTone(r).color !== '#16794a'));

console.log('\n── 3. retry semantics are visible to the operator ────────────────────────────────────');
check('skipped + error say "will retry next sweep"',
  /will retry/.test(S.sweepText(ROWS.guard)) && /will retry/.test(S.sweepText(ROWS.error))
  && /will retry/.test(S.sweepText(ROWS.dlfail)));
check('terminal outcomes do NOT claim a retry',
  !/will retry/.test(S.sweepText(ROWS.clean)) && !/will retry/.test(S.sweepText(ROWS.empty))
  && !/will retry/.test(S.sweepText(ROWS.ignored)));
check('sweepTone.retries matches the backend `terminal` contract',
  S.sweepTone(ROWS.guard).retries === true && S.sweepTone(ROWS.error).retries === true
  && S.sweepTone(ROWS.clean).retries === false && S.sweepTone(ROWS.empty).retries === false
  && S.sweepTone(ROWS.ignored).retries === false);
check('an empty row says nothing was overwritten (the operator\'s real question)',
  /nothing was overwritten/.test(S.sweepText(ROWS.empty)));

console.log('\n── 4. OLD-vs-NEW differential vs origin/main\'s FTP-page interpreter ─────────────────');
// origin/main had NO shared module; the FTP page rendered this expression inline:
//   p.status === 'ok' ? `✓ ${p.rows_saved} rows` : `✕ ${p.detail}`
const oldFtpSrc = execFileSync('git', ['-C', REPO, 'show',
  'origin/main:frontend/src/app/(platform)/commcalc/ftp-imports/page.tsx']).toString();
check('origin/main FTP page really used the ok/else pair (anchor still present there)',
  oldFtpSrc.includes("p.status === 'ok' ? <span style={{ color: '#16794a' }}>✓ {p.rows_saved} rows</span> : <span style={{ color: '#dc2626' }}>✕ {p.detail}</span>"));
const oldFtp = (p) => (p.status === 'ok'
  ? { color: '#16794a', text: `✓ ${p.rows_saved} rows` }
  : { color: '#dc2626', text: `✕ ${p.detail}` });
for (const k of ['empty', 'ignored']) {
  const o = oldFtp(ROWS[k]);
  const n = { color: S.sweepTone(ROWS[k]).color, glyph: S.sweepTone(ROWS[k]).glyph, text: S.sweepText(ROWS[k]) };
  // The claim: the OLD page rendered these as an ERROR (red ✕) — which they are not — while the NEW
  // one renders them in their own tone and never as an error.
  check(`${k}: OLD rendered it as a RED ✕ ERROR, NEW does not (${n.glyph} ${n.color})`,
    o.color === '#dc2626' && n.color !== '#dc2626' && n.glyph !== '✕' && n.text.trim().length > 8);
}
check('emptyNoDet: OLD rendered the literal string "null" to the user',
  oldFtp(ROWS.emptyNoDet).text === '✕ null' && !/null/.test(S.sweepText(ROWS.emptyNoDet)));
check('caveat: OLD showed a CLEAN GREEN tick on an ingest with a caveat',
  oldFtp(ROWS.caveat).color === '#16794a' && S.sweepTone(ROWS.caveat).color === '#b45309');
check('clean: OLD and NEW agree (no collateral drift on the happy path)',
  oldFtp(ROWS.clean).color === S.sweepTone(ROWS.clean).color
  && S.sweepText(ROWS.clean) === '4,533 rows');

console.log('\n── 5. run-payload summary reports what "0 ingested" actually means ───────────────────');
const RUN = {
  ok: true, ingested: 1, retried: 2, journal_failures: 1, journal_first_error: 'permission denied',
  files: [
    { file: 'a.csv', status: 'ok', rows_saved: 4533, detail: null },
    { file: 'b.csv', status: 'empty', rows_saved: 0, detail: 'read but produced 0 ingestable rows' },
    { file: 'c.csv', status: 'ignored', rows_saved: 0, detail: "'sales_trend' has no importer" },
    { file: 'd.csv', status: 'skipped', rows_saved: 0, skipped: 'price_guard', detail: 'kept existing data' },
    { file: 'e.csv', status: 'skipped', rows_saved: 0, skipped: 'header_not_found', detail: 'no header row' },
    { file: 'f.csv', status: 'error', rows_saved: 0, detail: 'boom' },
    { file: 'g.csv', status: 'download_failed', rows_saved: 0, detail: 'RETR timeout' },
  ],
};
const sum = S.summarizeSweepRun(RUN);
for (const [what, re] of [['price-guard refusals', /refused by the price guard/],
                          ['other 0-row saves', /saved 0 rows/],
                          ['read-but-empty files', /no ingestable rows/],
                          ['ignored types', /ignored \(no importer/],
                          ['errors', /errored/],
                          ['download failures', /download failure/],
                          ['retries', /2 retried after a previous failure/],
                          ['journal-write failures', /history row\(s\) could not be recorded/]]) {
  check(`the run summary names ${what}`, re.test(sum), sum);
}
check('an all-clean run summarizes to nothing (no invented warnings)',
  S.summarizeSweepRun({ ok: true, ingested: 1, files: [{ file: 'a', status: 'ok', rows_saved: 5 }] }) === '');

console.log('\n── 6. both pages adopt the shared cell + summary ─────────────────────────────────────');
const ftp = fs.readFileSync(FTP_PAGE, 'utf8');
const eml = fs.readFileSync(EMAIL_PAGE, 'utf8');
for (const [name, src] of [['ftp-imports', ftp], ['email-imports', eml]]) {
  check(`${name}/page.tsx imports the shared module`,
    /from '\.\.\/_lib\/sweepOutcome'/.test(src));
  check(`${name}/page.tsx renders <SweepStatusCell row={p} />`,
    /<SweepStatusCell row=\{p\} \/>/.test(src));
  check(`${name}/page.tsx uses summarizeSweepRun for the run banner`,
    /summarizeSweepRun\(r\)/.test(src));
}
check('the FTP page no longer contains the old ok/else status expression',
  !ftp.includes("p.status === 'ok' ? <span"));
check('the email page no longer contains its old inline status ladder',
  !eml.includes("? <span style={{ color: '#b45309' }} title={p.detail || ''}>⚠ 0 rows"));
check('the FTP run banner no longer claims a bare "Ingested N file(s)" on a 0-row run',
  !ftp.includes("setMsg(r.ok ? `✅ Ingested ${r.ingested} file(s).`"));

console.log('\n══════════════════════════════════════════════════════════════════════════════════════');
console.log(`  ${pass} passed, ${fail} failed`);
console.log('══════════════════════════════════════════════════════════════════════════════════════');
process.exit(fail ? 1 : 0);
