"""Shazam payload normalizers -> clean snake_case shapes.

Three upstream dialects meet here:
  * Shazam's legacy discovery objects (amp discovery v5 track, cdn v3 list
    items, the recognition response) — `discovery_track`, `list_track`,
    `recognition`.
  * the `shazam-songs` / `shazam-events` / `venues` JSON:API objects the
    server-rendered pages embed — `page_song`, `event`, `venue`.
  * Apple Music catalog resources, which are shaped by apple_music/parsers
    (same repo) so a song looks identical on both APIs.

Conventions: `link` for web URLs, `shazam_key` + `apple_id` on every song
(the two id systems the site uses), `is_*`/`has_*` booleans, ISO dates,
null for missing values.

Upstream fields dropped on purpose (and why):
  * discovery `layout`, `display.actions`, `share.twitter/html/snapchat/
    avatar/subject/text` (share-sheet copy), `urlparams`, `hub.image/
    actions/options[].beacondata/overflowimage/colouroverflowimage`
    (player UI + analytics), `images.joecolor` (kept decoded as
    `colors`), `alias` (== the link's slug), `properties` (empty).
  * page `webUrl?co=IN&offsetInMilliseconds=…` tracking params (the clean
    link is kept), `share.snapchat/html`, `_preprocessed`, relationship
    hrefs to cdn.shazam.com/song/v2 (server-only, answer 401).
  * event `time.day/month/date/fullday/fulldate/startHourMin/fulldatetime`
    (localised renderings of startTime), `provider.attribution.logo`,
    `hasPostShowContent/allowsSubscriptions/removed` (app switches),
    venue `mapImageUrl` (needs a Maps token).
"""
import re
from datetime import datetime
from urllib.parse import unquote

from apple_music import parsers as AP

clean = AP.clean
to_int = AP.to_int
iso_date = AP.iso_date
iso_datetime = AP.iso_datetime
artwork = AP.artwork
strip_html = AP.strip_html

_TRACK_LINK_RE = re.compile(r"/track/(\d+)")
_LIST_ID_RE = re.compile(r"/tracks/([^?/]+)")
_JOECOLOR_RE = re.compile(r"([bpstq]):([0-9a-fA-F]{6})")
_TIME_RE = re.compile(r"^(\d+):(\d{2})$")


# ---- value helpers ----------------------------------------------------------------

def clean_link(link):
    """Drop the `?co=…&offsetInMilliseconds=…` tracking tail of a shazam link."""
    if not link or not isinstance(link, str):
        return None
    return link.split("?")[0]


def key_from_link(link):
    m = _TRACK_LINK_RE.search(link or "")
    return m.group(1) if m else None


def list_id_from_link(link):
    m = _LIST_ID_RE.search(link or "")
    return m.group(1) if m else None


def colors(joecolor):
    """'b:e1e5e8p:25231fs:372b1ft:4a4947q:595047' -> {background, primary,
    secondary, tertiary, quaternary} hex colours (the site's palette for
    the track artwork)."""
    if not joecolor or not isinstance(joecolor, str):
        return None
    names = {"b": "background", "p": "primary", "s": "secondary", "t": "tertiary", "q": "quaternary"}
    out = {names[k]: f"#{v.lower()}" for k, v in _JOECOLOR_RE.findall(joecolor) if k in names}
    return out or None


def image_size(link, size=800):
    """Shazam/Apple artwork links end in /{w}x{h}bb.jpg or /400x400cc.jpg —
    normalise to one usable size."""
    if not link or not isinstance(link, str):
        return None
    return re.sub(r"/\{w\}x\{h\}([a-z]{2})\.(jpg|png|webp)$", rf"/{size}x{size}\1.\2", link)


def seconds_of(text):
    """'02:23' -> 143; '0.539' -> 0.539; None otherwise."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return text
    m = _TIME_RE.match(str(text).strip())
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def year_or_date(text):
    """'2001' -> '2001'; '2001-11-27' unchanged; junk -> None."""
    text = clean(text)
    if text is None:
        return None
    text = str(text).strip()
    return text if re.match(r"^\d{4}(-\d{2}(-\d{2})?)?$", text) else None


# ---- discovery objects ----------------------------------------------------------

def _hub(hub):
    """The APPLEMUSIC hub -> {provider, is_explicit, listen_link, preview_link}."""
    if not isinstance(hub, dict):
        return None
    listen = preview = None
    for action in hub.get("actions") or []:
        if action.get("type") == "applemusicplay" and action.get("id"):
            listen = f"https://music.apple.com/song/{action['id']}"
        if action.get("type") == "uri" and str(action.get("uri", "")).startswith("http"):
            preview = action.get("uri")
    for option in hub.get("options") or []:
        for action in option.get("actions") or []:
            uri = str(action.get("uri", ""))
            if action.get("type") == "uri" and "music.apple.com" in uri and not listen \
                    and ("/song/" in uri or "/album/" in uri or "?i=" in uri):
                listen = uri.split("&mttnagencyid")[0]
    return {
        "provider": (hub.get("displayname") or hub.get("type") or "").title() or None,
        "is_explicit": bool(hub.get("explicit")),
        "listen_link": listen,
        "preview_link": preview,
    }


def _song_metadata(obj):
    """The SONG section's Album / Label / Released metadata rows."""
    out = {"album_name": None, "label": None, "released": None}
    for section in obj.get("sections") or []:
        if section.get("type") != "SONG":
            continue
        for row in section.get("metadata") or []:
            title = (row.get("title") or "").lower()
            if title == "album":
                out["album_name"] = clean(row.get("text"))
            elif title == "label":
                out["label"] = clean(row.get("text"))
            elif title == "released":
                out["released"] = clean(row.get("text"))
    return out


