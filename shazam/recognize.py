"""/shazam/recognize/*: identify a song from audio.

The upload (or the file behind a URL) is decoded by ffmpeg into 16 kHz
mono PCM, fingerprinted with sigx.py and POSTed to the discovery tag
endpoint. Without an explicit `offset_seconds` the clip is tried in up to
config.SHAZAM_RECOGNIZE_WINDOWS 12 s windows — the middle first for long
recordings (the reference client's heuristic), then from the start — and
the first window with a match wins. The result carries the discovery
track (Shazam key, Apple id, ISRC, artwork, preview) plus the alignment
matches (offset of the clip inside the song, time / frequency skew).
"""
import config
from shazam import parsers as P
from shazam import sigx
from shazam.fetch import ShazamBadRequest, download_session, recognize_signature
from shazam.sigx import AudioDecodeError

DOWNLOAD_TIMEOUT = 60


def _windows(total_seconds, offset_seconds, duration_seconds):
    if offset_seconds is not None:
        return [float(offset_seconds)]
    tries = max(1, int(getattr(config, "SHAZAM_RECOGNIZE_WINDOWS", 3)))
    starts = []
    if total_seconds and total_seconds > 3 * duration_seconds:
        starts.append(max(0.0, total_seconds / 2 - duration_seconds / 2))
    cursor = 0.0
    while len(starts) < tries:
        if cursor not in starts and (total_seconds is None or cursor < total_seconds - sigx.MIN_WINDOW_SECONDS):
            starts.append(cursor)
        cursor += duration_seconds
        if total_seconds is not None and cursor >= total_seconds:
            break
    return starts or [0.0]


def from_bytes(audio_bytes, *, offset_seconds=None, duration_seconds=sigx.DEFAULT_WINDOW_SECONDS,
               country="US", language="en-US", source=None):
    if not audio_bytes:
        raise ShazamBadRequest("no audio received")
    limit = getattr(config, "SHAZAM_MAX_AUDIO_BYTES", 30 * 1024 * 1024)
    if len(audio_bytes) > limit:
        raise ShazamBadRequest(f"audio is larger than {limit // (1024 * 1024)} MB")
    total = sigx.probe_duration(audio_bytes)
    attempts = []
    result = None
    for start in _windows(total, offset_seconds, duration_seconds):
        try:
            samples = sigx.decode_pcm(audio_bytes, start, duration_seconds)
            uri, sample_ms = sigx.signature_uri(samples)
        except AudioDecodeError as e:
            if not attempts:
                raise ShazamBadRequest(str(e))
            break
        body = recognize_signature(uri, sample_ms, country=country, language=language)
        parsed = P.recognition(body)
        attempts.append({"offset_seconds": round(start, 3), "duration_seconds": round(sample_ms / 1000, 3),
                         "is_matched": parsed["is_matched"]})
        if parsed["is_matched"]:
            result = parsed
            break
    if result is None:
        result = {"is_matched": False, "matches": None, "track": None, "tag_id": None}
    return {
        "is_matched": result["is_matched"],
        "source": source,
        "audio": {"bytes": len(audio_bytes), "duration_seconds": round(total, 3) if total else None},
        "attempts": attempts,
        "matches": result["matches"],
        "track": result["track"],
    }


def from_url(url, offset_seconds=None, duration_seconds=sigx.DEFAULT_WINDOW_SECONDS,
             country="US", language="en-US"):
    limit = getattr(config, "SHAZAM_MAX_AUDIO_BYTES", 30 * 1024 * 1024)
    try:
        resp = download_session().get(url, timeout=DOWNLOAD_TIMEOUT, stream=True,
                              headers={"accept": "*/*", "user-agent": "Mozilla/5.0"})
    except Exception as e:
        raise ShazamBadRequest(f"could not download the audio: {type(e).__name__}: {e}")
    try:
        if resp.status_code != 200:
            raise ShazamBadRequest(f"the audio URL answered HTTP {resp.status_code}")
        chunks, size = [], 0
        for chunk in resp.iter_content():
            size += len(chunk)
            if size > limit:
                raise ShazamBadRequest(f"audio is larger than {limit // (1024 * 1024)} MB")
            chunks.append(chunk)
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return from_bytes(b"".join(chunks), offset_seconds=offset_seconds, duration_seconds=duration_seconds,
                      country=country, language=language, source=url)
