import type { NextConfig } from "next";
// ONE HOME for where the backend is (src/lib/apiBase.ts) and the site routing policy (site-routing.ts),
// owner 2026-09-28, index §40. Locked by backend/harness_one_domain_lock.py.
import { API_ENV, apiRewrites, directOrigin, productionConfigProblems } from "./src/lib/apiBase";
import { canonicalHostRedirects, connectSrc } from "./site-routing";

// Frontend security headers (Security Controls Spec §4, item 11).
//
// The enforceable headers below are safe and applied to every route. The Content-Security-Policy is
// shipped as REPORT-ONLY on purpose: this app uses inline styles (style={{…}}) throughout and Next
// injects inline hydration scripts, so an enforcing policy risks breaking the app in ways we can't
// verify without running it. Report-Only surfaces violations in the browser console/report pipeline
// without blocking anything; once the console is clean we promote it to an enforcing
// `Content-Security-Policy` (tracked in docs/SECURITY_DAILY_QUESTIONS.md).
//
// NOTE on Permissions-Policy: geolocation is allowed for `self` because the access-log feature reads
// navigator.geolocation from our own origin. (The backend API, which never needs it, disables it.)
const CSP_REPORT_ONLY = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "img-src 'self' data: blob: https:",
  "font-src 'self' data:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  // API calls are same-origin now; the only other API origin is the DIRECT-class one, when there is one.
  connectSrc(directOrigin()),
  "form-action 'self'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "geolocation=(self), microphone=(), camera=()" },
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
  { key: "Content-Security-Policy-Report-Only", value: CSP_REPORT_ONLY },
];

// A PRODUCTION build asserts the configuration it cannot work without, and FAILS rather than shipping a
// deploy that renders and then cannot reach its API (owner incident 2026-10-03, index §40.10). The
// development fallbacks in apiBase.ts stay — they are right for `next dev` and for a preview — but they
// must not be reachable by a production build. The rules live in the home; this is the one caller that
// enforces them.
const CONFIG_PROBLEMS = productionConfigProblems(API_ENV, process.env.VERCEL_ENV === "production");
if (CONFIG_PROBLEMS.length) {
  throw new Error(
    "This production build is not configured and would ship an app that cannot reach its backend:\n" +
    CONFIG_PROBLEMS.map((p) => `  - ${p}`).join("\n") +
    "\nFix the environment variables in the production environment and redeploy. See index §40.8/§40.10.",
  );
}

const nextConfig: NextConfig = {
  // The browser only ever talks to this site's own host: /api/v1/* and /health are proxied to the
  // backend server-side. The origin is BACKEND_ORIGIN (server-only), falling back to
  // NEXT_PUBLIC_API_URL so an existing deploy keeps working with no dashboard change.
  async rewrites() {
    return apiRewrites();
  },
  // A PRODUCTION request on the platform's own hostname is sent (308) to the canonical site, same path
  // and query. Previews and localhost are untouched. Off until NEXT_PUBLIC_SITE_URL is set explicitly.
  async redirects() {
    return canonicalHostRedirects({
      VERCEL_ENV: process.env.VERCEL_ENV,
      NEXT_PUBLIC_SITE_URL: API_ENV.NEXT_PUBLIC_SITE_URL,
    });
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: SECURITY_HEADERS,
      },
    ];
  },
};

export default nextConfig;
