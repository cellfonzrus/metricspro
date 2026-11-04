// THE ONE HOME for "read a construct out of a Python source, from a frontend proof"
// (found 2026-11-04, index §CORE-34).
//
// WHY THIS FILE EXISTS. `asset-borrowed-registry-proof.mjs` was red with:
//
//     AssertionError: filter-options return statement found
//
// and nothing was wrong with the thing it guards. It located the endpoint's response by matching
// `/return \{"markets": sorted\(markets\)…/`, and a later change — the §13c canonical market
// vocabulary union — moved the `sorted()` call up into a variable, so the return now begins
// `return {"markets": markets,`. Every key the proof protects is still returned. The proof broke on
// the SPELLING OF A VALUE, and its message named its own regex instead of the invariant.
//
// THE CLASS, not the instance:
//
//     **A guard that reads source must locate a construct by WHAT IT IS — a function, a return
//     statement, a set of dict keys — never by how one of its values happens to be written.**
//
// A guard anchored on formatting has two failure directions and both are bad. It cries wolf when
// the code is reworded, which is what trains people to stop reading proof output; and it goes
// quietly blind when the construct moves somewhere its regex no longer reaches, which is the §CORE-10
// direction. The same proof carried a second instance of this: it matched `_registry_stores`'s body
// as `def _registry_stores\(…\)[\s\S]*?\n\n\n`, which depends on exactly two blank lines following
// the function. Indentation is Python's own rule for where a body ends, so that is what to read.
//
// SIBLING OF §CORE-32, deliberately noted. There, a source reader went blind to a new STRING shape
// (PEP 701 f-strings) and the repair was one home, `backend/harnesslib.py`. Here a source reader
// went blind to a new VALUE shape. The same home cannot serve both: `harnesslib.py` is Python,
// imported by `harness_*.py`; these are the two FRONTEND proofs that read backend Python
// (`asset-borrowed-registry-proof.mjs`, `payout-nav-proof.mjs`) and cannot import it. So this is
// the JS-side home for the same discipline, and it is named in the index beside §CORE-32 so the
// pair is findable from either side.
//
// Stdlib only. Unit-tested by `tools/__tests__/proof-source.test.mjs`, written RED first.

/**
 * The body of `def <name>(...)`, from the line after the `def` to where the indentation ends.
 *
 * Found by INDENTATION, which is Python's own rule, not by a trailing-blank-line pattern. Returns
 * null when there is no such function — a caller must assert on that rather than slice a wrong
 * region. The name is matched whole, so `helper` never matches `helper_two`.
 */
export function pyFunctionBody(src, name) {
  const lines = String(src).split('\n')
  const def = new RegExp(String.raw`^(\s*)(?:async\s+)?def\s+${name}\s*\(`)
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(def)
    if (!m) continue
    const indent = m[1].length
    const out = []
    for (let j = i + 1; j < lines.length; j++) {
      const line = lines[j]
      if (line.trim() === '') { out.push(line); continue }   // a blank line inside a body is fine
      const lead = line.length - line.trimStart().length
      if (lead <= indent) break                              // dedented to the def's level or out
      out.push(line)
    }
    return out.join('\n')
  }
  return null
}

/**
 * The LAST `return { … }` in `body`, as its literal text with balanced braces — the response shape
 * of an endpoint, after any early returns.
 *
 * Brace counting skips quoted text, so a `}` inside a string does not close the dict. Returns null
 * when the function's return is not a dict literal.
 */
export function pyReturnDict(body) {
  if (body === null || body === undefined) return null
  const text = String(body)
  let found = null
  for (const m of text.matchAll(/\breturn\s*\{/g)) {
    const open = text.indexOf('{', m.index)
    let depth = 0, quote = null
    for (let i = open; i < text.length; i++) {
      const ch = text[i]
      if (quote) {
        if (ch === '\\') { i++; continue }
        if (ch === quote) quote = null
        continue
      }
      if (ch === '"' || ch === "'") { quote = ch; continue }
      if (ch === '{') depth++
      else if (ch === '}') {
        depth--
        if (depth === 0) { found = text.slice(open, i + 1); break }
      }
    }
  }
  return found
}

/**
 * The TOP-LEVEL literal keys of a Python dict's text, in order.
 *
 * This is the invariant a response-shape proof actually cares about: which keys an endpoint
 * returns. It is indifferent to every value's spelling, which is the whole point. A nested dict's
 * keys are not reported, and a dict with no literal keys (`{**base}`) yields [] rather than throwing.
 */
export function pyDictKeys(dictText) {
  if (dictText === null || dictText === undefined) return []
  const text = String(dictText)
  const keys = []
  let depth = 0, quote = null, start = -1
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quote) {
      if (ch === '\\') { i++; continue }
      if (ch === quote) {
        if (depth === 1) {
          // A string that closes at depth 1 is a key only when a ':' follows it.
          const rest = text.slice(i + 1)
          const colon = rest.match(/^\s*:/)
          if (colon && start >= 0) keys.push(text.slice(start, i))
        }
        quote = null
        start = -1
      }
      continue
    }
    if (ch === '{') depth++
    else if (ch === '}') depth--
    else if ((ch === '"' || ch === "'") && depth === 1) { quote = ch; start = i + 1 }
    else if (ch === '"' || ch === "'") { quote = ch; start = -1 }
  }
  return keys
}

/**
 * `text` with every Python string literal blanked (docstrings included), so a keyword search reads
 * CODE and not prose.
 *
 * Without this, `!/\braise\b/.test(body)` is a claim about the docstring as much as about the code
 * — the §CORE-32 confusion between what a source SAYS and what it DOES, in the other direction.
 * Triple quotes are blanked first, so a docstring is not mistaken for three empty strings.
 */
export function pyStripStrings(text) {
  const TRIPLE_D = /"""[\s\S]*?"""/g
  const TRIPLE_S = new RegExp("'''[\\s\\S]*?'''", 'g')
  const SINGLE_D = /"(?:[^"\\\n]|\\.)*"/g
  const SINGLE_S = new RegExp("'(?:[^'\\\\\\n]|\\\\.)*'", 'g')
  return String(text)
    .replace(TRIPLE_D, '""')
    .replace(TRIPLE_S, "''")
    .replace(SINGLE_D, '""')
    .replace(SINGLE_S, "''")
}
