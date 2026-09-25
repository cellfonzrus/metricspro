"""SUPPLY ORDERING — the browser side (index §36). Launches NOTHING itself outside the EXISTING browser path.

Two jobs, both on a vendor-portal page that the existing machinery opened:

1. CATALOG READ (read-only). `catalog_pull_on_page` walks the vendor's catalog with THE one reader
   (catalog_scrape.VendorScraper.crawl — the same code the owner's kit runs) and lands the rows through THE
   one lander (store.land_catalog). It runs:
     · LIVE — commcalc `_live_pull` dispatches a supply login's live_login session here (the commcalc
       /data-sources/{sid}/live-login/start endpoint; supply's "Read catalog now" calls exactly that);
     · SCHEDULED — `run_catalog_sweep` is the `_SOURCE_SCRAPERS[SUPPLY_PROCESSOR]` handler, so the EXISTING
       /commcalc/data-sources/sweep/run-due cron (require_browser_service → BROWSER_SERVICE_URL) runs it for
       every enabled supply login, restoring the saved session or signing in with the stored login.

2. ASSISTED ORDER. `order_session` builds a live_login `pull_fn` for ONE purchase order: it adds each line
   to the vendor's cart by the vendor's configured recipe (portal_config.ordering), opens the cart / checkout
   review, and captures the vendor's cart total + a screenshot. It NEVER submits the order on its own:
   the final submit is a human act — the human clicks in the streamed browser, or presses Confirm in
   MetricsPro after seeing the captured vendor total, and even then only when the vendor's recipe HAS a
   submit step. The same pull_fn, switched to mode "capture", reads the confirmation page (pattern from
   config) for the order number/total. Without a recipe the session still opens signed in (on the cart when
   a cart_url is configured) for the human to finish — and says so.

Every Playwright call happens on live_login's worker thread (through pull_fn) or inside run_catalog_sweep,
which sits behind assert_browser_allowed() — the SERVICE_ROLE=api choke point.
"""
from __future__ import annotations

import base64
import re
import threading

from app.modules.supply import ordering_logic as L
from app.modules.supply import store

try:
    from app.core.service_role import assert_browser_allowed
except ImportError:                                  # loaded outside the app (harness)
    def assert_browser_allowed():
        return None

# The data_source.processor of a supply vendor's portal login — a connector KIND, not a vendor.
SUPPLY_PROCESSOR = "supply_vendor"

# Order sessions in THIS worker's memory: (org_id, data_source id) → {"po_id", "mode", ...}. Same
# single-process caveat as live_login itself (the session lives where its browser lives).
_ORDER = {}
_ORDER_LOCK = threading.Lock()


def is_supply_source(processor):
    return str(processor or "").strip().lower() == SUPPLY_PROCESSOR


def _vp():
    from app.modules.commcalc import vidapay_sweep as vp
    return vp


def _scraper():
    from app.modules.supply import catalog_scrape
    return catalog_scrape


def _page_text(page):
    parts = []
    for fr in getattr(page, "frames", []) or []:
        try:
            parts.append(fr.evaluate("() => document.body ? document.body.innerText : ''") or "")
        except Exception:
            continue
    return "\n".join(parts)


def _shot(page):
    try:
        return base64.b64encode(page.screenshot(type="jpeg", quality=50, full_page=False)).decode("ascii")
    except Exception:
        return None


# ══ 1. CATALOG READ ══════════════════════════════════════════════════════════════════════════════════
def catalog_pull_on_page(client, org_id, src_row, page, should_stop=None):
    """Read this vendor's catalog on an ALREADY signed-in page and land it. Read-only (the reader refuses
    cart / checkout / order / logout links). Returns the delivery shape live_login + run_data_source read."""
    vendor = store.vendor_for_source(client, org_id, (src_row or {}).get("id"))
    if not vendor:
        return {"ok": False, "delivered": False,
                "error": "This login is not linked to a supply vendor (Supply → Vendors → Login)."}
    cfg = L.vendor_scrape_config(vendor)
    if not cfg.get("start_urls"):
        return {"ok": False, "delivered": False,
                "error": "No catalog links are set for this vendor (Supply → Vendors → Catalog links)."}
    cs = _scraper()
    s = cs.VendorScraper(page, cfg, None, None, None, log=lambda *_a, **_k: None, interactive=False)
    s.login_ok = True
    rows = s.crawl(cfg.get("max_pages"), should_stop=should_stop)
    res = store.land_catalog(client, org_id, vendor["id"], rows, source="portal")
    res["pages"] = s.pages_seen
    res["notes"] = list(s.notes)
    res["status"] = (f"Read {s.pages_seen} catalog page(s) at {vendor.get('name')}: "
                     f"{res['rows_ingested']} product(s) priced"
                     + (f", {res['rejected']} row(s) without a name/price skipped" if res.get("rejected") else "")
                     + ("." if res["rows_ingested"] else " — nothing landed (check the catalog links / "
                        "selectors in the vendor's portal config)."))
    return res


