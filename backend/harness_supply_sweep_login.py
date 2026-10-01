"""Lock: a supply vendor's portal is signed in by ONE routine — the vendor's own login recipe (index §36).

Regression (live 2026-09-30): the scheduled catalog read (supply/portal.run_catalog_sweep) used a generic login driver
that ignored the vendor's portal config, so a vendor whose password box sits behind a "Log in" link never signed in —
"The vendor portal did not accept the saved login (it shows 'unknown')" — and its prices never landed, while the kit's
reader (catalog_scrape.VendorScraper.login) already followed that recipe. Two login paths, one drifted.

  A. The REAL portal.sign_in + the REAL VendorScraper.login against a fake vendor site:
     a home page whose login sits behind a link → signed in; a direct login page → signed in; a saved session → no
     login typed; a wrong password → refused with the reason; no saved login → refused.
  B. Source lock: run_catalog_sweep signs in through sign_in() with VendorScraper; supply/portal.py never calls the
     generic typed-login driver.

Run: `python3 harness_supply_sweep_login.py` from the backend dir. DB-free, stdlib only.
"""
import ast
import re
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS = FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


# portal.py imports store / ordering_logic (which need the database client); sign_in needs neither.
for mod in ("app.modules.supply.store",):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)
import app.modules.supply.ordering_logic  # noqa: E402,F401  (stdlib-safe)
from app.modules.supply import catalog_scrape as CS  # noqa: E402
from app.modules.supply import portal as P  # noqa: E402


# ── a fake vendor site, just enough of Playwright's page API for VendorScraper.login ────────────────────────────────
class Site:
    def __init__(self, user="buyer@x", pw="secret", login_behind_link=True):
        self.user, self.pw, self.behind = user, pw, login_behind_link
        self.state = "home"          # home | login | in
        self.typed = {}
        self.gotos = []


class El:
    def __init__(self, site, kind):
        self.site, self.kind = site, kind

    def is_visible(self):
        return True

    def fill(self, v):
        self.site.typed[self.kind] = v

    def press(self, _key):
        self.click()

    def click(self, **_k):
        if self.kind in ("submit", "password"):
            ok = self.site.typed.get("user") == self.site.user and self.site.typed.get("password") == self.site.pw
            self.site.state = "in" if ok else "login"


class Handle:
    def __init__(self, el):
        self.el = el

    def as_element(self):
        return self.el


class Frame:
    def __init__(self, site):
        self.site = site

    def query_selector(self, sel):
        if self.site.state == "login" and "password" in sel:
            return El(self.site, "password")
        return None

    def evaluate(self, _js):
        return {"home": "Our shop. Member Login. Products",
                "login": "Member sign-in. User id Password",
                "in": "Hello buyer. Log off. My orders"}[self.site.state]

    def evaluate_handle(self, js, _pw):
        if "querySelectorAll('input')" in js:
            return Handle(El(self.site, "user"))
        return Handle(El(self.site, "submit"))

    def click(self, _sel, **_k):
        El(self.site, "submit").click()


class Text:
    def __init__(self, site, rx):
        self.site, self.rx = site, rx

    @property
    def first(self):
        return self

    def click(self, **_k):
        if self.site.state == "home" and self.rx.search("Member Login"):
            self.site.state = "login"
        else:
            raise RuntimeError("no such link")


class Page:
    def __init__(self, site):
        self.site = site

    def goto(self, url, **_k):
        self.site.gotos.append(url)
        if self.site.state != "in":
            self.site.state = "home" if self.site.behind else "login"

    @property
    def frames(self):
        return [Frame(self.site)]

    def get_by_text(self, rx):
        return Text(self.site, rx)

    def wait_for_load_state(self, *_a, **_k):
        pass


def classify(page):
    """vidapay_sweep._classify's answer for the fake site: a password box → login, an auth marker → authenticated."""
    st = page.site.state
    return {"login": "login", "in": "authenticated"}.get(st, "unknown")


def cfg(behind_link=True):
    login = {"url": "https://vendor.example/shop"}
    if behind_link:
        login["open_login_link_text"] = "log ?in|sign ?in|member login"
    return {"key": "v1", "name": "V", "login": login, "start_urls": ["https://vendor.example/shop"], "delay_seconds": 0}


SRC = {"username": "buyer@x", "password": "secret"}

print("── A. the real sign_in + VendorScraper.login against a fake vendor site ─────────────────────────────")
site = Site(login_behind_link=True)
ok, how = P.sign_in(Page(site), cfg(True), SRC, classify, CS.VendorScraper)
check("login behind a 'Member Login' link → signed in (was: 'it shows unknown')", ok and site.state == "in", (ok, how))
check("…typed the saved user id and password", site.typed.get("user") == "buyer@x" and site.typed.get("password") == "secret",
      site.typed)

site = Site(login_behind_link=False)
ok, how = P.sign_in(Page(site), cfg(False), SRC, classify, CS.VendorScraper)
check("a direct login page → signed in", ok and site.state == "in", (ok, how))

site = Site()
site.state = "in"
ok, how = P.sign_in(Page(site), cfg(True), SRC, classify, CS.VendorScraper)
check("a restored session → in, nothing typed", ok and how == "saved session" and not site.typed and not site.gotos, (how, site.typed))

site = Site(pw="different")
ok, why = P.sign_in(Page(site), cfg(True), SRC, classify, CS.VendorScraper)
check("a wrong password → refused, not a fake success", not ok and site.state != "in", (ok, why))
check("…and the reason says the login page is still showing", "still showing" in why, why)

site = Site()
ok, why = P.sign_in(Page(site), cfg(True), {"username": "", "password": ""}, classify, CS.VendorScraper)
check("no saved login → refused before anything is typed", not ok and "no saved login" in why and not site.typed, why)

site = Site(login_behind_link=True)
ok, why = P.sign_in(Page(site), cfg(False), SRC, classify, CS.VendorScraper)
check("login behind a link but no link text in the recipe → refused with 'no password box'", not ok and "no password box" in why,
      why)

print("── B. source lock: one login routine for supply vendors ─────────────────────────────────────────────")
src = (HERE / "app/modules/supply/portal.py").read_text(encoding="utf-8")
tree = ast.parse(src)
fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_catalog_sweep")
body = ast.get_source_segment(src, fn)
check("run_catalog_sweep signs in through sign_in(... VendorScraper)",
      re.search(r"sign_in\(page,\s*cfg,\s*src,\s*vp\._classify,\s*_scraper\(\)\.VendorScraper\)", body) is not None)
check("run_catalog_sweep follows the vendor's recipe (vendor_scrape_config) with the login page SSRF-guarded",
      "L.vendor_scrape_config(vendor)" in body and re.search(r'login\["url"\]\s*=\s*vp\._norm_url\(', body) is not None)
check("supply/portal.py never calls the generic typed-login driver", "drive_typed_login" not in src)
si = ast.get_source_segment(src, next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "sign_in"))
check("sign_in delegates to the scraper's login (no second field-filling routine)",
      ".login()" in si and ".fill(" not in si and "query_selector" not in si)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
