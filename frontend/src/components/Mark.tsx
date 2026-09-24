/* The MetricsPro mark — "The Fold": two sources laid over one another, amber where they disagree,
 * green where they have settled. Identical geometry to website/favicon.svg and the marketing site's
 * inline header mark; a 32-unit grid in whole units so edges land on whole pixels at small sizes.
 *
 * Size and colour are ATTRIBUTES, not CSS. The marketing site learned this the hard way: when the
 * mark's fill and size came only from a stylesheet, a stale CSS cache rendered it black at its
 * default 300x150. Drawn this way it is correct with no stylesheet at all.
 */
export default function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" style={{ display: 'block', flex: 'none' }}>
      <rect fill="#c9770f" x="3" y="3" width="19" height="19" rx="4" />
      <rect fill="#217a5e" x="10" y="10" width="19" height="19" rx="4" />
      <rect fill="#c9770f" x="10" y="10" width="12" height="12" rx="3" opacity=".55" />
    </svg>
  )
}
