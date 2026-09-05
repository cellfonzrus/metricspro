'use client'
// Shared presentation atoms for the PLATFORM OPERATOR CONSOLE.
//
// One place, because five console pages needing the same panel is exactly the kind of duplication
// this codebase's build gate exists to prevent — and because the console's chrome carries meaning:
// it is deliberately NOT the tenant app's blue, so an operator can tell at a glance whether they are
// running the platform or looking at a company's books.
import Link from 'next/link'
import { OPS, LAMP_COLOR } from '@/lib/operator'

export function H1({ children, sub }: { children: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 6px', color: OPS.text }}>{children}</h1>
      {sub && <p style={{ color: OPS.text2, fontSize: 13.3, lineHeight: 1.6, margin: '0 0 20px',
        maxWidth: 760 }}>{sub}</p>}
    </>
  )
}

export function Panel({ title, right, children }:
  { title?: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section style={{ background: OPS.panel, border: `1px solid ${OPS.border}`, borderRadius: 12,
      padding: '14px 16px', margin: '0 0 16px' }}>
      {(title || right) && (
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 10, gap: 10 }}>
          {title && <h2 style={{ fontSize: 13, fontWeight: 700, margin: 0, color: OPS.text }}>{title}</h2>}
          <span style={{ flex: 1 }} />
          {right}
        </div>
      )}
      {children}
    </section>
  )
}

export function Row({ label, value, warn }: { label: string; value: React.ReactNode; warn?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 14, padding: '5px 0', fontSize: 13, flexWrap: 'wrap' }}>
      <span style={{ color: OPS.text3, minWidth: 210 }}>{label}</span>
      <span style={{ color: warn ? OPS.warn : OPS.text }}>{value}</span>
    </div>
  )
}

export function Note({ children, tone = 'info' }:
  { children: React.ReactNode; tone?: 'info' | 'warn' | 'bad' }) {
  const border = tone === 'bad' ? OPS.bad : tone === 'warn' ? OPS.warn : OPS.border
  return (
    <div style={{ marginTop: 10, padding: '10px 12px', borderRadius: 9, fontSize: 12.6,
      lineHeight: 1.65, color: OPS.text2, background: OPS.accentSoft, border: `1px solid ${border}` }}>
      {children}
    </div>
  )
}

export function Lamp({ lamp, size = 9 }: { lamp?: string; size?: number }) {
  return <span aria-label={lamp} style={{ display: 'inline-block', width: size, height: size,
    borderRadius: size, flexShrink: 0, background: LAMP_COLOR[lamp || ''] || OPS.text3 }} />
}

export function Btn({ children, onClick, disabled, tone = 'primary', type = 'button' }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean
  tone?: 'primary' | 'ghost' | 'danger'; type?: 'button' | 'submit'
}) {
  const s: React.CSSProperties = tone === 'primary'
    ? { background: OPS.accent, color: '#1c1917', border: 'none' }
    : tone === 'danger'
      ? { background: 'transparent', color: OPS.bad, border: `1px solid ${OPS.bad}` }
      : { background: 'transparent', color: OPS.text2, border: `1px solid ${OPS.border}` }
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      style={{ ...s, padding: '7px 13px', borderRadius: 8, fontSize: 12.6, fontWeight: 600,
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1 }}>
      {children}
    </button>
  )
}

export function Field({ label, children, hint }:
  { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, flex: '1 1 190px' }}>
      <span style={{ color: OPS.text3 }}>{label}</span>
      {children}
      {hint && <span style={{ color: OPS.text3, fontSize: 11 }}>{hint}</span>}
    </label>
  )
}

export const inputStyle: React.CSSProperties = {
  background: OPS.bg, color: OPS.text, border: `1px solid ${OPS.border}`,
  borderRadius: 8, padding: '7px 9px', fontSize: 13, width: '100%',
}

export function Table({ head, children }: { head: string[]; children: React.ReactNode }) {
  return (
    // Wide tables scroll inside their own container so the console body never scrolls sideways.
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.7 }}>
        <thead>
          <tr>{head.map(h => (
            <th key={h} style={{ textAlign: 'left', padding: '7px 10px', color: OPS.text3,
              fontWeight: 600, fontSize: 11.4, letterSpacing: '0.04em',
              borderBottom: `1px solid ${OPS.border}`, whiteSpace: 'nowrap' }}>{h.toUpperCase()}</th>
          ))}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

export const td: React.CSSProperties = {
  padding: '8px 10px', borderBottom: `1px solid ${OPS.border}`, color: OPS.text, verticalAlign: 'top',
}

export function Err({ children }: { children: React.ReactNode }) {
  if (!children) return null
  return <div style={{ color: OPS.bad, fontSize: 12.6, margin: '8px 0' }}>{children}</div>
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ color: OPS.text3, fontSize: 12.8, padding: '10px 2px' }}>{children}</div>
}

export function Stat({ label, value, hint, lamp, href }:
  { label: string; value: string; hint?: string; lamp?: string; href?: string }) {
  const body = (
    <div style={{ background: OPS.panel, border: `1px solid ${OPS.border}`, borderRadius: 12,
      padding: '14px 16px', marginBottom: 16, height: '100%' }}>
      <div style={{ color: OPS.text3, fontSize: 11.5, letterSpacing: '0.05em', marginBottom: 6 }}>
        {label.toUpperCase()}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {lamp && <Lamp lamp={lamp} size={10} />}
        <span style={{ fontSize: 21, fontWeight: 700, color: OPS.text }}>{value}</span>
      </div>
      {hint && <div style={{ color: OPS.text3, fontSize: 11.8, marginTop: 4 }}>{hint}</div>}
    </div>
  )
  return href ? <Link href={href} style={{ textDecoration: 'none' }}>{body}</Link> : body
}
