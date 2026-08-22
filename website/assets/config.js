/* MetricsPro website configuration — THE ONLY FILE YOU EDIT TO POINT THE SITE AT YOUR SYSTEMS.
 *
 * Both values below are already set to the live URLs as of this writing. Change them here if either
 * ever moves; nothing else on the site hardcodes them (the HTML ships with these same URLs as its
 * no-JavaScript default, and this file rewrites the links on load if they differ).
 */
window.MP_CONFIG = {
  // Where the platform API lives. The site reads ONE public, read-only endpoint from it —
  // /api/v1/billing/public-pricing — to show the prices you publish in Admin → Pricing & Free Trial.
  // Leave it blank ("") to skip the call entirely and show the built-in "priced against your
  // operation" card instead.
  apiBase: 'https://metricspro-production.up.railway.app',

  // Where the platform itself lives — the target of every "Sign in" / "Start free trial" link.
  appUrl: 'https://metricspro-five.vercel.app',
}
