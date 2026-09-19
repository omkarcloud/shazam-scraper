"""/shazam/songs/*: one song from every angle.

Identity: a song has a Shazam key (the discovery id shazam.com/track/{key}
uses) and an Apple song id (music.apple.com, the site's /song/{id} form).
`resolve()` turns any accepted reference (shazam/refs.py) into both:

  * Shazam key (bare digits / track link) -> discovery v5 -> Apple id
  * Apple id (apple:<id> / link) or ISRC  -> Apple catalog -> a2st -> key
"""
from apple_music import parsers as AP

from shazam import pages
from shazam import parsers as P
from shazam.fetch import (ShazamNotFound, catalog_get, cdn_list, discovery_track, gather, resource)
from shazam.shared import shazam_keys_for, storefront

_SONG_INCLUDE = "artists,albums"


def _apple_song(sf, apple_id, language):
    return resource(sf, "songs", apple_id, {"include": _SONG_INCLUDE}, lang=language)


def _apple_song_by_isrc(sf, isrc, language):
    """The catalog song for an ISRC. Several uploads can carry the same
    ISRC, so the candidates Shazam knows win, then the earliest release."""
    body = catalog_get(sf, "songs", {"filter[isrc]": isrc, "include": _SONG_INCLUDE}, lang=language)
    data = [d for d in body.get("data") or [] if isinstance(d, dict)]
    if not data:
        raise ShazamNotFound(f"no song with ISRC {isrc} in the '{sf}' catalog")
    if len(data) == 1:
        return data[0], None
    keys = shazam_keys_for([d.get("id") for d in data], country=sf, language=language)
    data.sort(key=lambda d: (0 if keys.get(str(d.get("id"))) else 1,
                             (d.get("attributes") or {}).get("releaseDate") or "9999"))
    return data[0], keys.get(str(data[0].get("id")))


def resolve(ref, *, country="US", language="en-US"):
    """{key|id|isrc|digits, country?} -> {key, apple_id, discovery, apple_song}
    where `discovery` is the raw discovery v5 object (when the key is known)
    and `apple_song` the raw Apple song (fetched lazily by callers)."""
    sf = storefront(country, ref)
    if ref.get("key"):
        disc = discovery_track(ref["key"], country=sf, language=language)
        return {"key": ref["key"], "apple_id": P.discovery_track(disc).get("apple_id"), "discovery": disc,
                "apple_song": None}
    key = None
    if ref.get("isrc"):
        song, key = _apple_song_by_isrc(sf, ref["isrc"], language)
    else:
        song = _apple_song(sf, ref["id"], language)
    apple_id = song.get("id")
    if not key:
        key = shazam_keys_for([apple_id], country=sf, language=language).get(str(apple_id))
    if not key:
        raise ShazamNotFound(f"Apple song {apple_id} is not in Shazam's catalog")
    return {"key": key, "apple_id": apple_id, "discovery": None, "apple_song": song}


def _song_shape(sf, language, resolved):
    """The merged song object: Apple metadata + Shazam identity."""
    disc = resolved["discovery"] or discovery_track(resolved["key"], country=sf, language=language)
    shazam = P.discovery_track(disc)
    apple_id = resolved["apple_id"] or shazam.get("apple_id")
    song = resolved["apple_song"]
    if song is None and apple_id:
        try:
            song = _apple_song(sf, apple_id, language)
        except ShazamNotFound:
            song = None
    apple = AP.song(song) if song else {}
    artists = [AP.artist(a) for a in AP.rel(song, "artists")] if song else []
    album = next((AP.album(a) for a in AP.rel(song, "albums")), None) if song else None
    out = {
        "shazam_key": shazam["shazam_key"],
        "apple_id": apple_id,
        "title": apple.get("name") or shazam["title"],
        "artist_name": apple.get("artist_name") or shazam["artist_name"],
        "link": shazam["link"],
        "apple_music_link": apple.get("link") or (shazam.get("hub") or {}).get("listen_link"),
        "isrc": apple.get("isrc") or shazam["isrc"],
        "genre": shazam["genre"],
        "genres": apple.get("genres"),
        "release_date": apple.get("release_date") or shazam["release_date"],
        "label": shazam["label"],
        "album": album or ({"id": shazam["album_apple_id"], "name": shazam["album_name"]}
                           if shazam["album_apple_id"] or shazam["album_name"] else None),
        "artists": artists or None,
        "composer_name": apple.get("composer_name"),
        "duration_ms": apple.get("duration_ms"),
        "duration": apple.get("duration"),
        "track_number": apple.get("track_number"),
        "disc_number": apple.get("disc_number"),
        "is_explicit": apple.get("is_explicit", shazam["is_explicit"]),
        "has_lyrics": apple.get("has_lyrics"),
        "has_time_synced_lyrics": apple.get("has_time_synced_lyrics"),
        "audio_traits": apple.get("audio_traits"),
        "preview_link": apple.get("preview_link") or (shazam.get("hub") or {}).get("preview_link"),
        "artwork": apple.get("artwork"),
        "images": shazam["images"],
        "colors": shazam["colors"],
        "related_list_id": shazam["related_list_id"],
        "highlight_links": shazam["highlight_links"],
    }
    return out


def details(track, country="US", language="en-US"):
    sf = storefront(country, track)
    resolved = resolve(track, country=sf, language=language)
    return _song_shape(sf, language, resolved)


