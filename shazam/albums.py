"""/shazam/albums/*: an album from the Apple catalog (what shazam.com's
/album/{id} page renders client-side) with its track list stamped with
Shazam keys."""
from apple_music import parsers as AP

from shazam.fetch import ShazamNotFound, catalog_get, resource
from shazam.shared import storefront, with_shazam_keys

RELATED_VIEWS = ["related-albums", "appears-on", "other-versions"]


def _album(sf, ref, language, views=None):
    params = {"include": "tracks,artists,record-labels"}
    if views:
        params["views"] = ",".join(views)
    if ref.get("upc"):
        body = catalog_get(sf, "albums", {"filter[upc]": ref["upc"], **params}, lang=language)
        data = body.get("data") or []
        if not data:
            raise ShazamNotFound(f"no album with UPC {ref['upc']} in the '{sf}' catalog")
        return data[0]
    return resource(sf, "albums", ref["id"], params, lang=language)


def details(album, country="US", language="en-US"):
    sf = storefront(country, album)
    obj = _album(sf, album, language)
    base = AP.album(obj)
    tracks = with_shazam_keys(AP.items(AP.rel(obj, "tracks")), country=sf, language=language)
    labels = [AP.label(l) for l in AP.rel(obj, "record-labels")]
    return {
        "apple_id": base["id"],
        "name": base["name"],
        "link": f"https://www.shazam.com/album/{base['id']}",
        "apple_music_link": base["link"],
        "artist_name": base["artist_name"],
        "artists": [AP.artist(a) for a in AP.rel(obj, "artists")] or None,
        "release_type": base["release_type"],
        "record_label": base["record_label"],
        "record_labels": labels or None,
        "copyright": base["copyright"],
        "upc": base["upc"],
        "release_date": base["release_date"],
        "track_count": base["track_count"],
        "genres": base["genres"],
        "audio_traits": base["audio_traits"],
        "is_single": base["is_single"],
        "is_compilation": base["is_compilation"],
        "is_complete": base["is_complete"],
        "is_prerelease": base["is_prerelease"],
        "is_explicit": base["is_explicit"],
        "description": base["description"],
        "artwork": base["artwork"],
        "editorial_artwork": base["editorial_artwork"],
        "tracks": tracks,
    }


def related(album, country="US", language="en-US"):
    sf = storefront(country, album)
    obj = _album(sf, album, language, RELATED_VIEWS)
    out = {"apple_id": obj.get("id"), "name": (obj.get("attributes") or {}).get("name"), "country": sf.upper()}
    for view in RELATED_VIEWS:
        out[view.replace("-", "_")] = AP.items(AP.view(obj, view), typed=True) or None
    return out
