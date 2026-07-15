"""Threaded proof for the persistent LIVE-login session (commcalc/live_login.py).

Drives the REAL LiveLoginSession against a LOCAL http.server serving replica VidaPay/T-CETRA pages
(login → New-Device "Next" interstitial → trust-radio code screen → dashboard), adapted from the
scratchpad vidasim examples (replica_login2 / replica_newdevice / replica_trustcode). The New-Device
"Next" is the code DISPATCH: it navigates to /next, which the server counts — so the number of /next
hits == the number of times the portal is asked to send a code == the New-Device window.__sends.

ASSERTIONS
  1. HEADLINE — code sent EXACTLY ONCE: reaching the code screen through the LIVE session hits /next
     exactly 1 time (the two-call begin_login+complete_2fa path would hit it twice — the bug this fixes).
  2. SUBMIT_CODE with the right code + trust radio → phase 'authenticated' (and the durable session is
     persisted); /next count is STILL 1 (submitting did not re-dispatch).
  3. A WRONG code keeps phase 'awaiting_code' with the box still open (retryable) and does NOT re-dispatch
     (/next still 1); a follow-up correct code then authenticates.
  4. RESEND clicks the LIVE page's resend control (/resend +1) WITHOUT a re-login (/next stays 1).
  5. Screenshots are captured (state()['shot'] is a data-uri JPEG while awaiting the code).
  6. Org isolation: get_session(sid, other_org) is None.
  7. Cancel closes the session (phase 'cancelled').

Run:  cd backend && python3 scratchpad/live_login_proof.py
"""
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.modules.commcalc import live_login   # noqa: E402

# ── replica pages (adapted from vidasim/replica_login2 · replica_newdevice · replica_trustcode) ──
LOGIN_HTML = """<html><head><title>SSO - Vidapay</title></head><body>
<h2>SIGN IN</h2>
<input type="number" id="AccountId"><input type="text" id="Username"><input type="password" id="Password">
<button type="submit" id="btnClick" disabled onclick="event.preventDefault();location.href='/newdevice';">login</button>
<script>function chk(){document.getElementById('btnClick').disabled=!(AccountId.value&&Username.value&&Password.value);}
['AccountId','Username','Password'].forEach(function(id){var e=document.getElementById(id);e.addEventListener('keyup',chk);e.addEventListener('input',chk);});</script>
</body></html>"""

NEWDEVICE_HTML = """<html><head><title>SSO - Vidapay</title></head><body>
<h2>New Sign In</h2><p>We don't recognize this device. Verify your identity through 2-Factor Authentication.</p>
<a href="#" onclick="return false">Cancel</a>
<button type="submit" onclick="window.__sends=(window.__sends||0)+1;location.href='/next';">Next</button>
</body></html>"""

# served by /next (the code-dispatch route); {sc} = the server-side dispatch counter, mirrored to window.__sends
CODE_HTML = """<html><head><title>SSO - Vidapay</title></head><body>
<div id="app">
<h2>Verification</h2><p>Enter the code we sent</p>
<input type="text" id="VerificationCode" name="VerificationCode">
<div><label><input type="radio" name="trustchoice" id="t_yes" value="trust"> Trust this device for 90 days</label></div>
<div><label><input type="radio" name="trustchoice" id="t_no" value="public"> This is a public computer - don't trust</label></div>
<button id="verifyBtn" onclick="verify()">Verify</button>
<button id="resendBtn" onclick="doResend()">Resend code</button>
</div>
<script>
window.__sends = {sc};
function doResend(){{ try{{ fetch('/resend'); }}catch(e){{}} }}
function render(msg){{
  document.getElementById('app').innerHTML =
    '<h2>Verification</h2><p>'+msg+'</p>'+
    '<input type="text" id="VerificationCode" name="VerificationCode">'+
    '<div><label><input type="radio" name="trustchoice" id="t_yes" value="trust"> Trust this device for 90 days</label></div>'+
    '<div><label><input type="radio" name="trustchoice" id="t_no" value="public"> This is a public computer</label></div>'+
    '<button onclick="verify()">Verify</button> <button onclick="doResend()">Resend code</button>';
}}
function verify(){{
  var el=document.getElementById('VerificationCode'); var v=el?el.value:'';
  var t=document.getElementById('t_yes'); var trusted=t&&t.checked;
  if(v==='123456'&&trusted){{ document.title='Main Panel'; document.getElementById('app').innerHTML='<h2>Main Panel</h2><p>Welcome to your dashboard - you are signed in.</p><a href="#">Sign Out</a>'; }}
  else if(v==='123456'&&!trusted){{ render('Please choose a trust option to proceed'); }}
  else{{ render('Invalid code, try again'); }}
}}
</script></body></html>"""

_LOCK = threading.Lock()
COUNTS = {"sends": 0, "resends": 0}


def reset_counts():
    with _LOCK:
        COUNTS["sends"] = 0
        COUNTS["resends"] = 0


def counts():
    with _LOCK:
        return dict(COUNTS)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html"):
        b = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/login"):
            self._send(LOGIN_HTML)
        elif path == "/newdevice":
            self._send(NEWDEVICE_HTML)
        elif path == "/next":
            with _LOCK:
                COUNTS["sends"] += 1
                sc = COUNTS["sends"]
            self._send(CODE_HTML.format(sc=sc))
        elif path == "/resend":
            with _LOCK:
                COUNTS["resends"] += 1
            self._send("ok", "text/plain")
        else:
            self.send_response(404)
            self.end_headers()


# ── test harness ─────────────────────────────────────────────────────────────────────────────────
PASS = 0
FAIL = 0
LINES = []


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        LINES.append("  ok   " + name)
    else:
        FAIL += 1
        LINES.append("  FAIL " + name)


