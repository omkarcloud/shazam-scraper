"""Configuration for the Shazam Scraper. Everything can be set with an
environment variable; the defaults work out of the box.

    PORT                    port the API listens on (default 8000)
    SHAZAM_PROXY            proxy URL for every request, e.g.
                            http://user:pass@host:port (default: none — direct).
                            Shazam's JSON APIs and the Apple Music catalog did
                            not rate-limit in testing, so you very likely don't
                            need this.
    SHAZAM_FALLBACK_PROXY   proxy URL used ONLY for song pages (lyrics, audio
                            analysis, popular segments) after Shazam shows a
                            bot challenge to your IP (default: none). A single
                            IP gets challenged after roughly 60 song-page loads
                            an hour; set a rotating residential proxy here if
                            you pull those endpoints at volume.
    SHAZAM_MUSICKIT_TOKEN   pin an Apple MusicKit developer token (default:
                            none — the scraper fetches Shazam's own token and
                            refreshes it before it expires).

Everything else below is a plain constant with a working default — edit it
here if you need to.
"""
import os

PORT = int(os.environ.get("PORT", "8000"))

# Retry policy for transport errors and blocks (every request).
MAX_RETRIES = 3
RETRY_BACKOFF = 2          # seconds, multiplied by the attempt number

SHAZAM_PROXY = os.environ.get("SHAZAM_PROXY") or None
SHAZAM_FALLBACK_PROXY = os.environ.get("SHAZAM_FALLBACK_PROXY") or None
SHAZAM_MUSICKIT_TOKEN = os.environ.get("SHAZAM_MUSICKIT_TOKEN") or None

# After a bot challenge, song pages go through SHAZAM_FALLBACK_PROXY for this
# many seconds, then back to a direct connection.
SHAZAM_DIRECT_COOLDOWN = 900

# Song recognition: timezone sent with every lookup, the largest file
# accepted (upload or URL), and how many 12-second windows of a clip are
# tried before giving up.
SHAZAM_TIMEZONE = "America/Chicago"
SHAZAM_MAX_AUDIO_BYTES = 30 * 1024 * 1024
SHAZAM_RECOGNIZE_WINDOWS = 3


def shazam_proxy():
    """Proxy for every request (None = direct)."""
    return os.environ.get("SHAZAM_PROXY") or None


def shazam_page_fallback_proxy():
    """Proxy for song pages after a bot challenge (None = no fallback)."""
    return os.environ.get("SHAZAM_FALLBACK_PROXY") or None
