"""Reference resolvers: ONE param per input that auto-detects its forms
(tripadvisor QueryOrIdField convention — never a sibling `url`/`id` pair).
Every resolver returns a small JSON-able dict so the validated params stay
usable as a response-cache key.

  track     40333609                     Shazam key (bare digits are ALWAYS the
                                         Shazam key: the Apple song id space
                                         overlaps it — 850571371 is both an
                                         Apple id and another song's key)
            apple:1436677761             Apple song id, explicitly
            HKOA10616988                 ISRC
            https://www.shazam.com/track/40333609/river-flows-in-you
            https://www.shazam.com/song/1436677761/river-flows-in-you  (Apple id)
            https://music.apple.com/us/song/river-flows-in-you/1436677761
            https://music.apple.com/us/album/first-love/1436677753?i=1436677761
            -> {"key": "40333609"} | {"id": "1436677761", "country": "us"}
               | {"isrc": …}
  artist    567072 | https://www.shazam.com/artist/gorillaz/567072
            | https://music.apple.com/us/artist/gorillaz/567072
            -> {"id": "567072", "country": None|"us"}
  album     850576570 | 803680097182 (UPC/EAN) | https://www.shazam.com/album/850576570
            | https://music.apple.com/us/album/humanz/850576570
  playlist  pl.92d704ba99a3411289a34fab82866a62 | a music.apple.com playlist link
  event     4159f761-6cf2-44de-89fa-28ebdca31126
            | https://www.shazam.com/event/4159f761-6cf2-44de-89fa-28ebdca31126
  region    US | united-states | "United States" | world  (charts / events)

Apple-side forms are delegated to apple_music/refs.py (same repo) so a
pasted Apple Music link behaves identically on both APIs.
"""
import re
from urllib.parse import urlparse

from apple_music import refs as apple_refs

_DIGITS_RE = re.compile(r"^\d{1,12}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}\d{7}$")
_PLAYLIST_RE = re.compile(r"^pl\.[A-Za-z0-9-]{6,}$")
_SHAZAM_HOST_RE = re.compile(r"(^|\.)shazam\.com$")
_APPLE_HOST_RE = re.compile(r"(^|\.)(music|itunes)\.apple\.com$")
_LOCALE_RE = re.compile(r"^[a-z]{2}-[a-z]{2}$")


def _is_link(text):
    return text.startswith(("http://", "https://", "//")) or ".com/" in text


def _parse(text):
    url = text if "://" in text else ("https:" + text if text.startswith("//") else "https://" + text)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    segments = [s for s in parsed.path.split("/") if s]
    if segments and _LOCALE_RE.match(segments[0].lower()):     # /de-de/track/…
        segments = segments[1:]
    return host, segments, parsed


def _shazam_segments(text, kind):
    host, segments, _ = _parse(text)
    if _SHAZAM_HOST_RE.search(host):
        return segments
    if _APPLE_HOST_RE.search(host):
        return None
    raise ValueError(f"{kind} must be a shazam.com or music.apple.com link, or an id")


def _digits_after(segments, sections, kind):
    """The numeric segment that follows one of `sections` (either order:
    /artist/{slug}/{id} and /artist/{id}/{slug} both occur)."""
    for section in sections:
        if section in segments:
            tail = segments[segments.index(section) + 1:]
            for segment in tail:
                if _DIGITS_RE.match(segment):
                    return segment
    raise ValueError(f"no {kind} id in the link")


def resolve_track(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("track is required")
    if _DIGITS_RE.match(text):
        return {"key": text}
    lowered = text.lower()
    for prefix in ("apple:", "apple_id:", "adam:"):
        if lowered.startswith(prefix) and _DIGITS_RE.match(text[len(prefix):].strip()):
            return {"id": text[len(prefix):].strip(), "country": None}
    for prefix in ("shazam:", "key:"):
        if lowered.startswith(prefix) and _DIGITS_RE.match(text[len(prefix):].strip()):
            return {"key": text[len(prefix):].strip()}
    upper = text.upper().replace("-", "")
    if _ISRC_RE.match(upper) and not _is_link(text):
        return {"isrc": upper}
    if _is_link(text):
        segments = _shazam_segments(text, "track")
        if segments is None:
            return apple_refs.resolve_song(text)
        if "track" in segments:
            return {"key": _digits_after(segments, ["track"], "track")}
        if "song" in segments:
            return {"id": _digits_after(segments, ["song"], "track"), "country": None}
        raise ValueError("track link must look like https://www.shazam.com/track/<key>/<slug>")
    raise ValueError("track must be a Shazam key (e.g. 40333609), apple:<Apple song id>, an ISRC, "
                     "or a shazam.com / music.apple.com song link")


def resolve_track_list(values):
    refs = []
    for value in values or []:
        ref = resolve_track(value)
        if "isrc" in ref:
            raise ValueError(f"{value}: batch takes ids or links, not ISRCs")
        if ref not in refs:
            refs.append(ref)
    if not refs:
        raise ValueError("tracks is required")
    return refs


def resolve_artist(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("artist is required")
    if _DIGITS_RE.match(text):
        return {"id": text, "country": None}
    if _is_link(text):
        segments = _shazam_segments(text, "artist")
        if segments is None:
            return apple_refs.resolve_artist(text)
        return {"id": _digits_after(segments, ["artist"], "artist"), "country": None}
    raise ValueError("artist must be an Apple artist id (e.g. 567072) or a shazam.com / "
                     "music.apple.com artist link")


def resolve_album(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("album is required")
    if _is_link(text):
        segments = _shazam_segments(text, "album")
        if segments is None:
            return apple_refs.resolve_album(text)
        return {"id": _digits_after(segments, ["album"], "album"), "country": None}
    return apple_refs.resolve_album(text)


def resolve_playlist(value):
    text = str(value or "").strip()
    if _PLAYLIST_RE.match(text):
        return {"id": text, "country": None}
    return apple_refs.resolve_playlist(text)


def resolve_event(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("event is required")
    if _UUID_RE.match(text):
        return {"id": text.lower()}
    if _is_link(text):
        segments = _shazam_segments(text, "event")
        if segments and "event" in segments:
            tail = segments[segments.index("event") + 1:]
            for segment in tail:
                if _UUID_RE.match(segment):
                    return {"id": segment.lower()}
        raise ValueError("event link must look like https://www.shazam.com/event/<uuid>")
    raise ValueError("event must be a Shazam event id (uuid) or a shazam.com event link")


def slugify(text):
    """'United States' -> 'united-states', 'Hip-Hop/Rap' -> 'hip-hop-rap'
    (the site's own URL slugs, with 'Türkiye' kept as 'türkiye')."""
    text = (text or "").strip().lower().replace("&", "and")
    text = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE)
    return text.strip("-")


def resolve_region(value):
    """Chart / events region: 'world', an ISO code, a slug or a name -> a
    normalised token the chart catalog is searched with."""
    text = str(value or "").strip()
    if not text:
        return "world"
    if _is_link(text):
        _, segments, _ = _parse(text)
        if "charts" in segments and len(segments) >= 3:
            return slugify(segments[2])
        if "events" in segments and len(segments) >= 2:
            return slugify(segments[1])
        raise ValueError("region must be a country code, name or slug (e.g. US, united-states)")
    if text.lower() in ("world", "global", "worldwide"):
        return "world"
    return slugify(text) if len(text) > 2 else text.upper()


def resolve_slug(value):
    """City / genre: slug or name -> slug (matched against the chart catalog)."""
    text = str(value or "").strip()
    return slugify(text) if text else None
