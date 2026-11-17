"""The JSON sessions' egress ladder (shazam/fetch.py): direct until the
tag endpoint rate-limits the pod's IP, then one sticky residential exit
per worker thread for SHAZAM_DIRECT_COOLDOWN seconds (measured 2026-11-04:
30 concurrent recognitions on the direct egress -> 10 then 30 x HTTP 429;
the same bursts through per-thread residential exits -> 60/60 matched)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from shazam import fetch as F  # noqa: E402


@pytest.fixture(autouse=True)
def egress(monkeypatch):
    monkeypatch.setattr(config, "shazam_proxy", lambda: None)
    monkeypatch.setattr(config, "shazam_api_fallback_proxy", lambda: "http://u:p@gw:20001")
    monkeypatch.setattr(config, "SHAZAM_DIRECT_COOLDOWN", 900)
    monkeypatch.setattr(config, "MAX_RETRIES", 3)
    monkeypatch.setattr(F.time, "sleep", lambda s: None)
    F._api_egress["direct_blocked_until"] = 0.0
    F._local.session = None
    yield
    F._api_egress["direct_blocked_until"] = 0.0
    F._local.session = None


def test_direct_until_rate_limited_then_residential(monkeypatch):
    assert F._api_proxy() == (None, True, 0.0)
    F.bench_direct_api()
    until = F._api_egress["direct_blocked_until"]
    assert F._api_proxy() == ("http://u:p@gw:20001", False, until)     # the session lapses with the bench
    assert until > time.time() + 800
    # a second 429 inside the cooldown does not extend it
    F.bench_direct_api()
    assert F._api_egress["direct_blocked_until"] == until
    # no fallback configured: the bench changes nothing
    monkeypatch.setattr(config, "shazam_api_fallback_proxy", lambda: None)
    assert F._api_proxy() == (None, True, 0.0)


def test_a_residential_session_lapses_with_the_bench_and_the_thread_goes_direct_again(monkeypatch):
    pytest.importorskip("curl_cffi")
    F.bench_direct_api()
    first = F._session()
    assert first.shazam_direct is False and first.proxies["https"] == "http://u:p@gw:20001"
    assert F._session() is first                                       # inside the cooldown: kept
    F._api_egress["direct_blocked_until"] = time.time() - 1            # the cooldown lapses
    first.shazam_until = F._api_egress["direct_blocked_until"]
    second = F._session()
    assert second is not first and second.shazam_direct is True and not second.proxies
    assert second.shazam_until == 0.0 and F._session() is second       # a direct session lives until it fails


def test_a_block_on_the_direct_session_benches_it_and_the_retry_leaves_through_an_exit(monkeypatch):
    sessions = []

    class Sess:
        def __init__(self, proxies=None):
            self.proxies = proxies or {}
            self.closed = False

        def close(self):
            self.closed = True

    def fake_session():
        sess = getattr(F._local, "session", None)
        if sess is None:
            proxy, direct, until = F._api_proxy()
            sess = Sess({"https": proxy} if proxy else None)
            sess.shazam_direct, sess.shazam_until = direct, until
            F._local.session = sess
            sessions.append(sess)
        return sess

    monkeypatch.setattr(F, "_session", fake_session)
    answers = iter([F.ShazamBlocked("HTTP 429 from recognize"), {"matches": []}])

    def once():
        F._session()
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert F._retrying(once) == {"matches": []}
    assert [s.shazam_direct for s in sessions] == [True, False]       # direct, then a residential exit
    assert sessions[0].closed and sessions[1].proxies == {"https": "http://u:p@gw:20001"}
    assert F._api_egress["direct_blocked_until"] > time.time()


def test_a_forced_proxy_is_never_direct(monkeypatch):
    monkeypatch.setattr(config, "shazam_proxy", lambda: "http://u:p@gw:30001")
    assert F._api_proxy() == ("http://u:p@gw:30001", False, 0.0)
