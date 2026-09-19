"""/shazam/charts/*: Shazam's charts are Apple Music playlists now (one
`pl.…` id per country top-200, US-city top-50, genre chart, viral and
momentum list). The catalog of ids comes from the charts page
(shared.chart_catalog); the tracks from the playlist's `tracks`
relationship, ranked by position and stamped with Shazam keys."""
from apple_music import parsers as AP

from shazam.fetch import ShazamNotFound, resource
from shazam.shared import (chart_catalog, find_city, find_genre, find_region, paged, region_summary,
                           storefront, with_shazam_keys)


def list_charts(country=None):
    catalog = chart_catalog()
    if country:
        entry = find_region(country)
        return {"count": 1, "regions": [entry]}
    regions = [{**region_summary(c), "city_count": len(c["cities"]), "genre_count": len(c["genres"]),
                "cities": [x["slug"] for x in c["cities"]] or None,
                "genres": [x["slug"] for x in c["genres"]] or None} for c in catalog]
    return {"count": len(regions), "regions": regions}


def _playlist_tracks(sf, playlist_id, page, limit, language):
    path = f"/v1/catalog/{sf}/playlists/{playlist_id}/tracks"
    items, pagination = paged(path, page, limit, max_per_call=100, lang=language, numbered="rank")
    items = with_shazam_keys(items, country=sf, language=language)
    return items, pagination


def _playlist_meta(sf, playlist_id, language):
    try:
        return AP.playlist(resource(sf, "playlists", playlist_id, lang=language))
    except ShazamNotFound:
        return None


def _chart(sf, language, playlist_id, page, limit, header):
    if not playlist_id:
        raise ShazamNotFound("Shazam publishes no such chart for that region (see /shazam/charts/list)")
    items, pagination = _playlist_tracks(sf, playlist_id, page, limit, language)
    meta = _playlist_meta(sf, playlist_id, language) if page == 1 else None
    return {**header, "playlist_id": playlist_id,
            "chart_name": (meta or {}).get("name"),
            "last_updated_at": (meta or {}).get("last_modified_at"),
            "apple_music_link": (meta or {}).get("link"),
            "count": len(items), "results": items, "pagination": pagination}


def top(region="world", city=None, page=1, limit=50, country="US", language="en-US"):
    sf = storefront(country)
    entry = find_region(region)
    header = {"chart": "top", "region": region_summary(entry), "city": None}
    playlist_id = entry["top_playlist_id"]
    if city:
        c = find_city(entry, city)
        header["city"] = {k: v for k, v in c.items() if k != "playlist_id"}
        playlist_id = c["playlist_id"]
    return _chart(sf, language, playlist_id, page, limit, header)


def genre(genre, region="world", page=1, limit=50, country="US", language="en-US"):
    sf = storefront(country)
    entry = find_region(region)
    g = find_genre(entry, genre)
    header = {"chart": "genre", "region": region_summary(entry),
              "genre": {k: v for k, v in g.items() if k not in ("playlist_id", "link")}}
    return _chart(sf, language, g["playlist_id"], page, limit, header)


def viral(region="world", page=1, limit=50, country="US", language="en-US"):
    sf = storefront(country)
    entry = find_region(region)
    return _chart(sf, language, entry["viral_playlist_id"], page, limit,
                  {"chart": "viral", "region": region_summary(entry)})


def momentum(region="US", page=1, limit=50, country="US", language="en-US"):
    sf = storefront(country)
    entry = find_region(region)
    return _chart(sf, language, entry["momentum_playlist_id"], page, limit,
                  {"chart": "momentum", "region": region_summary(entry)})
