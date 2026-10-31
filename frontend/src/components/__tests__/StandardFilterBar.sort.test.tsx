// THE SORT CONTROL ON THE SHARED BAR (owner ask 2026-10-31, index §CORE-28) — rendered, clicked, and
// read back, in jsdom. The pure rules are proved in `lib/__tests__/standard-sort.test.ts`; this file
// proves the BAR dereferences them: that the control appears only where a page declared fields, that
// picking an option emits the shared `sort` state and nothing else, and that Clear puts the report
// back in its own order.
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import StandardFilterBar from '../StandardFilterBar'
import { emptyStandardFilter, type SortFieldDecl, type StandardFilterValue } from '@/lib/standard-filters'

const FIELDS: SortFieldDecl[] = [
  { field: 'rep', label: 'Employee', get: r => r.rep },
  { field: 'store', label: 'Store', get: r => r.store },
]

const bar = (value: StandardFilterValue, onChange = vi.fn(), fields?: SortFieldDecl[]) => {
  render(<StandardFilterBar value={value} onChange={onChange} periodMode="none" sortFields={fields} />)
  return onChange
}

describe('<StandardFilterBar> sort by', () => {
  it('renders a Sort by control listing each declared field in both directions', () => {
    bar(emptyStandardFilter(), vi.fn(), FIELDS)
    const select = screen.getByLabelText('Sort rows by') as HTMLSelectElement
    const labels = [...select.options].map(o => o.textContent)
    expect(labels).toEqual([
      'Report order', 'Employee (A–Z)', 'Employee (Z–A)', 'Store (A–Z)', 'Store (Z–A)',
    ])
    expect(select.value).toBe('')
  })

  it('renders NO sort control when the page declared no sortable fields', () => {
    bar(emptyStandardFilter(), vi.fn(), undefined)
    expect(screen.queryByLabelText('Sort rows by')).toBeNull()
    bar(emptyStandardFilter(), vi.fn(), [])
    expect(screen.queryByLabelText('Sort rows by')).toBeNull()
  })

  it('emits the shared sort state when a sort is picked, and changes nothing else', () => {
    const onChange = bar(emptyStandardFilter('2026-09'), vi.fn(), FIELDS)
    fireEvent.change(screen.getByLabelText('Sort rows by'), { target: { value: 'store:desc' } })
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(onChange.mock.calls[0][0]).toEqual({
      period: '2026-09', periodTo: '', stores: [], markets: [], reps: [],
      sort: { field: 'store', dir: 'desc' },
    })
  })

  it('shows the chosen sort back to the user (a control that forgets is the month-dropdown defect)', () => {
    bar({ ...emptyStandardFilter(), sort: { field: 'rep', dir: 'desc' } }, vi.fn(), FIELDS)
    expect((screen.getByLabelText('Sort rows by') as HTMLSelectElement).value).toBe('rep:desc')
  })

  it('offers Clear when ONLY a sort is set, and clearing returns the report own order', () => {
    const onChange = vi.fn()
    bar({ ...emptyStandardFilter(), sort: { field: 'rep', dir: 'asc' } }, onChange, FIELDS)
    fireEvent.click(screen.getByText('Clear filters'))
    expect(onChange.mock.calls[0][0].sort).toBeNull()
  })

  it('puts the report back in its own order from the control itself', () => {
    const onChange = bar({ ...emptyStandardFilter(), sort: { field: 'rep', dir: 'asc' } }, vi.fn(), FIELDS)
    fireEvent.change(screen.getByLabelText('Sort rows by'), { target: { value: '' } })
    expect(onChange.mock.calls[0][0].sort).toBeNull()
  })

  it('sorts by whatever a tenant calls its own stores — the labels come from the rows, not the bar', () => {
    // Vzone names shops, Cellfonz uses B-codes, LuxeLink uses street addresses. The bar never spells
    // any of them: the page declares the FIELD and the rows carry the values.
    const vzone: SortFieldDecl[] = [{ field: 'store', label: 'Shop', get: r => r.store }]
    bar(emptyStandardFilter(), vi.fn(), vzone)
    const labels = [...(screen.getByLabelText('Sort rows by') as HTMLSelectElement).options].map(o => o.textContent)
    expect(labels).toEqual(['Report order', 'Shop (A–Z)', 'Shop (Z–A)'])
  })
})
