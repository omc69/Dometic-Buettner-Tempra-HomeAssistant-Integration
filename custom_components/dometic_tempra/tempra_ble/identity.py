"""Parsing of battery identity from advertised names and Bluetooth addresses.

Pure helpers with no third-party imports, so they stay unit testable without
bleak or Home Assistant.
"""

from __future__ import annotations

import re

#: ``KAA_502048_TLB150`` -> ``502048``
_SERIAL_RE = re.compile(r"^KAA_(?P<serial>[^_]+)_TLB150$", re.IGNORECASE)

_NON_HEX_RE = re.compile(r"[^0-9A-Fa-f]")
_MAC_RE = re.compile(r"^[0-9A-F]{12}$")


def serial_from_name(name: str | None) -> str | None:
    """Extract the serial number from an advertised local name."""
    if not name:
        return None
    match = _SERIAL_RE.match(name.strip())
    return match.group("serial") if match else None


def normalize_address(value: str | None) -> str | None:
    """Normalise a hand-typed Bluetooth address to ``AA:BB:CC:DD:EE:FF``.

    Accepts the separators people actually paste -- colons, dashes, spaces, or
    nothing at all. Returns ``None`` when the input is not a MAC address, which
    the config flow turns into a form error rather than a broken entry.
    """
    if not value:
        return None
    cleaned = _NON_HEX_RE.sub("", value).upper()
    if not _MAC_RE.match(cleaned):
        return None
    return ":".join(cleaned[index : index + 2] for index in range(0, 12, 2))
