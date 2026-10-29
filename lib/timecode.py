"""SMPTE timecode helpers: Open Clip <startTimecode> ticks <-> "HH:MM:SS:FF" text, and the
packed BCD value OpenEXR stores in its smpte:TimeCode header attribute.

A timecode is a separate concept from a frame number: nothing here converts one into the
other. `rate` is the integer timecode rate Flame writes in <startTimecode><rate> (24 for
23.976, 30 for 29.97); `ticks` (<nbTicks>) counts real frames from 00:00:00:00.

Drop-frame (";" before the frames field) exists only at rates 30 and 60. Drop-frame ticks are
taken as real frame counts, the usual SMPTE meaning; no real Flame drop-frame clip has been
checked (UNVERIFIED).
"""
from __future__ import annotations

import re

ZERO = "00:00:00:00"

_TC_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})([:;.,])(\d{2,3})$")
_DROP_FRAMES = {30: 2, 60: 4}


def format_tc(ticks: int, rate: int, drop: bool = False) -> str:
    """Ticks since 00:00:00:00 -> 'HH:MM:SS:FF' ('HH:MM:SS;FF' for drop-frame)."""
    if rate < 1:
        raise ValueError(f"timecode rate must be >= 1, got {rate}")
    if ticks < 0:
        raise ValueError(f"timecode ticks must be >= 0, got {ticks}")
    if drop:
        ticks = _drop_ticks_to_label_ticks(ticks, rate)
    h, m, s, f = _split(ticks, rate)
    return f"{h:02d}:{m:02d}:{s:02d}{';' if drop else ':'}{f:02d}"


def parse_tc(text: str, rate: int) -> tuple[int, bool]:
    """'HH:MM:SS:FF' or 'HH:MM:SS;FF' -> (ticks, drop). Raises ValueError on bad text."""
    m = _TC_RE.match(text.strip())
    if not m:
        raise ValueError(f"timecode '{text}' is not HH:MM:SS:FF (or HH:MM:SS;FF for drop-frame)")
    h, mi, s, sep, f = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), int(m.group(5))
    drop = sep in ";,"
    if mi > 59 or s > 59 or f >= rate:
        raise ValueError(f"timecode '{text}' is out of range at {rate} fps")
    if drop:
        if rate not in _DROP_FRAMES:
            raise ValueError(f"timecode '{text}': drop-frame exists only at 29.97/59.94 fps (rate 30/60), not {rate}")
        d = _DROP_FRAMES[rate]
        if s == 0 and mi % 10 != 0 and f < d:
            raise ValueError(f"timecode '{text}' does not exist in drop-frame (frames 0-{d - 1} are skipped)")
        total_minutes = 60 * h + mi
        ticks = ((h * 3600 + mi * 60 + s) * rate + f) - d * (total_minutes - total_minutes // 10)
        return ticks, True
    return (h * 3600 + mi * 60 + s) * rate + f, False


def normalize(text: str, rate: int) -> str:
    """Canonical form of a timecode string (validates it)."""
    ticks, drop = parse_tc(text, rate)
    return format_tc(ticks, rate, drop)


def tc_rate(fps: float) -> int:
    """Integer timecode rate for a frame rate (23.976 -> 24, 29.97 -> 30)."""
    return max(1, round(fps))


def smpte_bcd(ticks: int, rate: int, drop: bool = False) -> tuple[int, int] | None:
    """OpenEXR smpte:TimeCode value (time-and-flags, user-data) for one frame, or None when
    the frame number can't be stored: the BCD frames field holds 0-39, so rates above 30
    (48/50/59.94/60) are not written."""
    if rate > 30:
        return None
    if drop:
        ticks = _drop_ticks_to_label_ticks(ticks, rate)
    h, m, s, f = _split(ticks, rate)
    h %= 24
    value = (
        (f % 10) | ((f // 10) << 4) | ((1 << 6) if drop else 0)
        | ((s % 10) << 8) | ((s // 10) << 12)
        | ((m % 10) << 16) | ((m // 10) << 20)
        | ((h % 10) << 24) | ((h // 10) << 28)
    )
    return value, 0


def _split(ticks: int, rate: int) -> tuple[int, int, int, int]:
    f = ticks % rate
    total_s = ticks // rate
    return total_s // 3600, (total_s // 60) % 60, total_s % 60, f


def _drop_ticks_to_label_ticks(ticks: int, rate: int) -> int:
    """Real frame count -> the frame count the drop-frame label shows (labels skip numbers)."""
    if rate not in _DROP_FRAMES:
        raise ValueError(f"drop-frame exists only at rate 30/60, not {rate}")
    d = _DROP_FRAMES[rate]
    per_10min = rate * 600 - 9 * d
    per_min = rate * 60 - d
    tens, rem = divmod(ticks, per_10min)
    extra = 9 * d * tens
    if rem > d:
        extra += d * ((rem - d) // per_min)
    return ticks + extra
