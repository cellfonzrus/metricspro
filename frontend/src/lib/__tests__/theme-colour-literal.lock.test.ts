// THE LOCK against a colour LITERAL on a themed surface (index §CORE-50).
//
// Owner, of the Schedule screen on 2026-11-26: *"cannot see he employee name in white background
// and white font"*. The cause was one character class, not one screen: the zebra stripe read
//
//     const rowBg = ri % 2 === 1 ? 'var(--surface2)' : 'white'
//
// In the light theme `--surface` IS #ffffff, so the literal looked right for as long as anybody
// tested in light; in the dark theme it painted a white band under white text and every other
// employee's name and weekly total disappeared. The rows were never misaligned — half of them were
// simply invisible.
//
// THE CLASS: a colour written as a literal cannot know which theme is on, so it is right in one
// theme by luck and wrong in the other by construction. Every colour belongs to a token
// (`var(--surface)`, `var(--c-fg-ffffff)`, …), which is defined once per theme.
//
// THE SHAPE THIS CATCHES. A literal is hardest to spot, and most harmful, exactly where it is the
// OTHER BRANCH of a choice whose first branch is already a token — the author tokenised one half
// and left the other. So a line that reaches for `var(--…)` AND carries a bare colour keyword is
// the defect, wherever the value eventually lands; and the plain `background: 'white'` form is
// caught too. A semantic TONE name (`lamp={'green'}`, `tone: 'red'`) is a word a component maps to
// a token itself, not a colour value, so it is only an offence when it shares a line with a token.
//
// It is a RATCHET: the count is zero and may only stay zero.
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'

const KEYWORDS = 'white|black|red|blue|green|grey|gray|silver|yellow|orange|purple|pink|brown'
const QUOTED = String.raw`'(${KEYWORDS})'`
const PROPS = 'background|backgroundColor|color|borderColor|fill|stroke|[Bb]g|[Cc]olor'
const PATTERNS = [
  // a tokenised branch and a literal branch in the same choice
  String.raw`var\(--[^)]*\).{0,60}${QUOTED}`,
  String.raw`${QUOTED}.{0,60}var\(--`,
  // the plain property / variable assignment form
  String.raw`\b(${PROPS})\s*[:=]\s*${QUOTED}`,
]

function grep(pattern: string): string[] {
  try {
    const out = execFileSync('grep', ['-rnE', pattern, 'src', '--include=*.tsx', '--include=*.ts'],
                             { encoding: 'utf8' })
    return out.split('\n').filter(Boolean)
      // A comment line is prose about the defect, not a style value — and this lock's own file
      // quotes the broken line on purpose, as the proof below that the pattern still sees it.
      .filter(l => !/^[^:]*:\d+:\s*(\/\/|\*|\/\*)/.test(l))
      .filter(l => !l.startsWith('src/lib/__tests__/theme-colour-literal.lock.test.ts:'))
  } catch (e: unknown) {
    if ((e as { status?: number }).status === 1) return []        // grep found nothing — the state this lock defends
    throw e
  }
}
const offenders = () => Array.from(new Set(PATTERNS.flatMap(grep))).sort()

describe('a colour on a themed surface is a token, never a literal', () => {
  it('no screen paints with a bare colour keyword', () => {
    expect(offenders()).toEqual([])
  })

  it('the lock really sees the shape that broke the Schedule screen', () => {
    // Proof the pattern is not merely satisfied by today's source: the exact reported line must
    // match, and the two shapes it must NOT flag must not.
    const broke = `const rowBg = ri % 2 === 1 ? 'var(--surface2)' : 'white'`
    const fixed = `const rowBg = ri % 2 === 1 ? 'var(--surface2)' : 'var(--surface)'`
    const tone = `<Lamp lamp={chain.ok ? 'green' : 'red'} size={10} />`
    const hit = (s: string) => PATTERNS.some(p => new RegExp(p).test(s))
    expect(hit(broke)).toBe(true)
    expect(hit(fixed)).toBe(false)
    expect(hit(tone)).toBe(false)
  })
})
