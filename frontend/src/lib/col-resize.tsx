'use client'
// Reusable auto-fit + user-resizable table columns.
//
// Auto-fit: give the table `tableLayout:'auto'` and cells `whiteSpace:'nowrap'` so each column sizes to
// its content (no more over-wide columns). Then render a <colgroup> using `width(key)` and drop a
// <ResizeHandle> in each <th>: the user drags a column's right edge to set an explicit width, or
// double-clicks the handle to reset that column back to auto-fit.
import { useState, useRef } from 'react'

export function useColumnResize() {
  const [widths, setWidths] = useState<Record<string, number>>({})
  const drag = useRef<{ key: string; startX: number; startW: number } | null>(null)

  function start(key: string, e: React.MouseEvent<HTMLElement>) {
    const th = e.currentTarget.closest('th') as HTMLElement | null
    const startW = th ? th.getBoundingClientRect().width : (widths[key] || 120)
    drag.current = { key, startX: e.clientX, startW }
    const move = (ev: MouseEvent) => {
      if (!drag.current) return
      const w = Math.max(44, drag.current.startW + (ev.clientX - drag.current.startX))
      setWidths(x => ({ ...x, [drag.current!.key]: Math.round(w) }))
    }
    const up = () => {
      drag.current = null
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    document.body.style.cursor = 'col-resize'
    e.preventDefault(); e.stopPropagation()
  }
  const reset = (key: string) => setWidths(x => { const n = { ...x }; delete n[key]; return n })
  const resetAll = () => setWidths({})
  const width = (key: string): number | undefined => widths[key]
  const dirty = Object.keys(widths).length > 0
  return { width, start, reset, resetAll, dirty }
}

// Right-edge grip for a <th> (which must be position:relative). Drag = resize, double-click = auto-fit.
export function ResizeHandle({ onDown, onReset }: { onDown: (e: React.MouseEvent<HTMLElement>) => void; onReset?: () => void }) {
  return (
    <span
      onMouseDown={onDown}
      onDoubleClick={onReset}
      title="Drag to resize · double-click to auto-fit"
      style={{ position: 'absolute', top: 0, right: 0, height: '100%', width: 7, cursor: 'col-resize', userSelect: 'none' }}
    />
  )
}
