#!/usr/bin/env node
// Run a command with the environment mobile/eas.json declares for one build profile.
//
//   node scripts/with-profile-env.mjs production eas update --branch production --message "..."
//
// WHY A WRAPPER. `eas build` reads `build.<profile>.env` itself; `eas update` does not, and no flag
// makes it (see scripts/delivery-env.js for the full account). Rather than restate the values in
// the publish workflow — a second copy, which the house rules forbid and which is what silently
// diverged — the publish DEREFERENCES eas.json through this wrapper. Spawning rather than printing
// shell exports on purpose: nothing is quoted, escaped or eval'd, so a value containing a space,
// a quote or a `$` cannot change the command that runs.
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)
const here = dirname(fileURLToPath(import.meta.url))
const { profileEnv } = require('./delivery-env.js')

const [profile, command, ...args] = process.argv.slice(2)
if (!profile || !command) {
  console.error('usage: node scripts/with-profile-env.mjs <profile> <command> [args...]')
  process.exit(2)
}

let env
try {
  env = profileEnv(require(resolve(here, '..', 'eas.json')), profile)
} catch (error) {
  console.error(String(error instanceof Error ? error.message : error))
  process.exit(1)
}

// Named, not valued: a Supabase anon key is public, but a log is not the place to print one.
console.error(`[with-profile-env] ${profile}: ${Object.keys(env).sort().join(', ')}`)

const child = spawn(command, args, { stdio: 'inherit', env: { ...process.env, ...env } })
child.on('error', (error) => {
  console.error(`[with-profile-env] could not run ${command}: ${error.message}`)
  process.exit(1)
})
child.on('exit', (code, signal) => process.exit(signal ? 1 : (code ?? 1)))
