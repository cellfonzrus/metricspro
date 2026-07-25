/*
 * Gate-1 follow-up ③ — FRONTEND half, proven against the REAL shipped source.
 *
 * `next build` cannot run in this environment (the canonical repo at origin/main dc01434 fails the same
 * Turbopack build with 154 module-resolution errors — an environment/toolchain limitation, not this
 * change), so the page's two behavioural additions are proven by EXTRACTING them from the real
 * page.tsx text and executing them:
 *   1. dedupeCats(...)  — case-variant category options collapse to one, FILE spelling preferred
 *   2. the debounce      — `search` no longer appears in the loader's dependency array; `searchQ`
 *                          (set on a 350ms timer) does, so a keystroke can't fire a request
 *
 * Run:  node backend/scratchpad/catalog_page_nit3_proof.js
 */
const fs = require('fs');
const path = require('path');

const PAGE = path.join(__dirname, '..', '..', 'frontend', 'src', 'app', '(platform)',
                       'commcalc', 'catalog', 'page.tsx');
const src = fs.readFileSync(PAGE, 'utf8');

let pass = 0, fail = 0;
const check = (name, cond, extra = '') => {
  if (cond) { pass++; console.log('  ok  ' + name); }
  else { fail++; console.log('FAIL  ' + name + '   ' + extra); }
};

// ── 1. extract + run the REAL dedupeCats ────────────────────────────────────────────────────────────
const start = src.indexOf('function dedupeCats(');
const end = src.indexOf('\n}\n', start) + 3;
check('dedupeCats is present in page.tsx', start > 0 && end > start);
const tsFn = src.slice(start, end);
const jsFn = tsFn
  .replace('function dedupeCats(values: (string | null | undefined)[], prefer?: string): string[] {',
           'function dedupeCats(values, prefer) {')
  .replace('const out = new Map<string, string>()', 'const out = new Map()');
check('the extracted function has no TS types left', !/[:<]\s*string/.test(jsFn), jsFn.slice(0, 120));
// eslint-disable-next-line no-eval
const dedupeCats = eval('(' + jsFn.replace('function dedupeCats', 'function') + ')');

let r = dedupeCats(['Accessories', 'accessories', 'Handsets']);
check('N1 case variants collapse to ONE option', r.length === 2, JSON.stringify(r));
check('N2 the mixed-case (file) spelling is the survivor', r.includes('Accessories'), JSON.stringify(r));
r = dedupeCats(['accessories', 'Accessories']);
check('N3 …regardless of which order they arrive in', r.length === 1 && r[0] === 'Accessories',
      JSON.stringify(r));
r = dedupeCats(['ACCESSORIES', 'accessories'], 'accessories');
check('N4 an explicit `prefer` (the row file spelling) wins', r.length === 1 && r[0] === 'accessories',
      JSON.stringify(r));
r = dedupeCats(['  Cases  ', 'cases', '', null, undefined]);
check('N5 blanks/nulls dropped and values trimmed', r.length === 1 && r[0] === 'Cases', JSON.stringify(r));
r = dedupeCats(['zeta', 'Alpha', 'mid']);
check('N6 sorted case-insensitively', JSON.stringify(r) === JSON.stringify(['Alpha', 'mid', 'zeta']),
      JSON.stringify(r));
r = dedupeCats(['Screen Protector', 'Cases', 'Audio']);
check('N7 genuinely distinct categories are all kept', r.length === 3, JSON.stringify(r));

// the value a <select> resolves to must still find its option, whatever the stored casing
const opts = dedupeCats(['Accessories', 'accessories'], 'Accessories');
const cur = opts.find(c => c.toLowerCase() === 'accessories'.trim().toLowerCase()) || '';
check('N8 the row select still resolves its effective (lowercased) category to an option',
      cur === 'Accessories', cur);

// ── 2. the debounce wiring ──────────────────────────────────────────────────────────────────────────
check('N9 a debounced searchQ state exists', /const \[searchQ, setSearchQ\] = useState\('' *\)/.test(src));
check('N10 it is set on a timer, not on every keystroke',
      /setTimeout\(\(\) => setSearchQ\(search\.trim\(\)\), 350\)/.test(src));
check('N11 the timer is cleaned up (no stale fetch after unmount/retype)',
      /return \(\) => clearTimeout\(t\)/.test(src));
const dep = src.match(/\}, \[fCat, ([a-zA-Z]+), onlyOv\]\)/);
check('N12 the loader depends on searchQ, NOT on the raw keystroke state',
      dep && dep[1] === 'searchQ', dep ? dep[1] : 'no dep array found');
check('N13 the request itself carries the debounced value',
      /if \(searchQ\) qs\.set\('search', searchQ\)/.test(src));
check('N14 the typed value still drives the input (controlled, no lag)',
      /value=\{search\} onChange=\{e => setSearch\(e\.target\.value\)\}/.test(src));
check('N15 the row option list is de-duped too, preferring the file spelling',
      /dedupeCats\(\[\.\.\.cats, r\.file_category, r\.effective_category\], r\.file_category\)/.test(src));
check('N16 the top filter list is de-duped at load',
      /setCats\(dedupeCats\(d\?\.categories \|\| \[\]\)\)/.test(src));

console.log('\n' + '='.repeat(70) + `\nPASS ${pass}   FAIL ${fail}\n` + '='.repeat(70));
process.exit(fail ? 1 : 0);
