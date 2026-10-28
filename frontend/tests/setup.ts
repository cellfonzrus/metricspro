// Vitest setup — runs before every frontend test file.
//
// THE PROMISE: a frontend test never reaches the network. There is no staging environment; the
// same live Supabase sits behind every URL this app knows. So `fetch` is replaced with one that
// throws, and a test that needs a response stubs it deliberately:
//
//     vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ ok: true })))
//
// A deliberate stub is a decision; a real request from a test is an accident.
import { beforeEach, afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    throw new Error(
      `OutboundNetworkBlocked: a test called fetch(${String(input)}). Tests never reach ` +
      'production — stub it with vi.spyOn(globalThis, "fetch"). See docs/TESTING.md.',
    )
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})
