"""Connection manager for a single Tempra TLB150 battery.

A battery accepts one BLE connection at a time and only streams telemetry
after a fixed handshake. Rather than holding that connection open forever,
this polls: take the radio, connect, run the handshake, collect one complete
snapshot, disconnect, and hand the radio to the next battery.

That matters for a bank. Three simultaneous GATT links at -72 to -88 dBm on a
Raspberry Pi's onboard antenna are not reliable -- in testing whichever
battery came third would fail, with the session write rejected or the link
dropped moments after the first command, and which battery lost rotated
between runs. Taking turns removes the contention entirely, at the cost of a
reading every POLL_INTERVAL rather than continuously, which is ample for a
battery bank.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from bleak import BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    COMMAND_REPLY_TIMEOUT,
    DATA_STALE_AFTER,
    DEFAULT_AUTH_TOKEN,
    HANDSHAKE_COMMANDS,
    HANDSHAKE_DELAY,
    HANDSHAKE_REPLY_TIMEOUT,
    HANDSHAKE_TERMINATOR,
    INDICATE_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    POLL_INTERVAL,
    SESSION_CHAR_UUID,
    SESSION_OPEN_VALUE,
    SESSION_SETTLE_DELAY,
    SESSION_WRITE_ATTEMPTS,
    SNAPSHOT_TIMEOUT,
    UNDECODED_COMMANDS,
    WRITE_CHAR_UUID,
)
from .identity import serial_from_name
from .models import MEASUREMENT_KEYS, TempraState
from .parser import FrameStream, decode_frame

_LOGGER = logging.getLogger(__name__)

#: Backoff bounds after a failed poll, seconds.
_BACKOFF_START = 10.0
_BACKOFF_MAX = 180.0


class TempraBleDevice:
    """Polls one battery, taking turns on the adapter with its siblings."""

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        radio: asyncio.Lock,
        auth_token: str = DEFAULT_AUTH_TOKEN,
        name: str | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._radio = radio
        self._auth_token = auth_token
        self._name = name or ble_device.name or ble_device.address
        self._listeners: list[Callable[[TempraState], None]] = []

        self._client: BleakClientWithServiceCache | None = None
        self._streams: dict[str, FrameStream] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

        self._reply = asyncio.Event()
        self._complete = asyncio.Event()
        self._dropped = asyncio.Event()
        self._wake = asyncio.Event()
        self._last_success: float | None = None

        self.state = TempraState(
            address=ble_device.address,
            name=self._name,
            serial=serial_from_name(self._name),
        )

    # -- public API ---------------------------------------------------------

    @property
    def address(self) -> str:
        """Bluetooth address of the battery."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Advertised name of the battery."""
        return self._name

    @property
    def available(self) -> bool:
        """Whether the last reading is recent enough to trust."""
        if self._last_success is None:
            return False
        return (time.monotonic() - self._last_success) < DATA_STALE_AFTER

    def add_listener(self, listener: Callable[[TempraState], None]) -> Callable[[], None]:
        """Register a callback fired on every state change. Returns a remover."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher ``BLEDevice`` (new advertisement, better adapter)."""
        self._ble_device = ble_device
        if ble_device.name:
            self._name = ble_device.name

    def request_poll(self) -> None:
        """Cut the wait short, e.g. when the battery advertises again."""
        self._wake.set()

    async def async_start(self) -> None:
        """Start the polling loop."""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name=f"tempra-{self.address}")

    async def async_stop(self) -> None:
        """Stop polling and disconnect."""
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._async_disconnect()

    # -- polling loop -------------------------------------------------------

    async def _run(self) -> None:
        backoff = _BACKOFF_START
        while not self._stopping:
            ok = False
            # Only one battery may hold the radio at a time.
            async with self._radio:
                try:
                    ok = await self._async_poll_once()
                except asyncio.CancelledError:
                    raise
                except (BleakError, TimeoutError, OSError) as err:
                    _LOGGER.debug("%s: poll failed: %s", self._name, err)
                except Exception:  # noqa: BLE001 - the loop must never die
                    _LOGGER.exception("%s: unexpected error while polling", self._name)
                finally:
                    await self._async_disconnect()

            self._notify()
            if self._stopping:
                break
            if ok:
                backoff = _BACKOFF_START
                await self._async_wait(POLL_INTERVAL)
            else:
                await self._async_wait(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _async_wait(self, delay: float) -> None:
        """Wait, but let a fresh advertisement cut a long wait short."""
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=delay)

    async def _async_poll_once(self) -> bool:
        """Connect, handshake, and collect one snapshot. True if it worked."""
        self._streams.clear()
        self._reply.clear()
        self._complete.clear()
        self._dropped.clear()

        _LOGGER.debug("%s: connecting to %s", self._name, self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: self._ble_device,
            use_services_cache=True,
        )
        self._client = client

        # Order is taken byte-for-byte from the iOS captures: notifications,
        # then indications -- which is what actually unlocks the session --
        # then the C8 write, then the commands.
        await client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
        await client.start_notify(INDICATE_CHAR_UUID, self._on_notification)
        await self._async_open_session(client)
        await asyncio.sleep(SESSION_SETTLE_DELAY)
        await self._async_handshake(client)

        try:
            await asyncio.wait_for(
                asyncio.wait(
                    [
                        asyncio.ensure_future(self._complete.wait()),
                        asyncio.ensure_future(self._dropped.wait()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                ),
                timeout=SNAPSHOT_TIMEOUT,
            )
        except TimeoutError:
            missing = [
                key for key in MEASUREMENT_KEYS if getattr(self.state, key) is None
            ]
            _LOGGER.debug("%s: incomplete snapshot, missing %s", self._name, missing)

        # A snapshot counts if the core readings arrived. Fields like the
        # rated capacity are sent rarely, so insisting on every one of them
        # would throw away otherwise good polls.
        if self.state.voltage is None or self.state.soc is None:
            return False

        self._last_success = time.monotonic()
        return True

    async def _async_open_session(self, client: BleakClientWithServiceCache) -> None:
        """Write the C8 byte that unlocks the command channel.

        A link at -80 dBm sometimes rejects this with GATT Unlikely Error and
        accepts it moments later, so retry rather than throwing away the whole
        poll on one flaky ATT transaction.
        """
        for attempt in range(1, SESSION_WRITE_ATTEMPTS + 1):
            try:
                await client.write_gatt_char(
                    SESSION_CHAR_UUID, SESSION_OPEN_VALUE, response=True
                )
            except BleakError:
                if attempt == SESSION_WRITE_ATTEMPTS:
                    raise
                _LOGGER.debug(
                    "%s: session write attempt %d rejected, retrying",
                    self._name,
                    attempt,
                )
                await asyncio.sleep(0.2)
            else:
                return

    async def _async_handshake(self, client: BleakClientWithServiceCache) -> None:
        """Run the command sequence that starts the telemetry stream."""
        for template in HANDSHAKE_COMMANDS:
            if self._dropped.is_set():
                # Writing into a link the battery has already closed only
                # produces a confusing "Service Discovery has not been
                # performed yet" and holds the radio away from the next
                # battery for no reason.
                raise BleakError("battery closed the connection mid-handshake")
            command = template.format(token=self._auth_token) + HANDSHAKE_TERMINATOR
            _LOGGER.debug("%s: -> %r", self._name, command)
            self._reply.clear()
            await client.write_gatt_char(WRITE_CHAR_UUID, command.encode("ascii"))
            # The app sends the next command as soon as the previous one is
            # answered, so follow the battery's pace rather than a fixed timer.
            # A command that draws no reply must not stall the rest.
            try:
                await asyncio.wait_for(self._reply.wait(), timeout=COMMAND_REPLY_TIMEOUT)
            except TimeoutError:
                _LOGGER.debug("%s: no reply to %r", self._name, command)
                await asyncio.sleep(HANDSHAKE_DELAY)

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(BleakError, TimeoutError, OSError):
            await client.disconnect()

    # -- callbacks ----------------------------------------------------------

    def _on_disconnected(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self._name)
        self._dropped.set()

    def _on_notification(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        raw = bytes(data)
        _LOGGER.debug("%s: <- %s %s %r", self._name, sender.uuid, raw.hex(" "), raw)
        self._reply.set()

        changed: dict[str, object] = {}
        undecoded: dict[int, str] | None = None
        stream = self._streams.setdefault(sender.uuid, FrameStream())

        for frame in stream.feed(raw):
            values = decode_frame(frame)
            if values is not None:
                changed.update(values)
                continue
            if frame.cmd in UNDECODED_COMMANDS:
                if undecoded is None:
                    undecoded = dict(self.state.undecoded)
                undecoded[frame.cmd] = frame.payload.hex()

        if not changed and undecoded is None:
            return

        if undecoded is not None:
            changed["undecoded"] = undecoded
        self.state = self.state.with_values(changed)
        if all(getattr(self.state, key) is not None for key in MEASUREMENT_KEYS):
            self._complete.set()
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self.state)