def _related_list_id(obj):
    for section in obj.get("sections") or []:
        if section.get("type") == "RELATED" and section.get("url"):
            return list_id_from_link(section["url"])
    return list_id_from_link(obj.get("relatedtracksurl"))


def _artists(obj):
    return [{"apple_id": a.get("adamid"), "shazam_id": a.get("id"), "slug": a.get("alias")}
            for a in obj.get("artists") or [] if isinstance(a, dict)] or None


def discovery_track(obj):
    """amp discovery v5 track (also the recognition response's `track`)."""
    if not isinstance(obj, dict):
        return None
    images = obj.get("images") or {}
    meta = _song_metadata(obj)
    hub = _hub(obj.get("hub"))
    apple_id = obj.get("trackadamid")
    if not apple_id and hub and hub.get("listen_link"):
        apple_id = hub["listen_link"].rsplit("/", 1)[-1]
    return {
        "shazam_key": obj.get("key"),
        "apple_id": apple_id,
        "title": obj.get("title"),
        "artist_name": obj.get("subtitle"),
        "link": clean_link(obj.get("url")),
        "isrc": obj.get("isrc"),
        "genre": (obj.get("genres") or {}).get("primary"),
        "release_date": (iso_date(obj.get("releasedate")) or parsed_date(obj.get("releasedate"), "%d-%m-%Y")
                         or year_or_date(meta["released"])),
        "album_name": meta["album_name"],
        "label": meta["label"],
        "album_apple_id": obj.get("albumadamid"),
        "artists": _artists(obj),
        "is_explicit": bool((obj.get("hub") or {}).get("explicit")),
        "type": (obj.get("type") or "").lower() or None,
        "images": {
            "cover_art": images.get("coverart"),
            "cover_art_hq": images.get("coverarthq"),
            "background": images.get("background"),
        } if images else None,
        "colors": colors(images.get("joecolor")),
        "hub": hub,
        "related_list_id": _related_list_id(obj),
        "highlight_links": clean({k: v for k, v in (obj.get("highlightsurls") or {}).items() if v}),
    }


def list_track(obj, rank=None):
    """cdn v3 list item (similar songs, genre charts) — the lighter shape."""
    if not isinstance(obj, dict):
        return None
    images = obj.get("images") or {}
    out = {
        "shazam_key": obj.get("key"),
        "title": obj.get("title"),
        "artist_name": obj.get("subtitle"),
        "link": clean_link(obj.get("url")),
        "artists": _artists(obj),
        "is_explicit": bool((obj.get("hub") or {}).get("explicit")),
        "cover_art": images.get("coverart") or images.get("coverarthq"),
        "colors": colors(images.get("joecolor")),
        "hub": _hub(obj.get("hub")),
    }
    return {"rank": rank, **out} if rank is not None else out


def recognition(body):
    """Discovery tag response -> {is_matched, matches, track}."""
    body = body or {}
    matches = []
    for m in body.get("matches") or []:
        if not isinstance(m, dict):
            continue
        matches.append({
            "shazam_key": m.get("id"),
            "offset_seconds": m.get("offset"),
            "time_skew": m.get("timeskew"),
            "frequency_skew": m.get("frequencyskew"),
        })
    track = discovery_track(body.get("track")) if body.get("track") else None
    return {"is_matched": bool(track), "matches": matches or None, "track": track,
            "tag_id": body.get("tagid")}


# ---- page objects ----------------------------------------------------------------

