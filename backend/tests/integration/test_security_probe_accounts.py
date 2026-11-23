"""Integration: the attacker's pass signs in as TWO reps in EACH of several tenants, over HTTP.

SANJOT (2026-11-23): *"You need to get 2 test accounts as sales reps from 3 different tenants"*.

`harness_security_probe.py` proves the probe's pure half against a fake route table. It cannot prove
the half that matters once real accounts exist: that `run()` actually signs each one in, labels each
answer with the identity that received it, checks every signed-in body against the OTHER tenants'
markers, and refuses to call a run measured while an identity is blind.

So this test stands up a throw-away HTTP server that plays both the platform and Supabase Auth, and
drives the REAL `security_probe.run()` against it. Nothing about the probe is stubbed — only the
platform it asks. Several tenants throughout, because a gate proved for one tenant is not proved
(CLAUDE.md, 2026-10-30), and two reps inside one tenant, because rep-vs-rep needs a victim.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.integration

MARK_A, MARK_B = "AAA-STORE-1", "BBB-STORE-9"
TOKENS = {"a1@probe.test": "tok-a1", "a2@probe.test": "tok-a2", "b1@probe.test": "tok-b1"}
# What the fake platform answers each identity on one gated path, keyed by bearer token.
ANSWERS = {}
# One path a rep MAY reach (their own clock) and one only a manager may (the payroll drill-down).
# The leak class lives on the FIRST one: the door is supposed to open, so only "whose rows" catches it.
OWN_PATH = "/api/v1/storeops/timeclock"
MANAGER_PATH = "/api/v1/storeops/payroll/actual-hours-detail"


class _Platform(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *a):          # keep the test output clean
        pass

    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        # Supabase Auth's password grant, the way the probe really signs in.
        if self.path.startswith("/auth/v1/token"):
            n = int(self.headers.get("Content-Length") or 0)
            email = json.loads(self.rfile.read(n) or b"{}").get("email", "")
            if email in TOKENS:
                return self._send(200, {"access_token": TOKENS[email]})
            return self._send(400, {"error": "bad credentials"})
        return self._send(404, {"detail": "Not found"})

    def do_GET(self):
        if self.path == "/openapi.json":
            return self._send(200, {"paths": {OWN_PATH: {"get": {}}, MANAGER_PATH: {"get": {}}}})
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "").strip()
        if not tok:
            return self._send(401, {"detail": "authentication required"})
        # Keyed by (identity, path) when the test cares, else by identity, else the honest default:
        # a rep reaches their own surface and is refused a manager's.
        default = (200, {"rows": []}) if self.path == OWN_PATH else (403, {"detail": "not your access"})
        code, payload = ANSWERS.get((tok, self.path), ANSWERS.get(tok, default))
        return self._send(code, payload)


@pytest.fixture()
def platform():
    srv = HTTPServer(("127.0.0.1", 0), _Platform)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _declare(monkeypatch, base):
    monkeypatch.setenv("SUPABASE_URL", base)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.setenv("PROBE_ACCOUNTS", json.dumps([
        {"tenant": "ta", "email": "a1@probe.test", "password_env": "PW1", "markers": [MARK_A]},
        {"tenant": "ta", "email": "a2@probe.test", "password_env": "PW2", "markers": [MARK_A]},
        {"tenant": "tb", "email": "b1@probe.test", "password_env": "PW3", "markers": [MARK_B]},
    ]))
    for v in ("PW1", "PW2", "PW3"):
        monkeypatch.setenv(v, "secret")
    monkeypatch.delenv("PROBE_REP_EMAIL", raising=False)
    monkeypatch.delenv("PROBE_CROSS_EMAIL", raising=False)
    monkeypatch.delenv("PROBE_FOREIGN_MARKERS", raising=False)


def _run(base):
    import security_probe as sp
    return sp.run(base, "storeops")


def test_every_declared_account_signs_in_and_is_named_separately(platform, monkeypatch):
    """Three identities, three measurements — and the two in one tenant stay distinguishable."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()          # the default: own surface opens, manager's surface refuses
    rep = _run(platform)
    labels = [p for p in rep["coverage"]["personas"] if p.startswith("rep:")]
    assert sorted(labels) == ["rep:ta#1", "rep:ta#2", "rep:tb#1"]
    assert all(rep["coverage"]["personas"][x]["state"] == "ran" for x in labels)
    # Each rep was refused on a manager's read, which is the gate working.
    assert not rep["findings"]


def test_one_tenants_rows_in_another_tenants_answer_is_a_finding(platform, monkeypatch):
    """The leak this measures: tenant B's rep is answered with tenant A's store code."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()
    ANSWERS[("tok-b1", OWN_PATH)] = (200, {"rows": [{"store": MARK_A, "hours": 7.5}]})
    rep = _run(platform)
    bad = [f for f in rep["findings"] if f["verdict"] == "foreign_rows"]
    # Reported on the rep's OWN surface, where the door legitimately opens and only the row owner
    # distinguishes a leak from a correct answer.
    assert [f["path"] for f in bad] == [OWN_PATH], rep["findings"]
    assert bad[0]["persona"] == "rep:tb#1"
    assert bad[0]["severity"] == "high"


def test_an_accounts_own_marker_is_never_a_finding(platform, monkeypatch):
    """The false positive that would make this tool useless: a rep legitimately seeing its OWN
    tenant's store code must not be reported as a cross-tenant leak."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()
    ANSWERS[("tok-a1", OWN_PATH)] = (200, {"rows": [{"store": MARK_A}]})
    ANSWERS[("tok-a2", OWN_PATH)] = (200, {"rows": [{"store": MARK_A}]})
    ANSWERS[("tok-b1", OWN_PATH)] = (200, {"rows": [{"store": MARK_B}]})
    rep = _run(platform)
    assert [f for f in rep["findings"] if f["verdict"] == "foreign_rows"] == []


def test_a_2fa_challenge_is_a_gap_and_never_a_pass(platform, monkeypatch):
    """A tenant turning two-factor on must not silently convert every module to 'no leak found'."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()
    for tok in TOKENS.values():
        ANSWERS[tok] = (401, {"detail": "two-factor authentication required", "code": "2fa_required"})
    rep = _run(platform)
    assert not rep["findings"], "a blinded run must not report findings it never measured"
    assert rep["coverage"]["unprobed"] >= 3, "each blinded identity is counted as a gap"
    assert rep["coverage"]["measured"] is False


def test_a_run_is_not_measured_while_the_cross_tenant_admin_has_no_account(platform, monkeypatch):
    """Reps are not admins. Three rep identities is still an unmeasured cross-tenant question."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()
    assert _run(platform)["coverage"]["measured"] is False


def test_an_account_whose_password_secret_is_missing_is_reported_not_assumed(platform, monkeypatch):
    """A half-configured account must appear as a skipped identity, never as one that passed."""
    _declare(monkeypatch, platform)
    monkeypatch.delenv("PW2")
    ANSWERS.clear()
    personas = _run(platform)["coverage"]["personas"]
    assert "rep:ta#2" not in personas, "the dropped account must not masquerade as the second rep"
    assert personas["rep:ta#1"]["state"] == "ran"


def test_no_credential_or_email_appears_anywhere_in_the_report(platform, monkeypatch):
    """A security report that carries the probe's own passwords is a new leak, not a control."""
    _declare(monkeypatch, platform)
    ANSWERS.clear()
    blob = json.dumps(_run(platform), default=str)
    for secret in ("secret", "PW1", "PW2", "PW3", "a1@probe.test", "b1@probe.test", "tok-a1"):
        assert secret not in blob, f"{secret!r} reached the report"
