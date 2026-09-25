"""Browser side: walk a vendor portal's catalog pages and read every product row (ONE copy).

Home: backend/app/modules/supply/catalog_scrape.py (index §34 / §36). Used by the platform's portal read
(supply/portal.py — on the live_login page or the scheduled cold session) AND by the standalone kit
(tools/vendor_price_compare, which loads this file by relative path / from its bundled copy). Stdlib +
the Playwright page object handed in — no app imports, so it runs on the owner's laptop too.

READ-ONLY by construction: it only follows plain <a href> links, and never follows a link whose URL
looks like a cart / checkout / order / logout action (see SAFE_SKIP). It never submits any form other
than the login form (and only when `login()` is called — the platform logs in through live_login and
calls `crawl()` only). Placing an order is a SEPARATE, human-confirmed flow (supply/portal.py).

Everything vendor-specific comes from the vendor's config block (vendors.json / po_vendor.portal_config).
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import parse_qsl, urldefrag, urlencode, urljoin, urlparse, urlunparse

try:                                    # inside the backend package
    from . import pricing_core as core
except ImportError:                     # loaded by path (the kit): its loader registers `pricing_core`
    import pricing_core as core  # type: ignore

# Links never followed — anything that could change the cart, an order, or the session.
SAFE_SKIP = re.compile(
    r"log_?off|log_?out|sign_?out|checkout|shopping_cart|cart\b|basket|add_?to|addcart|add_product|"
    r"buy_?now|action=|remove|delete|cancel|place_?order|submit_?order|ordadd|password|account_edit|"
    r"address_book|\.pdf$|\.jpe?g$|\.png$|\.gif$|mailto:|javascript:|tel:",
    re.I)

LOGGED_IN_HINT = re.compile(r"log\s*off|log\s*out|sign\s*out|my\s*account|welcome\b", re.I)

# Runs inside each page/frame. Finds every element holding a dollar price, climbs to the product
# "container" (table row / list item / card) and reads name, item number, prices, stock text, link.
EXTRACT_JS = r"""
(cfg) => {
  const PRICE = /\$\s?\d{1,3}(?:,\d{3})*(?:\.\d{1,4})?|\$\s?\d+(?:\.\d{1,4})?/;
  const GENERIC = /^(more info|details|view|buy now|add to cart|add|order|select|click here|read more|\.\.\.|more)$/i;
  // "Item #: 123", "Model: BX-12", "SKU 5521" — the value must contain a digit ("In Stock Add" is not an item number)
  const SKU_RE = /\b(?:item|sku|model|part|catalog|cat\.)\s*(?:#|no\.?|num(?:ber)?|code|id)?\s*[:#]?\s*((?=[A-Z\-\.\/]*\d)[A-Z0-9][A-Z0-9\-\.\/]{2,24})/i;
  const ACTION = /add|cart|order|buy|basket|checkout|remove|delete/i;
  const out = [];
  const seen = new Set();
  const txt = (el) => (el ? (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const struck = (el) => !!el.closest('s,del,strike,.normalprice,.productBasePrice.strike,[style*="line-through"]');
  const q = (sel) => { try { return sel ? Array.from(document.querySelectorAll(sel)) : []; } catch (e) { return []; } };

  let containers = q(cfg.product_selector);
  if (!containers.length) {
    const priceEls = [];
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      if (PRICE.test(n.nodeValue) && n.parentElement && !n.parentElement.closest('script,style,select,option,header,footer,nav'))
        priceEls.push(n.parentElement);
    }
    for (const el of priceEls) {
      let c = el, pick = null;
      for (let i = 0; i < 8 && c && c !== document.body; i++) {
        const tag = c.tagName;
        const cls = (c.className && typeof c.className === 'string') ? c.className : '';
        const links = Array.from(c.querySelectorAll('a[href]')).filter(a => txt(a).length >= 4 && !GENERIC.test(txt(a)) && !PRICE.test(txt(a)));
        if (tag === 'TR' || tag === 'LI' || /product|item|listing|result|card|centerBoxContents/i.test(cls) || tag === 'ARTICLE') {
          if (links.length >= 1 || txt(c).length > 12) { pick = c; break; }
        }
        c = c.parentElement;
      }
      if (!pick) {
        const main = document.querySelector('h1');
        if (main) pick = document.body;   // detail page: one product on the page
      }
      if (pick && !seen.has(pick)) { seen.add(pick); containers.push(pick); }
    }
  }

  for (const c of containers) {
    const isPage = c === document.body;
    const all = txt(c);
    if (!all || all.length > (isPage ? 200000 : 1500)) continue;
    let nameEl = isPage ? (document.querySelector(cfg.name_selector || 'h1') || document.querySelector('h1'))
                        : (cfg.name_selector && c.querySelector(cfg.name_selector));
    let link = null;
    if (!nameEl) {
      const links = Array.from(c.querySelectorAll('a[href]')).filter(a => txt(a).length >= 4 && !GENERIC.test(txt(a)) && !PRICE.test(txt(a)) && !ACTION.test(a.href));
      links.sort((a, b) => txt(b).length - txt(a).length);
      link = links[0] || null;
      nameEl = link || c.querySelector('h1,h2,h3,h4,.name,.title,[class*=name],[class*=title]');
    }
    if (!nameEl && c.tagName === 'TR') {
      const cells = Array.from(c.cells || []).map(txt).filter(t => t.length >= 4 && !PRICE.test(t) && !/^[\d\s.,]+$/.test(t));
      cells.sort((a, b) => b.length - a.length);
      if (cells[0]) nameEl = { innerText: cells[0] };
    }
    const name = txt(nameEl).slice(0, 300);
    if (!name || name.length < 3) continue;
    const priceNodes = isPage ? q(cfg.price_selector || '#productPrices, .productPrices, [itemprop=price], .price, [class*=price]').filter(e => PRICE.test(txt(e)))
                              : (cfg.price_selector ? Array.from(c.querySelectorAll(cfg.price_selector)) : []);
    let live = [], list = [];
    const scan = priceNodes.length ? priceNodes : [c];
    for (const pe of scan) {
      const w = document.createTreeWalker(pe, NodeFilter.SHOW_TEXT);
      let t;
      while ((t = w.nextNode())) {
        const m = t.nodeValue.match(new RegExp(PRICE.source, 'g'));
        if (!m || !t.parentElement || t.parentElement.closest('select,option,script,style')) continue;
        (struck(t.parentElement) ? list : live).push(...m);
      }
    }
    if (!live.length && !list.length) continue;
    let sku = '';
    if (cfg.sku_selector) { const s = c.querySelector(cfg.sku_selector) || (isPage && document.querySelector(cfg.sku_selector)); if (s) sku = txt(s); }
    if (!sku) { const m = (isPage ? txt(document.querySelector('#productDetailsList, .productDetailsList, .product-details, .sku') || c) : all).match(SKU_RE); if (m) sku = m[1]; }
    if (!sku && c.tagName === 'TR') {
      const cell = Array.from(c.cells || []).map(txt).find(t => /^[A-Z0-9][A-Z0-9\-\.\/]{2,24}$/i.test(t) && /\d/.test(t) && !PRICE.test(t));
      if (cell) sku = cell;
    }
    let stockText = '';
    if (cfg.stock_selector) { const s = c.querySelector(cfg.stock_selector) || (isPage && document.querySelector(cfg.stock_selector)); if (s) stockText = txt(s); }
    if (!stockText) {
      const m = (isPage ? txt(document.body) : all).match(/(units?\s*in\s*stock\s*:?\s*\d+|qty\s*(?:available|on\s*hand)\s*:?\s*\d+|\b\d{1,6}\s*units?\s*in\s*stock|out\s*of\s*stock|sold\s*out|back[\s-]?order|in\s*stock|unavailable|discontinued|available\s*now|ships?\s+in\s+[^.;]{1,20})/i);
      if (m) stockText = m[0];
    }
    const safeLink = Array.from(c.querySelectorAll('a[href]')).find(a => !ACTION.test(a.href + ' ' + txt(a)));
    const href = (link && link.href) || (isPage ? location.href : ((safeLink || {}).href || location.href));
    out.push({ name, sku, live, list, stock_text: stockText, url: href,
               text: (isPage ? txt(document.querySelector('#productDescription, .productDescription, [itemprop=description]')) || name : all).slice(0, 600),
               kind: isPage ? 'detail' : 'listing' });
  }
  const links = Array.from(document.querySelectorAll('a[href]')).map(a => a.href);
  const frames = Array.from(document.querySelectorAll('frame[src],iframe[src]')).map(f => f.src);
  return { rows: out, links, frames, title: document.title };
}
"""


def clean_url(url, strip_params=()):
    """Drop the #fragment and per-session query params (e.g. a shop's session id) so one page is one URL."""
    url = urldefrag(url)[0]
    if not strip_params:
        return url
    pu = urlparse(url)
    q = [kv for kv in parse_qsl(pu.query, keep_blank_values=True) if kv[0].lower() not in strip_params]
    return urlunparse(pu._replace(query=urlencode(q)))


def _to_product(vendor_key, raw):
    live = [core.parse_money(x) for x in raw.get("live") or []]
    lst = [core.parse_money(x) for x in raw.get("list") or []]
    live = [x for x in live if x is not None and x > 0]
    lst = [x for x in lst if x is not None and x > 0]
    price = min(live) if live else (min(lst) if lst else None)
    list_price = max(lst) if lst else (max(live) if len(live) > 1 else None)
    if list_price is not None and price is not None and list_price <= price:
        list_price = None
    text = f"{raw.get('name', '')} {raw.get('text', '')}"
    url = raw.get("url") or ""
    if SAFE_SKIP.search(url):          # never hand back a cart / order link as "the product link"
        url = raw.get("page_url") or ""
    status, qty = core.classify_availability(raw.get("stock_text") or "")
    return {
        "vendor": vendor_key,
        "name": raw.get("name", "").strip(),
        "sku": (raw.get("sku") or "").strip(),
        "mfr_part": "",
        "price": price,
        "list_price": list_price,
        "pack_qty": core.parse_pack(text),
        "availability": status,
        "stock_qty": qty,
        "stock_text": raw.get("stock_text") or "",
        "url": url,
        "description": (raw.get("text") or "")[:600],
        "source": raw.get("kind"),
    }


def _merge(existing, new):
    """Keep one row per product; the detail page usually knows more (stock, item number)."""
    for k, v in new.items():
        if v in (None, "", "unknown") or k == "vendor":
            continue
        if existing.get(k) in (None, "", "unknown") or (k in ("availability", "stock_qty", "stock_text", "sku") and new.get("source") == "detail"):
            existing[k] = v
    return existing


def _dedupe_keys(p):
    """Every identity a row can be found under: its item number and its link + name. A listing row
    (link, no item number) and the product's own page (item number) meet on the link + name."""
    keys = ["u:" + urldefrag(p["url"])[0] + "|" + p["name"].lower()]
    sku = core.normalize_sku(p["sku"])
    if sku:
        keys.insert(0, "s:" + sku)
    return keys


class VendorScraper:
    def __init__(self, page, vendor, username, password, out_dir, log=print, interactive=True):
        self.page = page
        self.v = vendor
        self.user = username
        self.pw = password
        self.out = Path(out_dir) if out_dir else None   # None = keep no page snapshots (the platform)
        self.log = log
        self.interactive = interactive
        self.products = {}   # identity key → row (several keys can point at the same row)
        self.pages_seen = 0
        self.snapshots = 0
        self.login_ok = False
        self.notes = []

    # ── login ─────────────────────────────────────────────────────────────────────────────────────
    def _pw_selectors(self):
        sel = self.v.get("login", {}).get("password_selector")
        return [s for s in (sel, "input[type=password]") if s]

    def _find_login_frame(self):
        """(frame, password selector) of the first visible password box — the vendor's configured
        selector first, the generic one as a fallback."""
        for sel in self._pw_selectors():
            for fr in self.page.frames:
                try:
                    el = fr.query_selector(sel)
                    if el and el.is_visible():
                        return fr, sel
                except Exception:
                    continue
        return None, None

    def login(self):
        lg = self.v.get("login", {})
        url = lg.get("url") or self.v["start_urls"][0]
        self.log(f"  opening login page {url}")
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._settle()
        fr, pw_sel = self._find_login_frame()
        if fr is None and lg.get("open_login_link_text"):
            try:
                self.page.get_by_text(re.compile(lg["open_login_link_text"], re.I)).first.click(timeout=5000)
                self._settle()
                fr, pw_sel = self._find_login_frame()
            except Exception:
                pass
        if fr is None:
            if LOGGED_IN_HINT.search(self._body_text()):
                self.log("  already logged in")
                self.login_ok = True
                return True
            return self._manual_login("no password box found on the login page")
        pw_el = fr.query_selector(pw_sel)
        user_el = fr.query_selector(lg["username_selector"]) if lg.get("username_selector") else None
        if user_el is not None and not user_el.is_visible():
            user_el = None
        if user_el is None:
            handle = fr.evaluate_handle(
                """(pw) => {
                     const scope = pw.form || document;
                     const ins = Array.from(scope.querySelectorAll('input')).filter(i =>
                         ['text','email','tel',''].includes((i.getAttribute('type')||'').toLowerCase()) && i.offsetParent !== null);
                     const before = ins.filter(i => i.compareDocumentPosition(pw) & Node.DOCUMENT_POSITION_FOLLOWING);
                     return before.length ? before[before.length-1] : (ins[0] || null);
                   }""", pw_el)
            user_el = handle.as_element()
        if user_el is None:
            return self._manual_login("could not find the user-id box")
        user_el.fill(self.user)
        pw_el.fill(self.pw)
        submitted = False
        if lg.get("submit_selector"):
            try:
                fr.click(lg["submit_selector"], timeout=5000)
                submitted = True
            except Exception:
                pass
        if not submitted:
            btn = fr.evaluate_handle(
                """(pw) => { const f = pw.form; if (!f) return null;
                     return f.querySelector('button[type=submit],input[type=submit],input[type=image],button:not([type])'); }""",
                pw_el).as_element()
            if btn:
                btn.click()
            else:
                pw_el.press("Enter")
        self._settle(4)
        still, _ = self._find_login_frame()
        if still is None or LOGGED_IN_HINT.search(self._body_text()):
            self.log("  logged in")
            self.login_ok = True
            return True
        return self._manual_login("the login page is still showing after submitting (wrong password, captcha, or a second step?)")

    def _manual_login(self, why):
        self.notes.append(f"automatic login: {why}")
        if not self.interactive:
            self.log(f"  ✗ login failed: {why}")
            return False
        self.log(f"  ! {why}")
        input("    → Please finish logging in yourself in the browser window, then press Enter here… ")
        self.login_ok = True
        self.notes.append("login finished by hand")
        return True

    # ── crawl ─────────────────────────────────────────────────────────────────────────────────────
    def _settle(self, secs=1.5):
        try:
            self.page.wait_for_load_state("networkidle", timeout=int(secs * 4000))
        except Exception:
            pass
        time.sleep(float(self.v.get("delay_seconds", 0.6)))

    def _body_text(self):
        parts = []
        for fr in self.page.frames:
            try:
                parts.append(fr.evaluate("() => document.body ? document.body.innerText : ''"))
            except Exception:
                pass
        return " ".join(parts)

    def _allowed(self, url, hosts):
        if not url or SAFE_SKIP.search(url):
            return False
        pu = urlparse(url)
        if pu.scheme not in ("http", "https") or pu.hostname not in hosts:
            return False
        inc = self.v.get("follow_patterns") or []
        exc = self.v.get("skip_patterns") or []
        if any(re.search(p, url, re.I) for p in exc):
            return False
        return not inc or any(re.search(p, url, re.I) for p in inc)

    def _snapshot(self, url):
        limit = int(self.v.get("snapshot_pages", 40))
        if self.out is None or self.snapshots >= limit:
            return
        d = self.out / "snapshots" / self.v["key"]
        d.mkdir(parents=True, exist_ok=True)
        self.snapshots += 1
        for i, fr in enumerate(self.page.frames):
            try:
                html = fr.content()
            except Exception:
                continue
            (d / f"page{self.snapshots:03d}_frame{i}.html").write_text(f"<!-- {fr.url} -->\n" + html, encoding="utf-8")
        if self.snapshots <= 5:
            try:
                self.page.screenshot(path=str(d / f"page{self.snapshots:03d}.png"), full_page=True)
            except Exception:
                pass

    def crawl(self, max_pages=None, should_stop=None):
        """Walk the catalog from start_urls. `should_stop` (optional callable) is checked between pages so
        an operator's Cancel on the live session stops the walk (results so far are kept)."""
        max_pages = int(max_pages or self.v.get("max_pages", 400))
        hosts = {urlparse(u).hostname for u in self.v["start_urls"]}
        hosts |= {urlparse(self.v.get("login", {}).get("url", "")).hostname} - {None}
        hosts |= set(self.v.get("extra_hosts") or [])
        strip = {p.lower() for p in self.v.get("strip_params") or []}
        queue = [clean_url(u, strip) for u in self.v["start_urls"]]
        seen = set()
        cfg = self.v.get("selectors") or {}
        while queue and self.pages_seen < max_pages:
            if should_stop is not None and should_stop():
                self.notes.append("stopped on request — results so far kept")
                break
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                self.notes.append(f"could not open {url}: {e.__class__.__name__}")
                continue
            self._settle()
            self.pages_seen += 1
            self._snapshot(url)
            found = 0
            for fr in self.page.frames:
                try:
                    res = fr.evaluate(EXTRACT_JS, cfg)
                except Exception:
                    continue
                for raw in res["rows"]:
                    raw["url"] = clean_url(raw.get("url") or url, strip)
                    raw["page_url"] = url
                    p = _to_product(self.v["key"], raw)
                    if p["price"] is None:
                        continue
                    keys = _dedupe_keys(p)
                    hit = next((self.products[k] for k in keys if k in self.products), None)
                    if hit is not None:
                        p = _merge(hit, p)
                    else:
                        found += 1
                    for k in _dedupe_keys(p) + keys:
                        self.products[k] = p
                for link in res["links"] + res["frames"]:
                    link = clean_url(urljoin(fr.url, link), strip)
                    if link not in seen and self._allowed(link, hosts):
                        queue.append(link)
            self.log(f"  [{self.pages_seen}/{max_pages}] {found:3d} new products  ({self.count()} total)  {url[:90]}")
        if queue and self.pages_seen >= max_pages:
            self.notes.append(f"stopped at max_pages={max_pages} with {len(queue)} links not visited — raise max_pages in vendors.json for a full catalog")
        return self.rows()

    def rows(self):
        out, ids = [], set()
        for p in self.products.values():
            if id(p) not in ids:
                ids.add(id(p))
                out.append(p)
        return out

    def count(self):
        return len({id(p) for p in self.products.values()})
