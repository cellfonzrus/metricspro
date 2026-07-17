// Pure phone-format helpers for <PhoneInput> (OWNER DIRECTIVE 2026-07-17). No React, no DOM — so they
// can be proven offline (scratchpad/prove_phone_format.mjs) and stay a single source of truth with the
// backend (core.auth_security.normalize_phone). INVARIANT: composePhone(parse(v)) round-trips the digit
// string of any '+' value losslessly, so a heuristic CC split of an existing number never corrupts it.

// Mirror of backend normalize_cc: '+<1-3 digits>', fallback '+1'.
export function normCc(raw?: string | null): string {
  const digits = String(raw || '').replace(/\D/g, '')
  if (!digits) return '+1'
  const cand = '+' + digits
  return /^\+\d{1,3}$/.test(cand) ? cand : '+1'
}

export type Parsed = { cc: string; national: string; other: boolean }

// Split a stored value into (cc, national). knownSorted = candidate '+cc' codes, LONGEST digit-length first.
export function parsePhone(value: string | null | undefined, defaultCc: string, knownSorted: string[]): Parsed {
  const dcc = normCc(defaultCc)
  const v = String(value || '').trim()
  if (!v) return { cc: dcc, national: '', other: false }
  const hasPlus = v.startsWith('+')
  const digits = v.replace(/\D/g, '')
  if (!digits) return { cc: dcc, national: '', other: false }
  if (hasPlus) {
    for (const code of knownSorted) {
      const cd = code.slice(1)
      if (cd && digits.startsWith(cd)) return { cc: code, national: digits.slice(cd.length), other: false }
    }
    for (const len of [3, 2, 1]) {
      if (digits.length - len >= 7) return { cc: '+' + digits.slice(0, len), national: digits.slice(len), other: true }
    }
    return { cc: '+' + digits, national: '', other: true }
  }
  const dccDigits = dcc.slice(1)
  if (digits.length === 10) return { cc: dcc, national: digits, other: false }
  if (digits.length === dccDigits.length + 10 && digits.startsWith(dccDigits))
    return { cc: dcc, national: digits.slice(dccDigits.length), other: false }
  return { cc: dcc, national: digits, other: false }
}

// Compose the full emitted value: '+<cc><nationalDigits>', or '' when there is no national number.
export function composePhone(cc: string, national: string): string {
  const d = String(national || '').replace(/\D/g, '')
  return d ? normCc(cc) + d : ''
}

// Live display format. +1 → (XXX) XXX-XXXX progressive; others → grouped in 3s.
export function fmtNational(cc: string, national: string): string {
  const d = String(national || '').replace(/\D/g, '')
  if (normCc(cc) === '+1') {
    const a = d.slice(0, 3), b = d.slice(3, 6), c = d.slice(6, 10)
    if (d.length > 6) return `(${a}) ${b}-${c}`
    if (d.length > 3) return `(${a}) ${b}`
    if (d.length > 0) return `(${a}`
    return ''
  }
  return d.replace(/(\d{3})(?=\d)/g, '$1 ').trim()
}

// Max national digits for a CC (+1 = 10 NANP; else E.164 15 minus the CC length).
export function maxNationalFor(cc: string): number {
  const c = normCc(cc)
  return c === '+1' ? 10 : Math.max(4, 15 - c.slice(1).length)
}
