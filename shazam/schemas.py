"""Marshmallow request schemas for every /shazam/* route.

Generic fields (strings, flags, pages, choices, comma lists, ISO codes,
urls, the id-or-link `RefField`) live in the shared top-level
schema_fields.py; this module adds the Shazam resolvers (shazam/refs.py)
and the per-route schemas. Every schema's load() output is the kwargs dict
its endpoint function takes.

ONE param per input: `track` / `artist` / `album` / `event` each accept a
bare id OR a pasted shazam.com / music.apple.com link (an ISRC for
`track`, a UPC for `album`); `country` is the Apple storefront the
metadata is read from (default US — Shazam keys, ISRCs and Apple ids are
the same everywhere); `language` the catalog language (en-US default).
"""
from marshmallow import ValidationError, missing, post_load, validate

from schema_fields import (
    BaseSchema, ChoiceField, CommaListField, CountryCodeField, LanguageCodeField,
    LimitField, NonNegativeNumber, PageField, QueryField, RefField, StrippedString, UrlField,
)
from shazam import refs
from shazam.sigx import DEFAULT_WINDOW_SECONDS, MAX_WINDOW_SECONDS, MIN_WINDOW_SECONDS


# ---- shazam id-or-link fields ---------------------------------------------------------

class TrackRefField(RefField):
    resolver = staticmethod(refs.resolve_track)


class ArtistRefField(RefField):
    resolver = staticmethod(refs.resolve_artist)


class AlbumRefField(RefField):
    resolver = staticmethod(refs.resolve_album)


class EventRefField(RefField):
    resolver = staticmethod(refs.resolve_event)


class RegionField(RefField):
    """Chart / events region: world, ISO code, slug or name."""
    resolver = staticmethod(refs.resolve_region)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", "world")
        super().__init__(**kwargs)


class SlugField(RefField):
    """City / genre by slug or name."""
    resolver = staticmethod(refs.resolve_slug)

    def __init__(self, **kwargs):
        kwargs.setdefault("required", False)
        if not kwargs["required"]:
            kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)


class TrackListField(CommaListField):
    """Comma list of track ids / links (max 25) -> resolved refs."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        # CommaListField defaults to optional (load_default=None); a required
        # field must not carry a load_default, so pass `missing` explicitly
        super().__init__(upper=False, max_items=25, load_default=missing, **kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        items = super()._deserialize(value, attr, data, **kwargs)
        if not items:
            raise ValidationError("At least one track is required.")
        try:
            return refs.resolve_track_list(items)
        except ValueError as e:
            raise ValidationError(str(e))


class WindowSeconds(NonNegativeNumber):
    """Recognition window length in seconds (3-30, default 12)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", DEFAULT_WINDOW_SECONDS)
        kwargs.setdefault("validate", validate.Range(min=MIN_WINDOW_SECONDS, max=MAX_WINDOW_SECONDS))
        super().__init__(**kwargs)


class _Localised(BaseSchema):
    # no load_default: shared.storefront() falls back to a pasted Apple
    # link's storefront, then US — a "US" default here would always win
    country = CountryCodeField()
    language = LanguageCodeField(load_default="en-US")


# ---- search -----------------------------------------------------------------------------

SEARCH_TYPES = ["all", "songs", "artists", "albums", "playlists", "music-videos"]


class SearchSchema(_Localised):
    query = QueryField()
    type = ChoiceField(SEARCH_TYPES, load_default="all")
    page = PageField(max_page=40)
    limit = LimitField(default=10, max_size=25)


class AutocompleteSchema(_Localised):
    query = QueryField(max_length=100)
    limit = LimitField(default=10, max_size=10)


# ---- songs ---------------------------------------------------------------------------------

class TrackSchema(_Localised):
    track = TrackRefField()


class TrackBatchSchema(_Localised):
    tracks = TrackListField(required=True)


class SimilarSchema(_Localised):
    track = TrackRefField()
    page = PageField(max_page=10)
    limit = LimitField(default=20, max_size=20)


# ---- artists -------------------------------------------------------------------------------

class ArtistSchema(_Localised):
    artist = ArtistRefField()


class ArtistPagedSchema(ArtistSchema):
    page = PageField(max_page=20)
    limit = LimitField(default=20, max_size=100)


ARTIST_ALBUM_TYPES = ["all", "albums", "singles", "live", "compilations", "appears-on"]


class ArtistAlbumsSchema(ArtistPagedSchema):
    type = ChoiceField(ARTIST_ALBUM_TYPES, load_default="all")


class ArtistSimilarSchema(ArtistSchema):
    limit = LimitField(default=20, max_size=25)


# ---- albums ---------------------------------------------------------------------------------

class AlbumSchema(_Localised):
    album = AlbumRefField()


# ---- charts ---------------------------------------------------------------------------------

class ChartListSchema(BaseSchema):
    country = RegionField(load_default=None)


class ChartTopSchema(_Localised):
    region = RegionField()
    city = SlugField()
    page = PageField(max_page=4)
    limit = LimitField(default=50, max_size=200)


class ChartGenreSchema(_Localised):
    genre = SlugField(required=True)
    region = RegionField()
    page = PageField(max_page=4)
    limit = LimitField(default=50, max_size=200)


class ChartRegionSchema(_Localised):
    region = RegionField()
    page = PageField(max_page=4)
    limit = LimitField(default=50, max_size=200)


class ChartMomentumSchema(ChartRegionSchema):
    """The Discovery (momentum) chart exists per country only — no world list."""
    region = RegionField(load_default="US")


# ---- events ---------------------------------------------------------------------------------

class EventsSearchSchema(BaseSchema):
    region = RegionField(load_default="US")
    city = SlugField()
    genre = StrippedString(required=False, load_default=None, validate=validate.Length(max=60))
    upcoming_only = ChoiceField(["true", "false"], load_default="true")

    @post_load
    def _flags(self, data, **kwargs):
        data["upcoming_only"] = data.get("upcoming_only") != "false"
        return data


class EventSchema(BaseSchema):
    event = EventRefField()


# ---- recognition ----------------------------------------------------------------------------

class RecognizeQuerySchema(_Localised):
    """Query params shared by the upload and the URL recognizers."""
    offset_seconds = NonNegativeNumber()
    duration_seconds = WindowSeconds()


class AudioUrlField(UrlField):
    """Required http(s) link to the clip (UrlField is optional by default)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(load_default=missing, **kwargs)


class RecognizeUrlSchema(RecognizeQuerySchema):
    url = AudioUrlField(required=True)
