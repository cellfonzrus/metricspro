import type { NextConfig } from "next";

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
  "connect-src 'self' https://*.supabase.co wss://*.supabase.co https://*.up.railway.app",
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

const nextConfig: NextConfig = {
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
