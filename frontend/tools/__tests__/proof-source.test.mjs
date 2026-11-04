// Unit tests for the ONE home that reads a construct out of a Python source from a frontend proof.
//
// WHY THIS EXISTS. `asset-borrowed-registry-proof.mjs` was red on:
//
//     AssertionError: filter-options return statement found
//
// Nothing was wrong with the thing it guards. The proof located the endpoint's return statement with
// `/return \{"markets": sorted\(markets\)…/`, and a later change (the §13c canonical market
// vocabulary union) replaced `sorted(markets)` with a precomputed `markets` variable. Every key the
// proof protects is still returned. It broke on the SPELLING of a value, and its message named the
// regex rather than the invariant — a false alarm, which is how a proof teaches people to ignore it.
//
// THE CLASS: **a guard that reads source must locate a construct by WHAT IT IS — a function, a
// return statement, a set of dict keys — never by how one of its values happens to be written.**
// Sibling of §CORE-32: there the reader was blind to a new string shape, here to a new value shape.
import { describe, it, expect } from 'vitest'
import { pyFunctionBody, pyReturnDict, pyDictKeys, pyStripStrings } from '../_proof-source.mjs'

const SRC = `
import os


def _helper(client, org_id: str):
    """Doc."""
    try:
        rows = client.table("x").select("*").eq("org_id", org_id).execute().data
    except Exception:
        return []
    return [r for r in rows if r.get("is_active") is not False]


def filter_options(client, org_id):
    markets = set()
    registry_stores = _registry_stores(client, org_id)
    try:
        markets = _cscope.org_market_options(client, org_id, markets)
    except Exception as e:
        markets = sorted(markets)
    return {"markets": markets, "stores": stores, "store_groups": store_groups,
            "no_market_count": no_market_count, "no_market_value": NO_MARKET_SENTINEL,
            "registry_stores": registry_stores}


def after(x):
    return x
`

describe('pyFunctionBody — a function is found by its def, and ends where the indent ends', () => {
  it('returns the body of the named function', () => {
    const body = pyFunctionBody(SRC, '_helper')
    expect(body).toContain('.eq("org_id", org_id)')
    expect(body).toContain('return []')
  })

  it('stops at the next top-level def — it does not run on into the neighbour', () => {
    // The old proof matched `def _registry_stores(...)[\s\S]*?\n\n\n`, which depends on exactly
    // two blank lines surviving. Indentation is the language's own rule, so use that.
    const body = pyFunctionBody(SRC, '_helper')
    expect(body).not.toContain('filter_options')
    expect(body).not.toContain('NO_MARKET_SENTINEL')
  })

  it('finds a function whose body contains blank lines', () => {
    const src = 'def f(a):\n    x = 1\n\n    y = 2\n    return x + y\n\n\ndef g():\n    return 0\n'
    const body = pyFunctionBody(src, 'f')
    expect(body).toContain('y = 2')
    expect(body).not.toContain('return 0')
  })

  it('does not match a function whose name merely shares a prefix', () => {
    const src = 'def helper_two(a):\n    return 2\n\n\ndef helper(a):\n    return 1\n'
    expect(pyFunctionBody(src, 'helper')).toContain('return 1')
    expect(pyFunctionBody(src, 'helper')).not.toContain('return 2')
  })

  it('returns null for a function that is not there, rather than a misleading slice', () => {
    expect(pyFunctionBody(SRC, 'no_such_function')).toBeNull()
  })
})

describe("pyReturnDict — the return statement, found by what it is", () => {
  it('captures the whole multi-line dict with balanced braces', () => {
    const d = pyReturnDict(pyFunctionBody(SRC, 'filter_options'))
    expect(d.startsWith('{')).toBe(true)
    expect(d.endsWith('}')).toBe(true)
    expect(d).toContain('"registry_stores"')
  })

  it('is indifferent to how the first value is spelled — the defect that started this', () => {
    const sorted = pyReturnDict('    return {"markets": sorted(markets), "stores": s}')
    const plain = pyReturnDict('    return {"markets": markets, "stores": s}')
    expect(pyDictKeys(sorted)).toEqual(pyDictKeys(plain))
  })

  it('handles a nested dict without stopping at the inner close brace', () => {
    const d = pyReturnDict('    return {"a": {"inner": 1}, "b": 2}')
    expect(d).toBe('{"a": {"inner": 1}, "b": 2}')
  })

  it('ignores a brace inside a string literal', () => {
    const d = pyReturnDict('    return {"a": "a } brace", "b": 2}')
    expect(pyDictKeys(d)).toEqual(['a', 'b'])
  })

  it('returns null when the function returns something that is not a dict', () => {
    expect(pyReturnDict(pyFunctionBody(SRC, 'after'))).toBeNull()
  })

  it('takes the LAST return dict, which is the endpoint\'s response', () => {
    // `_helper` above returns [] early then a list; a function with an early dict return and a
    // final one must yield the final one.
    const src = 'def f():\n    if x:\n        return {"early": 1}\n    return {"real": 2}\n'
    expect(pyDictKeys(pyReturnDict(pyFunctionBody(src, 'f')))).toEqual(['real'])
  })
})

describe('pyDictKeys — the invariant a response-shape proof actually cares about', () => {
  it('lists the double-quoted keys in order', () => {
    expect(pyDictKeys(pyReturnDict(pyFunctionBody(SRC, 'filter_options')))).toEqual([
      'markets', 'stores', 'store_groups', 'no_market_count', 'no_market_value', 'registry_stores',
    ])
  })

  it('reads single-quoted keys too', () => {
    expect(pyDictKeys("{'a': 1, 'b': 2}")).toEqual(['a', 'b'])
  })

  it('does not mistake a nested dict\'s key for a top-level one', () => {
    expect(pyDictKeys('{"a": {"inner": 1}, "b": 2}')).toEqual(['a', 'b'])
  })

  it('is empty, not a throw, for a dict with no literal keys', () => {
    expect(pyDictKeys('{**base}')).toEqual([])
  })
})

describe('pyStripStrings — a keyword search must read code, not prose', () => {
  it('blanks a docstring, so a word in the prose is not read as code', () => {
    const body = '    """This function never raises."""\n    return 1\n'
    expect(pyStripStrings(body)).not.toMatch(/\braise\b/)
    expect(pyStripStrings(body)).toContain('return 1')
  })

  it('leaves a real keyword standing', () => {
    expect(pyStripStrings('    raise ValueError("nope")')).toMatch(/\braise\b/)
  })

  it('blanks single and double quoted literals', () => {
    expect(pyStripStrings(`x = 'a' + "b"`)).toBe(`x = '' + ""`)
  })

  it('does not let an escaped quote run the blanking past the literal', () => {
    expect(pyStripStrings('x = "a\\"b" + raise_me')).toBe('x = "" + raise_me')
  })
})
