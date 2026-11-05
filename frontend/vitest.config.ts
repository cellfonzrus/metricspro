/// <reference types="vitest" />
// THE FRONTEND TEST RUNNER (docs/TESTING.md).
//
// Vitest rather than Jest: it reads the same TS/JSX this app already compiles, needs no Babel
// config, and runs the `src/**/*.test.ts(x)` files co-located with the code they prove. jsdom gives
// component tests a DOM; a pure-logic test never touches it and costs nothing.
//
// NO NETWORK. `globals: false` keeps every helper an explicit import, and tests/setup.ts fails any
// test that calls fetch — the frontend's half of the same promise the backend conftest makes: a
// test never reaches production (there is no staging environment, see docs/TESTING.md).
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    environment: 'jsdom',
    // A COMPONENT TEST MUST BE ABLE TO IMPORT THE APP'S OWN MODULES. `lib/client` builds its Supabase
    // client AT IMPORT TIME and throws "supabaseUrl is required" without these, so every component
    // that touches it (any page, <EntityPicker>, the filter bar) was unrenderable in a test. These are
    // deliberate NON-SECRETS pointing at a host that does not exist; `setup.ts` already makes any real
    // fetch throw, so a test still cannot reach a network, let alone production.
    env: {
      NEXT_PUBLIC_SUPABASE_URL: 'http://supabase.invalid',
      NEXT_PUBLIC_SUPABASE_ANON_KEY: 'test-anon-key-not-a-secret',
    },
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'tools/**/*.test.mjs'],
    // `tools/**/*.test.mjs` are unit tests for the BUILD TOOLING (the proof transpiler), and they
    // belong under the shared runner like any other unit test. The `tools/*-proof.mjs` programs are
    // a different thing: standalone Node proofs with their own npm script (`test:proofs`) and their
    // own CI step. They match no `include` pattern here, so they are not collected twice — and the
    // `.proof-*` scratch directories those programs create mid-run are excluded explicitly, since a
    // watch run would otherwise try to collect a half-written transpile.
    exclude: ['node_modules/**', '.next/**', '.proof-*/**', '.grproof-*/**'],
  },
})
