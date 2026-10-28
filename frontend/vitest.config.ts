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
    globals: false,
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    // `tools/*-proof.mjs` are standalone Node proofs with their own npm script (`test:proofs`) and
    // their own CI step. Collecting them here would run them twice and in the wrong environment.
    exclude: ['node_modules/**', '.next/**', 'tools/**'],
  },
})
