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
 *
 * That silence is right for a VISITOR and useless for whoever has to fix it: every failure looks
 * identical from the outside. So each one now says which it was on the browser console (F12 →
 * Console), prefixed 'MetricsPro pricing:'. Console only — never on the page, and never a reason
 * to show a visitor an error about a price list they cannot do anything about.
 */
(function () {
  'use strict'
  var cfg = window.MP_CONFIG || {}

  // Says which failure mode happened, on the console, for whoever is diagnosing. Wrapped because
  // console is absent in a few embedded browsers and this must never be what breaks the page.
  var say = function (msg) {
    try { if (window.console && console.warn) console.warn('MetricsPro pricing: ' + msg) } catch (e) {}
  }

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
  if (!slot) { say('no #pricing-cards element on this page — nothing to fill.'); return }
  if (!cfg.apiBase) {
    say('MP_CONFIG.apiBase is empty, so no price list is requested. If assets/config.js did not '
      + 'reach the server, this is what it looks like.')
    return
  }

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
    if (data.ready === false) {
      say('the platform answered, but its pricing tables are missing — migration '
        + '908_pricing_and_trial.sql has not been applied. Run it in the SQL editor.')
      return
    }
    if (data.show_pricing === false) {
      say('the platform answered, but "show pricing" is switched off in Admin -> Pricing & Free '
        + 'Trial. Turn it on there.')
      return
    }
    if (!pkgs.length) {
      say('the platform answered and no package is published. Packages are drafts until you press '
        + '"Publish" on each one in Admin -> Pricing & Free Trial — saving a price does not publish it.')
      return
    }
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
      .then(function (r) {
        if (r.ok) return r.json()
        say('the platform answered ' + r.status + ' for ' + url + '. That is an API problem, not a '
          + 'publishing one — the price list was never reached.')
        return null
      })
      .then(function (d) { if (d) render(d) })
      .catch(function (e) {
        // A browser deliberately hides WHY a cross-origin request failed, so this cannot name CORS
        // with certainty — but on a reachable API, CORS is overwhelmingly what it is.
        say('could not reach ' + url + ' at all (' + (e && e.message ? e.message : 'network error')
          + '). Most likely this site\'s origin, ' + window.location.origin + ', is not in '
          + 'CORS_ORIGINS on the API. Open the URL directly in a tab: if it returns data there but '
          + 'fails here, it is CORS. The exact reason is on the Network tab.')
      })
  } catch (e) {
    say('this browser has no fetch(), so no price list is requested. The shipped card stands.')
  }
})()
