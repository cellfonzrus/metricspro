// Unit tests for the ONE home that prepares a frontend module for a bare-Node proof.
//
// WHY THIS EXISTS. `tsc` does NOT rewrite tsconfig `paths` aliases in the JavaScript it emits — the
// emitted file still says `from '@/lib/cell-safety'`, which bare Node cannot resolve ("Cannot find
// package '@/lib'"). Three proofs were red for exactly this reason, and they were red because the
// modules they transpile GAINED an `@/` import after the proofs were written. Nothing about that is
// specific to those three: it is a property of the transpile-and-import pattern, so the repair is a
// shared home for the pattern, and these are its tests.
import { describe, it, expect } from 'vitest'
import { rewriteAliasSpecifiers, aliasToRelative } from '../_proof-transpile.mjs'

describe('aliasToRelative — where does `@/x` live relative to the emitted file', () => {
  it('resolves a sibling inside the same directory', () => {
    expect(aliasToRelative('lib/export.js', '@/lib/cell-safety')).toBe('./cell-safety.js')
  })

  it('climbs out of a nested directory', () => {
    expect(aliasToRelative('app/(platform)/commcalc/_lib/commissionExport.js', '@/lib/sourceLabels'))
      .toBe('../../../../lib/sourceLabels.js')
  })

  it('always produces a specifier Node accepts — relative and with an extension', () => {
    const got = aliasToRelative('app/x/y.js', '@/lib/deep/thing')
    expect(got.startsWith('.')).toBe(true)
    expect(got.endsWith('.js')).toBe(true)
  })

  it('leaves an already-relative specifier WITH an extension alone', () => {
    expect(aliasToRelative('lib/export.js', './cell-safety.js')).toBeNull()
  })

  it('adds the extension to an extensionless RELATIVE specifier', () => {
    // tsc leaves `./apiBase` exactly as the source wrote it, and ESM under Node refuses a
    // specifier with no extension. Same class as the alias: the emitted JS is not runnable as-is.
    expect(aliasToRelative('lib/client.js', './apiBase')).toBe('./apiBase.js')
    expect(aliasToRelative('app/x/y.js', '../../lib/thing')).toBe('../../lib/thing.js')
  })

  it('leaves a relative specifier that already names a file alone', () => {
    expect(aliasToRelative('lib/client.js', './apiBase.js')).toBeNull()
    expect(aliasToRelative('lib/client.js', './data.json')).toBeNull()
  })

  it('leaves a real package alone — react must stay react', () => {
    expect(aliasToRelative('lib/export.js', 'react')).toBeNull()
    expect(aliasToRelative('lib/export.js', 'react/jsx-runtime')).toBeNull()
    expect(aliasToRelative('lib/export.js', '@tanstack/react-table')).toBeNull()
  })
})

describe('rewriteAliasSpecifiers — every shape the emitter can produce', () => {
  it('rewrites a named import', () => {
    const out = rewriteAliasSpecifiers("import { api } from '@/lib/client';", 'lib/export.js')
    expect(out).toBe("import { api } from './client.js';")
  })

  it('rewrites a double-quoted specifier', () => {
    const out = rewriteAliasSpecifiers('import { a } from "@/lib/client";', 'lib/export.js')
    expect(out).toBe('import { a } from "./client.js";')
  })

  it('rewrites a bare side-effect import', () => {
    expect(rewriteAliasSpecifiers("import '@/lib/boot';", 'lib/export.js')).toBe("import './boot.js';")
  })

  it('rewrites a re-export', () => {
    expect(rewriteAliasSpecifiers("export * from '@/lib/types';", 'lib/export.js'))
      .toBe("export * from './types.js';")
  })

  it('rewrites a dynamic import', () => {
    expect(rewriteAliasSpecifiers("const m = await import('@/lib/heavy');", 'lib/export.js'))
      .toBe("const m = await import('./heavy.js');")
  })

  it('rewrites a require, for a CommonJS emit target', () => {
    expect(rewriteAliasSpecifiers('const c = require("@/lib/client");', 'lib/export.js'))
      .toBe('const c = require("./client.js");')
  })

  it('rewrites several specifiers in one file, each relative to that file', () => {
    const src = [
      "import { pinSheetCellTypes } from '@/lib/cell-safety';",
      "import { isSafeMediaSrc } from '@/lib/safe-url';",
      "import { api } from '@/lib/client';",
    ].join('\n')
    const out = rewriteAliasSpecifiers(src, 'app/(platform)/x/y.js')
    expect(out).toBe([
      "import { pinSheetCellTypes } from '../../../lib/cell-safety.js';",
      "import { isSafeMediaSrc } from '../../../lib/safe-url.js';",
      "import { api } from '../../../lib/client.js';",
    ].join('\n'))
  })

  it('does not touch react, or a string that merely contains the alias text', () => {
    const src = [
      'import { useState } from \'react\';',
      'const help = "paths are written @/lib/foo in tsconfig";',
    ].join('\n')
    expect(rewriteAliasSpecifiers(src, 'lib/export.js')).toBe(src)
  })

  it('fixes an extensionless relative import, which is how tsc emits `./apiBase`', () => {
    expect(rewriteAliasSpecifiers("import { base } from './apiBase';", 'lib/client.js'))
      .toBe("import { base } from './apiBase.js';")
  })

  it('is idempotent — running it twice changes nothing more', () => {
    const once = rewriteAliasSpecifiers("import { api } from '@/lib/client';", 'lib/export.js')
    expect(rewriteAliasSpecifiers(once, 'lib/export.js')).toBe(once)
  })
})

// ── THE NODE-20 WEBSOCKET STUB (2026-11-05) ──────────────────────────────────────────────────────
// Three proofs passed on Node 22 and failed in CI on Node 20. `src/lib/client.ts` calls
// `createClient()` AT MODULE LOAD, and @supabase/realtime-js throws outright on Node < 22 unless a
// WebSocket constructor exists globally ("Node.js 20 detected without native WebSocket support").
// Node 22 has one natively, which is why no local run could see it. A proof reading a pure export
// must not be stopped by a transport it never uses, so the shared stub provides an inert one.
describe('stubBrowserEnv: the WebSocket a Supabase client insists on existing', () => {
  it('defines a global WebSocket when the runtime has none', async () => {
    const { stubBrowserEnv } = await import('../_proof-transpile.mjs')
    const had = Object.prototype.hasOwnProperty.call(globalThis, 'WebSocket')
    const original = globalThis.WebSocket
    try {
      delete globalThis.WebSocket
      expect(globalThis.WebSocket).toBeUndefined()
      stubBrowserEnv()
      expect(typeof globalThis.WebSocket).toBe('function')
      // Inert: constructing it must not reach the network or throw.
      expect(() => new globalThis.WebSocket('wss://proof.invalid')).not.toThrow()
    } finally {
      if (had) globalThis.WebSocket = original
      else delete globalThis.WebSocket
    }
  })

  it('leaves a real WebSocket alone — never replaces the runtime"s own', async () => {
    const { stubBrowserEnv } = await import('../_proof-transpile.mjs')
    const sentinel = function RealWebSocket() {}
    const had = Object.prototype.hasOwnProperty.call(globalThis, 'WebSocket')
    const original = globalThis.WebSocket
    try {
      globalThis.WebSocket = sentinel
      stubBrowserEnv()
      expect(globalThis.WebSocket).toBe(sentinel)
    } finally {
      if (had) globalThis.WebSocket = original
      else delete globalThis.WebSocket
    }
  })
})
