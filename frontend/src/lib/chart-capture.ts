'use client'
// Capture a rendered recharts SVG (inside `container`) to a PNG data URL, baking computed styles so
// CSS-variable colors (var(--border) etc.) resolve to concrete values in the image. Used to embed a
// report's trend chart into its PDF / Print export (the export lib otherwise renders tables only).
export async function captureChartPng(
  container: HTMLElement | null,
  opts?: { background?: string; scale?: number },
): Promise<string> {
  if (!container || typeof window === 'undefined') return ''
  const svg = container.querySelector('svg') as SVGSVGElement | null
  if (!svg) return ''
  const clone = svg.cloneNode(true) as SVGSVGElement
  const orig = svg.querySelectorAll('*')
  const cl = clone.querySelectorAll('*')
  const props = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-opacity', 'fill-opacity', 'opacity', 'font-size', 'font-family', 'font-weight', 'color', 'text-anchor']
  for (let i = 0; i < orig.length && i < cl.length; i++) {
    const cs = getComputedStyle(orig[i] as Element)
    const st = (cl[i] as SVGElement).style
    for (const pr of props) { const v = cs.getPropertyValue(pr); if (v) st.setProperty(pr, v) }
  }
  const rect = svg.getBoundingClientRect()
  const w = Math.max(1, Math.round(rect.width || (svg as any).clientWidth || 640))
  const h = Math.max(1, Math.round(rect.height || (svg as any).clientHeight || 320))
  clone.setAttribute('width', String(w))
  clone.setAttribute('height', String(h))
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  const xml = new XMLSerializer().serializeToString(clone)
  const src = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(xml)))
  const scale = opts?.scale || 2
  return await new Promise<string>((resolve) => {
    const img = new Image()
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas')
        canvas.width = w * scale; canvas.height = h * scale
        const ctx = canvas.getContext('2d')
        if (!ctx) { resolve(''); return }
        ctx.fillStyle = opts?.background || '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.scale(scale, scale)
        ctx.drawImage(img, 0, 0, w, h)
        resolve(canvas.toDataURL('image/png'))
      } catch { resolve('') }
    }
    img.onerror = () => resolve('')
    img.src = src
  })
}
