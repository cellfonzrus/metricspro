import { describe, expect, it } from 'vitest'

// eslint-disable-next-line @typescript-eslint/no-var-requires
const mod = require('./delivery-env.js')
const { profileEnv, missingRequiredExtra, REQUIRED_EXTRA, knownProfiles } = mod as {
  profileEnv: (easJson: unknown, profile: string) => Record<string, string>
  missingRequiredExtra: (extra: unknown) => string[]
  REQUIRED_EXTRA: Record<string, string>
  knownProfiles: (easJson: unknown) => string[]
}

// The shape of mobile/eas.json, reduced to what a delivery path reads out of it.
const EAS = {
  build: {
    development: { channel: 'development', env: { EXPO_PUBLIC_API_URL: 'http://localhost:8000' } },
    preview: { channel: 'preview', env: { EXPO_PUBLIC_API_URL: 'https://preview.example' } },
    production: {
      channel: 'production',
      env: {
        EXPO_PUBLIC_API_URL: 'https://api.example',
        EXPO_PUBLIC_SUPABASE_URL: 'https://db.example',
        EXPO_PUBLIC_SUPABASE_ANON_KEY: 'anon-key',
      },
    },
    'no-env': { channel: 'orphan' },
  },
}

describe('profileEnv — the build and the publish read ONE declaration', () => {
  it('returns the profile’s declared environment', () => {
    expect(profileEnv(EAS, 'production')).toEqual({
      EXPO_PUBLIC_API_URL: 'https://api.example',
      EXPO_PUBLIC_SUPABASE_URL: 'https://db.example',
      EXPO_PUBLIC_SUPABASE_ANON_KEY: 'anon-key',
    })
  })

  it('does not leak one profile’s values into another', () => {
    expect(profileEnv(EAS, 'preview')).toEqual({ EXPO_PUBLIC_API_URL: 'https://preview.example' })
  })

  it('refuses an unknown profile, and names the ones that exist', () => {
    expect(() => profileEnv(EAS, 'staging')).toThrow(/staging/)
    expect(() => profileEnv(EAS, 'staging')).toThrow(/production/)
  })

  // An empty object here is the whole defect this module exists for: a publish that silently got
  // nothing produced a bundle whose Supabase address was '', and the app died on launch.
  it('refuses a profile that declares no environment at all, rather than returning nothing', () => {
    expect(() => profileEnv(EAS, 'no-env')).toThrow(/no-env/)
  })

  it('refuses an eas.json with no build section', () => {
    expect(() => profileEnv({}, 'production')).toThrow(/build/)
  })

  it('lists the profiles it can serve', () => {
    expect(knownProfiles(EAS).sort()).toEqual(['development', 'no-env', 'preview', 'production'])
  })
})

describe('missingRequiredExtra — a blank bundle is never publishable', () => {
  const full = Object.fromEntries(Object.keys(REQUIRED_EXTRA).map((k) => [k, 'set']))

  it('declares at least the Supabase address, its key and the API url', () => {
    expect(Object.keys(REQUIRED_EXTRA).sort()).toEqual(['apiUrl', 'supabaseAnonKey', 'supabaseUrl'])
  })

  it('passes a fully populated extra', () => {
    expect(missingRequiredExtra(full)).toEqual([])
  })

  it('names the ENVIRONMENT VARIABLE to set, not the internal key', () => {
    expect(missingRequiredExtra({ ...full, supabaseUrl: '' })).toEqual(['EXPO_PUBLIC_SUPABASE_URL'])
  })

  it('treats a missing key and a blank one alike', () => {
    const without = { ...full }
    delete (without as Record<string, string>).apiUrl
    expect(missingRequiredExtra(without)).toEqual(missingRequiredExtra({ ...full, apiUrl: '' }))
  })

  it('treats whitespace as blank', () => {
    expect(missingRequiredExtra({ ...full, apiUrl: '   ' })).toEqual(['EXPO_PUBLIC_API_URL'])
  })

  it('reports every blank value, not just the first', () => {
    expect(missingRequiredExtra({}).sort()).toEqual(Object.values(REQUIRED_EXTRA).sort())
  })

  it('ignores values it does not require', () => {
    expect(missingRequiredExtra({ ...full, router: { origin: false } })).toEqual([])
  })

  it('refuses a non-object extra rather than reading it as complete', () => {
    expect(missingRequiredExtra(null).sort()).toEqual(Object.values(REQUIRED_EXTRA).sort())
  })
})
