"""Constants for the Dometic Büttner Tempra integration."""

from __future__ import annotations

from typing import Final

from homeassistant.const import Platform

DOMAIN: Final = "dometic_tempra"

MANUFACTURER: Final = "Dometic / Büttner Elektronik"
MODEL: Final = "Tempra TLB150"

CONF_AUTH_TOKEN: Final = "auth_token"

PLATFORMS: Final = [Platform.SENSOR]
