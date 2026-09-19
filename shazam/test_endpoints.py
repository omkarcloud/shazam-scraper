"""Offline endpoint tests: schema validation + upstream call routing +
payload shaping, with every transport in shazam.fetch monkeypatched to
fixtures (no network).

    python -m pytest shazam/test_endpoints.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schema_fields import load_query  # noqa: E402
from shazam import albums, artists, charts, events, fetch, pages, schemas, search, tracks  # noqa: E402
from shazam import shared  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json")) as f:
        return json.load(f)


APPLE = {
    "/v1/catalog/us/songs/1436677761": "apple_song_1436677761",
    "/v1/catalog/us/songs": "apple_songs_isrc",
    "/v1/catalog/us/artists/567072": "apple_artist_567072",
    "/v1/catalog/us/artists/567072/view/top-songs": "apple_artist_top_songs",
    "/v1/catalog/us/albums/850576570": "apple_album_850576570",
    "/v1/catalog/us/albums": "apple_album_850576570",
    "/v1/catalog/us/playlists/pl.92d704ba99a3411289a34fab82866a62/tracks": "apple_playlist_tracks",
    "/v1/catalog/us/playlists/pl.92d704ba99a3411289a34fab82866a62": "apple_playlist_meta",
    "/v1/catalog/us/search": "apple_search",
    "/v1/catalog/us/search/hints": "apple_hints",
}


@pytest.fixture
def calls(monkeypatch):
    log = {"apple": [], "discovery": [], "cdn": [], "a2st": [], "pages": []}

    def apple_get(path, params=None, *, lang=None, label=None):
        log["apple"].append((path, dict(params or {})))
        if path not in APPLE:
            raise fetch.ShazamNotFound(f"no fixture for {path}")
        return fixture(APPLE[path])

    def discovery_track(key, *, country=None, language=None):
        """The captured track for its key; the same object re-keyed for the
        synthetic keys the a2st fake mints; a 404 for an unknown 12-digit key."""
        log["discovery"].append(key)
        if key == "40333609":
            return fixture("discovery_track_40333609")
        if key.startswith("9") and len(key) == 9:
            return {**fixture("discovery_track_40333609"), "key": key}
        raise fetch.ShazamNotFound(f"track {key} not found")

    def cdn_list(list_id, *, start_from=0, page_size=20, country=None, language=None):
        log["cdn"].append((list_id, start_from, page_size))
        return fixture("cdn_similar_40333609")

    def a2st(ids, *, storefront="us", language="en"):
        """The captured map for the ids it knows; a synthetic key for any
        other id so ISRC / batch paths resolve offline."""
        log["a2st"].append(list(ids))
        table = fixture("a2st")
        return {i: table.get(i, "9" + str(i)[-8:]) for i in ids}

    monkeypatch.setattr(fetch, "apple_get", apple_get)
    monkeypatch.setattr(fetch, "discovery_track", discovery_track)
    monkeypatch.setattr(fetch, "cdn_list", cdn_list)
    monkeypatch.setattr(fetch, "apple_to_shazam_keys", a2st)
    for module in (tracks, artists, albums, charts, search, shared):
        for name in ("apple_get", "discovery_track", "cdn_list", "apple_to_shazam_keys"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, getattr(fetch, name))
    # catalog_get / resource / page_range are thin wrappers over apple_get
    monkeypatch.setattr(shared, "_catalog", {"at": 0, "value": None})
    monkeypatch.setattr(pages.rsc, "page_rows", lambda html: fixture("page_rows_charts"))
    monkeypatch.setattr(pages, "get_page", lambda path: (log["pages"].append(path), "self.__next_f")[1])
    return log


def load(schema, **query):
    data, error = load_query(schema, {k: str(v) for k, v in query.items()})
    assert error is None, error
    return data


# ---- validation --------------------------------------------------------------------

def test_schemas_reject_bad_input():
    assert "type" in load_query(schemas.SearchSchema, {"query": "a", "type": "bogus"})[1]["errors"]
    assert "foo" in load_query(schemas.SearchSchema, {"query": "a", "foo": "1"})[1]["errors"]
    assert "track" in load_query(schemas.TrackSchema, {"track": "https://example.com/x"})[1]["errors"]
    assert "tracks" in load_query(schemas.TrackBatchSchema, {})[1]["errors"]
    assert "genre" in load_query(schemas.ChartGenreSchema, {})[1]["errors"]
    assert "url" in load_query(schemas.RecognizeUrlSchema, {})[1]["errors"]
    assert "duration_seconds" in load_query(schemas.RecognizeUrlSchema, {"url": "https://x.com/a.mp3", "duration_seconds": "99"})[1]["errors"]
    assert "country" in load_query(schemas.TrackSchema, {"track": "1", "country": "zz"})[1]["errors"]


def test_schema_defaults():
    data = load(schemas.ChartTopSchema, region="United States", city="New York City")
    # country stays unset so a pasted Apple link's storefront can win (US otherwise)
    assert data == {"country": None, "language": "en-US", "region": "united-states", "city": "new-york-city",
                    "page": 1, "limit": 50}
    data = load(schemas.EventsSearchSchema, region="india", upcoming_only="false")
    assert data["region"] == "india" and data["upcoming_only"] is False
    assert shared.storefront(load(schemas.TrackSchema, track="https://music.apple.com/gb/song/x/1")["country"],
                             {"country": "gb"}) == "gb"
    assert load(schemas.TrackBatchSchema, tracks="40333609, apple:1436677761,40333609")["tracks"] == [
        {"key": "40333609"}, {"id": "1436677761", "country": None}]


# ---- songs ---------------------------------------------------------------------------

def test_song_details_by_key(calls):
    out = tracks.details(**load(schemas.TrackSchema, track="40333609"))
    assert out["shazam_key"] == "40333609" and out["apple_id"] == "1436677761"
    assert out["title"] == "River Flows In You" and out["isrc"] == "HKOA10616988"
    assert out["album"]["upc"] == "803680097182" and out["artists"][0]["name"] == "Yiruma"
    assert out["duration_ms"] and out["preview_link"] and out["colors"]["background"]
    assert calls["discovery"] == ["40333609"] and calls["apple"][0][0] == "/v1/catalog/us/songs/1436677761"


def test_song_details_by_apple_link_and_isrc(calls):
    out = tracks.details(**load(schemas.TrackSchema, track="https://music.apple.com/us/song/x/1436677761"))
    assert out["shazam_key"] == "40333609"
    assert calls["a2st"] == [["1436677761"]]
    out = tracks.details(**load(schemas.TrackSchema, track="QZJ842400387", country="US"))
    assert out["isrc"] == "QZJ842400387"


def test_song_batch(calls):
    out = tracks.batch(**load(schemas.TrackBatchSchema, tracks="40333609,apple:1217912247"))
    assert out["count"] == 2
    assert {r["shazam_key"] for r in out["results"]} == {"40333609", "347105120"}
    assert all(r["shazam_link"] for r in out["results"])


def test_similar(calls):
    out = tracks.similar(**load(schemas.SimilarSchema, track="40333609", page=2, limit=5))
    assert calls["cdn"] == [("track-similarities-id-40333609", 5, 5)]
    assert out["results"][0]["rank"] == 6 and out["pagination"]["page"] == 2


def test_unknown_key_is_404(calls):
    with pytest.raises(fetch.ShazamNotFound):
        tracks.details(**load(schemas.TrackSchema, track="999999999999"))


# ---- artists / albums ----------------------------------------------------------------

def test_artist_details(calls, monkeypatch):
    monkeypatch.setattr(pages, "artist_page", lambda aid, slug=None: {
        "name": "Gorillaz", "canonical_link": "https://www.shazam.com/artist/gorillaz/567072",
        "hometown": "London, England", "born": "1998", "formed": None, "genre": "Alternative", "bio": "bio",
        "links": [{"label": "Official Website", "link": "https://gorillaz.com"}], "similar_artists": None,
        "releases": {"albums": []}, "event_links": ["/event/x"], "badges": ["On Tour"]})
    out = artists.details(**load(schemas.ArtistSchema, artist="https://www.shazam.com/artist/gorillaz/567072"))
    assert out["apple_id"] == "567072" and out["name"] == "Gorillaz"
    assert out["hometown"] == "London, England" and out["born_or_formed"] == "1998"
    assert out["top_songs"] and out["latest_release"] and out["similar_artists"]
    assert out["is_on_tour"] is True and out["badges"] == ["On Tour"]


def test_artist_top_songs_paged(calls):
    out = artists.top_songs(**load(schemas.ArtistPagedSchema, artist="567072", page=1, limit=5))
    assert out["results"][0]["rank"] == 1 and out["count"] == 5
    assert calls["apple"][0][1] == {"offset": 0, "limit": 5}


def test_album_details(calls):
    out = albums.details(**load(schemas.AlbumSchema, album="850576570"))
    assert out["apple_id"] == "850576570" and out["track_count"] and out["tracks"]
    assert out["tracks"][0]["apple_id"] and "shazam_key" in out["tracks"][0]
    out = albums.details(**load(schemas.AlbumSchema, album="803680097182"))
    assert calls["apple"][-1][1]["filter[upc]"] == "803680097182"


# ---- search / charts / events ---------------------------------------------------------

def test_search(calls):
    out = search.search(**load(schemas.SearchSchema, query="gorillaz", limit=3))
    assert out["top_results"] and out["songs"] and out["artists"]
    assert out["songs"][0]["apple_id"]
    out = search.autocomplete(**load(schemas.AutocompleteSchema, query="kiss the"))
    assert out["suggestions"]


def test_charts(calls):
    out = charts.list_charts(**load(schemas.ChartListSchema))
    assert out["count"] >= 60
    out = charts.top(**load(schemas.ChartTopSchema, region="world", limit=5))
    assert out["results"][0]["rank"] == 1 and out["chart_name"] and out["playlist_id"].startswith("pl.")
    with pytest.raises(fetch.ShazamNotFound):
        charts.top(**load(schemas.ChartTopSchema, region="world", city="nowhere"))
    with pytest.raises(fetch.ShazamNotFound):
        charts.momentum(**load(schemas.ChartRegionSchema, region="world"))


def test_events(monkeypatch):
    monkeypatch.setattr(pages.rsc, "page_rows", lambda html: fixture("page_rows_events_india"))
    monkeypatch.setattr(pages, "get_page", lambda path: "<title>Find concerts by genres in India | Shazam</title>self.__next_f")
    monkeypatch.setattr(events, "chart_catalog", lambda: [{"code": "IN", "name": "India", "slug": "india"}])
    out = events.search(**load(schemas.EventsSearchSchema, region="IN", genre="bollywood"))
    assert out["region"] == "india" and out["count"] >= 1
    assert all("Bollywood" in (e["genres"] or []) for e in out["results"])
    with pytest.raises(ValueError):
        events.search(**load(schemas.EventsSearchSchema, region="world"))
