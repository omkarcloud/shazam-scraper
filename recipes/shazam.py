"""Shazam: Fastly "Client Challenge" on the server-rendered pages. A real
HEADED Chrome clears it in a few seconds and keeps `_fs_ch_cp_*` /
`_fs_cd_cp_*` cookies (~1 h, bound to the exit IP, not UA or TLS) that
plain curl_cffi replays. Headless Chrome is classed as automation and
served decoy pages, never the challenge: headed only.

GKE mints only after a curl page load came back as the challenge, through
the residential US fallback exit that was challenged (the pod's own egress
is refused outright, and a cookie minted on the box's own IP would not be
the pod's). It sends that exact proxy URL and the challenged page's path
as the variant, so the box opens the very page that was challenged (as
the in-pod mint did until 2026-10-22). An exit that shows no challenge
has nothing to mint: fail fast ("unchallenged") instead of polling."""
from browser_worker.types import CookieMint

SITE = "https://www.shazam.com"
CHALLENGE_MARKERS = ("/_fs-ch-", "Client Challenge")
CLEARANCE_COOKIE_PREFIXES = ("_fs_ch_cp_", "_fs_cd_cp_")


class Shazam(CookieMint):
    name = "shazam"
    headless = False
    variants = {"": f"{SITE}/charts/top-200/world"}
    challenge_markers = CHALLENGE_MARKERS
    require_challenge = True
    unchallenged_grace = 5.0
    timeout = 45
    poll = 0.5
    ttl_seconds = 3600                # the cookies live about an hour

    def seed_url(self, variant):
        # The variant may be the challenged page's own path ("/track/..."):
        # mint on that page, exactly as the in-pod mint navigated to it.
        if variant and variant.startswith("/"):
            return SITE + variant
        return super().seed_url(variant)

    def passed(self, cookies, driver):
        return any(name.startswith(CLEARANCE_COOKIE_PREFIXES) for name in cookies)

    def tokens(self, cookies, driver):
        return {name: value for name, value in cookies.items()
                if name.startswith(CLEARANCE_COOKIE_PREFIXES)}


RECIPE = Shazam
