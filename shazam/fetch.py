"""Shazam transport: plain curl_cffi with browser-impersonated TLS. No
browser, no cookies, no login; direct egress by default (validated
2026-09-19 from India: 12 parallel page loads and a 40-request Apple API
burst all answered 200; 30-day developer token). The one bot-protection
surface, Fastly's challenge on /track/* pages after heavy use from one IP,
is handled by moving page loads to a residential exit (see below).

What shazam.com is today and which of its surfaces this module uses
(everything the competitors on RapidAPI wrap — www.shazam.com/services/
search|count|charts|events|amapi — was removed with the 2025 Next.js
relaunch and now answers "Not supported"):

  1. Apple MusicKit developer token
     GET https://www.shazam.com/services/musickit/validate answers with the
     token in its `x-shz-validation` header (an ES256 JWT, issuer 4GWDBCF5A4,
     ~30 days, origin-locked to shazam.com/apple.com). With it plus
     `Origin: https://www.shazam.com` the Apple Music catalog answers
     anonymously at https://api.music.apple.com/v1/catalog/{storefront}/…:
     search, search/hints, songs (+ ?ids= batch, ?filter[isrc]=), artists
     (+ views), albums (+ ?filter[upc]=), playlists (Shazam's charts ARE
     Apple playlists now), music-videos, genres, charts, storefronts.
     Lyrics need a signed-in user token (400 "Insufficient Permissions").
     The token is harvested once per process, kept until a day before it
     expires and re-harvested on the first 401 (SHAZAM_MUSICKIT_TOKEN in
     config pins one by hand).

  2. Shazam's own JSON that is still open
     amp.shazam.com/discovery/v5/{lang}/{country}/web/-/track/{key}
         track by Shazam key (-> Apple ids, ISRC, genre, hub, related list)
     cdn.shazam.com/shazam/v3/{lang}/{country}/web/-/tracks/{list id}
         paged track lists: track-similarities-id-{key} (similar songs);
         the country/city/artist list ids answer 204 since the relaunch
     www.shazam.com/services/sd/s/a2st/{storefront}/{lang}/{apple ids}
         Apple song id -> Shazam key map (batch)
     amp.shazam.com/discovery/v5/{lang}/{country}/iphone/-/tag/{uuid}/{uuid}
         audio recognition: POST a SigX signature (sigx.py) -> matches +
         the discovery track object. The site's own /services/webrec/matchv2
         wants an RSA-wrapped signature from its sigx.js worker and is
         not used.

  3. Server-rendered pages (React Server Components flight payload,
     decoded with cricbuzz/rsc.py)
     /track/{key}            time-synced lyrics, AI audio analysis, popular
                             segments, the `shazam-songs` object
     /artist/{slug}/{id}     bio, hometown, born/formed, links, similar
                             artists, releases by type, event links
     /charts/top-200/world   the chart catalog: 71 countries with their
                             top-200 / viral / momentum playlist ids,
                             cities and genre playlists
     /events/{country}[/{city}], /event/{uuid}
                             Bandsintown-fed concerts with venues
     The storefront a page embeds follows the egress IP; the Apple ids in
     it are storefront-independent, so that never matters.

Bot protection: Fastly's "Client Challenge" (a JavaScript fingerprinting
interstitial, `/_fs-ch-…/` assets, a 200 with no flight data).
  2026-09-19  on /track/* pages only, for one IP after ~60 page loads in
              an hour; a fresh US residential exit loaded the same pages
              at once, so get_page moved a challenged thread's page loads
              to a config.shazam_page_fallback_proxy() exit for
              SHAZAM_DIRECT_COOLDOWN seconds.
  2026-10-06  on EVERY page, decided per request on the TLS/HTTP2
              fingerprint and only for origin fetches (edge/shield cache
              hits are served to anyone): curl_cffi's Chrome-124 handshake
              is challenged from its first request on any IP Fastly
              distrusts — the pod, every either proxy vendor exit tried
              (9 countries), this office IP after ~30 loads — whatever the
              User-Agent or client hints claim. Identities that declare
              automation (HeadlessChrome / curl UAs, headless Chrome) are
              NOT challenged but served decoy pages of other entities
              (another artist's profile, a song page for an event URL):
              never use them. A real headed Chrome is served normally, and
              when it navigates to a challenged URL the interstitial clears
              in ~5 s and leaves `_fs_ch_cp_*` / `_fs_cd_cp_*` cookies
              (1 h, bound to the exit IP, not to UA or TLS) that plain
              curl_cffi replays. get_page therefore mints such a clearance
              per egress with a throwaway headed Chrome (trustpilot's
              aws-waf-token pattern) and keeps the residential exit as the
              last rung, where it gets a clearance of its own the same
              way. The JSON hosts and the Apple API were never challenged.
  2026-10-22  no browser runs on GKE: the mint is one
              browser_client.mint("shazam", proxy=<the challenged exit>,
              variant=<the challenged path>) to the browser box
              (recipes/shazam.py), which opens that very page through that
              very proxy port (same exit IP as this pod's curl
              session) and returns the `_fs_c*_cp_` cookies. A challenge on
              DIRECT egress is never minted: a cookie minted on the box's
              own IP is not this pod's, so it goes straight to the exit
              rung. The per-egress clearance cache and single-flight stay
              here, as does the exit rung on a failed mint.
  2026-10-12  the GKE pod's direct egress is REFUSED outright: a Fastly
              "403 Forbidden ... Error 54113" (no challenge assets) for
              every page, to curl_cffi and to the pod's headed Chrome
              alike, so a browser mint on that IP can only collect no
              cookies. The 10-06 code counted that empty mint as success
              and re-minted on the same refused IP until the retries ran
              out (log: "clearance minted on direct egress (no cookies: the
              page came from cache)" x4 -> 502), never reaching the exit
              rung. Through a proxy US exit the same pod's Chrome
              clears the challenge in ~3 s and curl replays the
              `_fs_ch_cp_*` cookie on track/artist/events/charts pages
              (verified in-pod). So: a plain 401/403/429 goes straight to
              the exit rung, a mint that yields no `_fs_c*_cp_` cookie is
              a FAILED mint (exit rung again), and every worker thread
              shares ONE process-wide fallback exit (rotated only when it
              fails) so a burst mints one Chrome, not one per thread.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  ShazamUpstreamError  transport failure / 5xx            — retryable
  ShazamBlocked        403 / 429 / challenge or non-JSON  — retryable
  ShazamBadRequest     upstream 400 (bad storefront, …)   — never retried
  ShazamNotFound       404 / 204 / empty data             — never retried
"""
import base64
import json
import fastjson
import os
import sys
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait as wait_futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import browser_client
import config
import egress
from browser_client import BoxRefused, BoxUnavailable
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError
from scraper_utils import debug, fanout, sessions

