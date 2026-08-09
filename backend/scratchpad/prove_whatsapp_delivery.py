"""Proof harness — WhatsApp document-delivery selection ladder (pure decision helpers).

Run:  cd backend && python3 scratchpad/prove_whatsapp_delivery.py
Covers `plan_delivery` (which attempts, in order) and `classify_send_result` (how each Meta response is
read) — the window/template/fallback matrix — plus `_is_header_error` / `_is_window_error` detection.
Pure: no network. Also asserts `_to_number` stays byte-compatible with the pre-existing notify format.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.modules.notify.channels import whatsapp_meta as W    # noqa: E402

passed = failed = 0


def ok(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {name}")


# ── plan_delivery: the ordered attempt list ─────────────────────────────────────
# doc-header configured + media uploaded → try the doc-header template first, then in-window free-form,
# then the link template.
ok("doc_header + media + PROVEN window → full ladder",
   W.plan_delivery(True, True, True) == ["template_doc", "freeform_doc", "template_link"])
# 2026-08-05: the free-form rung needs POSITIVE window evidence. With none, it is SKIPPED — Meta answers
# 200 + a wamid out of window and then drops the message, which used to END the ladder (owner incident).
ok("doc_header + media, window UNKNOWN → doc-header template then link (no free-form)",
   W.plan_delivery(True, True, False) == ["template_doc", "template_link"])
# no doc-header template but media uploaded → free-form document only when the window is PROVEN open.
ok("no doc_header + media + PROVEN window → freeform then link",
   W.plan_delivery(False, True, True) == ["freeform_doc", "template_link"])
ok("no doc_header + media, window UNKNOWN → APPROVED TEMPLATE ONLY (the cold-recipient case)",
   W.plan_delivery(False, True, False) == ["template_link"])
ok("default window arg is the SAFE one (unknown ⇒ closed)",
   W.plan_delivery(False, True) == W.plan_delivery(False, True, False) == ["template_link"])
# media upload failed → we can't attach anything → link template only (still delivers).
ok("doc_header but NO media → link only", W.plan_delivery(True, False, True) == ["template_link"])
ok("no doc_header + NO media → link only", W.plan_delivery(False, False, True) == ["template_link"])
# invariant: the link template is ALWAYS the terminal guaranteed fallback, in EVERY combination.
for dh in (True, False):
    for md in (True, False):
        for wo in (True, False):
            ok(f"link template is terminal ({dh},{md},{wo})",
               W.plan_delivery(dh, md, wo)[-1] == "template_link")
            ok(f"plan is never empty ({dh},{md},{wo})", len(W.plan_delivery(dh, md, wo)) >= 1)
# invariant: an attach step (real file) is attempted whenever media is available AND we may attach —
# i.e. a doc-header template is configured, or the window is PROVEN open.
ok("media + doc_header ⇒ an attach step is attempted (window irrelevant)",
   all(any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(True, True, wo))
       for wo in (True, False)))
ok("media + PROVEN window ⇒ an attach step is attempted",
   any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(False, True, True)))
ok("no media ⇒ NO attach step attempted",
   not any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(True, False, True)))
# THE REGRESSION GUARD: a cold recipient (no doc-header template, no window evidence) must never be
# offered a free-form rung — that is the send Meta accepts and silently drops.
ok("cold recipient ⇒ free-form is NEVER planned",
   "freeform_doc" not in W.plan_delivery(False, True, False)
   and "freeform_doc" not in W.plan_delivery(False, False, False))

# ── classify_send_result: reading a Meta response ───────────────────────────────
ok("200 → ok", W.classify_send_result(200, '{"messages":[{"id":"wamid.X"}]}') == "ok")
ok("201 → ok", W.classify_send_result(201, "") == "ok")
ok("299 → ok", W.classify_send_result(299, "") == "ok")
ok("#132018 no-title-component → header_error",
   W.classify_send_result(400, '{"error":{"code":132018,"message":"Template does not contain title component"}}') == "header_error")
ok("#131047 re-engagement → window_error",
   W.classify_send_result(400, '{"error":{"code":131047,"message":"Re-engagement message"}}') == "window_error")
ok("legacy #470 24h → window_error",
   W.classify_send_result(400, '{"error":{"code":470,"message":"more than 24 hours have passed"}}') == "window_error")
ok("#131026 undeliverable → window_error",
   W.classify_send_result(400, '{"error":{"code":131026,"message":"Message undeliverable"}}') == "window_error")
ok("generic 400 → error",
   W.classify_send_result(400, '{"error":{"code":100,"message":"Invalid parameter"}}') == "error")
ok("#131030 not-in-allowed-list → error (not header/window)",
   W.classify_send_result(400, '{"error":{"code":131030,"message":"Recipient phone number not in allowed list"}}') == "error")
ok("500 → error", W.classify_send_result(500, "boom") == "error")

# ── detectors ────────────────────────────────────────────────────────────────
ok("_is_header_error 132018", W._is_header_error("code 132018 title component") is True)
ok("_is_header_error 'does not contain'", W._is_header_error("Template does not contain a header") is True)
ok("_is_header_error negative", W._is_header_error("some other error") is False)
ok("_is_window_error re-engagement", W._is_window_error("Re-engagement message (#131047)") is True)
ok("_is_window_error negative", W._is_window_error("invalid parameter") is False)

# ── END-TO-END MATRIX: walk the ladder against scripted responses, prove the DELIVERED step ───────────
def walk(doc_header, media_ok, responder, window_open=True):
    """Simulate send_document's loop purely: return the step that delivered ('ok'), or None if all failed."""
    for step in W.plan_delivery(doc_header, media_ok, window_open):
        status, text = responder(step)
        if W.classify_send_result(status, text) == "ok":
            return step
    return None


