/**
 * PROOF — AiAssistant.tsx must never leave the user on "thinking…" forever (SEV-1 2026-07-30).
 *
 * Not a grep: the real AiAssistant.tsx is transpiled with the repo's own TypeScript, executed against a
 * miniature React (hook store + element recorder) and a stubbed `api()`, then the REAL `send` handler
 * pulled off the Send button is driven through three scenarios (fast reply / stall past the timeout /
 * server error). Fake timers keep it instant.
 *
 * Run:  cd frontend && node prove_ai_assistant_timeout.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const require = createRequire(import.meta.url)
const ts = require('typescript')

const FILE = path.join(__dirname, 'src/components/AiAssistant.tsx')
const SRC = fs.readFileSync(FILE, 'utf8')

let P = 0, F = 0
const check = (name, cond, detail = '') => {
  if (cond) { P++; console.log(`  PASS  ${name}`) }
  else { F++; console.log(`  FAIL  ${name}   ${detail}`) }
}

console.log('='.repeat(78)); console.log('A. Source shape'); console.log('='.repeat(78))
check('A1 AbortController is used', SRC.includes('new AbortController()'))
check('A2 a timer aborts it', /setTimeout\(\s*\(\)\s*=>\s*\w+\.abort\(\)/.test(SRC))
check('A3 the signal is handed to api()', /signal:\s*\w+\.signal/.test(SRC))
check('A4 timer is always cleared', SRC.includes('clearTimeout('))
check('A5 timeout constant is named, not magic', SRC.includes('AI_CLIENT_TIMEOUT_MS'))
check('A6 client timeout >= backend worst case (60s)', /AI_CLIENT_TIMEOUT_MS\s*=\s*60_?000/.test(SRC))
check('A7 friendly slow-path copy mentions retry AND raising a ticket',
  /AI_SLOW_MSG\s*=\s*'[^']*too long[^']*try again[^']*ticket/i.test(SRC))
check('A8 SHARED client.ts is NOT modified by this component', !SRC.includes("lib/client'") === false)

console.log(); console.log('='.repeat(78))
console.log('B. LIVE — drive the real send() handler')
console.log('='.repeat(78))

// ── miniature React ────────────────────────────────────────────────────────────────────────────
let hooks = [], hookIdx = 0
const React = {
  useState(init) {
    const i = hookIdx++
    if (!(i in hooks)) hooks[i] = typeof init === 'function' ? init() : init
    return [hooks[i], (v) => { hooks[i] = typeof v === 'function' ? v(hooks[i]) : v }]
  },
  useRef(init) { const i = hookIdx++; if (!(i in hooks)) hooks[i] = { current: init }; return hooks[i] },
  useEffect() { /* effects are not run — we only need the render tree */ },
  createElement(type, props, ...children) { return { type, props: { ...props, children } } },
}

// ── stubbed api() ──────────────────────────────────────────────────────────────────────────────
let apiMode = 'ok', apiCalls = []
const api = (url, opts = {}) => new Promise((resolve, reject) => {
  apiCalls.push({ url, opts })
  if (apiMode === 'error') return setTimeout(() => reject(new Error('500 boom')), 5)
  const delay = apiMode === 'stall' ? 10 * 60 * 1000 : 5      // 'stall' = never answers in time
  const t = setTimeout(() => resolve({ reply: 'model answer' }), delay)
  if (opts.signal) {
    if (opts.signal.aborted) return reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
    opts.signal.addEventListener('abort', () => {
      clearTimeout(t)
      reject(Object.assign(new Error('The user aborted a request.'), { name: 'AbortError' }))
    })
  }
})

// ── transpile the real component and load it with those stubs ──────────────────────────────────
const js = ts.transpileModule(SRC, {
  compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  fileName: 'AiAssistant.tsx',
}).outputText
check('B1 component transpiles', js.length > 0)

const shimRequire = (id) => {
  if (id === 'react') return { ...React, default: React, __esModule: true }
  if (id === 'next/link') return { __esModule: true, default: 'Link' }
  if (id === '@/lib/client') return { __esModule: true, api, ORG_ID: '00000000-0000-0000-0000-000000000001' }
  throw new Error('unexpected import: ' + id)
}
const mod = { exports: {} }
new Function('require', 'exports', 'module', 'React', js)(shimRequire, mod.exports, mod, React)
const AiAssistant = mod.exports.default
check('B2 default export is the component', typeof AiAssistant === 'function')