def page_song(obj):
    """The `shazam-songs` JSON:API object a track page embeds."""
    if not isinstance(obj, dict):
        return None
    a = obj.get("attributes") or {}
    images = a.get("images") or {}
    art = a.get("artwork") or {}
    rels = obj.get("relationships") or {}
    related = ((rels.get("related-tracks") or {}).get("data") or [{}])[0]
    return {
        "shazam_key": obj.get("id"),
        "apple_id": obj.get("_adamId") or (((rels.get("songs") or {}).get("data") or [{}])[0].get("id")),
        "title": a.get("title"),
        "artist_name": a.get("artist"),
        "primary_artist_name": a.get("primaryArtist"),
        "link": clean_link(a.get("webUrl")),
        "isrc": a.get("isrc"),
        "label": a.get("label"),
        "genre": (a.get("genres") or {}).get("primary"),
        "type": (a.get("type") or "").lower() or None,
        "is_explicit": bool(a.get("explicit")),
        "has_lyrics": bool(obj.get("hasLyrics")),
        "has_time_synced_lyrics": bool(obj.get("hasTimeSyncedLyrics")),
        "is_available_in_classical": bool(a.get("classicalAvailability")),
        "preview_link": (a.get("streaming") or {}).get("preview"),
        "apple_music_link": (a.get("streaming") or {}).get("deeplink"),
        "images": {
            "cover_art": images.get("coverArt"),
            "cover_art_hq": images.get("coverArtHq"),
            "artist_avatar": images.get("artistAvatar"),
        } if images else None,
        "colors": {
            "background": f"#{art['bgColor']}" if art.get("bgColor") else None,
            "primary": f"#{art['textColor1']}" if art.get("textColor1") else None,
            "secondary": f"#{art['textColor2']}" if art.get("textColor2") else None,
        } if art else None,
        "album_apple_id": (((rels.get("albums") or {}).get("data") or [{}])[0].get("id")),
        "artist_apple_id": ((rels.get("artists") or {}).get("id")),
        "related_list_id": related.get("id"),
    }


def lyrics_sections(sections):
    """[{sectionTitle, startTimeInSeconds, endTimeInSeconds, lyricLines}] ->
    {is_time_synced, text, sections[{title, start_seconds, end_seconds,
    lines[{text, start_seconds, end_seconds}]}]}."""
    out = []
    for s in sections or []:
        lines = []
        for line in s.get("lyricLines") or []:
            if not isinstance(line, dict):
                continue
            lines.append({
                "text": line.get("content"),
                "start_seconds": seconds_of(line.get("startTimeInSeconds")),
                "end_seconds": seconds_of(line.get("endTimeInSeconds")),
            })
        out.append({
            "title": clean(s.get("sectionTitle")),
            "start_seconds": seconds_of(s.get("startTimeInSeconds")),
            "end_seconds": seconds_of(s.get("endTimeInSeconds")),
            "lines": lines,
        })
    if not out:
        return None
    synced = any(l["start_seconds"] is not None for s in out for l in s["lines"])
    text = "\n\n".join("\n".join(l["text"] for l in s["lines"] if l["text"]) for s in out).strip()
    return {"is_time_synced": synced, "text": text or None,
            "line_count": sum(len(s["lines"]) for s in out), "sections": out}


def audio_analysis(obj):
    """{melodicness, acousticness, valence, danceability, energy, bpm} ->
    same keys, floats rounded to 3 places, bpm int."""
    if not isinstance(obj, dict):
        return None
    out = {}
    for key in ("bpm", "energy", "danceability", "valence", "acousticness", "melodicness"):
        value = obj.get(key)
        if value is None:
            out[key] = None
        elif key == "bpm":
            out[key] = to_int(value)
        else:
            try:
                out[key] = round(float(value), 3)
            except (TypeError, ValueError):
                out[key] = None
    return out if any(v is not None for v in out.values()) else None


def popular_segments(points, track_length_seconds=None, description=None):
    """PopularSegments dataPoints -> the heat map as a list of windows."""
    segments = []
    for p in points or []:
        d = p.get("dataPoint") if isinstance(p, dict) and "dataPoint" in p else p
        if not isinstance(d, dict):
            continue
        segments.append({
            "start_seconds": seconds_of(d.get("startTime")),
            "end_seconds": seconds_of(d.get("endTime")),
            "shazam_count": to_int(d.get("tags")),
            "share_of_peak": round(float(d["heightPercentage"]), 4) if d.get("heightPercentage") is not None else None,
            "share_of_track": round(float(d["lengthPercentage"]), 4) if d.get("lengthPercentage") is not None else None,
        })
    if not segments:
        return None
    peak = max(segments, key=lambda s: s["shazam_count"] or 0)
    return {
        "period": "past_7_days",
        "track_length_seconds": track_length_seconds,
        "most_popular": {"start_seconds": peak["start_seconds"], "end_seconds": peak["end_seconds"],
                         "shazam_count": peak["shazam_count"]},
        "description": description,
        "segments": segments,
    }


# ---- events ----------------------------------------------------------------------

