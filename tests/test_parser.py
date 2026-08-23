"""Unit tests for the Tempra frame parser.

Every byte sequence here is taken verbatim from the PacketLogger captures
documented in ``docs/dometic_tempra_ble_protocol.md``, together with the value
the Dometic app displayed at that moment.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import the protocol layer directly: it is pure Python, so the parser can be
# tested without Home Assistant or bleak installed.
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "dometic_tempra"),
)

from tempra_ble.parser import (  # noqa: E402
    Frame,
    FrameStream,
    decode_frame,
)

SYNC = bytes.fromhex("2385cf")


def frame(cmd: int, payload: str) -> bytes:
    """Build a wire frame from a command id and a hex payload."""
    return SYNC + bytes([cmd]) + bytes.fromhex(payload)


def test_voltage_and_discharge_current() -> None:
    """13.39 V / -13.52 A, app showed 13.4 V and -13.9 A under AC load."""
    values = decode_frame(Frame(0x02, bytes.fromhex("053b8548")))
    assert values == {"voltage": 13.39, "current": -13.52, "power": -181.0}


def test_charge_current_is_positive() -> None:
    """Sign bit clear means charging (solar, 98 % SOC)."""
    values = decode_frame(Frame(0x02, bytes.fromhex("053b0098")))
    assert values is not None
    assert values["current"] == pytest.approx(1.52)
    assert values["power"] > 0


def test_sign_bit_is_masked_out_of_the_magnitude() -> None:
    """0x8548 must decode as -13.52 A, not as -341.20 A."""
    values = decode_frame(Frame(0x02, bytes.fromhex("053b8548")))
    assert values is not None
    assert abs(values["current"]) < 100


@pytest.mark.parametrize(
    ("payload", "expected"), [("64ffffff", 100), ("63ffffff", 99), ("62ffffff", 98)]
)
def test_soc(payload: str, expected: int) -> None:
    """SOC lives in the first payload byte."""
    assert decode_frame(Frame(0x0B, bytes.fromhex(payload))) == {"soc": expected}


def test_soh() -> None:
    """SOH lives in the first payload byte."""
    assert decode_frame(Frame(0x0E, bytes.fromhex("64000000"))) == {"soh": 100}


def test_capacity() -> None:
    """TLB150 reports its 150 Ah rating."""
    assert decode_frame(Frame(0x07, bytes.fromhex("00000096"))) == {"capacity_ah": 150}


def test_cell_voltages() -> None:
    """0x56 carries cells 1+2, 0x57 carries cells 3+4."""
    assert decode_frame(Frame(0x56, bytes.fromhex("0d660d93"))) == {
        "cell_1_mv": 3430,
        "cell_2_mv": 3475,
    }
    assert decode_frame(Frame(0x57, bytes.fromhex("0d940d75"))) == {
        "cell_3_mv": 3476,
        "cell_4_mv": 3445,
    }


def test_undecoded_and_padding_yield_nothing() -> None:
    """Unknown commands and the 0x00 padding must not publish values."""
    assert decode_frame(Frame(0x60, bytes.fromhex("60000100"))) is None
    assert decode_frame(Frame(0x00, bytes.fromhex("00000000"))) is None


def test_implausible_values_are_rejected() -> None:
    """A resync artefact must not reach the sensors."""
    assert decode_frame(Frame(0x02, bytes.fromhex("ffff0000"))) is None  # 655 V
    assert decode_frame(Frame(0x0B, bytes.fromhex("ff000000"))) is None  # 255 %
    assert decode_frame(Frame(0x56, bytes.fromhex("0d66ffff"))) is None  # 65 V cell


def test_stream_splits_a_multi_frame_notification() -> None:
    """One notification may carry several concatenated frames."""
    stream = FrameStream()
    frames = stream.feed(
        frame(0x02, "053b8548") + frame(0x0B, "64ffffff") + frame(0x00, "00000000")
    )
    assert [f.cmd for f in frames] == [0x02, 0x0B, 0x00]


def test_stream_reassembles_a_split_frame() -> None:
    """A frame split across two notifications is still decoded once."""
    stream = FrameStream()
    wire = frame(0x0E, "64000000")
    assert stream.feed(wire[:5]) == []
    frames = stream.feed(wire[5:])
    assert len(frames) == 1
    assert decode_frame(frames[0]) == {"soh": 100}


def test_stream_resyncs_past_ascii_replies() -> None:
    """ASCII handshake replies share the channel and must be skipped."""
    stream = FrameStream()
    frames = stream.feed(b"MST+NET=85CF0105000805010205 01" + frame(0x0B, "64ffffff"))
    assert [f.cmd for f in frames] == [0x0B]


def test_stream_survives_a_truncated_leading_frame() -> None:
    """Garbage before the first sync header is discarded, not misparsed."""
    stream = FrameStream()
    frames = stream.feed(bytes.fromhex("aabbcc") + frame(0x07, "00000096"))
    assert [f.cmd for f in frames] == [0x07]


def test_stream_does_not_grow_without_bound() -> None:
    """A channel that never yields a header must not buffer forever."""
    stream = FrameStream()
    for _ in range(100):
        stream.feed(b"\x00" * 64)
    assert len(stream._buf) < 512  # noqa: SLF001