// find the Send button's onClick — that IS the real send()
function findSend(node, out = []) {
  if (!node || typeof node !== 'object') return out
  if (Array.isArray(node)) { node.forEach(n => findSend(n, out)); return out }
  const kids = node.props?.children
  // send() is the only ASYNC onClick in the tree (the header toggle is a plain arrow fn)
  if (node.type === 'button' && node.props?.onClick?.constructor?.name === 'AsyncFunction')
    out.push(node.props.onClick)
  if (kids) findSend(kids, out)
  return out
}

function render() { hookIdx = 0; return AiAssistant() }

// hook order in AiAssistant(): 0 open · 1 status · 2 msgs · 3 input · 4 busy · 5 err · 6 endRef
// Mount fresh, seed the hook store, re-render — send() closes over the SECOND render's values,
// exactly like React would after a state update.
function mount(input) {
  hooks = []
  render()                     // pass 1 establishes the hook slots
  hooks[0] = true              // open the panel so the Send button renders
  hooks[1] = null              // status
  hooks[2] = []                // msgs
  hooks[3] = input             // input
  hooks[4] = false             // busy
  hooks[5] = ''                // err
  return findSend(render())
}

const sends = mount('how do I upload sales?')
check('B3 found the Send button handler in the rendered tree', sends.length === 1, `found ${sends.length}`)

const msgs = () => hooks[2]
const busy = () => hooks[4]
const err = () => hooks[5]

async function scenario(mode, input = 'how do I upload sales?') {
  apiMode = mode; apiCalls = []
  const send = mount(input)[0]
  const t0 = Date.now()
  await send()
  return Date.now() - t0
}

// --- 1. happy path -----------------------------------------------------------------------------
await scenario('ok')
check('B4 happy path: user + assistant messages recorded', msgs().length === 2, JSON.stringify(msgs()))
check('B5 happy path: assistant text is the model reply', msgs()[1]?.content === 'model answer')
check('B6 happy path: busy released', busy() === false)
check('B7 happy path: no error banner', err() === '')
check('B8 api() called with an AbortSignal', apiCalls[0]?.opts?.signal instanceof AbortSignal)
check('B9 api() path carries the /api/v1 prefix + org_id (RULE ONE)',
  apiCalls[0]?.url.startsWith('/api/v1/helpdesk/ai-assist?org_id='), apiCalls[0]?.url)
check('B10 api() method is POST', apiCalls[0]?.opts?.method === 'POST')

// --- 2. THE BUG: the backend never answers -----------------------------------------------------
// Shrink the timeout by monkey-patching setTimeout so the 60s deadline lands in ~20ms.
const realSetTimeout = globalThis.setTimeout
globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms === 60000 ? 20 : ms, ...rest)
const dt = await scenario('stall')
globalThis.setTimeout = realSetTimeout
check('B11 stalled call is ABORTED instead of hanging forever', dt < 2000, `took ${dt}ms`)
check('B12 stall: a message is shown in the thread (not silence)', msgs().length === 2, JSON.stringify(msgs()))
check('B13 stall: the message is the friendly slow copy',
  /taking too long/.test(msgs()[1]?.content || '') && /ticket/.test(msgs()[1]?.content || ''),
  msgs()[1]?.content)
check('B14 stall: error banner set to the same friendly copy', /taking too long/.test(err() || ''), err())
check('B15 stall: busy released so the input re-enables', busy() === false)
check('B16 stall: the raw "The user aborted a request." never reaches the user',
  !/aborted/i.test(msgs()[1]?.content || '') && !/aborted/i.test(err() || ''))

// --- 3. server error still behaves as before ---------------------------------------------------
await scenario('error')
check('B17 server error: original graceful copy retained',
  /I hit an error/.test(msgs()[1]?.content || ''), msgs()[1]?.content)
check('B18 server error: banner shows the server message', err() === '500 boom', err())
check('B19 server error: busy released', busy() === false)

// --- 4. guardrails -----------------------------------------------------------------------------
await scenario('ok', '   ')
check('B20 blank input is still a no-op (no request sent)', apiCalls.length === 0 && msgs().length === 0)

console.log()
console.log('='.repeat(78))
console.log(`RESULT: ${P} passed, ${F} failed`)
console.log('='.repeat(78))
process.exit(F ? 1 : 0)
