"""The 25 Shazam endpoints (+ song recognition by file upload). Every path is
served with and without the `/shazam` prefix, so code generated against the
hosted API on RapidAPI (paths like /songs/details) runs unchanged against this
server.

Params are validated by the marshmallow schemas in shazam/schemas.py: ONE
param per input — `track` / `artist` / `album` / `event` accept an id OR a
pasted shazam.com / music.apple.com link. Paged endpoints answer with the
same flat pagination block the hosted API uses (count / per_page /
current_page / total_pages / next / previous).
"""
import json
from urllib.parse import urlencode

from bottle import request, response, route

import config
from schema_fields import load_query
from scraper_errors import BadRequest, NotFound
from shazam import albums, artists, charts, events, recognize, schemas, search, tracks


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query string as unicode (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Beyoncé" would arrive as "BeyoncÃ©")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def _page_link(params, page):
    if not page:
        return None
    query = {k: v for k, v in params.items() if v not in (None, "")}
    query["page"] = page
    parts = request.urlparts
    return f"{parts.scheme}://{parts.netloc}{parts.path}?{urlencode(query)}"


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def paginate(result, params):
    """Lift the scraper's `pagination` block into the flat shape, with
    next/previous links built from the caller's own query."""
    pagination = result.pop("pagination", None) or {}
    result.pop("count", None)
    page = _as_int(pagination.get("page")) or _as_int(params.get("page")) or 1
    total_pages = max(_as_int(pagination.get("total_pages")), 0)
    out = {
        "count": pagination.get("total_count"),
        "per_page": pagination.get("items_per_page"),
        "current_page": page,
        "total_pages": total_pages,
        "next": _page_link(params, page + 1 if page < total_pages else None),
        "previous": _page_link(params, page - 1 if page > 1 else None),
    }
    out.update(result)
    return out


def call(label, schema, impl, paginated=False):
    """Validate the query, run the scraper, map failures:
    bad params -> 400, missing entity -> 404, anything else -> 500."""
    params = query_dict()
    data, error = load_query(schema, params)
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"shazam rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:                 # retries exhausted / blocked
        return json_response({"error": f"shazam {label} failed: {e}"}, 500)
    if paginated:
        result = paginate(result, params)
    return json_response(result)


def mount(path, schema, impl, paginated=False):
    """Serve a GET handler at /path and /shazam/path."""
    def handler():
        return call(path.strip("/"), schema, impl, paginated)
    handler.__name__ = "shazam_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route("/shazam" + path, method="GET")(handler)


ENDPOINTS = [
    ("/songs/details", schemas.TrackSchema, tracks.details, False),
    ("/recognize/url", schemas.RecognizeUrlSchema, recognize.from_url, False),
    ("/search/auto-complete", schemas.AutocompleteSchema, search.autocomplete, False),
    ("/search", schemas.SearchSchema, search.search, True),
    ("/songs/lyrics", schemas.TrackSchema, tracks.lyrics, False),
    ("/songs/audio-analysis", schemas.TrackSchema, tracks.audio_analysis, False),
    ("/songs/popular-segments", schemas.TrackSchema, tracks.popular_segments, False),
    ("/songs/similar", schemas.SimilarSchema, tracks.similar, True),
    ("/songs/music-videos", schemas.TrackSchema, tracks.music_videos, False),
    ("/songs/batch", schemas.TrackBatchSchema, tracks.batch, False),
    ("/artists/details", schemas.ArtistSchema, artists.details, False),
    ("/artists/top-songs", schemas.ArtistPagedSchema, artists.top_songs, True),
    ("/artists/albums", schemas.ArtistAlbumsSchema, artists.albums, True),
    ("/artists/similar", schemas.ArtistSimilarSchema, artists.similar, False),
    ("/artists/music-videos", schemas.ArtistPagedSchema, artists.music_videos, True),
    ("/artists/events", schemas.ArtistSchema, artists.events, False),
    ("/albums/details", schemas.AlbumSchema, albums.details, False),
    ("/albums/related", schemas.AlbumSchema, albums.related, False),
    ("/charts/top", schemas.ChartTopSchema, charts.top, True),
    ("/charts/genre", schemas.ChartGenreSchema, charts.genre, True),
    ("/charts/viral", schemas.ChartRegionSchema, charts.viral, True),
    ("/charts/momentum", schemas.ChartMomentumSchema, charts.momentum, True),
    ("/charts/list", schemas.ChartListSchema, charts.list_charts, False),
    ("/events/search", schemas.EventsSearchSchema, events.search, False),
    ("/events/details", schemas.EventSchema, events.details, False),
]

for _path, _schema, _impl, _paginated in ENDPOINTS:
    mount(_path, _schema, _impl, _paginated)


@route("/recognize/file", method="POST")
@route("/shazam/recognize/file", method="POST")
def recognize_file():
    """Identify a song from an uploaded clip: multipart/form-data with the
    audio under `file` (any format ffmpeg reads). The window params
    (offset_seconds, duration_seconds, country, language) ride in the query
    string like /recognize/url."""
    data, error = load_query(schemas.RecognizeQuerySchema, query_dict())
    if error:
        return json_response(error, 400)
    upload = request.files.get("file") or request.files.get("audio") or request.files.get("upload_file")
    if upload is None:
        return json_response({"error": "Missing file: send the audio as multipart form field `file`"}, 400)
    try:
        audio = upload.file.read(config.SHAZAM_MAX_AUDIO_BYTES + 1)
        return json_response(recognize.from_bytes(audio, source=upload.filename or None, **data))
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"shazam rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Exception as e:
        return json_response({"error": f"shazam recognize/file failed: {e}"}, 500)


@route("/", method="GET")
@route("/health", method="GET")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS] + ["POST /recognize/file"]})
