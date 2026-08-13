import { QueryClient } from '@tanstack/react-query'

import { ApiError, AuthError } from './client'

// ── React Query client ───────────────────────────────────────────────────────────────────────────
// The latency layer for READS. Stale-while-revalidate means a screen paints instantly from cache and
// refreshes in the background — critical on a slow store network. Defaults tuned for retail use:
//   • staleTime 30s          — a rep tabbing between screens doesn't refetch on every focus.
//   • retry: network only     — a 4xx/auth error is not worth retrying; a dropped connection is.
//   • no retry on AuthError   — bounce to sign-in instead of hammering.
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        if (error instanceof AuthError) return false
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false
        return failureCount < 3
      },
      retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    },
    mutations: {
      retry: false,
    },
  },
})
