import type { MetadataRoute } from 'next'

// WHAT A SEARCH ENGINE MAY LOOK AT. Owner 2026-09-27 ("no sitemap.xml" on a production checklist).
//
// THE ANSWER FOR THIS APP IS THE OPPOSITE OF THE CHECKLIST, and deliberately so. That checklist is
// written for consumer sites that WANT to rank. Everything under `(platform)` is behind auth and
// holds a tenant's commissions, payroll, cash counts and employees' personal data. A crawler must
// never be invited in, and a sitemap of those routes would be an inventory of them — so this file
// exists precisely to say "don't", and there is intentionally NO sitemap.ts.
//
// What IS public and worth indexing: the marketing root and the two policy pages. A privacy policy
// nobody can find is a privacy policy that fails its purpose, and link previews for them already
// work off the root metadata.
//
// `/hr/public/onboarding/*` is unauthenticated but token-gated and carries personal data — disallowed
// explicitly. robots.txt is a request, not a control: the real protection is the token check on the
// endpoint. This just stops a well-behaved crawler from putting a token in an index.
const SITE = process.env.NEXT_PUBLIC_SITE_URL || 'https://metricspro.tech'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{
      userAgent: '*',
      allow: ['/', '/privacy', '/terms'],
      disallow: [
        '/api/',
        '/hr/',            // the token-gated onboarding portal and everything under it
        '/referral/',      // redemption links carry a code
        '/login',
        '/account/',
        // Every authenticated product area. Listed rather than wildcarded so adding a new one is a
        // visible decision instead of silently inheriting "indexable".
        '/commcalc/', '/closing/', '/accounts/', '/pos/', '/storeops/', '/admin/',
        '/hub/', '/franchise/', '/supply/', '/marketing/', '/vision/', '/operator/',
        '/training/', '/payables/', '/asset/', '/crm/', '/helpdesk/', '/recovery/',
      ],
    }],
    host: SITE,
  }
}
