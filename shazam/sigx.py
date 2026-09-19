"""Shazam audio signatures ("SigX") without a browser or a native library.

Shazam recognizes a clip from a compact fingerprint: spectral peaks picked
from a stream of 2048-sample Hanning-windowed FFTs computed every 128
samples of 16 kHz mono PCM, grouped into four frequency bands and packed
into a small binary message (magic 0xCAFE2580, CRC-32, TLV per band). The
web player's sigx.js worker and the mobile apps produce the same thing.

This module is a dependency-light port of the reference Python
implementation (shazamio / SongRec, MIT): the same peak-picking rules and
byte layout, with the per-bin loops vectorised over numpy so an 8-12 s
clip fingerprints in well under a second on one core. Decoding of the
uploaded audio is delegated to ffmpeg (any container/codec ffmpeg reads:
mp3, m4a/aac, ogg, opus, flac, wav, webm, mp4 video…), which writes raw
s16le 16 kHz mono PCM for the requested window.

    samples = decode_pcm(audio_bytes, offset_seconds=0, duration_seconds=12)
    uri, sample_ms = signature_uri(samples)   # data:audio/vnd.shazam.sig;base64,…

Validated 2026-09-19: an 8 s window cut from an Apple preview matched its
track on the discovery tag endpoint with 3 alignment matches.
"""
import base64
import os
import shutil
import struct
import subprocess
import tempfile
from binascii import crc32
from io import BytesIO

import numpy as np

SAMPLE_RATE = 16000
SAMPLE_RATE_ID = 3              # the header's enum member for 16 kHz
DATA_URI_PREFIX = "data:audio/vnd.shazam.sig;base64,"
MAX_PEAKS = 255
DEFAULT_WINDOW_SECONDS = 12     # what the reference client sends per request
MIN_WINDOW_SECONDS = 3
MAX_WINDOW_SECONDS = 30

# Frequency bands (ids as stored in the TLV); nothing below 250 Hz is kept and
# the 3.5-5.5 kHz band is unused in the legacy signature format.
_BANDS = ((250.0, 520.0, 0), (520.0, 1450.0, 1), (1450.0, 3500.0, 2))
_HANNING = np.hanning(2050)[1:-1]                       # no zero at either edge
_BINS = np.arange(10, 1015)
_NEIGHBOUR_OFFSETS = np.array([-10, -7, -4, -3, 1, 2, 5, 8])
_OTHER_FFT_OFFSETS = np.array([-53, -45, *range(165, 201, 7), *range(214, 250, 7)])
_MAGNITUDE_FLOOR = 1.0 / 64
_LOG_SCALE = 1477.3
_LOG_OFFSET = 6144


class AudioDecodeError(Exception):
    """ffmpeg could not decode the input (not audio, truncated, unsupported)."""


# ---- audio decoding ------------------------------------------------------------------

