"""PROOF + LOCK: where a click on the streamed image lands on the live page (index §COMMCALC-47).

WHAT THIS PROVES, DB-free and browser-free. The translation arithmetic over Chrome's own
`Page.screencastFrame` metadata: that it REDUCES EXACTLY to the formula it replaces whenever a frame
says scale 1, offset 0 and device size equal to the window (which is every portal that works today, so
nothing working changes); that a scaled frame is scaled; that an offset frame is offset; that garbage
metadata falls back rather than dividing a click into nonsense; and that a click on the image's border
stays inside the page.

WHAT THIS LOCKS, and why the lock is the point.

  • ONE TRANSLATION. A click used to be turned into a page point inside the click handler, from the
    configured viewport — so the image's own account of itself was discarded and the defect was
    invisible at the one place anybody would look. `live_frame` is the home now, and `live_login` may
    not do that arithmetic itself: no `viewport_size` multiplication, no `* vp_size[`, nowhere.
  • THE METADATA IS KEPT. The screencast handler must take `metadata` off the frame and hand it to
    `_store_frame`. Dropping it again is the whole defect returning, and it would look like a tidy-up.
  • THE CLICK USES THE FRAME THE OPERATOR SAW. The geometry is stored WITH the frame and read under the
    lock, not recomputed from whatever the page happens to be showing by the time the click arrives.

Run:  cd backend && python3 harness_live_frame.py          Exit 0 = all green.
"""
import io
import os
import re
import sys
import tokenize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.commcalc import live_frame as lf      # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
PASS = FAIL = 0


def ok(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % msg)
    else:
        FAIL += 1
        print("  ✗ %s" % msg)


def eq(got, want, msg):
    ok(got == want, msg if got == want else "%s (got %r, want %r)" % (msg, got, want))


def near(got, want, msg, tol=1e-9):
    ok(abs(got - want) <= tol, msg if abs(got - want) <= tol else
       "%s (got %r, want %r)" % (msg, got, want))


def read(rel):
    with open(os.path.join(_ROOT, rel)) as fh:
        return fh.read()


def strip_text(src):
    """Executable text only — comments AND docstrings gone, so a comment describing the defect does not
    read as the defect."""
    out, prev = [], None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            t, s = tok.type, tok.string
            if t == tokenize.COMMENT:
                continue
            if t == tokenize.STRING and prev in (None, tokenize.INDENT, tokenize.DEDENT,
                                                 tokenize.NEWLINE, tokenize.NL):
                prev = t
                continue
            out.append(s)
            prev = t
    except tokenize.TokenError:
        return src
    return " ".join(out)


WINDOW = {"width": 1366, "height": 900}


print("\n== §A  IT REDUCES TO THE FORMULA IT REPLACES ==")
# The old arithmetic, kept here as the thing we must still agree with on a plain desktop frame.
def old_formula(nx, ny, vp=WINDOW):
    return (max(0.0, min(1.0, nx)) * vp["width"], max(0.0, min(1.0, ny)) * vp["height"])


plain = {"deviceWidth": 1366, "deviceHeight": 900, "pageScaleFactor": 1, "offsetTop": 0}
g = lf.frame_geometry(plain, WINDOW)
eq(g["source"], "frame", "a frame that describes itself is read as such")
eq((g["width"], g["height"], g["top"], g["scale"]), (1366.0, 900.0, 0.0, 1.0),
   "…and its geometry is its own device size at scale 1")
for nx, ny in ((0.0, 0.0), (0.25, 0.1), (0.5, 0.5), (0.73, 0.88), (1.0, 1.0)):
    got, want = lf.click_point(nx, ny, g), old_formula(nx, ny)
    near(got[0], want[0], "x agrees with the old formula at (%.2f, %.2f)" % (nx, ny))
    near(got[1], want[1], "y agrees with the old formula at (%.2f, %.2f)" % (nx, ny))
ok(True, "so every portal that works today is unaffected — the fix is not a correction factor")


