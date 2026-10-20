#!/usr/bin/env node
/*
 * THE till helper (§POS-04) — the only thing that can hand a receipt to the printer.
 *
 * Owner 2026-10-20: *"the receipts will print to the thermal printer only if desired by the user,
 * there cannot be any harcoded rule so we need have a helper"*.
 *
 * WHY IT EXISTS. A browser can hand a page to the operating system's print dialog, but it cannot
 * speak to a USB till printer and it cannot install a driver. This helper runs on the counter PC,
 * listens on the loopback address ONLY, and passes the bytes the platform rendered to the printer
 * the operating system already knows about — so the driver stays where it belongs (Windows, macOS
 * or CUPS) and no printer make appears in any of our code.
 *
 * WHAT IT WILL NOT DO. It accepts one request: print these bytes. It runs no command the caller
 * names, reads no file the caller names, and binds nothing but 127.0.0.1, so nothing outside this
 * PC can reach it. It holds no credential and talks to no database.
 *
 * Node 18 or newer, no dependencies.
 *
 *   node receipt-helper.js --printer "Receipt Printer"
 *
 * Options (or the matching RECEIPT_HELPER_* environment variables):
 *   --printer   the printer's name as the operating system lists it (required)
 *   --port      the port to listen on (default 9100 — the platform's default setting)
 *   --origin    an extra browser origin allowed to call it, repeatable
 */
'use strict'
const http = require('http')
const os = require('os')
const path = require('path')
const fs = require('fs')
const { execFile } = require('child_process')
const crypto = require('crypto')

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`)
  if (i > -1 && process.argv[i + 1]) return process.argv[i + 1]
  return process.env[`RECEIPT_HELPER_${name.toUpperCase()}`] || fallback
}

const PRINTER = arg('printer', '')
const PORT = Number(arg('port', '9100'))
// The app's own pages are the only callers. Anything else is refused by the browser itself, because
// this list is what the helper will echo back as an allowed origin.
const ORIGINS = ['https://app.metricspro.tech', 'http://localhost:3000']
  .concat(process.argv.reduce((acc, v, i) => (v === '--origin' && process.argv[i + 1]
    ? acc.concat(process.argv[i + 1]) : acc), []))

if (!PRINTER) {
  console.error('Which printer? Pass --printer "<name as the system lists it>" and start again.')
  console.error('Windows: see the names with   wmic printer get name')
  console.error('macOS / Linux: see them with  lpstat -p')
  process.exit(1)
}

/** Hand the bytes to the printer the operating system knows, through its own spooler. */
function spool(bytes, done) {
  const file = path.join(os.tmpdir(), `receipt-${crypto.randomUUID()}.bin`)
  fs.writeFile(file, bytes, err => {
    if (err) return done(err)
    const cleanup = e => fs.unlink(file, () => done(e))
    if (process.platform === 'win32') {
      // The spooler takes the file as-is when it is told the data is already printer-ready.
      execFile('powershell.exe', ['-NoProfile', '-Command',
        'Get-Content -Encoding Byte -ReadCount 0 -Path $env:RH_FILE | ' +
        'Out-Printer -Name $env:RH_PRINTER'],
        { env: Object.assign({}, process.env, { RH_FILE: file, RH_PRINTER: PRINTER }) }, cleanup)
    } else {
      execFile('lp', ['-d', PRINTER, '-o', 'raw', file], cleanup)
    }
  })
}

const server = http.createServer((req, res) => {
  const origin = req.headers.origin || ''
  const allowed = ORIGINS.includes(origin)
  const cors = {
    'Access-Control-Allow-Origin': allowed ? origin : ORIGINS[0],
    'Access-Control-Allow-Headers': 'content-type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  }
  if (req.method === 'OPTIONS') { res.writeHead(204, cors); return res.end() }
  if (req.method !== 'POST') {
    res.writeHead(405, Object.assign({ 'content-type': 'application/json' }, cors))
    return res.end(JSON.stringify({ ok: false, error: 'Send the receipt as a POST.' }))
  }
  let body = ''
  req.on('data', chunk => {
    body += chunk
    // A receipt is a few kilobytes. Anything larger is not one, and is dropped rather than buffered.
    if (body.length > 512 * 1024) { req.destroy(); }
  })
  req.on('end', () => {
    let payload
    try { payload = JSON.parse(body || '{}') } catch (e) { payload = null }
    const b64 = payload && typeof payload.escpos_base64 === 'string' ? payload.escpos_base64 : ''
    if (!b64) {
      res.writeHead(400, Object.assign({ 'content-type': 'application/json' }, cors))
      return res.end(JSON.stringify({ ok: false, error: 'No receipt in that request.' }))
    }
    const bytes = Buffer.from(b64, 'base64')
    spool(bytes, err => {
      if (err) {
        console.error(`[${new Date().toISOString()}] could not print:`, err.message)
        res.writeHead(502, Object.assign({ 'content-type': 'application/json' }, cors))
        return res.end(JSON.stringify({ ok: false, error: `The printer refused it: ${err.message}` }))
      }
      console.log(`[${new Date().toISOString()}] printed ${bytes.length} bytes to ${PRINTER}`)
      res.writeHead(200, Object.assign({ 'content-type': 'application/json' }, cors))
      res.end(JSON.stringify({ ok: true, bytes: bytes.length, printer: PRINTER }))
    })
  })
})

server.listen(PORT, '127.0.0.1', () => {
  console.log(`Receipt helper ready on http://127.0.0.1:${PORT}/print → printer "${PRINTER}"`)
  console.log('Leave this window open while the counter is trading.')
})
