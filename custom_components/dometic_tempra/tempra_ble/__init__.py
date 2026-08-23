"""Standalone BLE protocol layer for the Dometic Büttner Tempra TLB150.

Deliberately free of Home Assistant imports so it can be exercised from the
command line (see ``tools/tempra_dump.py``) and unit tested without a HA test
harness.

Only the pure-Python parts are re-exported here. ``TempraBleDevice`` lives in
``.device`` and is imported from there directly, so that decoding frames --
the part worth unit testing -- never pulls in bleak.
"""

from __future__ import annotations

from .const import (
    DEFAULT_AUTH_TOKEN,
    LOCAL_NAME_PATTERN,
    NOTIFY_CHAR_UUID,
    SERVICE_UUID,
    UNDECODED_COMMANDS,
    WRITE_CHAR_UUID,
)
from .models import MEASUREMENT_KEYS, TempraState
from .parser import Frame, FrameStream, decode_frame

__all__ = [
    "DEFAULT_AUTH_TOKEN",
    "LOCAL_NAME_PATTERN",
    "MEASUREMENT_KEYS",
    "NOTIFY_CHAR_UUID",
    "SERVICE_UUID",
    "UNDECODED_COMMANDS",
    "WRITE_CHAR_UUID",
    "Frame",
    "FrameStream",
    "TempraState",
    "decode_frame",
]
