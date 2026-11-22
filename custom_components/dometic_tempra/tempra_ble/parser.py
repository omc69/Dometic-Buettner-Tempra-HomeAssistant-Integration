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
    CELL_COUNT,
    CMD_CAPACITY,
    CMD_CELLS_1_2,
    CMD_CELLS_3_4,
    CMD_SOC,
    CMD_SOH,
    CMD_VOLTAGE_CURRENT,
    FRAME_LEN,
    FRAME_SYNC,
    FRAME_TYPE_BATTERY,
    FRAME_TYPES,
    MAX_CAPACITY_AH,
    MAX_CELL_MV,
    MAX_PACK_CURRENT,
    MAX_PACK_VOLTAGE,
    MIN_CELL_MV,
)

#: Cap on buffered bytes. A frame is 8 bytes and notifications are small; this
#: only exists so a stream that never yields a sync header cannot grow without
#: bound.
_MAX_BUFFER = 512


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded-from-the-wire telemetry frame.

    Layout ``23 <type> <node> <cmd> <payload:4>``. ``type_`` is the byte after
    the sync (``0x85`` battery registers, ``0x4D`` the second stream); only
    battery frames carry decodable measurements. ``node`` is the N-BUS address
    of the battery the frame came from -- a single BLE connection to the bus
    master relays every battery on the bus, so frames must be grouped by node
    before decoding, or two batteries' values get blended into one.
    """

    cmd: int
    payload: bytes
    node: int = 0
    type_: int = FRAME_TYPE_BATTERY

    @property
    def is_battery(self) -> bool:
        """Whether this frame carries battery registers (type ``0x85``)."""
        return self.type_ == FRAME_TYPE_BATTERY


class FrameStream:
    """Reassembles notify chunks into 8-byte frames.

    Frames are a back-to-back grid of 8-byte records, each starting with the
    single sync byte ``0x23``. We lock to that grid: once aligned we consume
    whole records; a record whose type byte is unknown, or that is not followed
    by another ``0x23``, means we are mis-aligned, so we resync on the next
    ``0x23``. (ASCII handshake replies carry no ``0x23`` and are discarded.)
    """

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[Frame]:
        """Append received bytes and return every complete frame in them."""
        self._buf += data
        if len(self._buf) > _MAX_BUFFER:
            del self._buf[: len(self._buf) - _MAX_BUFFER]

        frames: list[Frame] = []
        while len(self._buf) >= FRAME_LEN:
            if self._buf[0] != FRAME_SYNC or self._buf[1] not in FRAME_TYPES:
                if not self._resync():
                    break
                continue
            # Alignment check: the next record must also start on the grid. If
            # we cannot see the next byte yet, accept optimistically -- the type
            # byte already passed, and a wrong guess self-corrects next record.
            if len(self._buf) > FRAME_LEN and self._buf[FRAME_LEN] != FRAME_SYNC:
                if not self._resync():
                    break
                continue
            frames.append(
                Frame(
                    type_=self._buf[1],
                    node=self._buf[2],
                    cmd=self._buf[3],
                    payload=bytes(self._buf[4:FRAME_LEN]),
                )
            )
            del self._buf[:FRAME_LEN]
        return frames

    def _resync(self) -> bool:
        """Drop to the next plausible frame start. True if one may remain."""
        index = self._buf.find(FRAME_SYNC, 1)
        if index < 0:
            # Keep a trailing byte only if it could be a sync starting a frame
            # that continues in the next notification.
            tail = 1 if self._buf and self._buf[-1] == FRAME_SYNC else 0
            if len(self._buf) > tail:
                del self._buf[: len(self._buf) - tail]
            return False
        del self._buf[:index]
        return len(self._buf) >= FRAME_LEN

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


def cell_keys() -> tuple[str, ...]:
    """Measurement keys of the individual cell voltages."""
    return tuple(f"cell_{index}_mv" for index in range(1, CELL_COUNT + 1))