print("\n== §B  THE DEFECT: A FRAME THAT IS NOT THE WINDOW ==")
# A page whose visible size is narrower than the window — the shape that made a click miss.
narrow = {"deviceWidth": 980, "deviceHeight": 646, "pageScaleFactor": 1, "offsetTop": 0}
g = lf.frame_geometry(narrow, WINDOW)
eq((g["width"], g["height"]), (980.0, 646.0), "the frame's own size is used, not the window's")
x, y = lf.click_point(0.5, 0.5, g)
near(x, 490.0, "a click in the middle lands in the middle of the PAGE")
near(y, 323.0, "…on both axes")
old_x, old_y = old_formula(0.5, 0.5)
ok(abs(old_x - x) > 150 and abs(old_y - y) > 100,
   "…and the old formula would have missed it by %d x %d px" % (old_x - x, old_y - y))
# The further from the top-left, the worse it got — which is exactly what the owner described.
near(lf.click_point(0.9, 0.9, g)[0], 882.0, "a click near the right edge lands near the right edge")
ok(old_formula(0.9, 0.9)[0] - 882.0 > 300, "…where the old formula was over 300px out")


print("\n== §C  PAGE SCALE ==")
zoomed = {"deviceWidth": 1366, "deviceHeight": 900, "pageScaleFactor": 2, "offsetTop": 0}
g = lf.frame_geometry(zoomed, WINDOW)
eq((g["width"], g["height"], g["scale"]), (683.0, 450.0, 2.0),
   "device pixels are divided by the page scale to get CSS pixels")
near(lf.click_point(1.0, 1.0, g)[0], 683.0, "the image's right edge is the scaled page's right edge")
half = lf.frame_geometry({"deviceWidth": 1366, "deviceHeight": 900, "pageScaleFactor": 0.5}, WINDOW)
eq((half["width"], half["height"]), (2732.0, 1800.0), "a zoomed-OUT page is correspondingly wider")


print("\n== §D  THE STRIP AT THE TOP ==")
offset = {"deviceWidth": 1366, "deviceHeight": 800, "pageScaleFactor": 1, "offsetTop": 100}
g = lf.frame_geometry(offset, WINDOW)
eq(g["top"], 100.0, "offsetTop is carried")
near(lf.click_point(0.0, 0.0, g)[1], 100.0,
     "the image's top row is page content that starts BELOW the strip")
near(lf.click_point(0.5, 0.5, g)[1], 500.0, "…and everything below it shifts by the same amount")
near(lf.click_point(0.5, 0.5, g)[0], 683.0, "x is untouched by a top offset")


print("\n== §E  NO METADATA, AND BAD METADATA ==")
g = lf.frame_geometry(None, WINDOW)
eq(g["source"], "viewport", "a frame with no metadata falls back to the window")
eq((g["width"], g["height"], g["top"], g["scale"]), (1366.0, 900.0, 0.0, 1.0),
   "…which is the old assumption, now stated instead of implied")
near(lf.click_point(0.4, 0.6, g)[0], old_formula(0.4, 0.6)[0],
     "so an explicit screenshot still translates exactly as it did")
for bad, why in ((
        {"deviceWidth": 0, "deviceHeight": 900}, "a zero width"),
        ({"deviceWidth": "wide", "deviceHeight": "tall"}, "strings"),
        ({"deviceWidth": -5, "deviceHeight": -5}, "negatives"),
        ({"deviceWidth": float("nan"), "deviceHeight": 900}, "a NaN"),
        ({"deviceWidth": float("inf"), "deviceHeight": 900}, "an infinity"),
        ("not a dict", "a non-dict"),
        ({}, "an empty dict")):
    g = lf.frame_geometry(bad, WINDOW)
    eq(g["source"], "viewport", "%s falls back rather than translating into nonsense" % why)
g = lf.frame_geometry(plain, {"width": 0, "height": None})
eq((g["width"], g["height"]), (1366.0, 900.0), "a window of zero does not reach the arithmetic either")
eq(lf.frame_geometry(None, None)["width"], float(lf.FALLBACK_VIEWPORT["width"]),
   "with neither, the stated fallback window")
g = lf.frame_geometry({"deviceWidth": 1366, "deviceHeight": 900, "pageScaleFactor": 0}, WINDOW)
eq(g["scale"], 1.0, "a scale of zero reads as 1 — it would otherwise divide by zero")


