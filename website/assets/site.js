/* MetricsPro website behaviour. Two small jobs, both optional:
 *
 *   1. Point every app link at MP_CONFIG.appUrl (the HTML already ships with the correct URL, so
 *      this only matters after you edit config.js).
 *   2. Fetch the published price list and render it.
 *
 * DESIGN RULE: the page must be complete WITHOUT this script. It ships with real content in the
 * pricing slot — the trial-led card — and this file replaces it only once a real price list comes
 * back. A blocked script, a blocked request, an API that is down, a CORS rejection, or nothing
 * published yet all end at the same place: the page you already see. Nothing here can blank it.
 */
(function () {
  'use strict'
  var cfg = window.MP_CONFIG || {}

  // ── 1. App links ────────────────────────────────────────────────────────────────────────────
  if (cfg.appUrl) {
    var base = String(cfg.appUrl).replace(/\/+$/, '')
    var links = document.querySelectorAll('[data-app-path]')
    for (var i = 0; i < links.length; i++) {
      links[i].href = base + links[i].getAttribute('data-app-path')
    }
  }

  // ── 2. Published pricing ────────────────────────────────────────────────────────────────────
  var slot = document.getElementById('pricing-cards')
  if (!slot || !cfg.apiBase) return

  var esc = function (s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
  }
  var money = function (amount, currency) {
    try {
      return new Intl.NumberFormat('en-US', {
        style: 'currency', currency: currency || 'USD',
        minimumFractionDigits: 0, maximumFractionDigits: Number.isInteger(amount) ? 0 : 2,
      }).format(amount)
    } catch (e) { return '$' + amount }
  }
  var appLink = function (path) {
    return (cfg.appUrl ? String(cfg.appUrl).replace(/\/+$/, '') : '') + path
  }

  var card = function (pkg, trialOn) {
    var featured = !!pkg.is_featured
    var price = Number(pkg.price) || 0
    var unit = pkg.unit_label || ('per ' + (pkg.cycle === 'annual' ? 'year' : 'month'))
    var html = '<article class="mpw-plan' + (featured ? ' mpw-plan-featured' : '') + '">'
    if (featured) html += '<span class="mpw-plan-flag">Most popular</span>'
    html += '<h3>' + esc(pkg.name) + '</h3>'
    if (pkg.tagline) html += '<p class="mpw-plan-tagline">' + esc(pkg.tagline) + '</p>'
    html += '<div class="mpw-plan-price">'
    html += price > 0
      ? '<span class="mpw-plan-amount">' + esc(money(price, pkg.currency)) + '</span>'
        + '<span class="mpw-plan-unit">' + esc(unit) + '</span>'
      : '<span class="mpw-plan-amount mpw-plan-amount-quiet">Talk to us</span>'
    html += '</div>'
    if (pkg.price_note) html += '<p class="mpw-plan-note">' + esc(pkg.price_note) + '</p>'
    if (pkg.features && pkg.features.length) {
      html += '<ul class="mpw-plan-features">'
      for (var i = 0; i < pkg.features.length; i++) html += '<li>' + esc(pkg.features[i]) + '</li>'
      html += '</ul>'
    }
    html += '<a class="mpw-btn ' + (featured ? 'mpw-btn-primary' : 'mpw-btn-ghost') + ' mpw-plan-cta"'
      + ' href="' + esc(appLink('/signup')) + '">'
      + esc(pkg.cta_label || (trialOn ? 'Start free trial' : 'Get started')) + '</a>'
    return html + '</article>'
  }

  var render = function (data) {
    var pkgs = Array.isArray(data.packages) ? data.packages : []
    // Nothing published, or pricing switched off in the back office → keep the page as shipped.
    if (!pkgs.length || data.show_pricing === false) return
    var trialOn = data.trial_enabled !== false
    var days = Number(data.trial_days) > 0 ? Number(data.trial_days) : 30
    var html = ''
    for (var i = 0; i < pkgs.length; i++) html += card(pkgs[i], trialOn)
    slot.className = 'mpw-plans mpw-plans-' + Math.min(pkgs.length, 4)
    slot.innerHTML = html

    var head = document.getElementById('pricing-headline')
    if (head && data.headline) head.textContent = data.headline
    var sub = document.getElementById('pricing-subhead')
    if (sub && data.subhead) sub.textContent = data.subhead
    var note = document.getElementById('pricing-note')
    if (note && trialOn && data.trial_note) note.textContent = data.trial_note

    // Keep every trial mention on the page consistent with what the back office actually says.
    if (days !== 30) {
      var spots = document.querySelectorAll('[data-trial-days]')
      for (var j = 0; j < spots.length; j++) {
        spots[j].textContent = spots[j].getAttribute('data-trial-days').replace('{d}', String(days))
      }
    }
  }

  try {
    var url = String(cfg.apiBase).replace(/\/+$/, '') + '/api/v1/billing/public-pricing'
    fetch(url, { credentials: 'omit' })
      .then(function (r) { return r.ok ? r.json() : null })
      .then(function (d) { if (d) render(d) })
      .catch(function () { /* offline, blocked, CORS, down — the shipped card stands */ })
  } catch (e) { /* no fetch in this browser — the shipped card stands */ }
})()
