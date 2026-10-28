// Unit tests for the pure phone helpers. Previously proven only by scratchpad/prove_phone_format.mjs,
// which no workflow ran — this brings the same invariants into CI under the shared runner.
import { describe, it, expect } from 'vitest'
import { normCc, parsePhone, composePhone, fmtNational, maxNationalFor } from '../phone-format'

const KNOWN = ['+353', '+44', '+91', '+1'] // longest digit-length first, as parsePhone requires

describe('normCc', () => {
  it('defaults to +1 when there is nothing to read', () => {
    expect(normCc(null)).toBe('+1')
    expect(normCc('')).toBe('+1')
    expect(normCc('abc')).toBe('+1')
  })

  it('keeps a 1-3 digit code and strips the punctuation around it', () => {
    expect(normCc('+44')).toBe('+44')
    expect(normCc('353')).toBe('+353')
    expect(normCc(' +91 ')).toBe('+91')
  })

  it('falls back to +1 rather than inventing a 4-digit country code', () => {
    expect(normCc('+1234')).toBe('+1')
  })
})

describe('parsePhone', () => {
  it('splits a known country code off a + value', () => {
    expect(parsePhone('+447911123456', '+1', KNOWN)).toEqual({ cc: '+44', national: '7911123456', other: false })
  })

  it('reads a bare 10-digit number as the default country', () => {
    expect(parsePhone('2125551234', '+1', KNOWN)).toEqual({ cc: '+1', national: '2125551234', other: false })
  })

  it('strips a leading default code from a bare 11-digit number', () => {
    expect(parsePhone('12125551234', '+1', KNOWN)).toEqual({ cc: '+1', national: '2125551234', other: false })
  })

  it('marks a + value whose code is not in the known list', () => {
    const got = parsePhone('+9715551234567', '+1', KNOWN)
    expect(got.other).toBe(true)
    expect(got.cc + got.national).toBe('+9715551234567')
  })

  it('is empty-safe', () => {
    expect(parsePhone('', '+44', KNOWN)).toEqual({ cc: '+44', national: '', other: false })
    expect(parsePhone(null, 'nonsense', KNOWN)).toEqual({ cc: '+1', national: '', other: false })
  })
})

describe('the round-trip invariant the file documents', () => {
  // "composePhone(parse(v)) round-trips the digit string of any '+' value losslessly, so a heuristic
  // CC split of an existing number never corrupts it."
  it.each([
    '+12125551234',
    '+447911123456',
    '+919876543210',
    '+353871234567',
    '+9715551234567',
    '+8613800138000',
  ])('%s survives parse -> compose unchanged', (value) => {
    const p = parsePhone(value, '+1', KNOWN)
    expect(composePhone(p.cc, p.national)).toBe(value)
  })
})

describe('fmtNational', () => {
  it('formats NANP progressively as the user types', () => {
    expect(fmtNational('+1', '2')).toBe('(2')
    expect(fmtNational('+1', '2125')).toBe('(212) 5')
    expect(fmtNational('+1', '2125551234')).toBe('(212) 555-1234')
    expect(fmtNational('+1', '')).toBe('')
  })

  it('groups a non-NANP number in threes', () => {
    expect(fmtNational('+44', '7911123456')).toBe('791 112 345 6')
  })
})

describe('maxNationalFor', () => {
  it('is 10 for NANP and E.164-bounded elsewhere', () => {
    expect(maxNationalFor('+1')).toBe(10)
    expect(maxNationalFor('+44')).toBe(13)
    expect(maxNationalFor('+353')).toBe(12)
  })

  it('never returns a cap so small the field is unusable', () => {
    expect(maxNationalFor('+999')).toBeGreaterThanOrEqual(4)
  })
})