SITE = "https://www.shazam.com"
BOX_SITE = "shazam"                  # recipes/shazam.py
AMP = "https://amp.shazam.com"
CDN = "https://cdn.shazam.com"
APPLE = "https://api.music.apple.com"
IMPERSONATE = "chrome"

JSON_TIMEOUT = 30
PAGE_TIMEOUT = 60          # chart pages are 3.6 MB, events pages took up to 19 s under load
RECOGNIZE_TIMEOUT = 30
FANOUT_WORKERS = 6

DEFAULT_LANG = "en-US"
DEFAULT_COUNTRY = "US"
TOKEN_REFRESH_MARGIN = 24 * 3600   # re-harvest a day before exp

PAGE_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": egress.site_accept_language("shazam"),
}
JSON_HEADERS = {
    "accept": "*/*",
    "accept-language": egress.site_accept_language("shazam"),
    "origin": SITE,
    "referer": SITE + "/",
}
# The Shazam iOS app's identity, which the discovery tag endpoint expects.
TAG_HEADERS = {
    "X-Shazam-Platform": "IPHONE",
    "X-Shazam-AppVersion": "14.1.0",
    "Accept": "*/*",
    "Accept-Language": "en",      # locale: app identity (the iOS app's fixed header, not a browser's)
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "Shazam/3685 CFNetwork/1197 Darwin/20.0.0",
    "Content-Type": "application/json",
}
NOT_FOUND_MARKER = 'id="__next_error__"'
CHALLENGE_MARKERS = ("/_fs-ch-", "Client Challenge")


