"""Framing and decoding of the Tempra TLB150 notify stream.

The battery emits fixed-length ``23 85 CF <cmd> <payload:4>`` frames on the
notify characteristic, interleaved with ASCII ``MST+...`` replies to the
handshake. A single BLE notification may carry several frames, a partial
frame, or an ASCII reply -- so the byte stream is reassembled and resynced on
the sync header rather than assuming one notification equals one frame.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CMD_CAPACITY,
    CMD_CELLS_1_2,
    CMD_CELLS_3_4,
    CMD_SOC,
    CMD_SOH,
    CMD_VOLTAGE_CURRENT,
    FRAME_LEN,
    MAX_CAPACITY_AH,
    MAX_CELL_MV,
    MAX_PACK_CURRENT,
    MAX_PACK_VOLTAGE,
    MIN_CELL_MV,
    SYNC_HEADER,
)

#: Cap on buffered bytes. A frame is 8 bytes and notifications are small; this
#: only exists so a stream that never yields a sync header cannot grow without
#: bound.
_MAX_BUFFER = 512


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded-from-the-wire telemetry frame."""

    cmd: int
    payload: bytes


class FrameStream:
    """Reassembles notify chunks into frames, resyncing on the sync header."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        """Append received bytes and return every complete frame in them."""
        self._buf += data
        if len(self._buf) > _MAX_BUFFER:
            del self._buf[: len(self._buf) - _MAX_BUFFER]

        frames: list[Frame] = []
        while True:
            index = self._buf.find(SYNC_HEADER)
            if index < 0:
                # No header in the buffer. A header may still straddle the next
                # notification, so keep the last two bytes and drop the rest
                # (ASCII handshake replies land here and are discarded).
                keep = len(SYNC_HEADER) - 1
                if len(self._buf) > keep:
                    del self._buf[: len(self._buf) - keep]
                break
            if index:
                del self._buf[:index]
            if len(self._buf) < FRAME_LEN:
                break
            frames.append(Frame(cmd=self._buf[3], payload=bytes(self._buf[4:FRAME_LEN])))
            del self._buf[:FRAME_LEN]
        return frames

    def reset(self) -> None:
        """Drop buffered bytes, e.g. after a reconnect."""
        self._buf.clear()


def _u16(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset : offset + 2], "big")


def decode_frame(frame: Frame) -> dict[str, Any] | None:
    """Decode one frame into measurement fields.

    Returns ``None`` for frames that are undecoded, padding, or fail the
    plausibility check -- the caller must not publish anything in that case.
    """
    payload = frame.payload

    if frame.cmd == CMD_VOLTAGE_CURRENT:
        voltage = _u16(payload, 0) / 100.0
        raw_current = _u16(payload, 2)
        # Bit 15 is the sign: set means discharge (negative), clear means
        # charge. The magnitude is the remaining 15 bits in 10 mA steps.
        magnitude = (raw_current & 0x7FFF) / 100.0
        current = -magnitude if raw_current & 0x8000 else magnitude
        if not 0.0 < voltage <= MAX_PACK_VOLTAGE or abs(current) > MAX_PACK_CURRENT:
            return None
        return {
            "voltage": round(voltage, 2),
            "current": round(current, 2),
            # The battery does not transmit power; the Dometic app derives it
            # the same way (verified: 13.4 V x -13.9 A vs. -186 W shown).
            "power": round(voltage * current, 1),
        }

    if frame.cmd == CMD_SOC:
        soc = payload[0]
        return {"soc": soc} if soc <= 100 else None

    if frame.cmd == CMD_SOH:
        soh = payload[0]
        return {"soh": soh} if soh <= 100 else None

    if frame.cmd == CMD_CAPACITY:
        # Documented as "byte 4"; read as the low 16 bits so capacities above
        # 255 Ah stay representable. Matches the 150 Ah observation either way.
        capacity = _u16(payload, 2)
        return {"capacity_ah": capacity} if 0 < capacity <= MAX_CAPACITY_AH else None

    if frame.cmd in (CMD_CELLS_1_2, CMD_CELLS_3_4):
        first = 1 if frame.cmd == CMD_CELLS_1_2 else 3
        values: dict[str, Any] = {}
        for offset, cell in ((0, first), (2, first + 1)):
            millivolts = _u16(payload, offset)
            if not MIN_CELL_MV <= millivolts <= MAX_CELL_MV:
                # One bad half means the frame is suspect; drop both.
                return None
            values[f"cell_{cell}_mv"] = millivolts
        return values

    return None