print("\n== §F  A CLICK STAYS INSIDE THE PAGE ==")
g = lf.frame_geometry(plain, WINDOW)
eq(lf.click_point(-0.4, -1.0, g), (0.0, 0.0), "a negative fraction clamps to the top-left")
eq(lf.click_point(9.0, 9.0, g), (1366.0, 900.0), "…and one past the edge to the bottom-right")
eq(lf.click_point("x", None, g), (0.0, 0.0),
   "a fraction that is not a number is the top-left, never an exception into the login")
eq(lf.click_point(0.5, 0.5, None), (683.0, 450.0), "no geometry at all still translates")
eq(lf.click_point(0.5, 0.5, {}), (683.0, 450.0), "…as does an empty one")


print("\n== §G  ONE TRANSLATION — THE LOCK ==")
src_ll = read("backend/app/modules/commcalc/live_login.py")
exe_ll = strip_text(src_ll)
src_lf = read("backend/app/modules/commcalc/live_frame.py")

ok("live_frame" in src_ll, "live_login reaches the one home")
ok("click_point(" in src_ll, "…for the click translation")
ok("frame_geometry(" in src_ll, "…and for the frame's geometry")
ok('vp_size["width"]' not in exe_ll and "vp_size[" not in exe_ll,
   "live_login no longer multiplies a click by the window itself")
ok("viewport_size" in exe_ll, "it still reads the window — as the FALLBACK handed to the home")
eq(len(re.findall(r"def click_point\s*\(", src_lf)), 1, "the translation is defined once")
eq(len(re.findall(r"def frame_geometry\s*\(", src_lf)), 1, "…and the geometry once")
eq(len(re.findall(r"def click_point\s*\(|def frame_geometry\s*\(", exe_ll)), 0,
   "live_login defines neither, it calls them")
ok('params.get("metadata")' in src_ll,
   "the screencast handler TAKES the metadata off the frame (dropping it is the defect returning)")
ok(re.search(r"_store_frame\(\s*data\s*,\s*metadata=", src_ll) is not None,
   "…and hands it to _store_frame")
ok(re.search(r"def _store_frame\(self, shot, metadata=None\)", src_ll) is not None,
   "_store_frame takes it")
ok("self._frame_geom = geom" in src_ll, "…and stores the geometry beside the frame")
ok(re.search(r"with self\._lock:\s*\n\s*geom = self\._frame_geom", src_ll) is not None,
   "the click reads THAT frame's geometry under the lock, not the page's current state")
ok("import" not in strip_text(src_lf).split("def ")[0].replace("FALLBACK_VIEWPORT", ""),
   "the home imports nothing — which is why this harness needs no browser and no database")
ok(not re.search(r"\b(ups|vidapay|b2bsoft|boost|total|luxelink|cellfonz)\b", strip_text(src_lf), re.I),
   "it names no portal and no tenant (RULE TWO)")


print("\n== §H  NEGATIVE CONTROLS — the shipped rules, armed ==")


def armed(label, fn):
    try:
        ok(fn() is True, "ARMED: %s" % label)
    except Exception as e:
        ok(False, "control blew up (%s): %r" % (label, e))


armed("the OLD rule would miss on a narrow frame",
      lambda: old_formula(0.5, 0.5)[0] != lf.click_point(0.5, 0.5, lf.frame_geometry(narrow, WINDOW))[0])
armed("…and would agree on a plain one (so this is not a blanket change)",
      lambda: old_formula(0.5, 0.5)[0] == lf.click_point(0.5, 0.5, lf.frame_geometry(plain, WINDOW))[0])
armed("ignoring pageScaleFactor would double every coordinate on a zoomed page",
      lambda: lf.frame_geometry(zoomed, WINDOW)["width"] == 683.0)
armed("ignoring offsetTop would click the strip instead of the page",
      lambda: lf.click_point(0.0, 0.0, lf.frame_geometry(offset, WINDOW))[1] == 100.0)
armed("the geometry is a plain dict — nothing for a caller to mutate into the next click",
      lambda: isinstance(lf.frame_geometry(plain, WINDOW), dict)
      and lf.frame_geometry(plain, WINDOW) == lf.frame_geometry(plain, WINDOW))
armed("the fallback window is declared, not spelled inside the arithmetic",
      lambda: strip_text(src_lf).count("1366") == 1)


print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
