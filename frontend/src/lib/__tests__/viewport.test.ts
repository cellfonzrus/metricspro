// THE narrow-viewport one home, proved (index §CORE-25).
//
// The defect this starts from, measured in a real browser on 2026-10-30: the POS register's sale
// view is `grid-template-columns: 1fr 340px` inside `overflow: hidden`. On an iPhone the app shell
// gives a page ~94px, so the 340px summary pane takes everything and the LEFT pane — the cart, its
// product lines and the "+ Add Product" button — computes to **0px** and is clipped. The screen
// shows no products. On a 1440px desktop the same pane measures 802px, which is why it had never
// been seen.
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { NARROW_MAX, NARROW_QUERY, isNarrowWidth, responsiveColumns, twoPaneColumns, twoPaneFrame, useNarrowViewport } from '../viewport'

describe('isNarrowWidth', () => {
  it('calls a phone narrow and a desk wide', () => {
    expect(isNarrowWidth(390)).toBe(true)     // iPhone 14
    expect(isNarrowWidth(430)).toBe(true)     // iPhone 15 Pro Max
    expect(isNarrowWidth(1440)).toBe(false)
  })
  it('puts the boundary ON the breakpoint, inclusive', () => {
    expect(isNarrowWidth(NARROW_MAX)).toBe(true)
    expect(isNarrowWidth(NARROW_MAX + 1)).toBe(false)
  })
  it('treats an unknown width as a desk — the fallback is today behaviour, never a surprise stack', () => {
    expect(isNarrowWidth(0)).toBe(false)
    expect(isNarrowWidth(Number.NaN)).toBe(false)
  })
  it('spells the media query from the same number, so the two can never disagree', () => {
    expect(NARROW_QUERY).toBe(`(max-width: ${NARROW_MAX}px)`)
  })
})

describe('responsiveColumns', () => {
  it('hands back the desk template untouched on a desk — a wide screen is never rearranged', () => {
    expect(responsiveColumns('180px 1fr 140px 220px', false)).toBe('180px 1fr 140px 220px')
    expect(responsiveColumns('minmax(0,1fr) 360px', false)).toBe('minmax(0,1fr) 360px')
  })
  it('collapses ANY desk template to one column on a narrow screen', () => {
    expect(responsiveColumns('180px 1fr 140px 220px', true)).toBe('1fr')
    expect(responsiveColumns('minmax(0,1fr) 360px', true)).toBe('1fr')
  })
})

describe('twoPaneColumns', () => {
  it('keeps the desk layout exactly as it was', () => {
    expect(twoPaneColumns(340, false)).toBe('1fr 340px')
    expect(twoPaneColumns(280, false, 'start')).toBe('280px 1fr')
  })
  it('stacks on a narrow screen instead of squeezing a pane to nothing', () => {
    expect(twoPaneColumns(340, true)).toBe('1fr')
    expect(twoPaneColumns(280, true, 'start')).toBe('1fr')
  })
  it('is the general helper underneath — one rule, not two', () => {
    expect(twoPaneColumns(340, false)).toBe(responsiveColumns('1fr 340px', false))
    expect(twoPaneColumns(340, true)).toBe(responsiveColumns('1fr 340px', true))
  })
})

describe('twoPaneFrame', () => {
  it('on a desk, the frame is the fixed-height clipped pane pair it has always been', () => {
    expect(twoPaneFrame(340, false, { height: 'calc(100vh - 220px)', minHeight: 480 })).toEqual({
      display: 'grid', gridTemplateColumns: '1fr 340px', overflow: 'hidden',
      height: 'calc(100vh - 220px)', minHeight: 480,
    })
  })
  it('stacked, nothing is clipped and the page grows — a viewport-height box would cut the pane below', () => {
    expect(twoPaneFrame(340, true, { height: 'calc(100vh - 220px)', minHeight: 480 })).toEqual({
      display: 'grid', gridTemplateColumns: '1fr', overflow: 'visible',
      height: 'auto', minHeight: undefined,
    })
  })
})

describe('useNarrowViewport', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  function stubMatchMedia(matches: boolean) {
    const listeners = new Set<() => void>()
    const mql = {
      matches, media: NARROW_QUERY,
      addEventListener: (_: string, fn: () => void) => { listeners.add(fn) },
      removeEventListener: (_: string, fn: () => void) => { listeners.delete(fn) },
    }
    vi.stubGlobal('matchMedia', vi.fn(() => mql))
    return { listeners, rotate: (m: boolean) => { mql.matches = m; listeners.forEach(fn => fn()) } }
  }

  it('reports the viewport it is asked about', () => {
    stubMatchMedia(true)
    expect(renderHook(() => useNarrowViewport()).result.current).toBe(true)
  })

  it('asks for the breakpoint from the one home', () => {
    stubMatchMedia(false)
    renderHook(() => useNarrowViewport())
    expect(globalThis.matchMedia).toHaveBeenCalledWith(NARROW_QUERY)
  })

  it('follows a rotation without a reload', () => {
    const { rotate } = stubMatchMedia(false)
    const { result } = renderHook(() => useNarrowViewport())
    expect(result.current).toBe(false)
    act(() => rotate(true))
    expect(result.current).toBe(true)
  })

  it('unsubscribes when the screen unmounts', () => {
    const { listeners } = stubMatchMedia(true)
    const { unmount } = renderHook(() => useNarrowViewport())
    expect(listeners.size).toBe(1)
    unmount()
    expect(listeners.size).toBe(0)
  })

  it('a browser without matchMedia renders the desk layout rather than throwing', () => {
    vi.stubGlobal('matchMedia', undefined)
    expect(renderHook(() => useNarrowViewport()).result.current).toBe(false)
  })
})
