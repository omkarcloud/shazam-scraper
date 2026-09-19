"""Helpers shared by the Shazam endpoint modules: storefront/locale choice,
Apple catalog paging in the route's page/limit terms, and the in-process
lookup tables (the chart catalog) several routes resolve names against."""
import threading
import time

from apple_music import parsers as AP

from shazam import pages
from shazam import parsers as P
from shazam.fetch import ShazamNotFound, apple_to_shazam_keys, page_range, run_parallel
from shazam.refs import slugify

LOOKUP_TTL = 6 * 3600          # seconds the chart catalog is reused in-process


def storefront(country=None, ref=None):
    """Explicit `country` wins, then a pasted Apple link's storefront, then US."""
    return (country or (ref or {}).get("country") or "US").lower()


def offset_of(page, limit):
    return (page - 1) * limit


def paged(path, page, limit, *, max_per_call=100, lang=None, params=None, typed=False, numbered=None):
    """One page of an offset-paged Apple collection -> (parsed items, pagination)."""
    offset = offset_of(page, limit)
    raw, has_more = page_range(path, offset, limit, max_per_call=max_per_call, lang=lang, params=params)
    parsed = AP.items(raw, typed=typed)
    if numbered:
        parsed = [{numbered: offset + i + 1, **x} for i, x in enumerate(parsed)]
    return parsed, P.pagination(page, limit, has_more)


def shazam_keys_for(apple_ids, *, country="us", language="en-US"):
    """{Apple song id: Shazam key} for the ids given. The a2st map answers
    under the storefront's OWN Apple id for a song (an album re-issue's id
    comes back as the canonical single's id), so entries that name an id
    we did not ask for are aligned back: a lone stray answer for a lone
    unmatched input is taken as its mapping, anything else is re-asked one
    id at a time (parallel)."""
    ids = [str(i) for i in dict.fromkeys(apple_ids) if i]
    if not ids:
        return {}
    raw = apple_to_shazam_keys(ids, storefront=country, language=language)
    out = {i: raw[i] for i in ids if i in raw}
    unmatched = [i for i in ids if i not in out]
    stray = [k for k in raw if k not in ids]
    if len(unmatched) == 1 and len(stray) == 1:
        out[unmatched[0]] = raw[stray[0]]
        return out
    if unmatched:
        def one(i):
            def run():
                try:
                    answer = apple_to_shazam_keys([i], storefront=country, language=language)
                except Exception:
                    return None
                return next(iter(answer.values()), None)
            return run
        for i, key in zip(unmatched, run_parallel([one(i) for i in unmatched])):
            if key:
                out[i] = key
    return out


def with_shazam_keys(songs, *, country="us", language="en-US"):
    """Stamp `shazam_key` + `shazam_link` on Apple song shapes."""
    keys = shazam_keys_for([s.get("id") for s in songs], country=country, language=language)
    out = []
    for song in songs:
        key = keys.get(str(song.get("id")))
        out.append({"shazam_key": key, "apple_id": song.get("id"),
                    "shazam_link": f"https://www.shazam.com/track/{key}" if key else None,
                    **{k: v for k, v in song.items() if k != "id"}})
    return out


# ---- chart catalog ---------------------------------------------------------------

_lock = threading.Lock()
_catalog = {"at": 0, "value": None}


def chart_catalog():
    now = time.time()
    with _lock:
        if _catalog["value"] and _catalog["at"] + LOOKUP_TTL > now:
            return _catalog["value"]
    value = pages.chart_catalog()
    with _lock:
        _catalog.update(at=now, value=value)
    return value


def find_region(token):
    """'world' | ISO code | slug | name -> the catalog entry (NotFound otherwise)."""
    catalog = chart_catalog()
    token = (token or "world").strip()
    low = token.lower()
    for c in catalog:
        if (low == "world" and c["slug"] == "world") or (c["code"] and c["code"].lower() == low):
            return c
    for c in catalog:
        if c["slug"] == low or (c["name"] or "").lower() == low:
            return c
    raise ShazamNotFound(f"no Shazam chart for region '{token}' (see /shazam/charts/list)")


def _match(items, token, what, region):
    wanted = slugify(token)
    for item in items or []:
        if slugify(item.get("slug")) == wanted or slugify(item.get("name")) == wanted:
            return item
    raise ShazamNotFound(f"no {what} chart '{token}' for {region['name']} (see /shazam/charts/list)")


def find_city(region, city):
    return _match(region.get("cities"), city, "city", region)


def find_genre(region, genre):
    return _match(region.get("genres"), genre, "genre", region)


def region_summary(entry):
    """A chart-catalog entry without its city/genre lists (for chart headers)."""
    return {k: v for k, v in entry.items() if k not in ("cities", "genres", "link")}
