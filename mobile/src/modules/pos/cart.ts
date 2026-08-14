import { useEffect, useState } from 'react'

import type { CartLine, Product } from '@/api/pos'

// ── POS cart store ───────────────────────────────────────────────────────────────────────────────
// A tiny in-memory, subscribable cart shared between the catalog screen (add items) and the checkout
// screen (review + pay). Deliberately NOT persisted: a cart is an in-progress transaction, not
// durable state — an abandoned app should not resurrect a stale cart at a different store. The
// *completed* sale is what becomes durable (via the offline queue on checkout).
let lines: CartLine[] = []
const subs = new Set<(l: CartLine[]) => void>()

function emit() {
  const snapshot = [...lines]
  for (const cb of Array.from(subs)) {
    try {
      cb(snapshot)
    } catch {
      /* ignore */
    }
  }
}

export function addToCart(p: Product, qty = 1) {
  const price = p.retail_price ?? 0
  const existing = lines.find((l) => l.product_id === p.id)
  if (existing) {
    existing.quantity += qty
  } else {
    lines.push({
      product_id: p.id,
      short_name: p.short_name,
      quantity: qty,
      unit_price: price,
      list_price: price,
      is_taxable: p.is_taxable ?? true,
    })
  }
  emit()
}

export function setQuantity(productId: string, qty: number) {
  const line = lines.find((l) => l.product_id === productId)
  if (!line) return
  if (qty <= 0) lines = lines.filter((l) => l.product_id !== productId)
  else line.quantity = qty
  emit()
}

export function setUnitPrice(productId: string, price: number) {
  const line = lines.find((l) => l.product_id === productId)
  if (!line) return
  line.unit_price = Math.max(0, price)
  emit()
}

export function removeFromCart(productId: string) {
  lines = lines.filter((l) => l.product_id !== productId)
  emit()
}

export function clearCart() {
  lines = []
  emit()
}

export function getCart(): CartLine[] {
  return [...lines]
}

export function cartCount(l: CartLine[] = lines): number {
  return l.reduce((s, x) => s + x.quantity, 0)
}

export function cartSubtotal(l: CartLine[] = lines): number {
  return l.reduce((s, x) => s + x.unit_price * x.quantity, 0)
}

export function useCart(): CartLine[] {
  const [state, setState] = useState<CartLine[]>(getCart())
  useEffect(() => {
    subs.add(setState)
    return () => {
      subs.delete(setState)
    }
  }, [])
  return state
}