def ffmpeg_binary():
    return os.environ.get("SHAZAM_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"


def probe_duration(audio_bytes):
    """Best-effort clip length in seconds via ffprobe (None when unknown)."""
    probe = os.environ.get("SHAZAM_FFPROBE") or shutil.which("ffprobe")
    if not probe:
        return None
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(audio_bytes)
        path = tmp.name
    try:
        out = subprocess.run([probe, "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", path],
                             capture_output=True, timeout=30)
        text = (out.stdout or b"").decode("ascii", "ignore").strip()
        return float(text) if text and text != "N/A" else None
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def decode_pcm(audio_bytes, offset_seconds=0.0, duration_seconds=DEFAULT_WINDOW_SECONDS):
    """Any audio/video bytes -> int16 numpy array of 16 kHz mono PCM for the
    window [offset, offset + duration). Raises AudioDecodeError."""
    if not audio_bytes:
        raise AudioDecodeError("empty audio")
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp.write(audio_bytes)
        path = tmp.name
    cmd = [ffmpeg_binary(), "-v", "error", "-nostdin", "-i", path,
           "-ss", f"{max(0.0, float(offset_seconds)):.3f}", "-t", f"{float(duration_seconds):.3f}",
           "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise AudioDecodeError("ffmpeg is not installed on this host")
    except subprocess.TimeoutExpired:
        raise AudioDecodeError("ffmpeg timed out decoding the audio")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if out.returncode != 0 or len(out.stdout) < 2 * SAMPLE_RATE * MIN_WINDOW_SECONDS:
        detail = (out.stderr or b"").decode("utf-8", "ignore").strip().splitlines()
        detail = detail[-1] if detail else "no audio in that window"
        raise AudioDecodeError(f"could not decode audio: {detail[:200]}")
    return np.frombuffer(out.stdout, dtype="<i2")


# ---- fingerprinting ----------------------------------------------------------------

class SignatureGenerator:
    """Feed int16 samples, then `signature()` -> the peaks of the first window
    that satisfies both the time and the peak-count targets (or of all the
    input when it is shorter)."""

    def __init__(self, max_seconds=DEFAULT_WINDOW_SECONDS, max_peaks=MAX_PEAKS):
        self.max_seconds = float(max_seconds)
        self.max_peaks = max_peaks
        self.number_samples = 0
        self.peaks = {band: [] for _, _, band in _BANDS}
        self._ring = np.zeros(2048, dtype=np.float64)
        self._ring_pos = 0
        self._ffts = np.zeros((256, 1025), dtype=np.float64)
        self._fft_pos = 0
        self._spread = np.zeros((256, 1025), dtype=np.float64)
        self._spread_pos = 0
        self._written = 0

    # one 128-sample step ---------------------------------------------------------
    def _fft(self, chunk):
        n = len(chunk)
        self._ring[self._ring_pos:self._ring_pos + n] = chunk
        self._ring_pos = (self._ring_pos + n) % 2048
        excerpt = np.concatenate((self._ring[self._ring_pos:], self._ring[:self._ring_pos]))
        spectrum = np.fft.rfft(_HANNING * excerpt)
        power = (spectrum.real ** 2 + spectrum.imag ** 2) / (1 << 17)
        self._ffts[self._fft_pos] = np.maximum(power, 1e-10)
        self._fft_pos = (self._fft_pos + 1) % 256

    def _spread_peaks(self):
        last = self._ffts[(self._fft_pos - 1) % 256]
        stacked = np.stack((last, np.roll(last, -1), np.roll(last, -2)))
        spread = np.concatenate((stacked.max(axis=0)[:-3], last[-3:]))
        i1, i2, i3 = ((self._spread_pos + k) % 256 for k in (-1, -3, -6))
        rows = np.stack((spread, self._spread[i1], self._spread[i2], self._spread[i3]))
        self._spread[i1] = rows[:2].max(axis=0)
        self._spread[i2] = rows[:3].max(axis=0)
        self._spread[i3] = rows[:4].max(axis=0)
        self._spread[self._spread_pos] = spread
        self._spread_pos = (self._spread_pos + 1) % 256
        self._written += 1

    def _pick_peaks(self):
        fft46 = self._ffts[(self._fft_pos - 46) % 256]
        s49 = self._spread[(self._spread_pos - 49) % 256]
        values = fft46[_BINS]
        candidate = (values >= _MAGNITUDE_FLOOR) & (values >= s49[_BINS - 1])
        if not candidate.any():
            return
        neighbours = s49[_BINS[:, None] + _NEIGHBOUR_OFFSETS].max(axis=1)
        candidate &= values > neighbours
        if not candidate.any():
            return
        rows = self._spread[(self._spread_pos + _OTHER_FFT_OFFSETS) % 256][:, _BINS - 1]
        others = np.maximum(rows.max(axis=0), neighbours)
        candidate &= values > others
        if not candidate.any():
            return
        fft_number = self._written - 46
        for b in _BINS[candidate]:
            mag = np.log(max(_MAGNITUDE_FLOOR, fft46[b])) * _LOG_SCALE + _LOG_OFFSET
            before = np.log(max(_MAGNITUDE_FLOOR, fft46[b - 1])) * _LOG_SCALE + _LOG_OFFSET
            after = np.log(max(_MAGNITUDE_FLOOR, fft46[b + 1])) * _LOG_SCALE + _LOG_OFFSET
            variation = mag * 2 - before - after
            if variation <= 0:
                continue
            corrected_bin = b * 64 + (after - before) * 32 / variation
            frequency = corrected_bin * (SAMPLE_RATE / 2 / 1024 / 64)
            for low, high, band in _BANDS:
                if low < frequency < high:
                    self.peaks[band].append((fft_number, int(mag), int(corrected_bin)))
                    break

    def _step(self, chunk):
        self.number_samples += len(chunk)
        self._fft(chunk)
        self._spread_peaks()
        if self._written >= 46:
            self._pick_peaks()

    def feed(self, samples):
        """Consume int16 samples until the window is long enough AND has
        enough peaks (the reference client's stop rule); returns the number
        of samples used."""
        samples = np.asarray(samples, dtype=np.float64)
        used = 0
        total = len(samples) - len(samples) % 128
        while used < total and (self.number_samples / SAMPLE_RATE < self.max_seconds
                                or self.peak_count() < self.max_peaks):
            self._step(samples[used:used + 128])
            used += 128
        return used

    def peak_count(self):
        return sum(len(p) for p in self.peaks.values())

    # binary message -------------------------------------------------------------
    def encode(self):
        contents = BytesIO()
        for band in sorted(self.peaks):
            if not self.peaks[band]:           # the reference writes only bands with peaks
                continue
            peaks = BytesIO()
            last_pass = 0
            for fft_pass, magnitude, corrected_bin in self.peaks[band]:
                if fft_pass - last_pass >= 255:
                    peaks.write(b"\xff" + fft_pass.to_bytes(4, "little"))
                    last_pass = fft_pass
                peaks.write(bytes([fft_pass - last_pass]))
                peaks.write(magnitude.to_bytes(2, "little"))
                peaks.write(corrected_bin.to_bytes(2, "little"))
                last_pass = fft_pass
            raw = peaks.getvalue()
            contents.write((0x60030040 + band).to_bytes(4, "little"))
            contents.write(len(raw).to_bytes(4, "little"))
            contents.write(raw + b"\x00" * (-len(raw) % 4))
        body = contents.getvalue()
        size_minus_header = len(body) + 8
        tail = (struct.pack("<II", 0x40000000, size_minus_header) + body)
        header_rest = struct.pack("<II3I I2I I I", size_minus_header, 0x94119C00, 0, 0, 0,
                                  SAMPLE_RATE_ID << 27, 0, 0,
                                  int(self.number_samples + SAMPLE_RATE * 0.24), (15 << 19) + 0x40000)
        checksum = crc32(header_rest + tail) & 0xFFFFFFFF
        return struct.pack("<II", 0xCAFE2580, checksum) + header_rest + tail

    def uri(self):
        return DATA_URI_PREFIX + base64.b64encode(self.encode()).decode("ascii")

    def sample_ms(self):
        return int(self.number_samples / SAMPLE_RATE * 1000)


def signature_uri(samples, max_seconds=DEFAULT_WINDOW_SECONDS):
    """int16 samples -> (data URI, sample length in ms) for the tag endpoint."""
    gen = SignatureGenerator(max_seconds=max_seconds)
    gen.feed(samples)
    if gen.number_samples < SAMPLE_RATE * MIN_WINDOW_SECONDS or gen.peak_count() == 0:
        raise AudioDecodeError("the window holds too little audio to fingerprint")
    return gen.uri(), gen.sample_ms()
