import { describe, expect, it } from 'vitest'

import { describeStartupFault, STARTUP_FAULT_TITLE } from './startup-fault'

const FULL = {
  supabaseUrl: 'https://db.example',
  supabaseAnonKey: 'anon',
  apiUrl: 'https://api.example',
}

describe('describeStartupFault — a launch fault is DESCRIBED, never thrown', () => {
  it('finds no fault in a fully configured bundle', () => {
    expect(describeStartupFault(FULL)).toBeNull()
  })

  it('names every missing setting, in the words of the variable to set', () => {
    const fault = describeStartupFault({ ...FULL, supabaseUrl: '' })
    expect(fault?.missing).toEqual(['EXPO_PUBLIC_SUPABASE_URL'])
  })

  it('reports all of them at once rather than the first', () => {
    expect(describeStartupFault({})?.missing.length).toBe(3)
  })

  it('treats a blank, a whitespace-only and an absent value alike', () => {
    const absent = { supabaseAnonKey: FULL.supabaseAnonKey, apiUrl: FULL.apiUrl }
    expect(describeStartupFault(absent)?.missing).toEqual(
      describeStartupFault({ ...FULL, supabaseUrl: '   ' })?.missing,
    )
  })

  it('refuses a non-object rather than reading it as configured', () => {
    expect(describeStartupFault(null)).not.toBeNull()
    expect(describeStartupFault(undefined)).not.toBeNull()
  })

  // The whole point: the app must stay ALIVE. A fault is a value the UI can render, so
  // expo-updates keeps running and can finish downloading the bundle that fixes it.
  it('carries a title and a detail a person can read, and never an empty string', () => {
    const fault = describeStartupFault({})
    expect(fault?.title).toBe(STARTUP_FAULT_TITLE)
    expect(fault?.title.trim().length).toBeGreaterThan(0)
    expect(fault?.detail.trim().length).toBeGreaterThan(0)
  })

  it('says in the detail that the app repairs itself from an update', () => {
    expect(describeStartupFault({})?.detail.toLowerCase()).toContain('update')
  })

  it('names the missing settings inside the detail, so a screenshot is diagnosable', () => {
    const fault = describeStartupFault({ ...FULL, apiUrl: '' })
    expect(fault?.detail).toContain('EXPO_PUBLIC_API_URL')
  })

  it('dereferences the declaration rather than a list of its own', () => {
    // Every name it can report must come from config/required-extra.json.
    const declared = Object.values(
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      require('../../config/required-extra.json').required as Record<string, string>,
    )
    expect(describeStartupFault({})?.missing.sort()).toEqual(declared.sort())
  })
})
