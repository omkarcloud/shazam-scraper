"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json.
"""
import json
import os

from shazam import charts, refs, tracks

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # a Shazam key, apple:<Apple song id>, an ISRC or any shazam.com / music.apple.com song link
    blinding_lights = refs.resolve_track("503576063")
    save("song_blinding_lights.json", tracks.details(blinding_lights))

    # time-synced lyrics, grouped into Verse / Chorus / Bridge sections
    save("lyrics_blinding_lights.json", tracks.lyrics(blinding_lights))

    # Shazam's global Top 200, first 50 ranked songs
    save("chart_top_world.json", charts.top(region="world", limit=50))
