"""Unit tests for name and address parsing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / "custom_components" / "dometic_tempra"),
)

from tempra_ble.identity import normalize_address, serial_from_name  # noqa: E402


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("KAA_502048_TLB150", "502048"),
        ("KAA_502269_TLB150", "502269"),
        ("  KAA_502048_TLB150  ", "502048"),
        ("kaa_502048_tlb150", "502048"),
        ("KAA_502048_TLB100", None),
        ("SomethingElse", None),
        (None, None),
        ("", None),
    ],
)
def test_serial_from_name(name: str | None, expected: str | None) -> None:
    """Serial numbers come out of the advertised local name."""
    assert serial_from_name(name) == expected


@pytest.mark.parametrize(
    "value",
    [
        "10:23:81:8B:13:AD",
        "10:23:81:8b:13:ad",
        "10-23-81-8B-13-AD",
        "1023818B13AD",
        "10 23 81 8b 13 ad",
        " 10:23:81:8B:13:AD ",
    ],
)
def test_normalize_address_accepts_what_people_paste(value: str) -> None:
    """Separators vary; the stored address must not."""
    assert normalize_address(value) == "10:23:81:8B:13:AD"


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "not an address",
        "10:23:81:8B:13",  # too short
        "10:23:81:8B:13:AD:EF",  # too long
        "10:23:81:8B:13:AZ",  # Z is not hex
        "550e8400-e29b-41d4-a716-446655440000",  # CoreBluetooth UUID
    ],
)
def test_normalize_address_rejects_the_rest(value: str | None) -> None:
    """Anything that is not a MAC becomes a form error, not a broken entry."""
    assert normalize_address(value) is None
