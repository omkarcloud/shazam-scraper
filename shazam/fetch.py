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

Bot protection seen so far (2026-09-19): Fastly's "Client Challenge" (a
JavaScript proof-of-work page, `/_fs-ch-…/` assets, a 200 with no flight
data) on /track/* pages only, for one IP after ~60 page loads within an
hour; artist/charts/events pages, the JSON hosts and the Apple API were
never challenged, and the same track pages loaded at once through a fresh
US residential exit. get_page therefore treats the challenge as a per-IP
signal: the worker thread's page session moves to a
config.shazam_page_fallback_proxy() exit for SHAZAM_DIRECT_COOLDOWN
seconds (rotating exits on repeat challenges) and returns to direct egress
afterwards.

FALLBACK HOOK: if direct requests AND the residential exits both start
being challenged, escalate the same way g2 does — add a patchright pool
with flag "shazam" to config.CHROME_POOLS, warm it on
https://www.shazam.com/, let the real Chrome solve the challenge and
replay its cookie jar over curl_cffi (the challenge sets a cookie), or
serve these same URLs through the in-page driver.get()
(patchright_driver.FetchResponse). Not built now: dead code while a fresh
exit passes.

Failure taxonomy (scraper_errors, mapped to HTTP by route_glue):
  ShazamUpstreamError  transport failure / 5xx            — retryable
  ShazamBlocked        403 / 429 / challenge or non-JSON  — retryable
  ShazamBadRequest     upstream 400 (bad storefront, …)   — never retried
  ShazamNotFound       404 / 204 / empty data             — never retried
"""
import base64
import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

SITE = "https://www.shazam.com"
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
    "accept-language": "en-US,en;q=0.9",
}
JSON_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": SITE,
    "referer": SITE + "/",
}
# The Shazam iOS app's identity, which the discovery tag endpoint expects.
TAG_HEADERS = {
    "X-Shazam-Platform": "IPHONE",
    "X-Shazam-AppVersion": "14.1.0",
    "Accept": "*/*",
    "Accept-Language": "en",
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
_local = threading.local()


def _session():
    sess = getattr(_local, "session", None)
    if sess is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.shazam_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        _local.session = sess
    return sess


def _drop_session():
    """Close the thread's session so a poisoned keep-alive connection dies."""
    sess = getattr(_local, "session", None)
    _local.session = None
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass


# Page loads: same per-thread session, but after a challenge the thread
# reads pages through a residential exit until the cooldown lapses.
_pages = threading.local()


def _page_session():
    state = getattr(_pages, "state", None)
    now = time.time()
    if state and state["until"] and state["until"] < now:
        _drop_page_session()
        state = None
    if state is None:
        from curl_cffi import requests as curl_requests
        sess = curl_requests.Session(impersonate=IMPERSONATE)
        proxy = config.shazam_proxy()
        if proxy:
            sess.proxies = {"http": proxy, "https": proxy}
        state = {"session": sess, "until": 0, "via_proxy": bool(proxy)}
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


def _escalate_page_session():
    """Swap the thread's page session for one on a fresh residential exit
    (sticky for the cooldown). Returns False when no fallback is configured."""
    proxy = config.shazam_page_fallback_proxy()
    _drop_page_session()
    if not proxy:
        return False
    from curl_cffi import requests as curl_requests
    sess = curl_requests.Session(impersonate=IMPERSONATE)
    sess.proxies = {"http": proxy, "https": proxy}
    _pages.state = {"session": sess, "until": time.time() + config.SHAZAM_DIRECT_COOLDOWN, "via_proxy": True}
    return True


def is_challenge(html):
    head = (html or "")[:4000]
    return all(marker in head for marker in CHALLENGE_MARKERS)


def dump_debug(name, text):
    """Write a raw body to $SHAZAM_DEBUG_DIR/<name>.txt for post-mortems."""
    dbg = os.environ.get("SHAZAM_DEBUG_DIR", "")
    if dbg and text:
        try:
            os.makedirs(dbg, exist_ok=True)
            with open(os.path.join(dbg, name + ".txt"), "w") as f:
                f.write(text)
        except OSError:
            pass


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
    Fastly challenge moves this thread's page loads to a fresh residential
    exit and retries."""
    url = SITE + path
    label = f"page {path}"
    last = None
    for attempt in range(1, config.MAX_RETRIES + 2):
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
        if is_challenge(html) or resp.status_code in (401, 403, 429):
            dump_debug("page_challenge", html)
            last = ShazamChallenged(f"{label}: bot challenge from this exit")
            if not _escalate_page_session():
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
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
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
        err = (json.loads(body).get("errors") or [{}])[0]
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


def run_parallel(fns):
    """Run zero-arg callables concurrently; results in order; exceptions re-raised."""
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]
    with ThreadPoolExecutor(min(FANOUT_WORKERS, len(fns))) as pool:
        return list(pool.map(lambda f: f(), fns))


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
