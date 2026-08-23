"""Data model for a Tempra TLB150 battery."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

#: Field name -> None, in publication order. Kept as an explicit tuple so the
#: sensor platform and the diagnostics dump stay in sync with the decoder.
MEASUREMENT_KEYS: tuple[str, ...] = (
    "voltage",
    "current",
    "power",
    "soc",
    "soh",
    "capacity_ah",
    "cell_1_mv",
    "cell_2_mv",
    "cell_3_mv",
    "cell_4_mv",
)


@dataclass(frozen=True, slots=True)
class TempraState:
    """Last known values of a single battery.

    Every measurement is optional: the battery streams each command on its own
    cadence, so a freshly connected device fills the fields in over the first
    few seconds.
    """

    address: str
    name: str
    serial: str | None = None

    voltage: float | None = None
    current: float | None = None
    power: float | None = None
    soc: int | None = None
    soh: int | None = None
    capacity_ah: int | None = None
    cell_1_mv: int | None = None
    cell_2_mv: int | None = None
    cell_3_mv: int | None = None
    cell_4_mv: int | None = None

    #: Raw payload of the most recent frame per undecoded command id, hex
    #: encoded. Feeds the diagnostics dump so the open items in section 4.2 of
    #: the protocol document can be worked on from a live system.
    undecoded: dict[int, str] = field(default_factory=dict)

    def with_values(self, values: dict[str, Any]) -> TempraState:
        """Return a copy with ``values`` applied."""
        return replace(self, **values)

    def as_dict(self) -> dict[str, Any]:
        """Measurements only, for diagnostics and tests."""
        return {key: getattr(self, key) for key in MEASUREMENT_KEYS}