def venue(obj):
    if not isinstance(obj, dict):
        return None
    a = obj.get("attributes") or {}
    address = a.get("address") or {}
    structured = address.get("structuredAddress") or {}
    geo = a.get("geo") or {}
    return {
        "id": obj.get("id"),
        "name": a.get("name"),
        "address": {
            "street": structured.get("address"),
            "city": structured.get("city"),
            "region": structured.get("region"),
            "postal_code": structured.get("postCode"),
            "country": structured.get("country"),
            "country_code": structured.get("countryIsoCode"),
            "formatted": clean(address.get("formattedAddressLine")),
        } if address else None,
        "latitude": geo.get("lat"),
        "longitude": geo.get("lng"),
    }


def _event_artist(obj):
    if not isinstance(obj, dict):
        return None
    a = obj.get("attributes") or {}
    genres = [g.get("id") for g in (((obj.get("relationships") or {}).get("genres") or {}).get("data") or [])
              if isinstance(g, dict) and g.get("id")]
    return {
        "apple_id": obj.get("id"),
        "name": a.get("name"),
        "link": a.get("url"),
        "artwork": artwork(a.get("artwork")),
        "genre_ids": genres or None,
    }


def event(obj, genres=None):
    """A `shazam-events` object (with the page's inlined `_artists` /
    `_venues`) -> one concert."""
    if not isinstance(obj, dict):
        return None
    a = obj.get("attributes") or {}
    t = a.get("time") or {}
    tickets = a.get("tickets") or {}
    provider = a.get("provider") or {}
    artists_raw = obj.get("_artists")
    artists = [artists_raw] if isinstance(artists_raw, dict) else (artists_raw or [])
    venues_raw = obj.get("_venues")
    venues = [venues_raw] if isinstance(venues_raw, dict) else (venues_raw or [])
    artist_items = [x for x in (_event_artist(x) for x in artists) if x]
    genre_names = None
    if genres and artist_items:
        names = []
        for art in artist_items:
            for gid in art.get("genre_ids") or []:
                name = ((genres.get(str(gid)) or {}).get("attributes") or {}).get("name")
                if name and name not in names:
                    names.append(name)
        genre_names = names or None
    return {
        "id": obj.get("id"),
        "name": a.get("name"),
        "link": a.get("webUrl"),
        "kind": (a.get("kind") or "").lower() or None,
        "starts_at": iso_datetime(t.get("startTime")),
        "ends_at": iso_datetime(t.get("endTime")),
        "announced_at": iso_datetime(t.get("announcementTime")),
        "timezone": t.get("timezone"),
        "duration_hours": t.get("duration"),
        "is_upcoming": t.get("isFuture"),
        "is_just_announced": t.get("isJustAnnounced"),
        "is_live_now": t.get("isActiveShow"),
        "is_virtual": bool(a.get("isVirtual")),
        "artists": artist_items or None,
        "genres": genre_names,
        "venue": venue(venues[0]) if venues else None,
        "tickets": {
            "provider": tickets.get("provider") or provider.get("name"),
            "link": tickets.get("url") or provider.get("url") or a.get("url"),
            "links": [{"vendor": l.get("vendor"), "link": l.get("url")}
                      for l in tickets.get("links") or [] if isinstance(l, dict)] or None,
        },
        "source": provider.get("name"),
    }


# ---- chart catalog -------------------------------------------------------------

def chart_country(obj):
    """One entry of the charts page's country selector."""
    if not isinstance(obj, dict):
        return None
    code = obj.get("id")
    is_world = code == "world"
    return {
        "code": None if is_world else code,
        "name": "Global" if is_world else obj.get("name"),
        "slug": "world" if is_world else (unquote(obj["urlName"]) if obj.get("urlName") else None),
        "link": obj.get("link"),
        "top_playlist_id": obj.get("listid"),
        "viral_playlist_id": obj.get("viral_listid"),
        "momentum_playlist_id": obj.get("momentum_listid"),
        "cities": [{
            "id": c.get("id"),
            "name": c.get("name"),
            "slug": unquote(c["urlName"]) if c.get("urlName") else None,
            "playlist_id": c.get("listid"),
        } for c in obj.get("cities") or [] if isinstance(c, dict)],
        "genres": [{
            "id": g.get("id"),
            "name": g.get("name"),
            "slug": unquote(g["urlName"]) if g.get("urlName") else None,
            "playlist_id": g.get("listid"),
            "track_count": to_int(g.get("count")),
            "link": g.get("link"),
        } for g in obj.get("genres") or [] if isinstance(g, dict)],
    }


# ---- pagination ------------------------------------------------------------------

def pagination(page, per_page, has_more, total_count=None):
    """The block route_glue.paginate() lifts into the flat gateway shape."""
    return AP.pagination(page, per_page, has_more, total_count)


def parsed_date(text, fmt="%B %d, %Y"):
    try:
        return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
    except (AttributeError, ValueError):
        return None