def run_catalog_sweep(client, org_id, src_row):
    """SCHEDULED catalog read (the `_SOURCE_SCRAPERS` handler for SUPPLY_PROCESSOR). Restores the saved
    session when there is one, else signs in with the stored login through the SHARED typed-login driver.
    Raises vidapay_sweep.VidaPayAuthError when the portal will not let us in, which run_data_source turns
    into the needs-login prompt on the login row (a human then uses the live login once)."""
    vp = _vp()
    src = dict(src_row or {})
    if not (src.get("portal_url") or "").strip():
        raise vp.VidaPayAuthError("This supply login has no portal address.")
    assert_browser_allowed()
    from playwright.sync_api import sync_playwright
    url = vp._norm_url(src.get("portal_url"), src.get("portal_url"))    # SSRF guard, use-time
    with sync_playwright() as p:
        browser = vp._launch(p)
        try:
            ctx = vp._new_context(browser, storage_state=src.get("session_state") or None,
                                  proxy=vp._proxy_arg(src.get("proxy_url")))
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            vp._wait_settle(page)
            state = vp._classify(page)
            if state == "login" and src.get("password") and (src.get("username") or src.get("account_id")):
                fr, pw = vp._password_frame(page)
                if pw:
                    vp.drive_typed_login(page, fr, pw, src.get("account_id"), src.get("username"), src.get("password"))
                    try:
                        page.wait_for_load_state("networkidle", timeout=25000)
                    except Exception:
                        pass
                    vp._wait_settle(page)
                    state = vp._classify(page)
            if state != "authenticated":
                raise vp.VidaPayAuthError(
                    "The vendor portal did not accept the saved login (it shows '%s'). Open the live login "
                    "once from Supply → Vendors to sign in by hand." % state)
            res = catalog_pull_on_page(client, org_id, src, page)
            try:
                res["storage_state"] = vp.capture_session_state(page, ctx)
            except Exception:
                pass
            return res
        finally:
            try:
                browser.close()
            except Exception:
                pass


# ══ 2. ASSISTED ORDER ════════════════════════════════════════════════════════════════════════════════
class RecipeStepError(Exception):
    pass


def _frames_for(page, frame_pat):
    frames = list(getattr(page, "frames", []) or [])
    if not frame_pat:
        return frames
    rx = re.compile(frame_pat, re.I)
    return [f for f in frames if rx.search(getattr(f, "url", "") or "") or rx.search(getattr(f, "name", "") or "")]


def run_step(page, st, hosts):
    """Execute ONE rendered recipe step on the live page. Navigation is refused off the vendor's hosts."""
    act = st["action"]
    if act == "goto":
        if not L.url_allowed(st.get("url"), hosts):
            raise RecipeStepError("refused to open %s — not this vendor's portal" % (st.get("url") or "")[:80])
        page.goto(st["url"], wait_until="domcontentloaded", timeout=45000)
    elif act == "wait":
        page.wait_for_timeout(st.get("ms") or 1500)
        return
    elif act == "press":
        page.keyboard.press(st["key"])
    else:
        el = None
        for fr in _frames_for(page, st.get("frame")):
            try:
                el = fr.query_selector(st["selector"])
            except Exception:
                el = None
            if el:
                break
        if el is None:
            if act == "wait_for":
                page.wait_for_timeout(st.get("ms") or 3000)
                for fr in _frames_for(page, st.get("frame")):
                    try:
                        if fr.query_selector(st["selector"]):
                            return
                    except Exception:
                        continue
            raise RecipeStepError("could not find %s on the page" % st["selector"][:80])
        if act == "click":
            el.click(timeout=10000)
        elif act == "fill":
            el.fill(str(st.get("value") or ""))
        elif act == "select":
            el.select_option(str(st.get("value") or ""))
        elif act == "wait_for":
            return
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    if st.get("ms"):
        page.wait_for_timeout(st["ms"])


def _run_steps(page, steps, ctx, hosts, should_stop=None):
    errors = []
    for st in steps or []:
        if should_stop is not None and should_stop():
            errors.append("stopped on request")
            break
        rs = L.render_step(st, ctx)
        try:
            run_step(page, rs, hosts)
        except Exception as e:
            if rs.get("optional"):
                continue
            errors.append("%s: %s" % (rs["action"], str(e)[:160]))
            break
    return errors