# Scenario A: doc-header template configured + APPROVED with a real header → the file attaches out-of-window.
ok("A: doc-header template delivers the file",
   walk(True, True, lambda s: (200, "") if s == "template_doc" else (400, "unreached")) == "template_doc")

# Scenario B: DOC_HEADER=false, recipient INSIDE the 24h window → free-form document delivers the file.
ok("B: in-window free-form document delivers the file",
   walk(False, True, lambda s: (200, "") if s == "freeform_doc" else (400, "unreached")) == "freeform_doc")

# Scenario C: DOC_HEADER=false, recipient OUTSIDE the window → free-form window_error → link template delivers.
def resp_C(s):
    return (400, '{"error":{"code":131047}}') if s == "freeform_doc" else (200, "")
ok("C: outside-window falls back to the link template", walk(False, True, resp_C) == "template_link")

# Scenario D: DOC_HEADER=true but the template has NO real header (#132018), outside window → link fallback.
def resp_D(s):
    if s == "template_doc":
        return (400, '{"error":{"code":132018,"message":"title component"}}')
    if s == "freeform_doc":
        return (400, '{"error":{"code":131047}}')
    return (200, "")
ok("D: header_error then window_error → link template", walk(True, True, resp_D) == "template_link")

# Scenario E: DOC_HEADER=true, real header, but recipient IS in window anyway → doc-header still wins first.
ok("E: doc-header template preferred over free-form",
   walk(True, True, lambda s: (200, "")) == "template_doc")

# Scenario F: media upload failed entirely → straight to link template.
ok("F: no media → link template", walk(True, False, lambda s: (200, "")) == "template_link")

# Scenario G: everything fails (e.g. #131030 not-allowed) → None (send_document would raise → logged failed).
ok("G: all attempts fail → None (surfaces as a failed send_log row)",
   walk(True, True, lambda s: (400, '{"error":{"code":131030}}')) is None)

# ── transport byte-compat (regression guard vs the pre-phone-cc notify send format) ──
ok("_to_number bare-10-digit → 1+digits", W._to_number("2125550123") == "12125550123")
ok("_to_number +E.164 → digits only", W._to_number("+12125550123") == "12125550123")
ok("_to_number formatted → digits only", W._to_number("(212) 555-0123") == "12125550123")
ok("_to_number intl kept", W._to_number("+447911123456") == "447911123456")


# ── ITEM 2 (owner incident 2026-07-18): rung-1 (doc-header) body = TITLE ONLY, no URL; rung-3 keeps URL ──
# The router builds the body as "<title> — <no-login download url>". When the real file is ATTACHED
# (doc-header rung) the tokenized URL just baits Meta's link-safety crawler, which then silently drops the
# message. So the doc-header rung must carry the title only; the link-fallback rung keeps the URL (it IS
# the deliverable there).
DL = "https://metricspro-production.up.railway.app/api/v1/notify/dl/abc.def.ghijkl"
BODY = f"June Sales Report — {DL}"


def _body_text(msg):
    for c in msg["template"]["components"]:
        if c.get("type") == "body":
            return c["parameters"][0]["text"]
    return None


def _has_doc_header(msg):
    return any(c.get("type") == "header" and c["parameters"][0].get("type") == "document"
               for c in msg["template"]["components"])


rung1 = W._template_msg("+12125550123", "june.pdf", "MEDIA123", BODY, True)   # doc-header rung
rung3 = W._template_msg("+12125550123", "june.pdf", "", BODY, False)          # link-fallback rung

ok("rung-1 attaches the document header", _has_doc_header(rung1) is True)
ok("rung-1 body carries NO url (crawler bait removed)", "http" not in _body_text(rung1).lower())
ok("rung-1 body is the TITLE ONLY", _body_text(rung1) == "June Sales Report")
ok("rung-3 has NO doc header", _has_doc_header(rung3) is False)
ok("rung-3 body STILL carries the download url (it is the deliverable)", DL in _body_text(rung3))
ok("rung-3 body unchanged from caller text", _body_text(rung3) == BODY)

# _strip_link unit cases (the flattener used for the doc-header body)
ok("_strip_link removes url + dangling em-dash", W._strip_link(f"Title — {DL}") == "Title")
ok("_strip_link removes url + dangling hyphen", W._strip_link(f"Title - {DL}") == "Title")
ok("_strip_link no-url passthrough", W._strip_link("Just a title") == "Just a title")
ok("_strip_link empty → empty", W._strip_link("") == "")
ok("_strip_link keeps a title that itself has an em-dash",
   W._strip_link(f"Q2 — Store 42 — {DL}") == "Q2 — Store 42")

# doc-header rung tolerates an empty/None body without crashing (still just a header + empty-safe body)
ok("rung-1 empty body → _clean_var placeholder, no url",
   "http" not in _body_text(W._template_msg("+1", "f.pdf", "M", "", True)).lower())

# invariant: a doc-header rung NEVER leaks a URL regardless of body content
for b in (BODY, f"{DL}", f"see {DL} now", "no link here", ""):
    ok(f"doc-header rung strips any url ({b[:20]!r})",
       "http" not in _body_text(W._template_msg("+1", "f.pdf", "M", b, True)).lower())

print(f"\nprove_whatsapp_delivery: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
