"""/shazam/search and /shazam/search/auto-complete: the Apple catalog
search shazam.com's own search box runs (through the site's MusicKit
token), with every song stamped with its Shazam key."""
from apple_music import parsers as AP

from shazam import parsers as P
from shazam.fetch import catalog_get
from shazam.shared import offset_of, storefront, with_shazam_keys

# public type -> Apple `types` token
_TYPES = {"songs": "songs", "artists": "artists", "albums": "albums",
          "playlists": "playlists", "music-videos": "music-videos"}
_ALL = ["songs", "artists", "albums", "playlists", "music-videos"]


def _section(results, kind):
    block = (results or {}).get(kind) or {}
    return block.get("data") or [], bool(block.get("next"))


def search(query, type="all", page=1, limit=10, country="US", language="en-US"):
    sf = storefront(country)
    offset = offset_of(page, limit)
    wanted = _ALL if type == "all" else [_TYPES[type]]
    body = catalog_get(sf, "search", {"term": query, "types": ",".join(wanted), "limit": limit,
                                      "offset": offset, "with": "topResults" if type == "all" else None},
                       lang=language)
    results = body.get("results") or {}
    out = {"query": query, "type": type, "country": sf.upper(), "language": language}
    has_more = False
    if type == "all":
        top, _ = _section(results, "topResults")
        out["top_results"] = AP.items(top, typed=True)
        for kind in _ALL:
            data, more = _section(results, kind)
            items = AP.items(data)
            if kind == "songs":
                items = with_shazam_keys(items, country=sf, language=language)
            out[kind.replace("-", "_")] = items
            has_more = has_more or more
        out["count"] = sum(len(out[k.replace("-", "_")]) for k in _ALL)
    else:
        data, has_more = _section(results, wanted[0])
        items = AP.items(data)
        if type == "songs":
            items = with_shazam_keys(items, country=sf, language=language)
        out["results"] = items
        out["count"] = len(items)
    out["pagination"] = P.pagination(page, limit, has_more)
    return out


def autocomplete(query, limit=10, country="US", language="en-US"):
    sf = storefront(country)
    body = catalog_get(sf, "search/hints", {"term": query, "limit": limit}, lang=language)
    terms = ((body.get("results") or {}).get("terms") or [])
    return {"query": query, "country": sf.upper(), "count": len(terms), "suggestions": terms}
