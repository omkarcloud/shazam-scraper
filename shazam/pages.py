"""Server-rendered shazam.com pages -> the data they embed.

Every page is a Next.js App Router page whose server data rides in the
React Server Components flight payload (`self.__next_f.push` chunks). The
decoder is cricbuzz/rsc.py (same repo, same format); this module only knows
WHERE each page keeps its data:

  /track/{key}[/{slug}]        `shazam-songs` object (+ Apple song/album/
                               artist objects), lyrics blocks
                               ({sectionTitle, lyricLines[]}), `audioAnalysis`
                               {bpm, energy…}, PopularSegments `dataPoint`s,
                               header badges ("On Tour")
  /artist/{slug}/{id}          ArtistAboutCard rows (Hometown / Born /
                               Formed / Genre), bio text blob, link pills
                               (official website, merch), similar-artist
                               tiles, releases grouped by type, event links.
                               Top songs / latest release are client-side
                               MusicKit calls (not embedded).
  /charts/top-200/world        the country selector: 71 countries with
                               their top / viral / momentum playlist ids,
                               cities and genre playlists
  /events/{country}[/{city}]   50 `shazam-events` with `_artists`/`_venues`
                               inlined + a genre id -> name map
  /event/{uuid}                one `shazam-events` object + its venue

Unknown ids render the site's error shell, which fetch.get_page raises as
ShazamNotFound.
"""
import re

from cricbuzz import rsc

from shazam import parsers as P
from shazam.fetch import ShazamNotFound, ShazamUpstreamError, get_page, run_parallel
from shazam.refs import slugify

_EVENT_ID_RE = re.compile(r"/event/([0-9a-f-]{36})")
_SIMILAR_RE = re.compile(r'"href":"(/artist/[^"]+)","artistId":"(\d+)"[^}]*?"dataTestId":"similarArtistItem"')


# ---- generic walkers ------------------------------------------------------------------

def _walk(node, pred, hits, limit):
    if len(hits) >= limit:
        return
    if isinstance(node, dict):
        if pred(node):
            hits.append(node)
            return
        for value in node.values():
            _walk(value, pred, hits, limit)
    elif isinstance(node, list):
        for value in node:
            _walk(value, pred, hits, limit)


def find_dicts(rows, pred, limit=50):
    """Every dict in any JSON row satisfying pred (depth-first, no descent
    into a hit)."""
    hits = []
    for row in rows.values():
        if isinstance(row, (dict, list)):
            _walk(row, pred, hits, limit)
            if len(hits) >= limit:
                break
    return hits


def find_dict(rows, pred):
    hits = find_dicts(rows, pred, limit=1)
    return hits[0] if hits else None


def _is_element(n):
    """A React element row: ["$", "<type>", key, {props}]."""
    return isinstance(n, list) and len(n) == 4 and n[0] == "$" and isinstance(n[3], dict)


def _deref_path(rows, value):
    """Resolve a flight path reference "$<row>:<key>:<key>…" (a value the
    page already sent elsewhere; "props" steps into a React element's props)
    to that value; anything else passes through. None when the path is gone."""
    if not (isinstance(value, str) and value.startswith("$") and ":" in value):
        return value
    row_id, *path = value[1:].split(":")
    node = rows.get(row_id)
    for key in path:
        if key == "props" and _is_element(node):
            node = node[3]
        elif isinstance(node, list) and key.isdigit() and int(key) < len(node):
            node = node[int(key)]
        elif isinstance(node, dict):
            node = node.get(key)
        else:
            return None
    return node


def _text_of(node):
    """The plain text inside a React element tree (children strings joined;
    element type names and "$ref" placeholders skipped)."""
    parts = []

    def walk(n):
        if isinstance(n, str):
            if not n.startswith("$"):
                parts.append(n)
        elif _is_element(n):
            walk(n[3].get("children"))
        elif isinstance(n, list):
            for v in n:
                walk(v)
        elif isinstance(n, dict):
            walk(n.get("children"))

    walk(node)
    return " ".join(p.strip() for p in parts if p and p.strip()) or None


def _labelled_pairs(rows, class_fragment, limit=40):
    """Sibling [header element, value element] lists whose props' className
    carries `class_fragment` -> {header text (lower): value text}."""
    out = {}

    def walk(n, depth=0):
        if len(out) >= limit or depth > 60:
            return
        if isinstance(n, list):
            if (len(n) == 2 and all(_is_element(c) and class_fragment in str(c[3].get("className", ""))
                                    for c in n)):
                header, value = _text_of(n[0]), _text_of(n[1])
                if header and value:
                    out[header.strip().lower()] = value.strip()
                return
            for v in n:
                walk(v, depth + 1)
        elif isinstance(n, dict):
            for v in n.values():
                walk(v, depth + 1)

    for row in rows.values():
        if isinstance(row, (dict, list)):
            walk(row)
    return out


