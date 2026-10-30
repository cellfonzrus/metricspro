// NO TEST REACHES THE NETWORK. There is no staging Supabase: every environment in this project
// points at the same live database, so a test that opens a socket is one mistake away from reading
// — or writing — production. Mirrors frontend/tests/setup.ts and backend/tests/conftest.py.
import { beforeEach, vi } from 'vitest'

beforeEach(() => {
  vi.stubGlobal('fetch', () => {
    throw new Error(
      'A test tried to call fetch(). There is no staging environment — stub the api module instead.',
    )
  })
})