def batch(tracks, country="US", language="en-US"):
    """Up to 25 songs by id/link -> the Apple song shape + Shazam identity
    (two upstream calls for the Apple-id half, one discovery call per key)."""
    sf = storefront(country)
    keyed = [r for r in tracks if r.get("key")]
    by_id = [r["id"] for r in tracks if r.get("id")]

    def one(ref):
        def run():
            try:
                res = resolve(ref, country=sf, language=language)
                return res
            except ShazamNotFound:
                return None
        return run

    resolved = [r for r in gather({i: one(ref) for i, ref in enumerate(keyed)}).values()
                if isinstance(r, dict)]
    apple_ids = [r["apple_id"] for r in resolved if r.get("apple_id")] + by_id
    songs = {}
    if apple_ids:
        body = catalog_get(sf, "songs", {"ids": ",".join(dict.fromkeys(apple_ids))}, lang=language)
        for obj in body.get("data") or []:
            songs[obj.get("id")] = obj
    key_by_apple = {r["apple_id"]: r["key"] for r in resolved if r.get("apple_id")}
    missing = [i for i in by_id if i not in key_by_apple]
    if missing:
        key_by_apple.update(shazam_keys_for(missing, country=sf, language=language))
    disc_ids = {r["apple_id"] for r in resolved if r.get("apple_id") and r.get("discovery")}
    items = []
    seen = set()
    for apple_id in dict.fromkeys(apple_ids):
        obj = songs.get(apple_id)
        key = key_by_apple.get(apple_id)
        if not obj and (not key or apple_id in disc_ids):
            continue
        song = AP.song(obj) if obj else {"id": apple_id}
        items.append({"shazam_key": key, "apple_id": apple_id,
                      "shazam_link": f"https://www.shazam.com/track/{key}" if key else None,
                      **{k: v for k, v in song.items() if k != "id"}})
        seen.add(apple_id)
    for r in resolved:                        # keys Apple has no record for
        if r.get("apple_id") in seen or not r.get("discovery"):
            continue
        d = P.discovery_track(r["discovery"])
        items.append({"shazam_key": d["shazam_key"], "apple_id": d["apple_id"], "shazam_link": d["link"],
                      "name": d["title"], "artist_name": d["artist_name"], "isrc": d["isrc"]})
    return {"country": sf.upper(), "count": len(items), "results": items}


def _page_for(track, country, language):
    sf = storefront(country, track)
    resolved = resolve(track, country=sf, language=language)
    page = pages.track_page(resolved["key"])
    song = P.page_song(page["song"])
    return sf, resolved, page, song


def _identity(song, resolved):
    return {"shazam_key": song["shazam_key"] or resolved["key"], "apple_id": song["apple_id"] or resolved["apple_id"],
            "title": song["title"], "artist_name": song["artist_name"], "link": song["link"]}


def lyrics(track, country="US", language="en-US"):
    sf, resolved, page, song = _page_for(track, country, language)
    parsed = P.lyrics_sections(page["lyrics"])
    return {**_identity(song, resolved),
            "has_lyrics": song["has_lyrics"], "has_time_synced_lyrics": song["has_time_synced_lyrics"],
            "lyrics": parsed}


def audio_analysis(track, country="US", language="en-US"):
    sf, resolved, page, song = _page_for(track, country, language)
    apple = AP.song(page["apple_song"]) if page.get("apple_song") else {}
    return {**_identity(song, resolved),
            "isrc": song["isrc"], "label": song["label"], "genre": song["genre"],
            "release_date": apple.get("release_date"), "duration_ms": apple.get("duration_ms"),
            "album_name": apple.get("album_name"),
            "is_explicit": song["is_explicit"],
            "badges": page["badges"],
            "audio_analysis": P.audio_analysis(page["audio_analysis"]),
            "is_ai_generated": True if page["audio_analysis"] else None}


def popular_segments(track, country="US", language="en-US"):
    sf, resolved, page, song = _page_for(track, country, language)
    return {**_identity(song, resolved),
            "popular_segments": P.popular_segments(page["popular_segments"], page["track_length_seconds"],
                                                   page["popular_segments_description"])}


def similar(track, page=1, limit=20, country="US", language="en-US"):
    sf = storefront(country, track)
    resolved = resolve(track, country=sf, language=language)
    disc = resolved["discovery"] or discovery_track(resolved["key"], country=sf, language=language)
    list_id = P._related_list_id(disc) or f"track-similarities-id-{resolved['key']}"
    start = (page - 1) * limit
    try:
        body = cdn_list(list_id, start_from=start, page_size=limit, country=sf, language=language)
    except ShazamNotFound:
        body = {"tracks": []}
    raw = body.get("tracks") or []
    items = [P.list_track(t, rank=start + i + 1) for i, t in enumerate(raw)]
    has_more = bool(body.get("next")) or len(raw) >= limit
    return {"shazam_key": resolved["key"], "apple_id": resolved["apple_id"], "title": disc.get("title"),
            "artist_name": disc.get("subtitle"), "count": len(items), "results": items,
            "pagination": P.pagination(page, limit, has_more)}


def music_videos(track, country="US", language="en-US"):
    sf = storefront(country, track)
    resolved = resolve(track, country=sf, language=language)
    if not resolved["apple_id"]:
        raise ShazamNotFound("this song has no Apple Music record, so no videos")
    try:
        body = catalog_get(sf, f"songs/{resolved['apple_id']}/music-videos", lang=language)
        raw = body.get("data") or []
    except ShazamNotFound:
        raw = []
    items = [AP.music_video(v) for v in raw]
    return {"shazam_key": resolved["key"], "apple_id": resolved["apple_id"], "count": len(items), "results": items}
