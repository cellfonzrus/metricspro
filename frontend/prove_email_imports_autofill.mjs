// Proof harness — the IMAP mailbox form must never be AUTOFILLED with the owner's site login (2026-09-20).
//
// Reported: on a tenant with NO mailbox config row, the browser's password manager injected the
// owner's own login email into the IMAP Username box with a phantom password — it read as a
// cross-tenant leak, and saving it would have persisted their login as the IMAP username.
//
// The hardening is attributes on the two inputs, so this is a static scan of the REAL page source:
//   Username  → autoComplete="off",          id/name = imap_user   (not a login field name)
//   Password  → autoComplete="new-password", id/name = imap_pass   (managers do not fill new-password)
//
// Run:  node frontend/prove_email_imports_autofill.mjs      (no network, no DB, no browser)

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const HERE = dirname(fileURLToPath(import.meta.url))
const src = readFileSync(join(HERE, 'src/app/(platform)/commcalc/email-imports/page.tsx'), 'utf8')

let pass = 0, fail = 0
const ck = (label, cond) => { if (cond) { pass++; console.log(`  ok  ${label}`) } else { fail++; console.error(`  XX  ${label}`) } }

// the two inputs, found by their bound state (cfg.username / pwd) so a re-layout cannot dodge the check
const userInput = src.match(/<input(?:=>|[^>])*value=\{cfg\.username \|\| ''\}(?:=>|[^>])*>/)?.[0] || ''
const passInput = src.match(/<input(?:=>|[^>])*value=\{pwd\}(?:=>|[^>])*>/)?.[0] || ''
ck('the IMAP Username input exists (bound to cfg.username)', !!userInput)
ck('the IMAP Password input exists (bound to pwd)', !!passInput)
ck('Username carries autoComplete="off"', /autoComplete="off"/.test(userInput))
ck('Username is named imap_user (id + name), not a login-shaped name', /id="imap_user"/.test(userInput) && /name="imap_user"/.test(userInput))
ck('Password carries autoComplete="new-password"', /autoComplete="new-password"/.test(passInput))
ck('Password is named imap_pass (id + name)', /id="imap_pass"/.test(passInput) && /name="imap_pass"/.test(passInput))
ck('Password stays a password field', /type="password"/.test(passInput))
ck('the existing behaviour is kept: the same onChange handlers', /onChange=\{e => set\(\{ username: e\.target\.value \}\)\}/.test(userInput) && /onChange=\{e => setPwd\(e\.target\.value\)\}/.test(passInput))

console.log(`\nprove_email_imports_autofill: ${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
