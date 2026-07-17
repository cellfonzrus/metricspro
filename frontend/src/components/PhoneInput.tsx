'use client'
// Shared phone entry with country-code auto-correct (OWNER DIRECTIVE 2026-07-17).
// A compact country-code <select> (default = the tenant's default_cc, common codes + free "Other…"
// entry per RULE THREE) + a national-number field that live-formats (e.g. (516) 233-0422) and EMITS
// the full normalized "+<cc><digits>" string via onChange. The backend (core.auth_security.normalize_phone)
// is authoritative and re-normalizes on save; this component makes the common 10-digit case correct by
// construction. Pure helpers live in lib/phone-format.ts (single source of truth, offline-proven).
import { useEffect, useMemo, useRef, useState } from 'react'
import { normCc, parsePhone, composePhone, fmtNational, maxNationalFor } from '@/lib/phone-format'

// Common codes (label carries a country hint; value is the '+<digits>' code). "Other…" is appended.
const COMMON: { code: string; label: string }[] = [
  { code: '+1', label: '+1 · US / Canada' },
  { code: '+44', label: '+44 · United Kingdom' },
  { code: '+52', label: '+52 · Mexico' },
  { code: '+91', label: '+91 · India' },
  { code: '+61', label: '+61 · Australia' },
  { code: '+63', label: '+63 · Philippines' },
]

export default function PhoneInput({
  value, onChange, defaultCc = '+1', disabled = false, placeholder = 'Phone number', style,
}: {
  value: string | null | undefined
  onChange: (fullValue: string) => void
  defaultCc?: string
  disabled?: boolean
  placeholder?: string
  style?: React.CSSProperties
}) {
  // The CC option list = tenant default + common codes (deduped).
  const options = useMemo(() => {
    const seen = new Set<string>()
    const list: { code: string; label: string }[] = []
    for (const c of [{ code: normCc(defaultCc), label: '' }, ...COMMON]) {
      if (seen.has(c.code)) continue
      seen.add(c.code)
      const known = COMMON.find(k => k.code === c.code)
      list.push({ code: c.code, label: known ? known.label : c.code })
    }
    return list
  }, [defaultCc])
  const knownSorted = useMemo(
    () => Array.from(new Set([...options.map(o => o.code), ...COMMON.map(c => c.code)]))
      .sort((a, b) => b.length - a.length),
    [options],
  )

  const init = useMemo(() => parsePhone(value, defaultCc, knownSorted), [])  // eslint-disable-line react-hooks/exhaustive-deps
  const [cc, setCc] = useState(init.cc)
  const [national, setNational] = useState(init.national)
  const [other, setOther] = useState(init.other)
  const [otherCc, setOtherCc] = useState(init.other ? init.cc : '')
  const touchedCc = useRef(false)

  const emit = (nextCc: string, nextNat: string) => onChange(composePhone(nextCc, nextNat))

  // Re-sync from an externally-changed value (async load) without clobbering in-progress typing.
  useEffect(() => {
    const composed = composePhone(cc, national)
    if ((value || '') !== composed) {
      const p = parsePhone(value, defaultCc, knownSorted)
      setCc(p.cc); setNational(p.national); setOther(p.other); setOtherCc(p.other ? p.cc : '')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value])

  // When the tenant default loads late and the field is untouched+empty, adopt it as the CC.
  useEffect(() => {
    if (!value && !national && !touchedCc.current && !other) setCc(normCc(defaultCc))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultCc])

  const activeCc = other ? normCc(otherCc || '+') : cc
  const maxNational = maxNationalFor(activeCc)

  const onSelect = (v: string) => {
    touchedCc.current = true
    if (v === '__other__') { setOther(true); emit(normCc(otherCc || '+1'), national); return }
    setOther(false); setCc(v); emit(v, national)
  }
  const onOtherCc = (raw: string) => {
    const cleaned = '+' + raw.replace(/[^\d]/g, '').slice(0, 3)
    setOtherCc(cleaned); emit(cleaned, national)
  }
  const onNational = (raw: string) => {
    const d = raw.replace(/\D/g, '').slice(0, maxNational)
    setNational(d); emit(activeCc, d)
  }

  const sel: React.CSSProperties = {
    padding: '7px 8px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, background: 'var(--bg1, #fff)',
  }
  const inp: React.CSSProperties = {
    padding: '7px 10px', borderRadius: 8, border: '1px solid var(--border)', fontSize: 14, flex: 1, minWidth: 120,
  }

  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center', ...style }}>
      <select aria-label="Country code" value={other ? '__other__' : cc} disabled={disabled}
        onChange={e => onSelect(e.target.value)} style={{ ...sel, width: 128 }}>
        {options.map(o => <option key={o.code} value={o.code}>{o.label || o.code}</option>)}
        <option value="__other__">Other…</option>
      </select>
      {other && (
        <input aria-label="Custom country code" value={otherCc} disabled={disabled}
          onChange={e => onOtherCc(e.target.value)} placeholder="+000" style={{ ...sel, width: 64 }} />
      )}
      <input type="tel" inputMode="numeric" aria-label="Phone number" value={fmtNational(activeCc, national)}
        disabled={disabled} onChange={e => onNational(e.target.value)} placeholder={placeholder} style={inp} />
    </div>
  )
}
