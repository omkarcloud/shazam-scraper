# 🎧 Shazam Scraper

Shazam Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Shazam data for free.

## ✨ What Can I Get?

- 🎧 **Recognize any song from a clip** — title, artist, ISRC, album and where in the song it plays, in about a second
- 🎵 **Full details on 100M+ songs** — ISRC, label, album, composers, preview & artwork
- 📝 **Lyrics, BPM & hot spots** — time-synced lyrics, audio analysis, the most-Shazamed seconds of any song
- 📈 **Charts in 70 countries + concerts** — Top 200, viral, genre & city charts, plus upcoming shows with venues

## 🎥 Example: A Full Shazam Song

```json
{
  "shazam_key": "503576063",
  "apple_id": "1499378607",
  "title": "Blinding Lights",
  "artist_name": "The Weeknd",
  "link": "https://www.shazam.com/track/503576063/blinding-lights",
  "isrc": "USUG11904206",
  "genre": "R&B/Soul",
  "release_date": "2019-11-29",
  "label": "Republic Records",
  "album": {
    "id": "1499378108",
    "name": "After Hours",
    "upc": "00602508831041",
    "record_label": "Republic Records",
    "release_date": "2020-03-20",
    "track_count": 14
  },
  "artists": [
    { "id": "479756766", "name": "The Weeknd", "link": "https://music.apple.com/us/artist/the-weeknd/479756766" }
  ],
  "composer_name": "Max Martin, Oscar Holter, Abel Tesfaye, Ahmad Balshe & Jason 'Daheala' Quenneville",
  "duration": "3:20",
  "has_lyrics": true,
  "preview_link": "https://audio-ssl.itunes.apple.com/itunes-assets/AudioPreview211/v4/19/d6/60/19d660ff-e3a9-8377-15a3-ce4b28e89cac/mzaf_18422426156481158187.plus.aac.p.m4a",
  "artwork": { "link": "https://is1-ssl.mzstatic.com/image/thumb/Music125/v4/6f/bc/e6/6fbce6c4-c38c-72d8-4fd0-66cfff32f679/20UMGIM12176.rgb.jpg/3000x3000bb.jpg", "width": 3000, "height": 3000 }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Shazam Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/shazam-scraper
cd shazam-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/songs/details?track=503576063"
```

```json
{
  "shazam_key": "503576063",
  "apple_id": "1499378607",
  "title": "Blinding Lights",
  "artist_name": "The Weeknd",
  "link": "https://www.shazam.com/track/503576063/blinding-lights",
  "isrc": "USUG11904206",
  "genre": "R&B/Soul",
  "release_date": "2019-11-29",
  "label": "Republic Records",
  "composer_name": "Max Martin, Oscar Holter, Abel Tesfaye, Ahmad Balshe & Jason 'Daheala' Quenneville",
  "duration": "3:20",
  "is_explicit": false,
  "has_lyrics": true,
  "album": {
    "id": "1499378108",
    "name": "After Hours",
    "upc": "00602508831041",
    "track_count": 14
  }
}
```

All 25 endpoints are now live at `http://localhost:8000`.

Song recognition needs [ffmpeg](https://ffmpeg.org/download.html) on your machine (`brew install ffmpeg` / `apt install ffmpeg`); the Docker image ships with it.

## 📚 Endpoints

25 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Song Details | `/songs/details` | Everything about one song in a single call |
| Recognize Song | `/recognize/url` | The song playing in any audio or video file |
| Autocomplete | `/search/auto-complete` | Search suggestions, exactly as the Shazam search box shows them |
| Search | `/search` | Songs, artists, albums, playlists and videos by keyword |
| Song Lyrics | `/songs/lyrics` | Full lyrics, time-synced line by line, by section |
| Song Audio Analysis | `/songs/audio-analysis` | BPM, energy, danceability, valence, acousticness, melodicness |
| Song Popular Segments | `/songs/popular-segments` | The most-Shazamed seconds of a song this week |
| Similar Songs / Song Music Videos | `/songs/similar`, `/songs/music-videos` | "You may also like" songs and official videos |
| Songs by IDs | `/songs/batch` | Up to 25 songs in one call |
| Artist Details | `/artists/details` | Bio, hometown, top songs, albums, similar artists, tour |
| Artist Top Songs / Albums / Videos | `/artists/top-songs`, `/artists/albums`, `/artists/music-videos` | Ranked hits, every release, every video |
| Similar Artists / Artist Concerts | `/artists/similar`, `/artists/events` | Related artists and upcoming shows |
| Album Details / Related Albums | `/albums/details`, `/albums/related` | Full track list with label and UPC, plus related releases |
| Top / Genre / Viral / Discovery Charts | `/charts/top`, `/charts/genre`, `/charts/viral`, `/charts/momentum` | Ranked Shazam charts for the world, countries and cities |
| Chart List | `/charts/list` | Every chart region, city and genre |
| Search Concerts / Concert Details | `/events/search`, `/events/details` | Upcoming shows with venues, times and tickets |

Bonus for the local API: `POST /recognize/file` recognizes an uploaded clip (multipart field `file`).

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-shazam-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-shazam-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-shazam-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/songs/details",
    params={"track": "503576063"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Shazam Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Shazam%20Scraper%20API.)

[![Ask Us by Email about Shazam Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Shazam%20Scraper%20API&body=I%20need%20help%20using%20the%20Shazam%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**IMDb Scraper**](https://github.com/omkarcloud/imdb-scraper) — movies & TV: ratings, cast, box office, reviews & all 6 IMDb charts
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**Etsy Scraper**](https://www.omkar.cloud/tools/etsy-scraper) — Etsy products: prices, discounts, shops & variations

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/shazam-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/shazam-scraper)
