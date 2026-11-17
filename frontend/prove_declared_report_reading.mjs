// Proof harness — THE UPLOAD SCREEN DEREFERENCES THE DECLARATION, frontend twin of
// backend/harness_feed_declared_metrics_lock.py (§COMMCALC-77).
//
// WHY THIS EXISTS. The backend lock proves the PARSER refuses a unit basis or a sign convention it
// does not know. That is only half a design fix: a SCREEN that types its own list of choices offers
// the operator a value the parser will refuse, and the operator reads the refusal as a bug in their
// file. Worse, a list typed here is the second copy CLAUDE.md's "one fact, one home" rule forbids —
// and a tenant's own column words typed into this file would be the per-tenant code RULE TWO bans.
//
// So this harness reads the REAL component source and the REAL endpoint source and proves:
//   §A  the three vocabularies are READ from the backend's reply, never written here
//   §B  the endpoint actually SERVES all three, from report_declaration's own homes
//   §C  the file goes to the one upload door, org-scoped, and an empty parse is not a green tick
//   §D  RULE TWO: no tenant's words are in the screen
// Negative controls prove each scan would see the defect it is there to stop.
//
// No network, no DB, no browser, no React. Run:  node frontend/prove_declared_report_reading.mjs

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = join(HERE, '..')

let pass = 0, fail = 0
const ck = (label, cond, extra) => {
  if (cond) { pass++; console.log(`  ok  ${label}`) }
  else { fail++; console.error(`  XX  ${label}${extra !== undefined ? '  — ' + String(extra).slice(0, 300) : ''}`) }
}

const EDITOR = readFileSync(join(HERE, 'src/components/commcalc/PortalReportEditor.tsx'), 'utf8')
const ROUTER = readFileSync(join(ROOT, 'backend/app/modules/commcalc/router.py'), 'utf8')
const DECL = readFileSync(join(ROOT, 'backend/app/modules/commcalc/report_declaration.py'), 'utf8')

// The component's EXECUTABLE text: comments carry the prose that explains the rule, and the rule is
// about what the code does. A `//` or `/* */` comment naming a tenant is handled by §D separately.
const CODE = EDITOR
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^[ \t]*\/\/.*$/gm, '')

console.log('\n§A — the three vocabularies are READ, not written here')
for (const key of ['bindable_fields', 'unit_bases', 'amount_signs']) {
  ck(`the screen dereferences meta?.${key}`,
     new RegExp(`meta\\?\\.${key}`).test(CODE))
}
// The parser's own words for a basis or a sign must never be typed here: offering one is how a
// screen comes to show a choice the parser refuses.
for (const word of ['payout_negative', 'payout_positive', 'payout_negative_netted']) {
  ck(`the screen does not spell the sign convention '${word}'`, !CODE.includes(word))
}
ck("the screen does not spell a unit basis value of its own",
   !/['"]column['"]\s*[,:)\]]/.test(CODE) && !/value=['"]row['"]/.test(CODE))
ck('every <option> for a basis or a sign is either blank or mapped from the reply',
   (CODE.match(/<option value=""/g) || []).length === 2)
ck('CONTROL: the component really does render options from the reply',
   /unit_bases \|\| \[\]\)\.map/.test(CODE) && /amount_signs \|\| \[\]\)\.map/.test(CODE))
ck('NEGATIVE CONTROL: a hand-typed sign option would be caught',
   ['payout_negative'].some(w => '<option value="payout_negative">'.includes(w)))

console.log('\n§B — the endpoint serves all three, from the declaration\'s own homes')
const block = ROUTER.slice(ROUTER.indexOf('"control_fields": list(rd.CONTROL_FIELDS)'))
  .slice(0, 1600)
ck('bindable_fields is served from rd.BINDABLE_FIELDS', /"bindable_fields": list\(rd\.BINDABLE_FIELDS\)/.test(block))
ck('unit_bases is served from rd.UNIT_BASES', /"unit_bases":[\s\S]{0,200}rd\.UNIT_BASES/.test(block))
ck('amount_signs is served from rd.AMOUNT_SIGNS', /"amount_signs":[\s\S]{0,200}rd\.AMOUNT_SIGNS/.test(block))
// and those homes are themselves dereferenced from the parser, never restated
ck('the declaration dereferences the parser rather than restating its vocabulary',
   /UNIT_BASES = _feed\.UNIT_BASES/.test(DECL) && /AMOUNT_SIGNS = _feed\.AMOUNT_SIGNS/.test(DECL)
   && /BINDABLE_FIELDS = tuple\(_feed\.COLUMN_SYNONYMS\)/.test(DECL))
ck('the three reading fields round-trip through the declaration row',
   /READING_FIELDS = \("column_bindings", "unit_basis", "amount_sign"\)/.test(DECL))

console.log('\n§C — one door, org-scoped, and an empty parse is not a green tick')
ck('the file is sent to the one upload door', /commcalc\/pos-report\/upload/.test(CODE))
ck('the post is org-scoped, from the client\'s own ORG_ID', /org_id=\$\{ORG_ID\}/.test(CODE)
   && /import \{[^}]*ORG_ID[^}]*\} from '@\/lib\/client'/.test(EDITOR))
ck('exactly one place in the screen sends a file',
   (CODE.match(/pos-report\/upload/g) || []).length === 1)
ck('the green tick is the BACKEND\'s `delivered`, not a row count read here',
   /landed\[r\.key\]\.delivered \? /.test(CODE) && !/rows_ingested[^\n]*> 0 \?/.test(CODE))
ck('what matched no declared category is SHOWN, never dropped',
   /unresolved_count/.test(CODE) && /\.unresolved \|\| \[\]/.test(CODE))
ck('a binding that names no column is reported by the backend, so the screen shows its warnings',
   /\.warnings \|\| \[\]/.test(CODE))
ck('NEGATIVE CONTROL: a tick computed on this screen would be caught',
   /rows_ingested[^\n]*> 0 \?/.test('x = landed.rows_ingested > 0 ? "ok" : "no"'))

console.log('\n§D — RULE TWO: no tenant\'s words are in the screen')
const FORBIDDEN = ['nda', '2da', 'ups domestic', 'mailbox', 'overnight', 'notary', 'luxelink',
                   'cellfonz', 'boost', 'logistics']
const spells = (text, word) =>
  new RegExp(`(?<![a-z0-9])${word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-z0-9])`)
    .test(text.toLowerCase())
for (const word of FORBIDDEN) ck(`the editor does not spell '${word}'`, !spells(EDITOR, word))
ck('CONTROL: the word scan finds a real mention and is not fooled by "upsert"',
   spells('a notary service', 'notary') && !spells('upsert', 'ups'))

console.log(`\n${pass} passed, ${fail} failed`)
if (fail) process.exit(1)
console.log('OK — the screen asks the backend what the choices are, and the backend asks the parser.')
