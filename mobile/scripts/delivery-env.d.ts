// Types for scripts/delivery-env.js, which is CommonJS so that BOTH the node delivery scripts and
// the app's own bundle can read the ONE declaration of what this app cannot run without. Declared
// here rather than converting the module to TypeScript, because the delivery scripts run under
// plain `node` with no transpiler (index §CORE-38).
export const REQUIRED_EXTRA: Record<string, string>
export function knownProfiles(easJson: unknown): string[]
export function profileEnv(easJson: unknown, profile: string): Record<string, string>
export function missingRequiredExtra(extra: unknown): string[]
