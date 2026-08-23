"""Connection manager for a single Tempra TLB150 battery.

The battery accepts exactly one BLE connection at a time and only starts
streaming telemetry after a fixed handshake. This module owns that lifecycle:
connect, handshake, consume notifications, detect a stalled or dropped link,
and reconnect with backoff.
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
    DEFAULT_AUTH_TOKEN,
    HANDSHAKE_DELAY,
    NOTIFY_CHAR_UUID,
    UNDECODED_COMMANDS,
    WRITE_CHAR_UUID,
)
from .identity import serial_from_name
from .models import TempraState
from .parser import FrameStream, decode_frame

_LOGGER = logging.getLogger(__name__)

#: Reconnect backoff bounds, seconds.
_BACKOFF_START = 5.0
_BACKOFF_MAX = 120.0

#: Force a reconnect if a connected battery has not produced a decodable frame
#: for this long. The stream is continuous once ``APP+DAT`` is accepted, so a
#: gap this large means the link is up but useless.
STALE_TIMEOUT = 90.0

class TempraBleDevice:
    """Maintains a live telemetry stream from one battery."""

    def __init__(
        self,
        ble_device: BLEDevice,
        *,
        auth_token: str = DEFAULT_AUTH_TOKEN,
        name: str | None = None,
    ) -> None:
        self._ble_device = ble_device
        self._auth_token = auth_token
        self._name = name or ble_device.name or ble_device.address
        self._listeners: list[Callable[[TempraState], None]] = []

        self._client: BleakClientWithServiceCache | None = None
        self._stream = FrameStream()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._link_lost = asyncio.Event()
        self._wake = asyncio.Event()
        self._last_frame: float | None = None

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
    def connected(self) -> bool:
        """Whether a usable, non-stalled telemetry stream exists."""
        if self._client is None or not self._client.is_connected:
            return False
        if self._last_frame is None:
            return False
        return (time.monotonic() - self._last_frame) < STALE_TIMEOUT

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

    async def async_start(self) -> None:
        """Start the connection supervisor."""
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(
            self._supervise(), name=f"tempra-{self.address}"
        )

    async def async_stop(self) -> None:
        """Stop the supervisor and disconnect."""
        self._stopping = True
        self._link_lost.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self._async_disconnect()

    def request_reconnect(self) -> None:
        """Cut the reconnect backoff short.

        Called when the battery advertises again -- the earliest reliable sign
        that its single connection slot is free. Deliberately does *not* touch
        the live link: a connection that has been established but has not yet
        produced a frame must be given the chance to finish its handshake.
        """
        self._wake.set()

    # -- supervisor ---------------------------------------------------------

    async def _supervise(self) -> None:
        backoff = _BACKOFF_START
        while not self._stopping:
            try:
                await self._async_connect_and_stream()
            except asyncio.CancelledError:
                raise
            except (BleakError, TimeoutError, OSError) as err:
                _LOGGER.debug("%s: connection attempt failed: %s", self._name, err)
            except Exception:  # noqa: BLE001 - supervisor must never die
                _LOGGER.exception("%s: unexpected error in connection loop", self._name)
            else:
                # A clean return means the link went down after working, so the
                # next attempt starts from the short backoff again.
                backoff = _BACKOFF_START

            await self._async_disconnect()
            self._notify()
            if self._stopping:
                break
            _LOGGER.debug("%s: reconnecting in up to %.0fs", self._name, backoff)
            await self._async_wait_before_retry(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _async_wait_before_retry(self, delay: float) -> None:
        """Wait before the next attempt, cut short by a fresh advertisement.

        The first ``_BACKOFF_START`` seconds are always waited out, so an early
        wake-up can shorten a long backoff but can never produce a reconnect
        storm.
        """
        self._wake.clear()
        await asyncio.sleep(min(delay, _BACKOFF_START))
        remaining = delay - _BACKOFF_START
        if remaining <= 0:
            return
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=remaining)

    async def _async_connect_and_stream(self) -> None:
        self._link_lost.clear()
        self._stream.reset()
        self._last_frame = None

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

        await client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
        await self._async_handshake(client)
        _LOGGER.debug("%s: handshake complete, streaming", self._name)

        # Hold the connection open until it drops or the stream goes stale.
        while not self._stopping:
            try:
                await asyncio.wait_for(self._link_lost.wait(), timeout=STALE_TIMEOUT)
            except TimeoutError:
                if self._last_frame is None:
                    _LOGGER.warning(
                        "%s: no telemetry after handshake, reconnecting", self._name
                    )
                    return
                if (time.monotonic() - self._last_frame) >= STALE_TIMEOUT:
                    _LOGGER.warning(
                        "%s: telemetry stalled for %.0fs, reconnecting",
                        self._name,
                        STALE_TIMEOUT,
                    )
                    return
                # Still healthy, keep waiting.
                continue
            return

    async def _async_handshake(self, client: BleakClientWithServiceCache) -> None:
        """Run the mandatory command sequence that unlocks the data stream.

        Order matters and the delays are deliberate -- the Dometic app spaces
        the writes the same way, and without ``APP+DAT`` the notify channel
        stays silent.
        """
        for command in (
            f"APP+AEN={self._auth_token}",
            "APP+NET",
            "APP+DAT",
            "APP+RDN=1",
        ):
            await client.write_gatt_char(WRITE_CHAR_UUID, command.encode("ascii"))
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
        self._link_lost.set()

    def _on_notification(
        self, _sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        changed: dict[str, object] = {}
        undecoded: dict[int, str] | None = None
        frames = self._stream.feed(bytes(data))

        for frame in frames:
            values = decode_frame(frame)
            if values is not None:
                changed.update(values)
                continue
            if frame.cmd in UNDECODED_COMMANDS:
                if undecoded is None:
                    undecoded = dict(self.state.undecoded)
                undecoded[frame.cmd] = frame.payload.hex()

        if frames:
            # Any well-formed frame proves the stream is alive, even one whose
            # command we cannot decode yet.
            self._last_frame = time.monotonic()

        if not changed and undecoded is None:
            return

        if undecoded is not None:
            changed["undecoded"] = undecoded
        self.state = self.state.with_values(changed)
        self._notify()

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener(self.state)