def _props_with_class(rows, class_fragment, limit=50):
    return find_dicts(rows, lambda d: isinstance(d.get("className"), str) and class_fragment in d["className"], limit)


# ---- track page -------------------------------------------------------------------------

def track_page(key):
    """/track/{key} -> {song, apple_song, apple_album, apple_artist, lyrics,
    audio_analysis, popular_segments, badges, canonical_link}."""
    html = get_page(f"/track/{key}")
    rows = rsc.page_rows(html)
    song_obj = find_dict(rows, lambda d: d.get("type") == "shazam-songs" and isinstance(d.get("attributes"), dict))
    if not song_obj:
        raise ShazamNotFound(f"track {key} has no page data")
    apple_song = find_dict(rows, lambda d: d.get("type") == "songs" and isinstance(d.get("attributes"), dict)
                           and (d.get("attributes") or {}).get("isrc"))
    apple_album = find_dict(rows, lambda d: d.get("type") == "albums" and isinstance(d.get("attributes"), dict)
                            and (d.get("attributes") or {}).get("trackCount") is not None)
    apple_artist = find_dict(rows, lambda d: d.get("type") == "artists" and isinstance(d.get("attributes"), dict)
                             and (d.get("attributes") or {}).get("name"))
    lyric_blocks = find_dicts(rows, lambda d: isinstance(d.get("lyricLines"), list), limit=200)
    analysis = find_dict(rows, lambda d: isinstance(d.get("audioAnalysis"), dict))
    points = find_dicts(rows, lambda d: isinstance(d.get("dataPoint"), dict) and "tags" in d["dataPoint"], limit=200)
    description = find_dict(rows, lambda d: d.get("data-test-id") == "popular_segment_discoveryDescription")
    pills = _props_with_class(rows, "PopularSegmentsBar_timePills")
    track_length = None
    if pills:
        times = [P.seconds_of(t) for t in re.findall(r"\d+:\d{2}", _text_of(pills[0]) or "")]
        track_length = max((t for t in times if t is not None), default=None)
    badges = [_text_of(b) for b in _props_with_class(rows, "HeaderBadges_badge__")]
    canonical = re.search(r'rel="canonical" href="([^"]+)"', html)
    return {
        "song": song_obj,
        "apple_song": apple_song,
        "apple_album": apple_album,
        "apple_artist": apple_artist,
        "lyrics": lyric_blocks,
        "audio_analysis": (analysis or {}).get("audioAnalysis"),
        "popular_segments": points,
        "popular_segments_description": _text_of(description) if description else None,
        "track_length_seconds": track_length,
        "badges": [b for b in badges if b] or None,
        "canonical_link": canonical.group(1) if canonical else None,
    }


# ---- artist page -------------------------------------------------------------------------

def artist_page(apple_id, slug=None):
    """/artist/{slug}/{id} -> profile extras the Apple catalog lacks."""
    html = get_page(f"/artist/{slug or 'artist'}/{apple_id}")
    rows = rsc.page_rows(html)
    flight = rsc.flight_text(html)

    # "Hometown / London, England" rows: sibling [header, value] elements
    info = _labelled_pairs(rows, "ArtistAboutCard_info")

    bio = None
    bio_box = find_dict(rows, lambda d: d.get("dataTestId") == "artist_bioViewMore")
    bio_ref = find_dict({"bio": bio_box}, lambda d: isinstance(d.get("dangerouslySetInnerHTML"), dict)) if bio_box else None
    if bio_ref:
        raw = bio_ref["dangerouslySetInnerHTML"].get("__html")
        raw = rsc.deref(rows, raw) if isinstance(raw, str) and raw.startswith("$") else raw
        bio = P.strip_html(raw) if isinstance(raw, str) else None

    links = []
    for pill in find_dicts(rows, lambda d: isinstance(d.get("className"), str)
                           and "ArtistSocialLinks_linkPill" in d["className"] and d.get("href"), limit=20):
        links.append({"label": _text_of(pill), "link": pill.get("href")})

    similar = []
    seen = set()
    for href, artist_id in _SIMILAR_RE.findall(flight):
        if artist_id in seen:
            continue
        seen.add(artist_id)
        name = href.split("/")[2].replace("-", " ").title() if href.count("/") >= 3 else None
        similar.append({"apple_id": artist_id, "name": name, "link": "https://www.shazam.com" + href})
    for tile in find_dicts(rows, lambda d: d.get("dataTestId") == "similarArtistItem", limit=30):
        name = _text_of(tile)
        for s in similar:
            if s["apple_id"] == tile.get("artistId") and name:
                s["name"] = name

    releases = find_dict(rows, lambda d: isinstance(d.get("albums"), list) and "singles" in d)
    groups = None
    if releases:
        groups = {}
        for kind, key in (("albums", "albums"), ("singles", "singles"), ("eps", "ep"),
                          ("live_albums", "liveAlbums"), ("compilations", "compilationAlbums")):
            groups[kind] = [{
                "apple_id": r.get("id"), "name": r.get("name"),
                "release_date": P.iso_date(r.get("releaseDate")), "artist_name": r.get("artistName"),
            } for r in releases.get(key) or [] if isinstance(r, dict)]

    # 2026-10-06: the concert tiles' "href":"/event/…" props left the flight
    # markup; the page now lists the artist's shows as MusicEvent @ids in
    # its ld+json MusicGroup (also copied inside the flight), so take every
    # /event/{uuid} the HTML mentions, deduplicated in page order.
    events = []
    for event_id in _EVENT_ID_RE.findall(html):
        if "/event/" + event_id not in events:
            events.append("/event/" + event_id)

    badges = [b for b in (_text_of(b) for b in _props_with_class(rows, "HeaderBadges_badge__")) if b]
    title = re.search(r"<title>(.*?)</title>", html)
    canonical = re.search(r'rel="canonical" href="([^"]+)"', html)
    # 2026-10-06: Fastly answers automation-class clients with another
    # artist's page (see fetch.py); never hand out someone else's shows.
    if canonical and not canonical.group(1).rstrip("/").endswith(f"/{apple_id}"):
        raise ShazamUpstreamError(f"artist page {apple_id} was answered with {canonical.group(1)}")
    return {
        "name": (title.group(1).rsplit(" - Shazam", 1)[0] if title else None),
        "canonical_link": canonical.group(1) if canonical else None,
        "badges": badges or None,
        "hometown": info.get("hometown"),
        "born": info.get("born"),
        "formed": info.get("formed"),
        "genre": info.get("genre"),
        "bio": bio,
        "links": links or None,
        "similar_artists": similar or None,
        "releases": groups,
        "event_links": events,
    }


