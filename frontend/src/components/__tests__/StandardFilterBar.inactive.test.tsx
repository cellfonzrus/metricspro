// SHOW INACTIVE, ON THE SHARED BAR (owner directive 2026-11-14, index §CORE-44) — rendered, ticked
// and read back in jsdom. The pure rules are proved in `lib/__tests__/standard-inactive.test.ts`;
// this file proves the BAR dereferences them, that the control appears only where a page can actually
// answer the question, and that hiding is never silent.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import StandardFilterBar from '../StandardFilterBar'
import { emptyStandardFilter, type StandardFilterValue } from '@/lib/standard-filters'

const bar = (value: StandardFilterValue, inactiveHidden?: number | null) => {
  const onChange = vi.fn()
  render(<StandardFilterBar value={value} onChange={onChange} periodMode="none"
                            inactiveHidden={inactiveHidden} />)
  return onChange
}

describe('<StandardFilterBar> Show inactive', () => {
  it('renders the checkbox, unticked, when the page says it can answer the question', () => {
    bar(emptyStandardFilter(), 0)
    const box = screen.getByLabelText('Show inactive') as HTMLInputElement
    expect(box.type).toBe('checkbox')
    expect(box.checked).toBe(false)
  })

  it('renders NO checkbox when the page declared no active flag', () => {
    // Same posture as `sortFields`: a control that cannot do anything must not be offered. A page
    // whose rows carry no flag would render a tick box that silently does nothing.
    bar(emptyStandardFilter(), undefined)
    expect(screen.queryByLabelText('Show inactive')).toBeNull()
    bar(emptyStandardFilter(), null)
    expect(screen.queryByLabelText('Show inactive')).toBeNull()
  })

  it('emits showInactive and changes nothing else when ticked', () => {
    const onChange = bar({ ...emptyStandardFilter('2026-09'), stores: ['B-1115'] }, 2)
    fireEvent.click(screen.getByLabelText('Show inactive'))
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual({
      period: '2026-09', periodTo: '', stores: ['B-1115'], markets: [], reps: [], sort: null,
      showInactive: true,
    })
  })

  it('SAYS how many rows it is hiding, rather than hiding them silently', () => {
    bar(emptyStandardFilter(), 3)
    expect(screen.getByText(/3 inactive hidden/i)).toBeTruthy()
  })

  it('says nothing when there is nothing hidden', () => {
    bar(emptyStandardFilter(), 0)
    expect(screen.queryByText(/inactive hidden/i)).toBeNull()
  })

  it('says nothing while Show inactive is on, because then nothing is hidden', () => {
    bar({ ...emptyStandardFilter(), showInactive: true }, 3)
    expect((screen.getByLabelText('Show inactive') as HTMLInputElement).checked).toBe(true)
    expect(screen.queryByText(/inactive hidden/i)).toBeNull()
  })

  it('offers Clear while inactive rows are shown, and Clear puts the default back', () => {
    const onChange = bar({ ...emptyStandardFilter(), showInactive: true }, 3)
    fireEvent.click(screen.getByText('Clear filters'))
    expect(onChange.mock.calls[0][0].showInactive).toBe(false)
  })
})
