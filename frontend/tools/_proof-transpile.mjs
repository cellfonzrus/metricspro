// THE ONE HOME for "turn a frontend TypeScript module into something a bare-Node proof can import"
// (found 2026-11-04, index §CORE-34).
//
// WHY THIS FILE EXISTS — the defect it replaces, stated so nobody re-derives it.
//
// Three proofs (`commission-export-proof.mjs`, `google-ratings-proof.mjs`, `people-export-proof.mjs`)
// were red with:
//
//     Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@/lib' imported from
//     …/.proof-XXXX/app/(platform)/commcalc/_lib/commissionExport.js
//
// THE MECHANISM, measured not guessed. Each proof wrote a throwaway tsconfig extending the app's,
// ran `tsc`, and imported the emitted JS under Node. **`tsc` does not rewrite tsconfig `paths`
// aliases in the JavaScript it emits** — that is documented TypeScript behaviour, not a bug here.
// The emitted `lib/export.js` still says `from '@/lib/cell-safety'`, and `@/lib` is not a valid
// package name, so Node refuses it. The proofs' own comments show why they once passed: they
// asserted the modules under test imported "ALL `import type`" or "react only". That stopped being
// true when `src/lib/export.tsx` gained `@/lib/cell-safety`, `@/lib/safe-url` and `@/lib/client`,
// and `commissionExport.ts` gained `@/lib/sourceLabels`.
//
// THE CLASS, not the instance — and the reason this is a shared home rather than three repairs:
//
//     **A proof that transpiles app code and runs it under bare Node depends on a fact about the
//     APP's import graph, and nothing tells the proof when that fact changes.** The proof does not
//     break when it is edited; it breaks when a module it never names gains an import.
//
// Three proofs hit it. Every future proof using the pattern would hit it too, because the pattern
// itself is what carries the assumption. So the pattern lives here, once, and resolves aliases
// instead of assuming there are none. There is nothing left for a module to gain that can break it.
//
// WHAT IT GUARANTEES
//   · `rootDir` is PINNED to `src`, so the emitted layout is `OUT/<path under src>` whatever set of
//     entries is passed. Without the pin, `tsc` derives the common root from the entry list, so
//     adding or removing one entry silently moves every emitted file (measured: a single
//     `src/lib/export.tsx` entry emits to `OUT/export.js`, two entries emit to `OUT/lib/export.js`).
//   · Every `@/…` specifier in the emitted JS is rewritten to a relative path WITH its `.js`
//     extension, which is what ESM under Node requires.
//   · A rewritten target that is not on disk RAISES, naming the specifier and the file that wanted
//     it — rather than handing Node a path that fails later with no context.
//   · The scratch directory and the throwaway tsconfig are removed on exit, including on a throw.
//
// Stdlib + the repo's own `tsc`. No new dependency. Unit-tested by `tools/__tests__/proof-transpile.test.mjs`.
import { mkdtempSync, rmSync, writeFileSync, readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** The tsconfig path alias this app uses. One spelling, read by both functions below. */
const ALIAS = '@/'

/**
 * What must `specifier` become, for the emitted file at `fromFileRel` (a path relative to the out
 * dir, e.g. `app/(platform)/x/y.js`) to be runnable under bare Node?
 *
 * TWO THINGS are wrong with what `tsc` emits, and they are the same class — *the emitted JS keeps
 * the specifier the TypeScript source wrote, and TypeScript's module resolution is not Node's*:
 *
 *   1. a `paths` ALIAS is left verbatim (`@/lib/cell-safety`), and `@/lib` is not a package;
 *   2. a RELATIVE import is left EXTENSIONLESS (`./apiBase`), and ESM under Node requires the
 *      extension. (Found immediately after fixing (1): `src/lib/client.ts` imports `./apiBase`.)
 *
 * Returns the replacement specifier, or **null** when it must be left exactly as it is — a real
 * package such as `react` or `@tanstack/x`, or a relative path that already names a file.
 */
export function aliasToRelative(fromFileRel, specifier) {
  if (typeof specifier !== 'string') return null
  const fromDir = path.posix.dirname(fromFileRel.split(path.sep).join('/'))

  // (1) the tsconfig alias. `@scope/pkg` is a real package and also starts with '@', but the alias
  // is exactly `@/`, so a scoped package never matches: '@tanstack/react-table' does not.
  if (specifier.startsWith(ALIAS)) {
    const target = specifier.slice(ALIAS.length)          // 'lib/cell-safety'
    let rel = path.posix.relative(fromDir === '.' ? '' : fromDir, target)
    if (!rel.startsWith('.')) rel = './' + rel
    return rel + '.js'
  }

  // (2) an extensionless relative import. A specifier that already carries ANY extension is left
  // alone — it may be '.js' already, or a real asset such as '.json' or '.css'.
  if (specifier.startsWith('./') || specifier.startsWith('../')) {
    if (path.posix.extname(specifier)) return null
    return specifier + '.js'
  }

  return null                                            // a real package
}

/**
 * Rewrite every aliased specifier in one emitted file's `code`. Pure: no disk access, so it is
 * directly unit-testable. `fromFileRel` is the file's path relative to the out dir.
 *
 * It matches the specifier position only — `from '…'`, a bare `import '…'`, `import('…')` and
 * `require('…')` — so a *string* that happens to contain `@/lib/foo` is never touched.
 */
export function rewriteAliasSpecifiers(code, fromFileRel) {
  // NAMED groups, and a NAMED backreference (`\k<q>`) for the closing quote. A numbered `\1` here
  // is a trap: composing the quoted part into a longer pattern renumbers the groups, so `\1` stops
  // meaning "the opening quote" and the pattern silently matches nothing. That bug was written and
  // caught by these tests before this file ever ran against a proof.
  const quoted = String.raw`(?<q>['"])(?<spec>[^'"\n]+)\k<q>`
  const patterns = [
    new RegExp(String.raw`(?<lead>\bfrom\s*)${quoted}`, 'g'),
    new RegExp(String.raw`(?<lead>\bimport\s*)${quoted}`, 'g'),
    new RegExp(String.raw`(?<lead>\bimport\s*\(\s*)${quoted}`, 'g'),
    new RegExp(String.raw`(?<lead>\brequire\s*\(\s*)${quoted}`, 'g'),
  ]
  let out = code
  for (const re of patterns) {
    out = out.replace(re, (whole, ...args) => {
      const { lead, q, spec } = args[args.length - 1]
      const rel = aliasToRelative(fromFileRel, spec)
      return rel === null ? whole : `${lead}${q}${rel}${q}`
    })
  }
  return out
}

/**
 * Placeholder values for the `NEXT_PUBLIC_*` variables that app code reads AT MODULE LOAD.
 *
 * `src/lib/client.ts` calls `createClient()` at the top level, so merely IMPORTING any module that
 * reaches it throws "supabaseUrl is required." before a single assertion runs. Every proof that
 * transpiles app code hits this, so it belongs here rather than in each proof —
 * `google-ratings-proof.mjs` already carried its own copy.
 *
 * The host is `proof.invalid`: `.invalid` is reserved by RFC 2606 and resolves nowhere, so a proof
 * that accidentally issues a request fails loudly instead of reaching production. Same promise the
 * test suites make (docs/TESTING.md), kept by a name that cannot resolve rather than by a guard.
 * `||=` so a developer who has set real values locally is not overridden.
 */
export function stubBrowserEnv() {
  process.env.NEXT_PUBLIC_SUPABASE_URL ||= 'https://proof.invalid'
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||= 'proof-anon-key'
}

/** Every emitted `.js` under `dir`, as paths relative to `dir`, POSIX-separated. */
function emittedFiles(dir, base = '') {
  const out = []
  for (const name of readdirSync(path.join(dir, base))) {
    const rel = base ? `${base}/${name}` : name
    const full = path.join(dir, rel)
    if (statSync(full).isDirectory()) out.push(...emittedFiles(dir, rel))
    else if (name.endsWith('.js')) out.push(rel)
  }
  return out
}

/**
 * Transpile `entries` (paths relative to `frontend/`, e.g. `src/lib/export.tsx`) plus everything
 * they import, into a scratch directory whose JS a bare-Node proof can import — or, with
 * `{ moduleKind: 'commonjs', jsx: 'react-jsx' }`, `require()` and server-render.
 *
 * Returns `{ outDir, moduleUrl(srcRelative) }`. `moduleUrl` takes the SOURCE path (the same spelling
 * passed in `entries`, or any module they pull in) and gives the absolute emitted `.js` path, so a
 * caller never hand-builds `path.join(OUT, 'app', '(platform)', …)` and never has to know how `tsc`
 * laid the output out.
 */
export function transpileForProof(entries, { label = 'proof', moduleKind = 'es2020', jsx } = {}) {
  stubBrowserEnv()
  const outDir = mkdtempSync(path.join(FRONTEND, `.${label}-`))   // inside frontend/ so bare imports resolve
  const cfg = path.join(FRONTEND, `tsconfig.${path.basename(outDir)}.json`)
  const cleanup = () => { rmSync(outDir, { recursive: true, force: true }); rmSync(cfg, { force: true }) }
  process.on('exit', cleanup)

  try {
    writeFileSync(cfg, JSON.stringify({
      extends: './tsconfig.json',
      compilerOptions: {
        noEmit: false, incremental: false, declaration: false,
        outDir: path.basename(outDir),
        // PINNED, not derived — see the header. Without it the emitted layout moves when the entry
        // set changes, and every `moduleUrl` below would silently point at nothing.
        rootDir: 'src',
        module: moduleKind, target: 'es2020',
        // `commonjs` callers also want node resolution and a JSX factory, because they `require()`
        // the result and server-render it. Passing them here means a caller never needs its own
        // `Module._resolveFilename` hook — a FOURTH copy of "make the alias resolve", which is what
        // `google-ratings-proof.mjs` carried before this home existed.
        ...(moduleKind === 'commonjs' ? { moduleResolution: 'node' } : {}),
        ...(jsx ? { jsx } : {}),
      },
      files: entries,
      include: [],
    }, null, 2))
    execFileSync('npx', ['tsc', '-p', cfg], { cwd: FRONTEND, stdio: ['ignore', 'ignore', 'inherit'] })

    for (const rel of emittedFiles(outDir)) {
      const full = path.join(outDir, rel)
      const before = readFileSync(full, 'utf8')
      const after = rewriteAliasSpecifiers(before, rel)
      if (after === before) continue
      // Every target we just wrote must exist, or the proof fails later with no context.
      for (const spec of after.matchAll(/(['"])(\.[^'"]+\.js)\1/g)) {
        const resolved = path.resolve(path.dirname(full), spec[2])
        if (!existsSync(resolved)) {
          throw new Error(
            `proof transpile: ${rel} needs ${spec[2]} but ${path.relative(outDir, resolved)} was not ` +
            `emitted. Add the module that defines it to this proof's entries.`)
        }
      }
      writeFileSync(full, after)
    }
  } catch (err) {
    cleanup()
    throw err
  }

  return {
    outDir,
    moduleUrl(srcRelative) {
      const rel = srcRelative.replace(/^src\//, '').replace(/\.tsx?$/, '.js')
      const full = path.join(outDir, rel)
      if (!existsSync(full)) {
        throw new Error(`proof transpile: ${srcRelative} was not emitted (looked for ${rel}). ` +
                        `Emitted: ${emittedFiles(outDir).join(', ')}`)
      }
      return full
    },
  }
}