# ---- charts catalog --------------------------------------------------------------------------

def chart_catalog():
    """The country selector of the charts page -> [chart_country, …]."""
    html = get_page("/charts/top-200/world")
    rows = rsc.page_rows(html)

    def is_country(o):
        return isinstance(o, dict) and "listid" in o and "genres" in o

    # 2026-11-13: the selector's first option (the world chart) became a
    # path reference to the header's `selected` entry
    # ("$23:props:children:1:props:children:props:selected"), so the list is
    # found by ANY full country entry and its references are resolved.
    # (The header's own selector lists only {caption, link} per country.)
    options = find_dict(rows, lambda d: isinstance(d.get("options"), list)
                        and any(is_country(o) for o in d["options"]))
    if not options:
        raise ShazamNotFound("chart catalog not found on the charts page")
    entries = [rsc.undefined_to_none(_deref_path(rows, o)) for o in options["options"]]
    countries = [c for c in (P.chart_country(o) for o in entries if is_country(o)) if c]
    # the world entry carries no slug of its own in some builds
    for c in countries:
        if c["slug"] is None and c["link"]:
            c["slug"] = c["link"].rstrip("/").rsplit("/", 1)[-1]
        if c["slug"] is None and c["name"]:
            c["slug"] = slugify(c["name"])
    return countries


# ---- events ------------------------------------------------------------------------------------

def _events_from_rows(rows):
    genres = find_dict(rows, lambda d: isinstance(d.get("genres"), dict) and d["genres"]
                       and all(isinstance(v, dict) and v.get("type") == "genres" for v in d["genres"].values()))
    genre_map = (genres or {}).get("genres") or {}
    seen = set()
    out = []
    for obj in find_dicts(rows, lambda d: d.get("type") == "shazam-events" and isinstance(d.get("attributes"), dict), limit=500):
        if obj.get("id") in seen:
            continue
        seen.add(obj.get("id"))
        out.append(P.event(obj, genre_map))
    return out, genre_map


def events_page(country_slug, city_slug=None):
    path = f"/events/{country_slug}" + (f"/{city_slug}" if city_slug else "")
    html = get_page(path)
    rows = rsc.page_rows(html)
    events, genre_map = _events_from_rows(rows)
    title = re.search(r"<title>(.*?)</title>", html)
    return {
        "title": title.group(1).split(" | ")[0] if title else None,
        "events": events,
        "genres": [{"id": gid, "name": (g.get("attributes") or {}).get("name")}
                   for gid, g in genre_map.items()] or None,
    }


def event_page(event_id):
    html = get_page(f"/event/{event_id}")
    rows = rsc.page_rows(html)
    events, _ = _events_from_rows(rows)
    for ev in events:
        if ev and ev.get("id") == event_id:
            return ev
    if events:
        return events[0]
    raise ShazamNotFound(f"event {event_id} not found")


def event_pages(event_ids, limit=10):
    """Several /event/{uuid} pages in parallel (failures dropped)."""
    def one(eid):
        def run():
            try:
                return event_page(eid)
            except Exception:
                return None
        return run
    return [e for e in run_parallel([one(e) for e in event_ids[:limit]]) if e]
