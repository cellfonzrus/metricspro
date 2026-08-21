// Proves the two decisions behind the Google link form, offline.
//
// The bug this exists to keep dead: the consent round trip navigates away and back, so every field
// held in component state returns empty. The form read those empty fields as "nothing saved",
// disabled the button, and the operator — looking at a blank form holding a project id the server
// had stored minutes earlier — reported that it does not save. Both decisions below now read the
// SERVER's state, not the form's, and the round trip finishes itself.
import { idsBlocker, authorizeBlocker, oauthReturn, syncMessage, storeOptions, withCurrent } from './src/lib/vision.ts'

let pass = 0, fail = 0
const eq = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want)
  if (ok) pass++
  else { fail++; console.log(`FAIL ${name}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`) }
}
const check = (name, got) => eq(name, !!got, true)
const blank = { project: '', clientId: '', secret: '' }

// ── idsBlocker: the ids save on their own, with no secret involved ─────────────────────────────
eq('nothing anywhere -> asks for the project id',
  idsBlocker({}, blank), 'Enter the Device Access project id.')
eq('project typed, nothing else -> asks for the client id',
  idsBlocker({}, { ...blank, project: 'c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3' }), 'Enter the OAuth client id.')

// THE REGRESSION. Both ids typed and NO secret: this must save. The old single gate refused, so the
// ids never reached the server and vanished with the page — reported as "it is not saving".
eq('both ids typed, no secret -> saves (the reported bug)',
  idsBlocker({}, { project: 'c25e6a1e-5f03-4b6a-9b72-3a9cf89fcbe3', clientId: '437700580502-x.apps.googleusercontent.com', secret: '' }), '')
eq('already saved server-side, blank form -> nothing left to ask',
  idsBlocker({ project_id: 'p', client_id: 'c' }, blank), '')
eq('typed value overrides the saved one',
  idsBlocker({ project_id: 'p', client_id: 'c' }, { ...blank, project: 'other' }), '')
eq('whitespace is not a value',
  idsBlocker({}, { project: '   ', clientId: '  ', secret: ' ' }), 'Enter the Device Access project id.')
eq('whitespace typed does not blank out a saved id',
  idsBlocker({ project_id: 'p', client_id: 'c' }, { ...blank, project: '   ' }), '')
eq('nulls from the server behave like absent',
  idsBlocker({ project_id: null, client_id: null }, blank), 'Enter the Device Access project id.')

// ── authorizeBlocker: the secret is typed every time, never assumed ────────────────────────────
eq('no ids at all -> the id gate speaks first',
  authorizeBlocker({}, blank), 'Enter the Device Access project id.')
eq('ids saved, no secret typed -> asks for the secret',
  authorizeBlocker({ project_id: 'p', client_id: 'c' }, blank), 'Enter the OAuth client secret to authorize.')
// A secret ON FILE is NOT a substitute for typing one. The operator asked for it this way, and it
// keeps a stale secret from a previous attempt from being silently reused.
eq('a secret on file does not stand in for typing one',
  authorizeBlocker({ project_id: 'p', client_id: 'c', has_secret: true }, blank),
  'Enter the OAuth client secret to authorize.')
eq('ids saved + secret typed -> ready',
  authorizeBlocker({ project_id: 'p', client_id: 'c' }, { ...blank, secret: 's' }), '')
eq('all three typed fresh -> ready',
  authorizeBlocker({}, { project: 'p', clientId: 'c', secret: 's' }), '')
eq('whitespace secret is not a secret',
  authorizeBlocker({ project_id: 'p', client_id: 'c' }, { ...blank, secret: '   ' }),
  'Enter the OAuth client secret to authorize.')

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


