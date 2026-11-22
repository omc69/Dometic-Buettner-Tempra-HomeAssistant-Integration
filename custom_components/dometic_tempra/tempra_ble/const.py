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

#: TX-credits characteristic (indicate). This is the heart of the Telit TIO
#: credit-based flow control the battery actually speaks: the battery indicates
#: here how many packets we are allowed to send it. The "8E" (=142) seen in the
#: iOS captures is such a credit grant, not an opaque unlock byte. We must
#: accumulate these credits and never write a command while we hold none.
INDICATE_CHAR_UUID: Final = "00000004-0000-1000-8000-008025000000"
TX_CREDITS_CHAR_UUID: Final = INDICATE_CHAR_UUID

#: RX-credits characteristic (write-with-response). We write a single byte here
#: granting the battery permission to send us that many more packets. The "C8"
#: (=200) the app writes is exactly this initial grant, not a magic session
#: opener. We top it up whenever our granted budget runs low.
RX_CREDITS_CHAR_UUID: Final = SESSION_CHAR_UUID

#: TX-MTU characteristic (indicate). The app subscribes to it as part of the
#: working handshake; the battery drops a marginal link that never does. We
#: subscribe and otherwise ignore it -- our commands are a handful of bytes.
TX_MTU_CHAR_UUID: Final = "0000000a-0000-1000-8000-008025000000"

#: Initial number of inbound packets we grant the battery (byte on RX credits).
#: Matches the current Dometic app (0xFF); the exact value is not critical as
#: long as it is non-zero and refilled before it drains.
INITIAL_LOCAL_CREDITS: Final = 0xFF

#: Refill our inbound grant once the battery has spent down to this many.
LOCAL_CREDIT_REFILL_THRESHOLD: Final = 32

#: How long to wait for the battery to grant us outbound credits (its first
#: TX-credits indication) before giving up on the poll.
CREDIT_WAIT_TIMEOUT: Final = 6.0

#: ASCII replies the battery sends during the handshake.
REPLY_ACCESS_GRANTED: Final = "MST+AEN"
REPLY_ACCESS_DENIED: Final = "MST+ANE"
REPLY_NETWORK: Final = "MST+NET"
REPLY_WAIT: Final = "MST+WAI"

# --- Advertising -----------------------------------------------------------

#: Advertised local name, ``KAA_<serial>_TLB150``.
LOCAL_NAME_PATTERN: Final = "KAA_*_TLB150"

# --- Handshake -------------------------------------------------------------

#: Password Home Assistant claims the battery with. The battery stores exactly
#: one password; when ``RESET_ON_ACCESS_DENIED`` is on we reset whatever is
#: stored and set this, so from then on Home Assistant owns the battery (the
#: Dometic app then has to be re-paired to take it back). 15 chars from the
#: app's own alphabet [0-9a-z], so the firmware accepts it like an app token.
DEFAULT_AUTH_TOKEN: Final = "homeassistant01"

#: Reset the stored password when the battery denies access (MST+ANE), then
#: re-claim it with DEFAULT_AUTH_TOKEN. This takes the battery away from the
#: Dometic phone app, which is what the owner asked for.
RESET_ON_ACCESS_DENIED: Final = True

#: Command that clears the battery's stored password (the app's "RESET
#: PASSWORD"). After it, the next APP+AEN sets a fresh password.
CMD_RESET_PASSWORD: Final = "APP+RPA"

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

#: Entities stay available this long after the last sign of life, so a few
#: missed or failed polls do not blank the dashboard. A battery at rest barely
#: changes, so a generous window is safe and keeps a slow relayed sibling from
#: flickering out between the polls that refresh it.
DATA_STALE_AFTER: Final = 600.0

#: Handshake commands. Unlike the old fixed sequence these are now driven by
#: the battery's ASCII replies (see device.py): we send AEN, wait for MST+AEN,
#: send NET, wait for MST+NET, then DAT (which starts the binary stream) and
#: RDN=1 (which keeps the real-time stream alive, as the app does).
CMD_ACCESS: Final = "APP+AEN={token}"
CMD_NETWORK: Final = "APP+NET"
CMD_DATA: Final = "APP+DAT"
CMD_REALTIME_START: Final = "APP+RDN=1"

#: Kept for backward compatibility with the standalone dump tool.
HANDSHAKE_COMMANDS: Final = (
    CMD_ACCESS,
    CMD_NETWORK,
    CMD_DATA,
)

#: Attempts for the session write. A link at -80 dBm sometimes rejects the
#: first one with GATT Unlikely Error and accepts the next.
SESSION_WRITE_ATTEMPTS: Final = 3

# --- Binary framing --------------------------------------------------------

#: Every telemetry frame is 8 bytes and begins with the single sync byte
#: ``0x23`` -- exactly what the Dometic app locks onto. The byte *after* the
#: sync is a stream *type*, not a constant: ``0x85`` is the battery-register
#: stream, ``0x4D`` a second interleaved stream (per-cell / bus-management,
#: not yet decoded). An earlier version treated ``23 85`` as a fixed two-byte
#: sync; that is wrong -- back-to-back frames put a ``0x23`` right after the
#: payload, so the two-byte matcher ate the next frame's sync byte into this
#: frame's payload (reading capacity as 0x23 = 35 instead of 0x64 = 100) and
#: mis-grouped the ``0x4D`` stream into phantom battery nodes. We sync on the
#: single ``0x23`` and lock to the 8-byte grid instead.
FRAME_SYNC: Final = 0x23

#: Known stream types (the byte after the sync).
FRAME_TYPE_BATTERY: Final = 0x85  # battery registers: V/I, SoC, SoH, cells, ...
FRAME_TYPE_AUX: Final = 0x4D  # second stream, not yet decoded -- never a battery

#: Stream types we accept as frame starts when locking to the grid. A ``0x23``
#: followed by any other type byte is treated as a mis-sync and skipped.
FRAME_TYPES: Final = frozenset({FRAME_TYPE_BATTERY, FRAME_TYPE_AUX})

#: Retained for the standalone dump tool; the live parser no longer uses it.
SYNC_HEADER: Final = b"\x23\x85"

#: ``23 <type:1> <node:1> <cmd:1> <payload:4>``
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
