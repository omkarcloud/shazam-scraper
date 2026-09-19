"""Cache TTL per /shazam/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?page=1` and no `page` share a
row). Tiers follow the other music scrapers: hours for anything ranked or
counted, a day for catalog lookups, a week for near-static profiles, a
month for text that never changes once published.
"""
from datetime import timedelta

# Apple catalog search reorders with popularity; a miss is one cheap call.
SEARCH_CACHE = timedelta(days=1)
AUTOCOMPLETE_CACHE = timedelta(days=30)
# Song metadata (ids, ISRC, label, release date) is static; the discovery
# object's hub/highlight links rotate rarely.
SONG_DETAILS_CACHE = timedelta(days=7)
SONG_BATCH_CACHE = timedelta(days=7)
# Lyrics and the AI audio analysis never change once published.
LYRICS_CACHE = timedelta(days=30)
AUDIO_ANALYSIS_CACHE = timedelta(days=30)
# Popular segments are "past 7 days" heat maps recomputed daily.
POPULAR_SEGMENTS_CACHE = timedelta(hours=12)
# Similar-song lists move with what people shazam.
SIMILAR_CACHE = timedelta(days=1)
MUSIC_VIDEOS_CACHE = timedelta(days=7)
# Artist profile (bio, hometown, links, similar) is near-static; the top
# songs and latest release inside it refresh on Apple's own cycle.
ARTIST_DETAILS_CACHE = timedelta(days=7)
ARTIST_TOP_SONGS_CACHE = timedelta(days=1)
ARTIST_ALBUMS_CACHE = timedelta(days=7)
ARTIST_SIMILAR_CACHE = timedelta(days=7)
ALBUM_CACHE = timedelta(days=7)
# Charts are "performance over the past 7 days", rebuilt daily.
CHART_CACHE = timedelta(hours=12)
# The chart catalog (countries, cities, genres and their playlist ids).
CHART_LIST_CACHE = timedelta(days=7)
# Concert listings gain new dates daily.
EVENTS_CACHE = timedelta(hours=12)
EVENT_DETAILS_CACHE = timedelta(days=1)
# Recognition is never cached (every upload is a different signature; a
# URL recognition keyed on url + window is safe for a day).
RECOGNIZE_URL_CACHE = timedelta(days=1)
