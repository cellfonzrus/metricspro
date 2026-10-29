"""WHERE ON THE LIVE PAGE DOES A CLICK ON THE STREAMED IMAGE LAND — the one home (index §COMMCALC-47).

WHY THIS MODULE EXISTS. The live login shows the operator a video of a real browser and lets them click
it. The browser is on a server, so the click has to be translated: the UI sends where the operator
clicked as a fraction of the image (0..1 in each axis — DPR-proof, because the image is rendered at
whatever size the operator's screen gives it), and the worker turns that fraction into a point on the
live page.

That translation assumed **the image is the browser window at scale 1**:

    x = nx * viewport_width        y = ny * viewport_height

The frames do not come from the viewport. They come from Chrome's screencast, and every frame arrives
with metadata saying what it actually shows: `deviceWidth` / `deviceHeight` (the visible page size in
device-independent pixels), `pageScaleFactor` (pinch / page zoom) and `offsetTop` (the strip at the top
of the image that is not page content). All of it was discarded — the handler kept the JPEG and the ack
and dropped the metadata on the floor.

So on any page whose visible size is not the configured viewport, every click landed off target by that
ratio, and the further from the top-left the operator aimed, the further off it went. The owner hit it
on his billing portal on 2026-10-29: *"it shows the live scren but cannot click on the box to enter the
user id"*. The box was there, in the picture, and clicking it did nothing.

THE CLASS, not the instance. A frame is not a guess about a viewport — it is an image that KNOWS what it
shows. The fix is not a correction factor for one portal: the geometry travels WITH the frame, and the
click dereferences the geometry of the frame the operator was looking at. The arithmetic below reduces
EXACTLY to the old formula when a frame says scale 1, offset 0 and device size equal to the viewport,
which is the case at every portal that works today — so nothing that worked changes.

WHAT IT DOES NOT DO. It does not make the stream faster. Latency is a different fact with a different
home (§COMMCALC-27 / §COMMCALC-31, the operator's input leading the tick).

Proof: `backend/harness_live_frame.py` — DB-free and browser-free. It pins the reduction to the old
formula, the scaled and offset cases, and fails the build if a click is translated anywhere but here.
"""

# The context's configured window (`vidapay_sweep._new_context`). Used only when a frame carries no
# metadata of its own — an explicit screenshot, or a Chrome that sent none — and named here so the
# fallback is a stated assumption rather than a number buried in the click handler.
FALLBACK_VIEWPORT = {"width": 1366, "height": 900}


def _pos(v, default=0.0):
    """A finite, non-negative float, else `default`. Metadata arrives from the browser as JSON, and a
    missing key, a null, a string or a zero width would otherwise divide or multiply the operator's
    click into nonsense."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        return default
    return f


def frame_geometry(metadata=None, viewport=None):
    """What one streamed frame actually shows, in CSS pixels of the live page. PURE.

    `metadata` is Chrome's `Page.screencastFrame` metadata as it arrives; `viewport` is
    `page.viewport_size`. Returns
    `{"width", "height", "top", "scale", "source": "frame"|"viewport"}` — `width`/`height` in CSS px,
    `top` the non-page strip at the image's top, `scale` the page scale factor.

    `source` is kept because the two cases are not equally trustworthy and whoever reads a bad click
    should be able to tell which one they got: "frame" is the image's own account of itself, "viewport"
    is the old assumption, now explicit and used only when the frame said nothing.
    """
    vp = dict(viewport or FALLBACK_VIEWPORT)
    vw = _pos(vp.get("width")) or FALLBACK_VIEWPORT["width"]
    vh = _pos(vp.get("height")) or FALLBACK_VIEWPORT["height"]

    md = metadata if isinstance(metadata, dict) else {}
    scale = _pos(md.get("pageScaleFactor")) or 1.0
    dw = _pos(md.get("deviceWidth"))
    dh = _pos(md.get("deviceHeight"))
    if dw and dh:
        # deviceWidth/Height are DIP of the VISIBLE page; CSS px = DIP / pageScaleFactor. At scale 1
        # this is the device size unchanged, which is why a normal desktop portal is unaffected.
        return {"width": dw / scale, "height": dh / scale,
                "top": _pos(md.get("offsetTop")), "scale": scale, "source": "frame"}
    return {"width": vw, "height": vh, "top": 0.0, "scale": 1.0, "source": "viewport"}


def click_point(nx, ny, geometry=None):
    """The live page's CSS-pixel point for a click at normalized `(nx, ny)` of the streamed image. PURE.

    Clamped into the frame on both axes, so a click on the image's border never becomes a negative
    coordinate or one past the page — Playwright would accept both and click nothing, which is
    indistinguishable to the operator from the platform ignoring them.

    `top` is added rather than subtracted: `offsetTop` is the height of the strip at the top of the
    image that is not page content, so page content starts that far down the image and the point the
    operator aimed at is that much further down the page.
    """
    g = geometry if isinstance(geometry, dict) and geometry.get("width") else frame_geometry()
    w, h = _pos(g.get("width")) or 1.0, _pos(g.get("height")) or 1.0
    fx = min(1.0, max(0.0, _pos(nx)))
    fy = min(1.0, max(0.0, _pos(ny)))
    return fx * w, _pos(g.get("top")) + fy * h