def wait_phase(sess, targets, timeout=100):
    """Block until the session phase is in `targets` (or timeout). Returns the final phase."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        ph = sess.snapshot_phase()
        if ph in targets:
            return ph
        time.sleep(0.4)
    return sess.snapshot_phase()


def wait_back_to_awaiting(sess, timeout=60):
    """After a SUBMIT, wait until it settles back to awaiting_code (via verifying) or terminal."""
    t0 = time.time()
    saw_verifying = False
    while time.time() - t0 < timeout:
        ph = sess.snapshot_phase()
        if ph == "verifying":
            saw_verifying = True
        if ph in ("authenticated", "cancelled", "error"):
            return ph
        if ph == "awaiting_code" and saw_verifying:
            return ph
        time.sleep(0.3)
    return sess.snapshot_phase()


def wait_resend(timeout=25):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if counts()["resends"] >= 1:
            return True
        time.sleep(0.3)
    return False


def make_row(base):
    return {"portal_url": base + "/login", "account_id": "12345", "username": "tester",
            "password": "secret", "proxy_url": None, "processor": "vidapay"}


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    base = "http://127.0.0.1:%d" % port
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    orgA = "org-aaaa"
    try:
        # ── Scenario 1: single-send + authenticate ───────────────────────────────────────────────
        reset_counts()
        captured = []
        persist = lambda upd: captured.append(upd)
        s1 = live_login.start_session("s1", orgA, make_row(base), persist)
        ph = wait_phase(s1, ("awaiting_code", "error"))
        check("S1 reaches the code screen (phase awaiting_code)", ph == "awaiting_code")
        check("S1 HEADLINE — code dispatched EXACTLY ONCE (/next hit == 1 == window.__sends)",
              counts()["sends"] == 1)
        st = s1.state()
        check("S1 screenshot captured (state.shot is a data-uri JPEG)",
              bool(st.get("shot")) and str(st.get("shot")).startswith("data:image/jpeg;base64,"))
        check("S1 org isolation — get_session under a DIFFERENT org is None",
              live_login.get_session("s1", "org-other") is None)
        check("S1 org match — get_session under the owning org returns the session",
              live_login.get_session("s1", orgA) is s1)
        s1.submit("123456")
        ph = wait_phase(s1, ("authenticated", "error"), timeout=60)
        check("S1 SUBMIT_CODE (right code + trust) → authenticated", ph == "authenticated")
        check("S1 submit did NOT re-dispatch a code (/next still == 1)", counts()["sends"] == 1)
        check("S1 durable session persisted (auth_status=authenticated + session_state present)",
              any(u.get("auth_status") == "authenticated" and u.get("session_state") for u in captured))
        check("S1 session_state stored on the session object", s1.session_state is not None)

        # ── Scenario 2: wrong code is retryable, no re-dispatch ───────────────────────────────────
        reset_counts()
        s2 = live_login.start_session("s2", orgA, make_row(base), lambda u: None)
        ph = wait_phase(s2, ("awaiting_code", "error"))
        check("S2 reaches awaiting_code", ph == "awaiting_code")
        check("S2 code dispatched once (/next == 1)", counts()["sends"] == 1)
        s2.submit("000000")   # wrong
        ph = wait_back_to_awaiting(s2)
        check("S2 WRONG code keeps phase awaiting_code (box still open, retryable)", ph == "awaiting_code")
        check("S2 wrong code did NOT re-dispatch (/next still == 1)", counts()["sends"] == 1)
        s2.submit("123456")   # right, on the SAME live page
        ph = wait_phase(s2, ("authenticated", "error"), timeout=60)
        check("S2 follow-up correct code → authenticated (retry on same live page)", ph == "authenticated")
        check("S2 still only one dispatch across the whole retry (/next == 1)", counts()["sends"] == 1)

        # ── Scenario 3: RESEND clicks the live page's control, no re-login ────────────────────────
        reset_counts()
        s3 = live_login.start_session("s3", orgA, make_row(base), lambda u: None)
        ph = wait_phase(s3, ("awaiting_code", "error"))
        check("S3 reaches awaiting_code", ph == "awaiting_code")
        check("S3 dispatched once before resend (/next == 1, /resend == 0)",
              counts()["sends"] == 1 and counts()["resends"] == 0)
        s3.resend()
        got = wait_resend()
        check("S3 RESEND clicked the live resend control (/resend == 1)", got and counts()["resends"] >= 1)
        check("S3 resend did NOT re-login / re-navigate (/next still == 1)", counts()["sends"] == 1)
        check("S3 still on the code screen after resend (phase awaiting_code)",
              s3.snapshot_phase() == "awaiting_code")
        s3.submit("123456")
        ph = wait_phase(s3, ("authenticated", "error"), timeout=60)
        check("S3 authenticates after resend", ph == "authenticated")

        # ── Scenario 4: cancel closes the session ────────────────────────────────────────────────
        reset_counts()
        s4 = live_login.start_session("s4", orgA, make_row(base), lambda u: None)
        wait_phase(s4, ("awaiting_code", "error"))
        s4.cancel()
        ph = wait_phase(s4, ("cancelled", "error"), timeout=30)
        check("S4 cancel closes the session (phase cancelled)", ph == "cancelled")

    finally:
        for sid in ("s1", "s2", "s3", "s4"):
            s = live_login.get_session(sid, orgA)
            if s:
                try:
                    s.cancel()
                except Exception:
                    pass
        time.sleep(1)
        server.shutdown()

    print("\n".join(LINES))
    print("\n=== live_login proof: %d/%d PASS ===" % (PASS, PASS + FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
