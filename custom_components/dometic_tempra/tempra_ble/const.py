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

#: Write-with-response characteristic. The Dometic app writes a single ``C8``
#: byte here during setup (section 2 of the protocol document, purpose
#: unrecorded). We had been skipping it. Unlike the ASCII command channel this
#: one acknowledges, so a rejection here is visible instead of silent.
SESSION_CHAR_UUID: Final = "00000003-0000-1000-8000-008025000000"
SESSION_OPEN_VALUE: Final = b"\xc8"

#: Indicate characteristic. Subscribing here is what unlocks the session: the
#: battery answers immediately with a single 8E indication, and only after that
#: does it accept the C8 session write and the APP+ commands. Confirmed in the
#: iOS captures. The app never touches 0x000A, so neither do we.
INDICATE_CHAR_UUID: Final = "00000004-0000-1000-8000-008025000000"

# --- Advertising -----------------------------------------------------------

#: Advertised local name, ``KAA_<serial>_TLB150``.
LOCAL_NAME_PATTERN: Final = "KAA_*_TLB150"

# --- Handshake -------------------------------------------------------------

#: Auth token the Dometic app sends in every capture. Not verified to be
#: device specific -- overridable via the integration options in case a
#: future capture shows a different value.
DEFAULT_AUTH_TOKEN: Final = "f560f1deba"

#: Pause after the session write, before the first command. The app waits
#: about 0.55 s here.
SESSION_SETTLE_DELAY: Final = 0.4

#: How long to wait for the battery's reply to each command before moving on.
#: The app is reply-driven rather than timer-driven: it sends the next command
#: as soon as the previous one is answered, roughly 0.3 s and 0.05 s apart in
#: the captures.
COMMAND_REPLY_TIMEOUT: Final = 2.0

#: Fallback gap when a command draws no reply, so a silent step cannot stall
#: the rest of the sequence.
HANDSHAKE_DELAY: Final = 0.12

#: No terminator. Confirmed byte-for-byte from the captures: the app writes
#: exactly "APP+AEN=f560f1deba" with no trailing CR or LF.
HANDSHAKE_TERMINATOR: Final = ""

#: How long to wait for the battery to say anything at all after a handshake.
HANDSHAKE_REPLY_TIMEOUT: Final = 6.0

#: How long to stay connected collecting frames before giving up on a complete
#: snapshot. The battery streams every field within a second or two once
#: APP+DAT is acknowledged, so this is generous.
SNAPSHOT_TIMEOUT: Final = 20.0

#: Gap between polls of one battery. Batteries take turns on the adapter, so
#: with a bank of three the effective cycle is this plus the time the other
#: batteries hold the radio. Battery state changes slowly; a reading a minute
#: is plenty and keeps the radio quiet the rest of the time.
POLL_INTERVAL: Final = 60.0

#: Entities stay available this long after the last complete reading, so a
#: single missed turn does not blank the dashboard.
DATA_STALE_AFTER: Final = 300.0

#: The handshake in the order the Dometic app sends it. Telemetry starts the
#: moment ``APP+DAT`` is acknowledged; ``APP+IMP`` and ``APP+RDN=1`` follow in
#: the captures but arrive after the stream is already running, so they are
#: sent for fidelity rather than necessity. ``{token}`` is substituted with the
#: auth token.
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