def build_cart(page, vendor, recipe, lines, hosts, should_stop=None):
    """Add every PO line to the vendor's cart by the recipe, open the review page, capture the vendor's
    cart total + a screenshot. Never submits the order."""
    ctx_base = {"cart_url": recipe.get("cart_url"), "portal_url": vendor.get("portal_url")}
    added, failed = 0, []
    status = L.recipe_status(recipe)
    if recipe.get("line_steps"):
        for ln in lines:
            if should_stop is not None and should_stop():
                failed.append({"line_no": ln.get("line_no"), "error": "stopped on request"})
                break
            ctx = dict(ctx_base, url=ln.get("url"), qty=ln.get("qty"), sku=ln.get("sku"), name=ln.get("name"),
                       line_no=ln.get("line_no"))
            errs = _run_steps(page, recipe["line_steps"], ctx, hosts, should_stop)
            if errs:
                failed.append({"line_no": ln.get("line_no"), "name": ln.get("name"), "error": "; ".join(errs)})
            else:
                added += 1
    review_errors = []
    if recipe.get("review_steps"):
        review_errors = _run_steps(page, recipe["review_steps"], ctx_base, hosts, should_stop)
    elif recipe.get("cart_url"):
        try:
            run_step(page, {"action": "goto", "url": recipe["cart_url"], "ms": 0}, hosts)
        except Exception as e:
            review_errors.append(str(e)[:160])
    text = _page_text(page)
    total = None
    if recipe.get("cart_total_selector"):
        for fr in getattr(page, "frames", []) or []:
            try:
                el = fr.query_selector(recipe["cart_total_selector"])
                if el:
                    total = L.core.parse_money(el.inner_text())
                    break
            except Exception:
                continue
    if total is None:
        total = L.parse_cart_total(text, recipe.get("cart_total_pattern"))
    n = len(lines)
    if status["level"] in ("full", "cart"):
        msg = (f"Cart built at {vendor.get('name')}: {added} of {n} line(s) added"
               + (f", vendor cart total ${total:,.2f}" if total is not None else ", the vendor's total was not found on the page")
               + ". Review it in the live window, then place the order there"
               + (" or press Confirm in MetricsPro." if recipe.get("submit_steps") else "."))
    else:
        msg = status["text"]
    return {"ok": True, "mode": "cart", "delivered": added > 0, "lines_added": added, "lines_total": n,
            "lines_failed": failed, "review_errors": review_errors, "vendor_cart_total": total,
            "recipe_level": status["level"], "page_url": getattr(page, "url", None), "shot": _shot(page),
            "status": msg}


def capture_confirmation(page, recipe):
    """Read the page the human (or the submit step) left the browser on: is it the vendor's confirmation?"""
    det = L.detect_confirmation(getattr(page, "url", ""), _page_text(page), recipe.get("confirmation"))
    msg = ("Confirmation page found" + (f": order {det['order_ref']}" if det["order_ref"] else
                                        " — the order number was not found; type it in MetricsPro")
           if det["confirmed"] else
           "This is not the vendor's confirmation page yet — finish checkout in the live window, then capture again.")
    return {"ok": True, "mode": "capture", "delivered": det["confirmed"], **det,
            "page_url": getattr(page, "url", None), "shot": _shot(page), "status": msg}


def order_session(client, org_id, vendor, po_id, lines, human_confirmed_submit=None):
    """(pull_fn, state) for live_login.start_session. `state["mode"]` decides what the NEXT pull does:
    'cart' (the automatic pull right after sign-in), 'capture' (read the confirmation page), 'submit'
    (run the vendor's configured submit steps — set ONLY by the human Confirm endpoint — then capture)."""
    cfg = vendor.get("portal_config") or {}
    hosts = L.vendor_hosts(vendor)
    recipe, _errs = L.parse_recipe(cfg.get("ordering"), hosts)
    state = {"mode": "cart", "po_id": po_id, "recipe": recipe}

    def _pull(page, should_stop=None):
        mode = state.get("mode") or "cart"
        if mode == "cart":
            return build_cart(page, vendor, recipe, lines, hosts, should_stop)
        if mode == "submit":
            state["mode"] = "capture"                   # one-shot: a submit never repeats by itself
            if not recipe.get("submit_steps"):
                return {"ok": False, "mode": "submit", "delivered": False,
                        "error": "This vendor has no submit step configured — press the vendor's own Place-order "
                                 "button in the live window, then Capture confirmation."}
            errs = _run_steps(page, recipe["submit_steps"], {"cart_url": recipe.get("cart_url"),
                                                             "portal_url": vendor.get("portal_url")}, hosts)
            res = capture_confirmation(page, recipe)
            res["mode"] = "submit"
            res["submit_errors"] = errs
            return res
        return capture_confirmation(page, recipe)

    return _pull, state


def register_order_session(org_id, sid, state):
    with _ORDER_LOCK:
        _ORDER[(org_id, sid)] = state


def order_state(org_id, sid):
    with _ORDER_LOCK:
        return _ORDER.get((org_id, sid))


def cart_persist(client, org_id, po_id):
    """live_login `persist_pull`: store the automatic cart build's evidence on the PO (org-scoped). Capture
    / submit results are written by the endpoint that asked for them (it knows WHO asked)."""
    def _p(res):
        if not isinstance(res, dict) or res.get("mode") != "cart":
            return
        from datetime import datetime, timezone
        ev = {k: res.get(k) for k in ("lines_added", "lines_total", "lines_failed", "review_errors",
                                      "vendor_cart_total", "recipe_level", "page_url", "shot", "status")}
        ev["captured_at"] = datetime.now(timezone.utc).isoformat()
        try:
            store.save_cart_evidence(client, org_id, po_id, ev)
        except Exception:
            pass
    return _p
