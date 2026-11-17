"""Offline parser + page-extractor tests on captured payloads
(shazam/fixtures, captured 2026-09-19 from direct egress). No network.

    python -m pytest shazam/test_parsers.py -q
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shazam import parsers, refs, sigx  # noqa: E402
from shazam import pages  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name + ".json")) as f:
        return json.load(f)


# ---- value helpers ---------------------------------------------------------------

def test_value_helpers():
    assert parsers.colors("b:e1e5e8p:25231fs:372b1ft:4a4947q:595047") == {
        "background": "#e1e5e8", "primary": "#25231f", "secondary": "#372b1f",
        "tertiary": "#4a4947", "quaternary": "#595047"}
    assert parsers.colors("") is None
    assert parsers.seconds_of("02:23") == 143
    assert parsers.seconds_of("0.539") == 0.539
    assert parsers.seconds_of("x") is None
    assert parsers.clean_link("https://www.shazam.com/track/1/a?co=IN&x=1") == "https://www.shazam.com/track/1/a"
    assert parsers.key_from_link("https://www.shazam.com/track/40333609/river") == "40333609"
    assert parsers.list_id_from_link("https://cdn.shazam.com/shazam/v3/en-US/GB/web/-/tracks/track-similarities-id-1?startFrom=0") == "track-similarities-id-1"
    assert parsers.year_or_date("2001") == "2001"
    assert parsers.year_or_date("Released 2001") is None


# ---- refs ------------------------------------------------------------------------

def test_track_refs():
    assert refs.resolve_track("40333609") == {"key": "40333609"}
    assert refs.resolve_track("apple:1436677761") == {"id": "1436677761", "country": None}
    assert refs.resolve_track("hkoa10616988") == {"isrc": "HKOA10616988"}
    assert refs.resolve_track("https://www.shazam.com/track/40333609/river-flows-in-you") == {"key": "40333609"}
    assert refs.resolve_track("https://www.shazam.com/de-de/track/40333609") == {"key": "40333609"}
    assert refs.resolve_track("https://www.shazam.com/song/1436677761/river") == {"id": "1436677761", "country": None}
    assert refs.resolve_track("https://music.apple.com/gb/album/x/1436677753?i=1436677761") == {"id": "1436677761", "country": "gb"}
    with pytest.raises(ValueError):
        refs.resolve_track("https://example.com/track/1")
    with pytest.raises(ValueError):
        refs.resolve_track("not an id")
    assert refs.resolve_track_list(["40333609", "40333609", "apple:1"]) == [{"key": "40333609"}, {"id": "1", "country": None}]
    with pytest.raises(ValueError):
        refs.resolve_track_list(["HKOA10616988"])


def test_other_refs():
    assert refs.resolve_artist("https://www.shazam.com/artist/gorillaz/567072") == {"id": "567072", "country": None}
    assert refs.resolve_artist("https://www.shazam.com/artist/567072/gorillaz")["id"] == "567072"
    assert refs.resolve_artist("https://music.apple.com/us/artist/gorillaz/567072") == {"id": "567072", "country": "us"}
    assert refs.resolve_album("803680097182") == {"upc": "803680097182"}
    assert refs.resolve_album("https://www.shazam.com/album/850576570/humanz")["id"] == "850576570"
    assert refs.resolve_event("4159F761-6CF2-44DE-89FA-28EBDCA31126") == {"id": "4159f761-6cf2-44de-89fa-28ebdca31126"}
    assert refs.resolve_event("https://www.shazam.com/event/4159f761-6cf2-44de-89fa-28ebdca31126")["id"].startswith("4159")
    with pytest.raises(ValueError):
        refs.resolve_event("12345")
    assert refs.resolve_region("United States") == "united-states"
    assert refs.resolve_region("us") == "US"
    assert refs.resolve_region("Global") == "world"
    assert refs.resolve_region("https://www.shazam.com/charts/top-200/brazil") == "brazil"
    assert refs.resolve_slug("Hip-Hop/Rap") == "hip-hop-rap"
    assert refs.slugify("Música Mexicana") == "música-mexicana"


# ---- discovery objects ----------------------------------------------------------

def test_discovery_track():
    t = parsers.discovery_track(fixture("discovery_track_40333609"))
    assert t["shazam_key"] == "40333609"
    assert t["apple_id"] == "1436677761"
    assert t["title"] == "River Flows In You" and t["artist_name"] == "Yiruma"
    assert t["link"] == "https://www.shazam.com/track/40333609/river-flows-in-you"
    assert t["isrc"] == "HKOA10616988"
    assert t["genre"] == "New Age"
    assert t["label"] == "Stomp Music"
    assert t["album_apple_id"] == "1436677753"
    assert t["artists"][0]["apple_id"] == "73406786"
    assert t["colors"]["background"].startswith("#")
    assert t["hub"]["provider"] == "Apple Music"
    assert t["hub"]["preview_link"].endswith(".m4a")
    assert t["related_list_id"] == "track-similarities-id-40333609"
    assert parsers.discovery_track(None) is None


def test_list_track_and_recognition():
    items = fixture("cdn_similar_40333609")["tracks"]
    first = parsers.list_track(items[0], rank=1)
    assert first["rank"] == 1 and first["shazam_key"] and first["title"]
    assert first["link"].startswith("https://www.shazam.com/track/")
    r = parsers.recognition(fixture("recognition_response"))
    assert r["is_matched"] is True
    assert r["track"]["shazam_key"] == "40333609"
    assert r["matches"][0]["shazam_key"] and "offset_seconds" in r["matches"][0]
    assert parsers.recognition({"matches": []}) == {"is_matched": False, "matches": None, "track": None, "tag_id": None}


# ---- pages -------------------------------------------------------------------------

def test_track_page_extraction(monkeypatch):
    rows = fixture("page_rows_track_694618227")
    monkeypatch.setattr(pages.rsc, "page_rows", lambda html: rows)
    monkeypatch.setattr(pages, "get_page", lambda path: '<link rel="canonical" href="https://www.shazam.com/song/1756057163/x"/><script>self.__next_f</script>')
    page = pages.track_page("694618227")
    song = parsers.page_song(page["song"])
    assert song["shazam_key"] == "694618227" and song["apple_id"] == "1756057163"
    assert song["isrc"] == "QZJ842400387" and song["has_time_synced_lyrics"] is True
    assert song["link"] == "https://www.shazam.com/track/694618227/i-like-the-way-you-kiss-me"
    lyrics = parsers.lyrics_sections(page["lyrics"])
    assert lyrics["is_time_synced"] and lyrics["line_count"] > 10
    assert lyrics["sections"][0]["title"] == "Intro"
    assert lyrics["sections"][0]["lines"][0]["text"] == "I like the way you kiss me"
    assert lyrics["sections"][0]["lines"][0]["start_seconds"] == 0.539
    analysis = parsers.audio_analysis(page["audio_analysis"])
    assert analysis["bpm"] == 151 and 0 < analysis["energy"] <= 1
    segments = parsers.popular_segments(page["popular_segments"], page["track_length_seconds"], page["popular_segments_description"])
    assert segments["track_length_seconds"] == 143
    assert segments["most_popular"]["shazam_count"] == 1981
    assert segments["segments"][0] == {"start_seconds": 0, "end_seconds": 5, "shazam_count": 1981,
                                       "share_of_peak": 1.0, "share_of_track": 0.0667}
    assert page["badges"] == ["On Tour"]


def test_artist_page_extraction(monkeypatch):
    rows = fixture("page_rows_artist_567072")
    # 2026-10-06: concert links live in the page's ld+json MusicGroup (MusicEvent @ids), not the flight
    html = (open(os.path.join(FIXTURES, "page_artist_567072_head.html")).read()
            + '<script type="application/ld+json">{"@type":"MusicGroup","event":[{"@type":"MusicEvent",'
              '"@id":"https://www.shazam.com/event/9465ad18-3f77-4518-a058-0b625a7e3a28"}]}</script>'
            + "<script>self.__next_f</script>")
    monkeypatch.setattr(pages.rsc, "page_rows", lambda h: rows)
    # the real flight text carries no spaces after separators (the similar-artist regex relies on it)
    monkeypatch.setattr(pages.rsc, "flight_text", lambda h: json.dumps(rows, separators=(",", ":")))
    monkeypatch.setattr(pages, "get_page", lambda path: html)
    page = pages.artist_page("567072", "gorillaz")
    assert page["name"] == "Gorillaz"
    assert page["hometown"] == "London, England" and page["born"] == "1998" and page["genre"] == "Alternative"
    assert page["bio"].startswith("At first glance")
    assert any(l["label"] == "Official Website" for l in page["links"])
    assert len(page["similar_artists"]) >= 5 and page["similar_artists"][0]["apple_id"]
    assert set(page["releases"]) == {"albums", "singles", "eps", "live_albums", "compilations"}
    assert page["releases"]["albums"][0]["apple_id"]
    assert page["event_links"] and page["event_links"][0].startswith("/event/")


def test_chart_catalog_and_events(monkeypatch):
    monkeypatch.setattr(pages.rsc, "page_rows", lambda h: fixture("page_rows_charts"))
    monkeypatch.setattr(pages, "get_page", lambda path: "self.__next_f")
    catalog = pages.chart_catalog()
    assert len(catalog) >= 60
    world = next(c for c in catalog if c["slug"] == "world")
    assert world["top_playlist_id"].startswith("pl.") and world["viral_playlist_id"]
    us = next(c for c in catalog if c["code"] == "US")
    assert us["momentum_playlist_id"] and any(x["slug"] == "chicago" for x in us["cities"])
    assert any(g["slug"] == "hip-hop-rap" for g in us["genres"])

    # 2026-11-13 page shape: options[0] (the world chart) is a path reference
    # "$23:props:…:selected" to the header selector's entry; ids may be "$undefined".
    monkeypatch.setattr(pages.rsc, "page_rows", lambda h: fixture("page_rows_charts_2026_11"))
    catalog = pages.chart_catalog()
    assert len(catalog) == 71
    world = catalog[0]
    assert world["slug"] == "world" and world["code"] is None and world["name"] == "Global"
    assert world["top_playlist_id"].startswith("pl.") and world["viral_playlist_id"].startswith("pl.")
    assert any(g["slug"] == "pop" for g in world["genres"])
    algeria = next(c for c in catalog if c["code"] == "DZ")
    assert algeria["top_playlist_id"].startswith("pl.") and algeria["momentum_playlist_id"] is None
    us = next(c for c in catalog if c["code"] == "US")
    assert us["momentum_playlist_id"] and any(x["slug"] == "chicago" for x in us["cities"])
    assert not any(isinstance(v, str) and v.startswith("$") for c in catalog for v in c.values())

    monkeypatch.setattr(pages.rsc, "page_rows", lambda h: fixture("page_rows_events_india"))
    monkeypatch.setattr(pages, "get_page", lambda path: "<title>Find concerts by genres in India | Shazam</title>self.__next_f")
    page = pages.events_page("india")
    assert page["title"] == "Find concerts by genres in India"
    assert len(page["events"]) >= 10
    ev = page["events"][0]
    assert ev["id"] and ev["starts_at"].endswith("Z") and ev["venue"]["latitude"]
    assert ev["tickets"]["provider"] == "Bandsintown"
    assert ev["artists"][0]["apple_id"]
    assert any(g["name"] == "Bollywood" for g in page["genres"])


# ---- signature -------------------------------------------------------------------

def test_signature_binary_layout():
    import numpy as np
    rng = np.random.default_rng(1)
    t = np.arange(16000 * 4) / 16000
    tone = (0.4 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 1200 * t) + 0.05 * rng.standard_normal(len(t)))
    samples = (tone * 32767).astype("<i2")
    uri, ms = sigx.signature_uri(samples, max_seconds=4)
    assert uri.startswith(sigx.DATA_URI_PREFIX) and ms == 4000
    import base64
    raw = base64.b64decode(uri[len(sigx.DATA_URI_PREFIX):])
    assert raw[:4] == bytes.fromhex("8025feca")            # magic 0xCAFE2580 little-endian
    assert int.from_bytes(raw[12:16], "little") == 0x94119C00
    assert int.from_bytes(raw[8:12], "little") == len(raw) - 48
    with pytest.raises(sigx.AudioDecodeError):
        sigx.signature_uri(np.zeros(100, dtype="<i2"))