// ── syncMessage: the button must always say something ──────────────────────────────────────────
// THE REGRESSION. act(fn, '') ran setMsg('') AFTER fn had set its own message, so a successful sync
// wiped its own result and the button looked dead. These prove every branch produces text.
{
  const nonEmpty = (label, r) => {
    const m = syncMessage(r)
    check(label, typeof m === 'string' && m.length > 0)
  }
  nonEmpty('a totally empty response still says something', {})
  nonEmpty('a normal sync says something', { found: 3, added: 3, updated: 0 })
  nonEmpty('an all-skipped sync says something', { found: 3, skipped: 3, skipped_homes: { Home: 3 } })

  // Nothing came back from Google: the two causes we cannot see from here are both named.
  const none = syncMessage({ found: 0 })
  check('no cameras names the wrong-account cause', none.includes('owns the store cameras'))
  check('no cameras names the not-shared-on-consent cause', none.includes('consent screen'))

  // Everything skipped: this is migration 901 working, so it must read as an action, not a failure.
  const all = syncMessage({ found: 4, skipped: 4, skipped_homes: { 'Sanjot Home': 4 } })
  check('all-skipped reports the real count found', all.includes('4 camera(s)'))
  check('all-skipped names the home', all.includes('Sanjot Home'))
  check('all-skipped points at section 3b', all.includes('3b'))
  check('all-skipped is not phrased as an error', !all.toLowerCase().includes('failed'))

  // The ordinary path, and the partial path.
  const ok = syncMessage({ found: 2, added: 2, updated: 0 })
  check('a clean sync leads with the number kept', ok.startsWith('Synced 2 camera(s)'))
  check('a clean sync mentions no skipping', !ok.includes('Skipped'))

  const part = syncMessage({ found: 5, added: 3, updated: 0, skipped: 2, skipped_homes: { House: 2 } })
  check('a partial sync counts only what was kept', part.startsWith('Synced 3 camera(s)'))
  check('a partial sync still reports what was skipped', part.includes('2 in House'))

  // Missing counters must not surface as "undefined" in front of an operator.
  const sparse = syncMessage({ found: 1 })
  check('absent added/updated render as 0, never undefined', !sparse.includes('undefined'))
}

// ── storeOptions / withCurrent: assign a store, never type one ─────────────────────────────────
// A typed store code does not fail loudly — it saves, and that camera's customers are counted
// against a store that does not exist. The traffic never shows up and nothing says why.
{
  const rows = [
    { store_code: 'BOOST-02', address: 'Elm St', is_active: true },
    { store_code: 'BOOST-01', address: 'Main St' },
    { store_code: 'CLOSED-9', address: 'Old Rd', is_active: false },
    { store_code: '  ', address: 'blank' },
    { store_code: 'BOOST-01', address: 'duplicate row' },
    { address: 'no code at all' },
  ]
  const o = storeOptions(rows)
  eq('closed, blank and duplicate stores are dropped', o.map(x => x.code), ['BOOST-01', 'BOOST-02'])
  eq('the label carries the address so two codes are tellable apart',
    o[0].label, 'BOOST-01 — Main St')
  eq('a missing is_active is treated as active', o.some(x => x.code === 'BOOST-02'), true)

  eq('an object response is unwrapped too',
    storeOptions({ stores: [{ store_code: 'A' }] }).map(x => x.code), ['A'])
  eq('null is not a crash', storeOptions(null), [])
  eq('undefined is not a crash', storeOptions(undefined), [])
  eq('a junk response is not a crash', storeOptions({}), [])
  eq('a code with no address labels as just the code', storeOptions([{ store_code: 'X' }])[0].label, 'X')
  eq('an address equal to the code is not doubled up',
    storeOptions([{ store_code: 'X', address: 'X' }])[0].label, 'X')

  // THE ONE THAT MATTERS. A camera set to a store that has since closed must still show what it is
  // set to. Dropping it would re-label the camera as unassigned the moment anyone opened the page,
  // and the first symptom would be traffic quietly going missing.
  const opts = withCurrent(o, 'CLOSED-9')
  eq('a value outside the list is kept, not silently dropped',
    opts.map(x => x.code), ['BOOST-01', 'BOOST-02', 'CLOSED-9'])
  eq('and it is labelled so the operator knows why it looks odd',
    opts[2].label, 'CLOSED-9 — not in the store list')
  eq('a value already in the list is not duplicated',
    withCurrent(o, 'BOOST-01').map(x => x.code), ['BOOST-01', 'BOOST-02'])
  eq('no value leaves the list alone', withCurrent(o, null), o)
  eq('a blank value leaves the list alone', withCurrent(o, '   '), o)
}

console.log(`\n${pass} passed, ${fail} failed`)
process.exit(fail ? 1 : 0)