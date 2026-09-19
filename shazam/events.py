"""/shazam/events/*: concerts shazam.com lists per country / city (fed by
Bandsintown) and single event pages with venue and tickets."""
from shazam import pages
from shazam.fetch import ShazamNotFound
from shazam.refs import slugify
from shazam.shared import chart_catalog

# Countries the events section names differently from the chart catalog.
_ALIASES = {"world": None, "GB": "united-kingdom", "US": "united-states", "AE": "united-arab-emirates"}


def _country_slug(token):
    """ISO code / name / slug -> the /events/{slug} the site uses."""
    if not token or token == "world":
        raise ValueError("region must be a country (events are listed per country)")
    if token in _ALIASES:
        return _ALIASES[token]
    if len(token) == 2 and token.isupper():
        for c in chart_catalog():
            if c["code"] == token:
                return c["slug"] or slugify(c["name"])
        raise ShazamNotFound(f"unknown country code {token}")
    return slugify(token)


def search(region="US", city=None, genre=None, upcoming_only=True):
    country_slug = _country_slug(region)
    page = pages.events_page(country_slug, city)
    items = page["events"]
    if genre:
        wanted = genre.strip().lower()
        items = [e for e in items if any(wanted in (g or "").lower() for g in e.get("genres") or [])]
    if upcoming_only:
        items = [e for e in items if e.get("is_upcoming") is not False]
    items.sort(key=lambda e: e.get("starts_at") or "")
    return {"region": country_slug, "city": city, "title": page["title"], "genre_filter": genre,
            "genres": page["genres"], "count": len(items), "results": items}


def details(event):
    return pages.event_page(event["id"])
