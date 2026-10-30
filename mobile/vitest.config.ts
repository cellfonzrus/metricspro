/// <reference types="vitest" />
// THE MOBILE TEST RUNNER (docs/TESTING.md, project directive 2026-10-28).
//
// WHAT WAS HERE BEFORE: nothing. `mobile/` had a typecheck and a lint script and no test of any
// kind, which is how the "Choose a company" screen shipped printing raw org ids (2026-10-30) —
// there was no gate a label rule could fail.
//
// Vitest, matching the frontend's runner so there is one answer to "how do I run a test here".
// `node` rather than jsdom: this suite covers the app's PURE logic (labels, formatting, the shapes
// a payload can arrive in), which needs no DOM and must not pull React Native's native modules
// into a CI runner that has no device. A screen test belongs behind a renderer, not here.
//
// NO NETWORK: `tests/setup.ts` fails any test that calls fetch — the mobile half of the same
// promise the backend conftest makes, because there is no staging Supabase (docs/TESTING.md).
import { resolve } from 'node:path'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  resolve: {
    alias: { '@': resolve(process.cwd(), 'src') },
  },
  test: {
    environment: 'node',
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    exclude: ['node_modules/**', '.expo/**', 'android/**', 'ios/**'],
  },
})
