"""Protocol constants for the Dometic Büttner Tempra TLB150 BLE interface.

Everything in this module is derived from the reverse-engineering write-up
``dometic_tempra_ble_protocol.md`` (Apple PacketLogger HCI traces correlated
against the Dometic app across idle / discharge / solar-charge states).
"""

from __future__ import annotations

from typing import Final

# --- GATT ------------------------------------------------------------------

#: Proprietary service carrying all battery data.
SERVICE_UUID: Final = "0000fefb-0000-1000-8000-00805f9b34fb"

#: Write-only characteristic, takes ASCII ``APP+...`` commands.
WRITE_CHAR_UUID: Final = "00000001-0000-1000-8000-008025000000"

#: Notify characteristic, carries ASCII replies *and* binary telemetry.
NOTIFY_CHAR_UUID: Final = "00000002-0000-1000-8000-008025000000"

#: Do NOT subscribe to 0x000A or 0x0004. Both are Indicate, not Notify, and
#: enabling indications on 0x000A makes the battery hang up ~1.2 s later --
#: reproduced on consecutive attempts against KAA_502269_TLB150, before the
#: first handshake write even went out.

# --- Advertising -----------------------------------------------------------

#: Advertised local name, ``KAA_<serial>_TLB150``.
LOCAL_NAME_PATTERN: Final = "KAA_*_TLB150"

# --- Handshake -------------------------------------------------------------

#: Auth token the Dometic app sends in every capture. Not verified to be
#: device specific -- overridable via the integration options in case a
#: future capture shows a different value.
DEFAULT_AUTH_TOKEN: Final = "f560f1deba"

#: Gap between handshake writes. The handover suggested 0.3 s, but a real
#: battery hangs up about 1.2 s after connecting, and five writes at 0.3 s do
#: not fit in that window -- two of three batteries were dropped before the
#: sequence finished. Keep the whole sequence comfortably under a second.
HANDSHAKE_DELAY: Final = 0.12

#: Command terminators to try, one per connection attempt, until the battery
#: answers. The captures show the payload without a terminator, but a
#: PacketLogger trace does not necessarily render a trailing CR/LF -- and the
#: 0x14 register returning ASCII "NNN\n" proves the protocol uses newlines
#: somewhere. Since the write characteristic is write-without-response, a
#: rejected command is silently discarded, so this cannot be told apart from a
#: wrong terminator except by trying.
HANDSHAKE_TERMINATORS: Final = ("", "\r\n", "\n", "\r")

#: How long to wait for the battery to say anything at all after a handshake
#: before treating that attempt's variant as wrong.
HANDSHAKE_REPLY_TIMEOUT: Final = 6.0

#: The handshake in the order the Dometic app sends it. ``APP+IMP`` is step 4
#: of the documented sequence; the handover pseudocode left it out, and the
#: battery hangs up a few seconds after the shortened sequence without ever
#: sending telemetry. ``{token}`` is substituted with the auth token.
HANDSHAKE_COMMANDS: Final = (
    "APP+AEN={token}",
    "APP+NET",
    "APP+DAT",
    "APP+IMP",
    "APP+RDN=1",
)

# --- Binary framing --------------------------------------------------------

#: Fixed sync header preceding every telemetry frame.
SYNC_HEADER: Final = b"\x23\x85\xcf"

#: ``23 85 CF <cmd:1> <payload:4>``
FRAME_LEN: Final = 8
PAYLOAD_LEN: Final = 4

# --- Command IDs -----------------------------------------------------------

CMD_PADDING: Final = 0x00
CMD_VOLTAGE_CURRENT: Final = 0x02
CMD_CAPACITY: Final = 0x07
CMD_SOC: Final = 0x0B
CMD_SOH: Final = 0x0E
CMD_CELLS_1_2: Final = 0x56
CMD_CELLS_3_4: Final = 0x57

#: Commands seen in captures but not yet decoded. Kept so diagnostics can
#: label them; see section 4.2 of the protocol document.
UNDECODED_COMMANDS: Final[dict[int, str]] = {
    0x0C: "constant 02 EE FF FF - unknown, possibly a calibration reference",
    0x14: "ASCII 'NNN\\n' - unknown status mnemonic",
    0x34: "slightly varying - temperature candidate (uncalibrated)",
    0x35: "slowly rising - temperature or BMS counter candidate",
    0x36: "load dependent - internal resistance or peak-current candidate",
    0x54: "ASCII 'KAA' - vendor/model prefix",
    0x55: "constant - serial fragment or firmware build id",
    0x60: "status flag bitfield candidate (poles status / internal regulator)",
    0x90: "alarm register candidate (no alarm observed)",
    0xA0: "alarm register candidate (no alarm observed)",
    0xA1: "alarm register candidate (no alarm observed)",
    0xC0: "alarm register candidate (no alarm observed)",
    0xF1: "alarm register candidate (no alarm observed)",
    0xF2: "alarm register candidate (no alarm observed)",
}

# --- Plausibility bounds ---------------------------------------------------
# The frames carry no checksum, so a resync in the middle of the stream can
# produce a structurally valid but nonsensical frame. Every decoder rejects
# values outside these bounds rather than publishing garbage.

MAX_PACK_VOLTAGE: Final = 100.0
MAX_PACK_CURRENT: Final = 500.0
MAX_CAPACITY_AH: Final = 10_000
MIN_CELL_MV: Final = 1_000
MAX_CELL_MV: Final = 5_000
CELL_COUNT: Final = 4
