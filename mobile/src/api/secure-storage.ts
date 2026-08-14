import * as SecureStore from 'expo-secure-store'

// ── Chunked SecureStore adapter ──────────────────────────────────────────────────────────────────
// The Supabase session (access + refresh JWT + user) is the app's crown jewel: whoever holds it is
// signed in. On mobile that must NOT live in AsyncStorage (plaintext, world-readable in a rooted /
// jailbroken device or a backup). expo-secure-store keeps it in the iOS Keychain / Android Keystore.
//
// One wrinkle: SecureStore warns that values > 2048 bytes may fail to store on Android, and a Supabase
// session can exceed that. So this adapter transparently CHUNKS a large value across multiple keyed
// entries and reassembles it on read. A small value stores as-is (one entry) — byte-identical to a
// plain SecureStore for the common case.
const CHUNK_SIZE = 1800 // headroom under the 2048 soft limit
const COUNT_SUFFIX = '__chunks'

const secureOptions: SecureStore.SecureStoreOptions = {
  // Require the device to be unlocked at least once since boot before the secret is readable.
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK,
}

async function clearChunks(key: string, count: number): Promise<void> {
  const deletions: Promise<void>[] = [SecureStore.deleteItemAsync(`${key}${COUNT_SUFFIX}`)]
  for (let i = 0; i < count; i++) deletions.push(SecureStore.deleteItemAsync(`${key}__${i}`))
  await Promise.all(deletions).catch(() => {})
}

async function readChunkCount(key: string): Promise<number> {
  const raw = await SecureStore.getItemAsync(`${key}${COUNT_SUFFIX}`)
  const n = raw ? parseInt(raw, 10) : 0
  return Number.isFinite(n) && n > 0 ? n : 0
}

export const chunkedSecureStore = {
  async getItem(key: string): Promise<string | null> {
    try {
      const direct = await SecureStore.getItemAsync(key, secureOptions)
      if (direct !== null) return direct
      const count = await readChunkCount(key)
      if (count === 0) return null
      const parts: string[] = []
      for (let i = 0; i < count; i++) {
        const part = await SecureStore.getItemAsync(`${key}__${i}`, secureOptions)
        if (part === null) return null // torn write — treat as absent, forces re-auth
        parts.push(part)
      }
      return parts.join('')
    } catch (e) {
      console.warn('[secure-storage] getItem failed', e)
      return null
    }
  },

  async setItem(key: string, value: string): Promise<void> {
    try {
      // Reset any prior representation (chunked or direct) before writing the new one.
      const prevCount = await readChunkCount(key)
      if (prevCount) await clearChunks(key, prevCount)

      if (value.length <= CHUNK_SIZE) {
        await SecureStore.setItemAsync(key, value, secureOptions)
        return
      }
      // Store chunked; remove any stale direct entry under the base key.
      await SecureStore.deleteItemAsync(key).catch(() => {})
      const chunks = Math.ceil(value.length / CHUNK_SIZE)
      for (let i = 0; i < chunks; i++) {
        await SecureStore.setItemAsync(
          `${key}__${i}`,
          value.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE),
          secureOptions,
        )
      }
      await SecureStore.setItemAsync(`${key}${COUNT_SUFFIX}`, String(chunks), secureOptions)
    } catch (e) {
      console.warn('[secure-storage] setItem failed', e)
    }
  },

  async removeItem(key: string): Promise<void> {
    try {
      const count = await readChunkCount(key)
      await SecureStore.deleteItemAsync(key).catch(() => {})
      if (count) await clearChunks(key, count)
    } catch (e) {
      console.warn('[secure-storage] removeItem failed', e)
    }
  },
}
