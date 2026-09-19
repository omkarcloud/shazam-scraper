"""/shazam/artists/*: the Apple catalog artist (which is what shazam.com
renders) plus the profile extras only the artist page carries (bio,
hometown, born/formed, official links, similar artists, releases by type,
concert links)."""
from apple_music import parsers as AP

from shazam import pages
from shazam import parsers as P
from shazam.fetch import catalog_get, gather, resource
from shazam.shared import paged, storefront, with_shazam_keys

DETAIL_VIEWS = ["top-songs", "latest-release", "similar-artists", "top-music-videos", "full-albums"]
_ALBUM_VIEWS = {"albums": "full-albums", "singles": "singles", "live": "live-albums",
                "compilations": "compilation-albums", "appears-on": "appears-on-albums"}


def _apple_artist(sf, artist_id, language, views=None):
    params = {"views": ",".join(views)} if views else None
    return resource(sf, "artists", artist_id, params, lang=language)


def _slug(name):
    from shazam.refs import slugify
    return slugify(name) if name else None


def details(artist, country="US", language="en-US"):
    sf = storefront(country, artist)
    artist_id = artist["id"]
    got = gather({
        "apple": lambda: _apple_artist(sf, artist_id, language, DETAIL_VIEWS),
        "page": lambda: pages.artist_page(artist_id),
    })
    apple_obj = got["apple"]
    if isinstance(apple_obj, Exception):
        raise apple_obj
    page = got["page"] if isinstance(got["page"], dict) else {}
    base = AP.artist(apple_obj)
    top = with_shazam_keys(AP.items(AP.view(apple_obj, "top-songs")), country=sf, language=language)
    latest = next((AP.album(a) for a in AP.view(apple_obj, "latest-release")), None)
    similar_apple = [AP.artist(a) for a in AP.view(apple_obj, "similar-artists")]
    videos = [AP.music_video(v) for v in AP.view(apple_obj, "top-music-videos")]
    albums = [AP.album(a) for a in AP.view(apple_obj, "full-albums")]
    born = page.get("born") or page.get("formed")
    return {
        "apple_id": base["id"],
        "name": base["name"],
        "link": page.get("canonical_link") or f"https://www.shazam.com/artist/{_slug(base['name']) or 'artist'}/{base['id']}",
        "apple_music_link": base["link"],
        "genres": base["genres"],
        "genre": page.get("genre"),
        "hometown": page.get("hometown"),
        "origin": base["origin"],
        "born_or_formed": P.year_or_date(born) or base["born_or_formed"],
        "is_group": base["is_group"] if base["is_group"] is not None else (True if page.get("formed") else None),
        "bio": page.get("bio") or base["bio"],
        "badges": page.get("badges"),
        "links": page.get("links"),
        "artwork": base["artwork"],
        "editorial_artwork": base["editorial_artwork"],
        "latest_release": latest,
        "top_songs": top or None,
        "albums": albums or None,
        "releases": page.get("releases"),
        "top_music_videos": videos or None,
        "similar_artists": similar_apple or page.get("similar_artists"),
        "upcoming_event_links": ["https://www.shazam.com" + e for e in page.get("event_links") or []] or None,
        "is_on_tour": bool(page.get("event_links")),
    }


def top_songs(artist, page=1, limit=20, country="US", language="en-US"):
    sf = storefront(country, artist)
    items, pagination = paged(f"/v1/catalog/{sf}/artists/{artist['id']}/view/top-songs", page, limit,
                              max_per_call=100, lang=language, numbered="rank")
    items = with_shazam_keys(items, country=sf, language=language)
    return {"artist_apple_id": artist["id"], "country": sf.upper(), "count": len(items),
            "results": items, "pagination": pagination}


def albums(artist, type="all", page=1, limit=20, country="US", language="en-US"):
    sf = storefront(country, artist)
    if type == "all":
        path = f"/v1/catalog/{sf}/artists/{artist['id']}/albums"
    else:
        path = f"/v1/catalog/{sf}/artists/{artist['id']}/view/{_ALBUM_VIEWS[type]}"
    items, pagination = paged(path, page, limit, max_per_call=100, lang=language)
    return {"artist_apple_id": artist["id"], "type": type, "country": sf.upper(), "count": len(items),
            "results": items, "pagination": pagination}


def similar(artist, limit=20, country="US", language="en-US"):
    sf = storefront(country, artist)
    body = catalog_get(sf, f"artists/{artist['id']}/view/similar-artists", {"limit": limit}, lang=language)
    items = [AP.artist(a) for a in body.get("data") or []]
    return {"artist_apple_id": artist["id"], "country": sf.upper(), "count": len(items), "results": items}


def music_videos(artist, page=1, limit=20, country="US", language="en-US"):
    sf = storefront(country, artist)
    items, pagination = paged(f"/v1/catalog/{sf}/artists/{artist['id']}/music-videos", page, limit,
                              max_per_call=100, lang=language)
    return {"artist_apple_id": artist["id"], "country": sf.upper(), "count": len(items),
            "results": items, "pagination": pagination}


def events(artist, country="US", language="en-US"):
    """Concerts linked from the artist page, each expanded from its own
    event page (venue, tickets, line-up)."""
    page = pages.artist_page(artist["id"])
    ids = [link.rsplit("/", 1)[-1] for link in page.get("event_links") or []]
    items = pages.event_pages(ids, limit=12) if ids else []
    items.sort(key=lambda e: e.get("starts_at") or "")
    return {"artist_apple_id": artist["id"], "artist_name": page.get("name"), "count": len(items),
            "results": items}