class ShazamUpstreamError(UpstreamError):
    """Transport failure or 5xx — retryable."""


class ShazamBlocked(ShazamUpstreamError, Blocked):
    """403 / 429 / challenge page / non-JSON where JSON was expected."""


class ShazamBadRequest(BadRequest):
    """Upstream 400 — never retried."""


class ShazamNotFound(NotFound):
    """404 / 204 / the site's error shell — never retried."""


class _TokenRejected(ShazamUpstreamError):
    """401 from the Apple API: the developer token expired — re-harvest once."""


class ShazamChallenged(ShazamBlocked):
    """Fastly Client Challenge page instead of the page — retry on a fresh exit."""


# ---- sessions ------------------------------------------------------------------

# One curl session per cheroot worker thread (a curl handle must not be
# shared across threads); all calls are stateless GET/POST with keep-alive.
#
# Egress of the JSON sessions: config.shazam_proxy() (None = the pod's own
# IP) until the JSON hosts rate-limit that IP (HTTP 429 / 403 on a call, see
# config.SHAZAM_API_FALLBACK_PROXY_COUNTRY for the 2026-11-04 measurement);
# then the direct egress is benched for SHAZAM_DIRECT_COOLDOWN seconds and
# every thread's next session takes its own sticky residential exit, so a
# recognition burst is spread over one per-IP budget per thread. A blocked
# residential exit dies with its session (_drop_session) and the retry
# rides a fresh one; a residential session also lapses with the bench
# (session.shazam_until), so the thread's JSON calls go back to the direct
# egress once the cooldown is over instead of riding the exit for the life
# of the worker thread.
_local = threading.local()
_api_egress = {"direct_blocked_until": 0.0}


def _api_proxy():
    """(proxy, is_direct, until) for a new JSON session: `until` is when a
    residential fallback session lapses (the bench's end), 0 for a direct
    or a forced-proxy session (those live until they fail)."""
    forced = config.shazam_proxy()
    if forced:
        return forced, False, 0.0
    with _egress_lock:
        until = _api_egress["direct_blocked_until"]
    fallback = config.shazam_api_fallback_proxy() if until > time.time() else None
    if fallback is None:
        return None, True, 0.0
    return fallback, False, until


def bench_direct_api():
    """The direct egress just got rate-limited: route new JSON sessions
    through residential exits for SHAZAM_DIRECT_COOLDOWN seconds."""
    with _egress_lock:
        if _api_egress["direct_blocked_until"] > time.time():
            return
        _api_egress["direct_blocked_until"] = time.time() + config.SHAZAM_DIRECT_COOLDOWN
    print(f"shazam: JSON calls rate-limited on the direct egress; residential exits for "
          f"{config.SHAZAM_DIRECT_COOLDOWN}s", flush=True)


def _session():
    sess = getattr(_local, "session", None)
    if sess is not None and getattr(sess, "shazam_until", 0.0) and sess.shazam_until < time.time():
        _drop_session()             # the bench lapsed: back to the direct egress
        sess = None
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy, direct, until = _api_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        sess.shazam_direct = direct
        sess.shazam_until = until
        _local.session = sess
    return sess


