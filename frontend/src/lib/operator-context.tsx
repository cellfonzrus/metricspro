'use client'
// The operator identity the console shell (`(operator)/operator/layout.tsx`) has already fetched,
// shared with every page under it.
//
// It lives in lib/ rather than being exported from the layout file on purpose: an App Router layout
// module is a route convention, and the only exports Next.js expects from it are the default
// component and the metadata helpers. Hanging an arbitrary hook off it works today but is exactly
// the kind of thing a framework upgrade breaks — and this file is what the whole console reads.
import { createContext, useContext } from 'react'
import type { OperatorMe } from '@/lib/operator'

export const OperatorContext = createContext<OperatorMe | null>(null)

/** The signed-in platform operator, or null while the shell is still resolving them. */
export function useOperator(): OperatorMe | null {
  return useContext(OperatorContext)
}
