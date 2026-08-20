// Proves the two decisions behind the Google link form, offline.
//
// The bug this exists to keep dead: the consent round trip navigates away and back, so every field
// held in component state returns empty. The form read those empty fields as "nothing saved",
// disabled the button, and the operator — looking at a blank form holding a project id the server
// had stored minutes earlier — reported that it does not save. Both decisions below now read the
// SERVER's state, not the form's, and the round trip finishes itself.
import { linkBlocker, oauthReturn } from './src/lib/vision.ts'

let pass = 0, fail = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (ok) pass++
  else { fail++; console.log(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`) }
}
const blank = { project: '', clientId: '', secret: '' }

// ── linkBlocker: what is still missing ─────────────────────────────────────────────────────────
eq('nothing anywhere -> asks for the project id',
  linkBlocker({}, blank), 'Enter the Device Access project id.')
eq('project typed, nothing else -> asks for the client id',
  linkBlocker({}, { ...blank, project: 'c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3' }), 'Enter the OAuth client id.')
eq('project + client typed -> asks for the secret',
  linkBlocker({}, { ...blank, project: 'p', clientId: 'c' }), 'Enter the OAuth client secret.')
eq('all three typed -> ready',
  linkBlocker({}, { project: 'p', clientId: 'c', secret: 's' }), '')

// THE REGRESSION. Everything saved server-side, form blank after the reload: ready, no retyping.
eq('saved server-side + blank form -> ready (the reported bug)',
  linkBlocker({ project_id: 'p', client_id: 'c', has_secret: true }, blank), '')
eq('saved project+client but secret never stored -> only the secret is asked for',
  linkBlocker({ project_id: 'p', client_id: 'c', has_secret: false }, blank), 'Enter the OAuth client secret.')
eq('secret stored, project saved, client id missing -> asks for the client id',
  linkBlocker({ project_id: 'p', has_secret: true }, blank), 'Enter the OAuth client id.')
eq('typed value overrides the saved one',
  linkBlocker({ project_id: 'p', client_id: 'c', has_secret: true }, { ...blank, project: 'other' }), '')
eq('whitespace is not a value',
  linkBlocker({}, { project: '   ', clientId: '  ', secret: ' ' }), 'Enter the Device Access project id.')
eq('a stored secret is never overridden by whitespace typed into the field',
  linkBlocker({ project_id: 'p', client_id: 'c', has_secret: true }, { ...blank, secret: '   ' }), '')
eq('nulls from the server behave like absent',
  linkBlocker({ project_id: null, client_id: null }, blank), 'Enter the Device Access project id.')

// ── oauthReturn: what Google put on the url ────────────────────────────────────────────────────
eq('no query -> nothing to do', oauthReturn(''), { code: '', error: '', none: true })
eq('unrelated query -> nothing to do', oauthReturn('?tab=cameras'), { code: '', error: '', none: true })
eq('code -> redeem it', oauthReturn('?code=4/0Ab_xyz'), { code: '4/0Ab_xyz', error: '', none: false })
eq('code without the leading ?', oauthReturn('code=4/0Ab_xyz'), { code: '4/0Ab_xyz', error: '', none: false })
eq('code alongside scope+state', oauthReturn('?state=x&code=abc&scope=https://www.googleapis.com/auth/sdm.service'),
  { code: 'abc', error: '', none: false })
eq('denial -> say so', oauthReturn('?error=access_denied'), { code: '', error: 'access_denied', none: false })
// A stale error left in the url must not discard a fresh authorization.
eq('code wins over a leftover error', oauthReturn('?error=access_denied&code=abc'),
  { code: 'abc', error: '', none: false })
eq('empty code param is not a code', oauthReturn('?code='), { code: '', error: '', none: true })
eq('url-encoded code is decoded once', oauthReturn('?code=4%2F0Ab'), { code: '4/0Ab', error: '', none: false })

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)