def download_session():
    """The thread's session for fetching a caller's audio URL: always the
    plain egress (no residential bandwidth for media; apify and Instagram's
    CDN never refused the pod's own IP)."""
    sess = getattr(_local, "download", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = _local.download = curl_requests.Session(impersonate=IMPERSONATE)
    return sess


def _drop_session():
    """Close the thread's session so a poisoned keep-alive connection dies."""
    sessions.drop(_local)


# Page loads: same per-thread session, but after a challenge the thread
# reads pages through a residential exit until the cooldown lapses.
_pages = threading.local()


def _new_page_state(proxy, until=0, via_fallback=None):
    from curl_cffi import requests as curl_requests
    sess = curl_requests.Session(impersonate=IMPERSONATE)
    if proxy:
        sess.proxies = {"http": proxy, "https": proxy}
    if via_fallback is None:
        via_fallback = bool(until)
    state = {"session": sess, "until": until, "via_proxy": bool(proxy), "proxy": proxy,
             "via_fallback": via_fallback, "egress": _egress_key(proxy), "clearance_at": 0}
    clearance = _current_clearance(state["egress"])
    if clearance:
        _apply_clearance(state, clearance)
    return state


# 2026-10-12: which egress NEW page sessions use, process-wide. Once direct
# page loads are refused/challenged past a clearance, every thread reads
# pages through the same sticky fallback exit until direct_blocked_until
# (one exit -> one clearance mint shared by all threads); that exit is
# replaced only when a session on it fails (_escalate_page_session).
_egress_lock = threading.Lock()
_page_egress = {"direct_blocked_until": 0, "fallback": None}


def _page_proxy():
    """(proxy, until) for a new page session: the shared fallback exit while
    direct egress is benched, else config.shazam_proxy() (None = direct)."""
    with _egress_lock:
        until = _page_egress["direct_blocked_until"]
        if until > time.time():
            if _page_egress["fallback"] is None:
                _page_egress["fallback"] = config.shazam_page_fallback_proxy()
            if _page_egress["fallback"]:
                return _page_egress["fallback"], until
    return config.shazam_proxy(), 0


def _page_session():
    state = getattr(_pages, "state", None)
    now = time.time()
    if state and state["until"] and state["until"] < now:
        _drop_page_session()
        state = None
    if state is None:
        proxy, until = _page_proxy()
        state = _new_page_state(proxy, until=until)
        _pages.state = state
    return state


def _drop_page_session():
    state = getattr(_pages, "state", None)
    _pages.state = None
    if state is not None:
        try:
            state["session"].close()
        except Exception:
            pass


def _escalate_page_session(state):
    """Move this thread's page loads off the egress that just failed: direct
    egress is benched for SHAZAM_DIRECT_COOLDOWN and the shared fallback
    exit is used; a failing fallback exit is replaced by a fresh one (unless
    another thread already rotated it). Returns False when no fallback is
    configured."""
    with _egress_lock:
        if _page_egress["direct_blocked_until"] <= time.time() or not state["via_fallback"]:
            _page_egress["direct_blocked_until"] = time.time() + config.SHAZAM_DIRECT_COOLDOWN
        if state["via_fallback"] and _page_egress["fallback"] == state["proxy"]:
            _page_egress["fallback"] = None
        if _page_egress["fallback"] is None:
            _page_egress["fallback"] = config.shazam_page_fallback_proxy()
        proxy, until = _page_egress["fallback"], _page_egress["direct_blocked_until"]
    _drop_page_session()
    if not proxy:
        return False
    _pages.state = _new_page_state(proxy, until=until, via_fallback=True)
    return True


def is_challenge(html):
    head = (html or "")[:4000]
    return all(marker in head for marker in CHALLENGE_MARKERS)


# ---- Fastly clearance ------------------------------------------------------------
# 2026-10-06 (see the module docstring): one clearance per egress ("direct"
# or the sticky proxy URL) — the shazam.com cookies a real headed Chrome
# holds once the challenge cleared — shared by every worker thread's page
# session on that egress, minted single-flight and re-minted when a session
# that already carries the newest one is challenged again (the cookies
# live ~1 h).
_clearance_lock = threading.Lock()
_clearances = {}          # egress -> {"cookies": {name: value}, "minted_at": monotonic}
_mint_locks = {}
STATE_COOKIE_PREFIX = "_fs_ch_st_"   # the 10 s token of a challenge in flight: never replayed
# 2026-10-12: what a cleared challenge leaves; a mint without one has nothing to replay
CLEARANCE_COOKIE_PREFIXES = ("_fs_ch_cp_", "_fs_cd_cp_")
MAX_MINTS_PER_PAGE = 2               # box mints one get_page call may trigger


def _egress_key(proxy):
    return proxy or "direct"


def _mint_lock(egress):
    with _clearance_lock:
        return _mint_locks.setdefault(egress, threading.Lock())


def _current_clearance(egress):
    with _clearance_lock:
        return _clearances.get(egress)


def _apply_clearance(state, clearance):
    for name, value in clearance["cookies"].items():
        state["session"].cookies.set(name, value, domain=".shazam.com")
    state["clearance_at"] = clearance["minted_at"]


def _mint_clearance(egress, proxy, url):
    """One /mint on the shazam browser box: a throwaway HEADED Chrome
    navigation to the challenged URL through the same exit (recipes/
    shazam.py) -> {"cookies", "minted_at"}. Headed matters: headless Chrome
    is classed as automation and served decoy pages instead of a challenge.
    The box waits for the clearance COOKIE (2026-10-12: a browser served
    something other than the challenge never gets one and must not count as
    a success) and fails fast on an unchallenged page. Raises BoxRefused /
    BoxUnavailable / RuntimeError (no clearance cookie came back)."""
    where = f"exit port {egress.rsplit(':', 1)[-1]}"
    path = url[len(SITE):] if url.startswith(SITE) else ""
    print(f"shazam: minting a Fastly clearance on {where} via the browser box...")
    ident = browser_client.mint(BOX_SITE, proxy=proxy, variant=path)
    cookies = {c["name"]: c["value"] for c in ident.get("cookies") or []
               if str(c.get("domain", "")).endswith("shazam.com")
               and not c["name"].startswith(STATE_COOKIE_PREFIX)}
    if not any(name.startswith(CLEARANCE_COOKIE_PREFIXES) for name in cookies):
        raise RuntimeError("the box answered without a clearance cookie")
    print(f"shazam: Fastly clearance minted on {where} ({', '.join(sorted(cookies))})")
    return {"cookies": cookies, "minted_at": time.monotonic()}


def _refresh_clearance(state, url):
    """Give this page session a clearance newer than the one it carries,
    minting one (single-flight per egress) unless another thread already
    did since. False when the mint failed or this session is on direct
    egress (the box cannot mint for this pod's own IP). A box that is down
    or busy raises ShazamUpstreamError: no exit would do better."""
    egress = state["egress"]
    if not state["proxy"]:
        print("shazam: challenged on direct egress; no box mint for the pod's own IP, exit rung")
        return False
    with _mint_lock(egress):
        clearance = _current_clearance(egress)
        if clearance is None or clearance["minted_at"] <= state["clearance_at"]:
            try:
                clearance = _mint_clearance(egress, state["proxy"], url)
            except BoxUnavailable as e:
                raise ShazamUpstreamError(f"browser box unavailable for the clearance mint: {e}")
            except (BoxRefused, Exception) as e:  # noqa: BLE001 - the exit rung still applies
                print(f"shazam: Fastly clearance mint failed on {egress}: {type(e).__name__}: {e}")
                return False
            with _clearance_lock:
                _clearances[egress] = clearance
    _apply_clearance(state, clearance)
    return True


def dump_debug(name, text):
    """Write a raw body to $SHAZAM_DEBUG_DIR/<name>.txt for post-mortems."""
    debug.dump("SHAZAM_DEBUG_DIR", name, text)


def _classify(resp, label):
    if resp.status_code == 400:
        raise ShazamBadRequest(f"{label}: {resp.text[:200]}")
    if resp.status_code in (401, 403, 429, 503):
        dump_debug(f"blocked_{resp.status_code}", resp.text)
        raise ShazamBlocked(f"HTTP {resp.status_code} from {label}")
    if resp.status_code in (204, 404):
        raise ShazamNotFound(f"{label} not found")
    if resp.status_code >= 500:
        raise ShazamUpstreamError(f"HTTP {resp.status_code} from {label}")
    if resp.status_code != 200:
        raise ShazamUpstreamError(f"unexpected HTTP {resp.status_code} from {label}")


def _retrying(fn, *, refresh_token_on_401=False):
    """Run fn() with the service's retry policy: transport errors, 5xx and
    blocks retry with backoff on a fresh session; 400/404 raise at once."""
    last = None
    token_refreshed = False
    attempt = 0
    while attempt < config.MAX_RETRIES:
        attempt += 1
        try:
            return fn()
        except _TokenRejected as e:
            last = e
            if not refresh_token_on_401 or token_refreshed:
                break
            token_refreshed = True
            developer_token(force=True)
            attempt -= 1
            continue
        except (ShazamBadRequest, ShazamNotFound):
            raise
        except (ShazamBlocked, ShazamUpstreamError) as e:
            last = e
            if isinstance(e, ShazamBlocked) and getattr(getattr(_local, "session", None), "shazam_direct", False):
                bench_direct_api()          # the retry's fresh session takes a residential exit
            _drop_session()
        if attempt < config.MAX_RETRIES:
            time.sleep(config.RETRY_BACKOFF * attempt)
    raise last


def _request(method, url, *, headers, timeout, label, **kwargs):
    try:
        resp = _session().request(method, url, headers=headers, timeout=timeout, **kwargs)
    except Exception as e:
        raise ShazamUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
    return resp


def _json_body(resp, label):
    body = resp.text or ""
    if not body.lstrip().startswith(("{", "[")):
        dump_debug("nonjson", body)
        raise ShazamBlocked(f"{label} returned a non-JSON body")
    try:
        return resp.json()
    except Exception:
        dump_debug("badjson", body)
        raise ShazamBlocked(f"{label} returned unparsable JSON")


# ---- pages ------------------------------------------------------------------------

def get_page(path):
    """One server-rendered shazam.com page -> HTML. The site's error shell
    (a 200/404 with id="__next_error__") and a 204 both mean NotFound; a
    Fastly challenge gets this egress a browser-minted clearance and
    retries, and only an egress that stays challenged with a fresh one
    moves this thread's page loads to a residential exit."""
    url = SITE + path
    label = f"page {path}"
    last = None
    mints = 0
    # 2026-10-12: +3 so "direct refused -> exit challenged -> mint -> page"
    # fits with a retry to spare
    for attempt in range(1, config.MAX_RETRIES + 3):
        state = _page_session()
        try:
            resp = state["session"].get(url, headers=PAGE_HEADERS, timeout=PAGE_TIMEOUT)
        except Exception as e:
            last = ShazamUpstreamError(f"{label}: request failed: {type(e).__name__}: {e}")
            _drop_page_session()
            time.sleep(config.RETRY_BACKOFF * attempt)
            continue
        html = resp.text or ""
        if resp.status_code in (204, 404) or NOT_FOUND_MARKER in html[:2000]:
            raise ShazamNotFound(f"{label} not found")
        if resp.status_code == 400:
            raise ShazamBadRequest(f"{label}: {html[:200]}")
        challenged = is_challenge(html)
        if challenged or resp.status_code in (401, 403, 429):
            dump_debug("page_challenge", html)
            last = ShazamChallenged(f"{label}: bot challenge from this exit" if challenged
                                    else f"{label}: HTTP {resp.status_code} refused from this exit")
            # 2026-10-06: residential exits are challenged from their first
            # request too, so a clearance for THIS egress comes first; the
            # exit rung stays for an egress that is challenged even with a
            # fresh clearance (it is minted one of its own on the next pass).
            # 2026-10-12: only a real challenge is worth a browser mint. A
            # plain 401/403/429 (Fastly "Error 54113" on the pod's IP) is an
            # IP refusal a browser on the same IP gets too: exit rung at once.
            if challenged and mints < MAX_MINTS_PER_PAGE:
                mints += 1
                if _refresh_clearance(state, url):
                    continue
            if not _escalate_page_session(state):
                break
            continue
        if resp.status_code >= 500:
            last = ShazamUpstreamError(f"HTTP {resp.status_code} from {label}")
            _drop_page_session()
            time.sleep(config.RETRY_BACKOFF * attempt)
            continue
        if "self.__next_f" not in html:
            dump_debug("page_noflight", html)
            last = ShazamBlocked(f"{label} rendered without page data")
            _drop_page_session()
            continue
        return html
    raise last


# ---- Shazam JSON --------------------------------------------------------------------

def lang_tag(language):
    return language or DEFAULT_LANG


def region(country):
    return (country or DEFAULT_COUNTRY).upper()


def discovery_track(key, *, country=None, language=None):
    """amp discovery v5: track by Shazam key -> the track object."""
    url = f"{AMP}/discovery/v5/{lang_tag(language)}/{region(country)}/web/-/track/{key}"
    label = f"track {key}"

    def once():
        resp = _request("GET", url, headers=JSON_HEADERS, timeout=JSON_TIMEOUT, label=label,
                        params={"shazamapiversion": "v3", "video": "v0"})
        _classify(resp, label)
        body = _json_body(resp, label)
        if not isinstance(body, dict) or not body.get("key"):
            raise ShazamNotFound(f"{label} not found")
        return body

    return _retrying(once)


def cdn_list(list_id, *, start_from=0, page_size=20, country=None, language=None):
    """cdn v3 track list (similar songs, genre charts) -> {tracks, next?}.
    A 204 is "no such list / past the end" -> NotFound."""
    url = f"{CDN}/shazam/v3/{lang_tag(language)}/{region(country)}/web/-/tracks/{list_id}"
    label = f"list {list_id}"

    def once():
        resp = _request("GET", url, headers=JSON_HEADERS, timeout=JSON_TIMEOUT, label=label,
                        params={"startFrom": start_from, "pageSize": page_size, "connected": "", "channel": ""})
        _classify(resp, label)
        return _json_body(resp, label)

    return _retrying(once)


def apple_to_shazam_keys(apple_ids, *, storefront="us", language="en"):
    """/services/sd/s/a2st: Apple song ids -> {apple id: Shazam key} (ids
    Shazam does not know are simply absent)."""
    ids = [str(i) for i in apple_ids if i]
    if not ids:
        return {}
    url = f"{SITE}/services/sd/s/a2st/{storefront.lower()}/{language.split('-')[0].lower()}/{','.join(ids)}"
    label = "a2st"

    def once():
        resp = _request("GET", url, headers=JSON_HEADERS, timeout=JSON_TIMEOUT, label=label)
        if resp.status_code == 404:
            return {}
        _classify(resp, label)
        body = _json_body(resp, label)
        return {str(k): str(v) for k, v in body.items() if v} if isinstance(body, dict) else {}

    return _retrying(once)


def recognize_signature(signature_uri, sample_ms, *, country=None, language=None, timezone=None):
    """POST one SigX signature to the discovery tag endpoint -> the raw
    response ({matches: [...], track: {...}} on a hit, {matches: []} otherwise)."""
    lang = (language or DEFAULT_LANG).split("-")[0].lower()
    url = (f"{AMP}/discovery/v5/{lang}/{region(country)}/iphone/-/tag/"
           f"{str(uuid.uuid4()).upper()}/{str(uuid.uuid4()).upper()}")
    params = {"sync": "true", "webv3": "true", "sampling": "true", "connected": "",
              "shazamapiversion": "v3", "sharehub": "true", "video": "v0"}
    body = {
        "timezone": timezone or config.SHAZAM_TIMEZONE,
        "signature": {"uri": signature_uri, "samplems": int(sample_ms)},
        "timestamp": int(time.time() * 1000),
        "context": {},
        "geolocation": {},
    }
    label = "recognize"

    def once():
        resp = _request("POST", url, headers=TAG_HEADERS, timeout=RECOGNIZE_TIMEOUT, label=label,
                        params=params, data=json.dumps(body))
        _classify(resp, label)
        return _json_body(resp, label)

    return _retrying(once)


# ---- Apple Music catalog through Shazam's MusicKit token ---------------------------

def jwt_payload(token):
    try:
        part = token.split(".")[1]
        return fastjson.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:
        return {}


_token_lock = threading.Lock()
_token = {"value": None, "exp": 0}


def _harvest_token():
    label = "musickit/validate"
    resp = _request("GET", SITE + "/services/musickit/validate", headers=JSON_HEADERS,
                    timeout=JSON_TIMEOUT, label=label)
    _classify(resp, label)
    token = resp.headers.get("x-shz-validation") or ""
    if token.count(".") != 2:
        dump_debug("validate", resp.text)
        raise ShazamUpstreamError("musickit/validate returned no developer token")
    return token


def developer_token(force=False):
    """The MusicKit developer token shazam.com hands its own visitors."""
    now = time.time()
    with _token_lock:
        if not force and _token["value"] and _token["exp"] - TOKEN_REFRESH_MARGIN > now:
            return _token["value"]
        pinned = getattr(config, "SHAZAM_MUSICKIT_TOKEN", None)
        if pinned and not force:
            token = pinned
        else:
            token = _retrying(_harvest_token)
        exp = jwt_payload(token).get("exp") or (now + 7 * 86400)
        _token.update(value=token, exp=float(exp))
        return token


def _apple_headers(token, lang):
    headers = {
        "authorization": f"Bearer {token}",
        "origin": SITE,
        "referer": SITE + "/",
        "accept": "application/json",
    }
    if lang:
        headers["accept-language"] = lang
    return headers


def _apple_error(body):
    try:
        err = (fastjson.loads(body).get("errors") or [{}])[0]
        return ": ".join(x for x in (err.get("title"), err.get("detail")) if x)
    except Exception:
        return (body or "")[:200]


def apple_get(path, params=None, *, lang=None, label=None):
    """GET one Apple catalog path through Shazam's developer token -> JSON.
    None-valued params are dropped; 401 re-harvests the token once."""
    label = label or path
    query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
    if lang:
        query.setdefault("l", lang)

    def once():
        token = developer_token()
        resp = _request("GET", APPLE + path, headers=_apple_headers(token, lang),
                        timeout=JSON_TIMEOUT, label=label, params=query)
        if resp.status_code == 401:
            raise _TokenRejected(f"developer token rejected on {label}")
        if resp.status_code == 400:
            raise ShazamBadRequest(f"{label}: {_apple_error(resp.text)}")
        if resp.status_code == 404:
            raise ShazamNotFound(f"{label}: {_apple_error(resp.text) or 'not found'}")
        _classify(resp, label)
        return _json_body(resp, label)

    return _retrying(once, refresh_token_on_401=True)


def catalog_get(country, tail, params=None, *, lang=None):
    return apple_get(f"/v1/catalog/{country.lower()}/{tail}", params, lang=lang)


def resource(country, kind, rid, params=None, *, lang=None):
    """One catalog resource -> its data[0] (NotFound when empty)."""
    body = catalog_get(country, f"{kind}/{rid}", params, lang=lang)
    data = (body or {}).get("data") or []
    if not data:
        raise ShazamNotFound(f"{kind.rstrip('s')} {rid} not found in the '{country}' catalog")
    return data[0]


def page_range(path, offset, limit, *, max_per_call, lang=None, params=None):
    """`limit` items starting at `offset` of an offset-paged Apple
    collection, following `next` across calls -> (objects, has_more)."""
    out = []
    has_more = False
    cursor = offset
    while len(out) < limit:
        want = min(max_per_call, limit - len(out))
        body = apple_get(path, {**(params or {}), "offset": cursor, "limit": want}, lang=lang)
        data = (body or {}).get("data") or []
        out.extend(data)
        has_more = bool(body.get("next"))
        if not has_more or not data:
            break
        cursor += len(data)
    return out[:limit], has_more or len(out) > limit


# Long-lived pools (2026-10-23): their threads keep their thread-local curl
# sessions — and the sticky exits behind them — between requests. The
# per-call executor they replace opened fresh sessions/exits for every
# fan-out and threw them away when the call returned. Two levels: a fan-out
# running inside a fan-out goes to the second pool, so it neither waits on
# the workers it occupies (deadlock) nor loses its parallelism; a third
# level runs inline.
_pools = (ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="shazam-fanout-0"),
          ThreadPoolExecutor(max_workers=FANOUT_WORKERS * 4, thread_name_prefix="shazam-fanout-1"))


def _windowed(fns, workers):
    """Run `fns` on the pool for this nesting level, at most `workers` at a
    time; results align with `fns`. The first failure (in `fns` order)
    propagates once the callables already started have finished."""
    return fanout.windowed(_pools, "shazam-fanout", fns, workers)


def run_parallel(fns):
    """Run zero-arg callables concurrently; results in order; exceptions re-raised."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    return _windowed(fns, FANOUT_WORKERS)


def gather(mapping):
    """{name: callable} -> {name: result | exception} (nothing raises)."""
    names = list(mapping)

    def safe(fn):
        def run():
            try:
                return fn()
            except Exception as e:  # noqa: BLE001 - collected per key
                return e
        return run

    results = run_parallel([safe(mapping[n]) for n in names])
    return dict(zip(names, results))
