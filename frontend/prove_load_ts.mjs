// Shared loader for the proof harnesses — ONE home for "import a pure src/lib TypeScript module
// under bare Node".
//
// WHY IT EXISTS. The provers drive the REAL shipped functions rather than a copy, which they did by
// importing the .ts straight off disk and letting Node strip the erasable types. That works only
// while a module imports nothing: as soon as `search-rank.ts` began dereferencing the search
// vocabulary (§68) through Next's `@/lib/...` alias, three provers broke at once, because bare Node
// cannot resolve that alias. Writing the same transpile-and-rewrite dance into each of them would
// have been three copies of one fact, so it lives here and they call it.
//
// It copies each requested module into ONE temporary directory, rewriting `@/lib/<name>` to the
// sibling `./<name>.mts`, so a module graph of pure libs resolves exactly as it does in the app.
// Nothing is stubbed and nothing is re-implemented — the file that ships is the file under test.
//
// NO TYPESCRIPT DEPENDENCY, and that is deliberate rather than tidy. These proofs run in a CI job
// with NO `npm ci` (that is what keeps the whole guard in the seconds-not-minutes class), so a
// `require('typescript')` here passes locally — where node_modules happens to exist — and dies on
// the runner. It did, on the first push of §68. Node strips erasable TypeScript itself, which is how
// the provers drove these modules before the alias appeared, so the emitted files keep a TypeScript
// extension (`.mts`) and Node does the stripping. The ONLY thing this loader changes is the import
// specifier. Non-erasable syntax (`enum`, `namespace`, parameter properties) would fail here — it
// would also fail Next's own build, so these pure libs cannot contain it.
import { pathToFileURL } from 'node:url'
import { join } from 'node:path'
import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'

/**
 * Load `names` (bare module names under src/lib, e.g. 'search-rank') plus everything they
 * import by alias, and return the loaded module for each name in order.
 *
 * `mutate` is an optional { <name>: src => src } map used by the ARMED controls: a proof that
 * claims "this rule is what stops the defect" must be able to break the rule and watch the defect
 * come back, and that means editing the real source on its way through — never maintaining a
 * deliberately-wrong copy on disk.
 */
export function loadLibs(root, names, mutate = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'provets-'))
  const done = new Set()
  const emit = name => {
    if (done.has(name)) return
    done.add(name)
    let src = readFileSync(join(root, 'src/lib', `${name}.ts`), 'utf8')
    for (const m of src.matchAll(/'@\/lib\/([a-z0-9-]+)'/g)) emit(m[1])
    src = src.replace(/'@\/lib\/([a-z0-9-]+)'/g, "'./$1.mts'")
    if (mutate[name]) src = mutate[name](src)
    writeFileSync(join(dir, `${name}.mts`), src, 'utf8')
  }
  for (const n of names) emit(n)
  return Promise.all(names.map(n => import(pathToFileURL(join(dir, `${n}.mts`)).href)))
}
