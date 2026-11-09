#!/usr/bin/env node
// REFUSE TO SHIP A BUNDLE THAT CARRIES NO CONFIGURATION.
//
// Evaluates app.config.ts exactly as the bundler will, in whatever environment this process has,
// and exits non-zero naming every required value that came out blank. Run it through
// with-profile-env.mjs so it checks the environment the delivery path will actually use:
//
//   node scripts/with-profile-env.mjs production node scripts/check-extra.mjs
//
// This is the gate that was missing on 2026-11-06: a publish with no environment produced
// `extra.supabaseUrl: ''`, every check in CI stayed green (they are all DB-free unit tests and
// source-text locks, none of which evaluates the config), and the bundle reached a phone and
// crashed on launch. The required set is declared once, in config/required-extra.json.
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { missingRequiredExtra, REQUIRED_EXTRA } = require('./delivery-env.js')

let config
try {
  config = JSON.parse(
    execFileSync('npx', ['expo', 'config', '--type', 'public', '--json'], {
      encoding: 'utf8',
      maxBuffer: 32 * 1024 * 1024,
      stdio: ['ignore', 'pipe', 'inherit'],
    }),
  )
} catch (error) {
  console.error(
    'check-extra: could not evaluate app.config.ts, so it is NOT known whether this bundle ' +
      'would carry its configuration. Refusing.\n' +
      String(error instanceof Error ? error.message : error),
  )
  process.exit(1)
}

const missing = missingRequiredExtra(config.extra)
if (missing.length > 0) {
  console.error(
    'check-extra: this bundle would ship with no ' +
      missing.join(', ') +
      '.\n\nThe app reads them from app.config.ts `extra`, and `createClient("")` throws ' +
      '`supabaseUrl is required.` at module scope — the app would die on launch, on every phone ' +
      'the update reaches. Declare them in mobile/eas.json under `build.<profile>.env` and run ' +
      'the command through scripts/with-profile-env.mjs.',
  )
  process.exit(1)
}

console.log(
  `check-extra: all ${Object.keys(REQUIRED_EXTRA).length} required values are present ` +
    `(${Object.values(REQUIRED_EXTRA).sort().join(', ')}).`,
)
