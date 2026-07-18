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
ok("doc_header + media → full ladder",
   W.plan_delivery(True, True) == ["template_doc", "freeform_doc", "template_link"])
# no doc-header template but media uploaded → free-form document (in-window) then link fallback.
ok("no doc_header + media → freeform then link",
   W.plan_delivery(False, True) == ["freeform_doc", "template_link"])
# media upload failed → we can't attach anything → link template only (still delivers).
ok("doc_header but NO media → link only", W.plan_delivery(True, False) == ["template_link"])
ok("no doc_header + NO media → link only", W.plan_delivery(False, False) == ["template_link"])
# invariant: the link template is ALWAYS the terminal guaranteed fallback.
for dh in (True, False):
    for md in (True, False):
        ok(f"link template is terminal ({dh},{md})", W.plan_delivery(dh, md)[-1] == "template_link")
# invariant: an attach step (real file) is attempted whenever media is available.
ok("media present ⇒ an attach step is attempted",
   any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(True, True)) and
   any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(False, True)))
ok("no media ⇒ NO attach step attempted",
   not any(s in ("template_doc", "freeform_doc") for s in W.plan_delivery(True, False)))

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
def walk(doc_header, media_ok, responder):
    """Simulate send_document's loop purely: return the step that delivered ('ok'), or None if all failed."""
    for step in W.plan_delivery(doc_header, media_ok):
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
ok("_to_number bare-10-digit → 1+digits", W._to_number("5162330422") == "15162330422")
ok("_to_number +E.164 → digits only", W._to_number("+15162330422") == "15162330422")
ok("_to_number formatted → digits only", W._to_number("(516) 233-0422") == "15162330422")
ok("_to_number intl kept", W._to_number("+447911123456") == "447911123456")

print(f"\nprove_whatsapp_delivery: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
