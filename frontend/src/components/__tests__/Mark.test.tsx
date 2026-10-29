// The React smoke test: proves the component pipeline (TSX -> jsdom -> Testing Library) works, over
// a component with no context and no network. If this ever goes red, the frontend runner is broken,
// not the mark.
import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import Mark from '../Mark'

describe('<Mark />', () => {
  it('renders an svg at the default size', () => {
    const { container } = render(<Mark />)
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg?.getAttribute('width')).toBe('26')
    expect(svg?.getAttribute('viewBox')).toBe('0 0 32 32')
  })

  it('takes its size from the prop, not from a stylesheet', () => {
    // The file's own comment: a stale CSS cache once rendered the mark black at 300x150 because
    // size and colour came only from CSS. Size is an attribute and this keeps it one.
    const { container } = render(<Mark size={48} />)
    expect(container.querySelector('svg')?.getAttribute('width')).toBe('48')
  })

  it('is hidden from assistive technology', () => {
    const { container } = render(<Mark />)
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe('true')
  })
})
