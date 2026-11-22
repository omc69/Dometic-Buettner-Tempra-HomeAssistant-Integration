"""Connection manager for the Tempra battery bus.

A single BLE connection to the bus master relays *every* battery on the N-BUS.
Each telemetry frame carries the battery's bus address in byte[2]
(``23 85 <node> <cmd> <payload:4>``), so one connection yields one battery per
node. This manager keeps a :class:`TempraState` per node and exposes them all.

Telit TIO credit-based flow control
-----------------------------------
The bus speaks the Telit TIO UART-over-BLE profile, governed by *credits*:
neither side may send an application packet unless the peer has granted it
credit. Four characteristics under service FEFB carry this:

* ``00000001`` (RX)          -- we *write* ASCII ``APP+...`` commands here.
* ``00000002`` (TX)          -- the master *notifies* replies and telemetry.
* ``00000003`` (RX credits)  -- we *write* a byte granting inbound packets.
* ``00000004`` (TX credits)  -- the master *indicates* our outbound credits.

Sequence: subscribe to TX, TX-credits and TX-MTU; grant the master inbound
credits; wait for it to grant us outbound credits; then run the reply-driven
``APP+AEN`` / ``APP+NET`` / ``APP+DAT`` / ``APP+RDN=1`` handshake, respecting
the credit budget on every write.
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
    CMD_ACCESS,
    CMD_DATA,
    CMD_NETWORK,
    CMD_REALTIME_START,
    CMD_RESET_PASSWORD,
    COMMAND_REPLY_TIMEOUT,
    CREDIT_WAIT_TIMEOUT,
    DATA_STALE_AFTER,
    DEFAULT_AUTH_TOKEN,
    HANDSHAKE_REPLY_TIMEOUT,
    INITIAL_LOCAL_CREDITS,
    LOCAL_CREDIT_REFILL_THRESHOLD,
    NOTIFY_CHAR_UUID,
    POLL_INTERVAL,
    REPLY_ACCESS_DENIED,
    REPLY_ACCESS_GRANTED,
    REPLY_NETWORK,
    REPLY_WAIT,
    RESET_ON_ACCESS_DENIED,
    RX_CREDITS_CHAR_UUID,
    TX_CREDITS_CHAR_UUID,
    TX_MTU_CHAR_UUID,
    UNDECODED_COMMANDS,
    WRITE_CHAR_UUID,
)
from .identity import serial_from_name
from .models import MEASUREMENT_KEYS, TempraState
from .parser import FrameStream, decode_frame

_LOGGER = logging.getLogger(__name__)

#: Backoff bounds after a failed poll, seconds.
_BACKOFF_START = 5.0
_BACKOFF_MAX = POLL_INTERVAL

#: Persistent-connection timing. We hold the link open like the Dometic app
#: instead of connecting for a few seconds each minute. The bus master echoes a
#: relayed sibling only in bursts, minutes apart; polling misses most of them,
#: so staying connected is the only way a second battery shows up promptly and
#: stays. We nudge the realtime stream and periodically re-request a full dump.
_RECONNECT_DELAY = 5.0  # gap before reconnecting after the master drops us
_KEEPALIVE_INTERVAL = 12.0  # how often to nudge the realtime stream (APP+RDN=1)
_REDATA_INTERVAL = 40.0  # how often to re-request a full all-battery dump (APP+DAT)
_SESSION_MAX = 600.0  # recycle the link this often for a clean re-handshake

#: A node counts as a battery once it reports a plausible state of charge and
#: pack voltage. Other bus devices (e.g. a solar regulator) do not report SoC,
#: so this keeps us from inventing batteries for them -- while not waiting on
#: the per-cell frames: a relayed battery sends its SoC/voltage burst and its
#: cell burst separately, and the master may echo only one of them for a while.
_BATTERY_FIELDS = ("soc", "voltage")


class TempraBleDevice:
    """Owns the BLE connection and tracks one battery per bus node."""

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
        self._base_name = name or ble_device.name or ble_device.address
        self._listeners: list[Callable[[], None]] = []
        self._new_node_cb: Callable[[int], None] | None = None

        self._client: BleakClientWithServiceCache | None = None
        self._stream = FrameStream()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

        # Credit-flow state, reset on every poll.
        self._remote_credits = 0
        self._local_credits = 0
        self._credit_event = asyncio.Event()
        self._access_granted = asyncio.Event()
        self._access_denied = asyncio.Event()
        self._network_seen = asyncio.Event()
        self._dropped = asyncio.Event()
        self._wake = asyncio.Event()

        # Per-node battery state.
        self._states: dict[int, TempraState] = {}
        self._last_success: dict[int, float] = {}
        self._battery_nodes: set[int] = set()

        # Latest frame of the undecoded second stream (type 0x4D), keyed
        # "<type>:<node>:<cmd>", kept only so diagnostics can expose it.
        self._aux_frames: dict[str, str] = {}

    # -- public API ---------------------------------------------------------

    @property
    def address(self) -> str:
        """Bluetooth address of the bus master."""
        return self._ble_device.address

    @property
    def name(self) -> str:
        """Advertised name of the bus master."""
        return self._base_name

    @property
    def battery_nodes(self) -> list[int]:
        """Bus addresses that have identified themselves as batteries, sorted."""
        return sorted(self._battery_nodes)

    @property
    def states(self) -> dict[int, TempraState]:
        """Latest state per battery node."""
        return self._states

    @property
    def aux_frames(self) -> dict[str, str]:
        """Latest frame of the undecoded second stream, for diagnostics."""
        return dict(self._aux_frames)

    def state(self, node: int) -> TempraState | None:
        """Latest state of one battery node, if seen."""
        return self._states.get(node)

    def battery_index(self, node: int) -> int:
        """1-based position of a node among the batteries (stable, sorted)."""
        nodes = self.battery_nodes
        return nodes.index(node) + 1 if node in nodes else 0

    @property
    def available(self) -> bool:
        """Whether any battery has a recent reading."""
        return any(self.node_available(node) for node in self._battery_nodes)

    def node_available(self, node: int) -> bool:
        """Whether one battery should still be shown.

        A battery is available while its own reading is fresh. An
        already-identified battery also stays available as long as the bus
        connection keeps delivering data from *some* battery: the master echoes
        a relayed sibling only in bursts, minutes apart, but both are wired in
        parallel and physically present, so only a connection that falls silent
        altogether should blank them -- and then every node ages out together.
        """
        last = self._last_success.get(node)
        if last is None:
            return False
        now = time.monotonic()
        if (now - last) < DATA_STALE_AFTER:
            return True
        if node in self._battery_nodes:
            newest = max(self._last_success.values(), default=0.0)
            return (now - newest) < DATA_STALE_AFTER
        return False

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired on every state change. Returns a remover."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def set_new_node_callback(self, callback: Callable[[int], None] | None) -> None:
        """Set a callback fired once, when a new battery node is first seen."""
        self._new_node_cb = callback
        # Replay nodes already discovered before the platform subscribed.
        if callback is not None:
            for node in self.battery_nodes:
                callback(node)

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        """Adopt a fresher ``BLEDevice`` (new advertisement, better adapter)."""
        self._ble_device = ble_device
        if ble_device.name:
            self._base_name = ble_device.name

    def request_poll(self) -> None:
        """Cut the wait short, e.g. when the master advertises again."""
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
            connected = False
            async with self._radio:
                try:
                    connected = await self._async_session()
                except asyncio.CancelledError:
                    raise
                except (BleakError, TimeoutError, OSError) as err:
                    _LOGGER.debug("%s: session ended: %s", self._base_name, err)
                except Exception:  # noqa: BLE001 - the loop must never die
                    _LOGGER.exception("%s: unexpected error in session", self._base_name)
                finally:
                    await self._async_disconnect()

            self._notify()
            if self._stopping:
                break
            if connected:
                # Held the link; reconnect promptly to stay effectively always-on.
                backoff = _BACKOFF_START
                await self._async_backoff(_RECONNECT_DELAY)
            else:
                # Could not establish a usable link; back off *uninterruptibly*.
                # The battery keeps advertising even while refusing connections,
                # and reacting to that is what turns a brief proxy blip into a
                # reconnect storm that wedges its single BLE slot. async_stop()
                # still cancels the task, so disabling stays instant.
                await self._async_backoff(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    async def _async_wait(self, delay: float) -> None:
        """Wait, but let a fresh advertisement cut a long wait short."""
        self._wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=delay)

    async def _async_backoff(self, delay: float) -> None:
        """Wait out a post-failure backoff, ignoring advertisement wake-ups.

        Unlike :meth:`_async_wait` this is not cut short by ``request_poll``:
        while the battery is refusing connections it still advertises, and
        reacting to every advertisement would reconnect in a tight loop. The
        task is still cancellable, so :meth:`async_stop` remains instant.
        """
        self._wake.clear()
        await asyncio.sleep(delay)

    async def _async_session(self) -> bool:
        """Connect, handshake, then hold the link open and keep it streaming.

        The Dometic app stays connected rather than polling, and that is the
        only way a relayed sibling -- echoed by the master only in bursts,
        minutes apart -- shows up promptly and stays. After the handshake we
        keep the realtime stream alive (APP+RDN=1) and periodically re-request a
        full all-battery dump (APP+DAT), until the master drops us, we are asked
        to stop, or the session has run long enough to warrant a clean restart.

        Returns True once the link was established and handshook (so reconnect
        soon), False if it never got that far (so back off).
        """
        self._stream.reset()
        self._remote_credits = 0
        self._local_credits = 0
        self._credit_event.clear()
        self._access_granted.clear()
        self._access_denied.clear()
        self._network_seen.clear()
        self._dropped.clear()

        _LOGGER.debug("%s: connecting to %s", self._base_name, self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self._base_name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: self._ble_device,
            use_services_cache=True,
        )
        self._client = client

        await client.start_notify(NOTIFY_CHAR_UUID, self._on_notification)
        await client.start_notify(TX_CREDITS_CHAR_UUID, self._on_notification)
        with contextlib.suppress(BleakError):
            await client.start_notify(TX_MTU_CHAR_UUID, self._on_notification)

        await self._async_grant_local_credits(client)
        await asyncio.sleep(0.3)
        try:
            await asyncio.wait_for(self._credit_event.wait(), timeout=CREDIT_WAIT_TIMEOUT)
        except TimeoutError:
            _LOGGER.debug("%s: battery granted no credits", self._base_name)
            return False

        await self._async_handshake(client)

        started = time.monotonic()
        last_redata = started
        while not self._stopping and not self._dropped.is_set():
            if time.monotonic() - started > _SESSION_MAX:
                _LOGGER.debug("%s: recycling the link after a long session", self._base_name)
                break
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._dropped.wait(), timeout=_KEEPALIVE_INTERVAL)
            if self._stopping or self._dropped.is_set():
                break
            now = time.monotonic()
            try:
                if now - last_redata >= _REDATA_INTERVAL:
                    await self._async_send(client, CMD_DATA)
                    last_redata = now
                await self._async_send(client, CMD_REALTIME_START)
            except (BleakError, TimeoutError):
                break
        return True

    async def _async_handshake(self, client: BleakClientWithServiceCache) -> None:
        """Run the reply-driven command sequence that starts telemetry."""
        if not await self._async_authenticate(client):
            return
        await self._async_send(client, CMD_NETWORK)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(
                self._network_seen.wait(), timeout=COMMAND_REPLY_TIMEOUT
            )
        await self._async_send(client, CMD_DATA)
        await self._async_send(client, CMD_REALTIME_START)

    async def _async_authenticate(self, client: BleakClientWithServiceCache) -> bool:
        """Claim access, resetting the stored password if the master denies it."""
        if await self._async_request_access(client):
            return True
        if not self._access_denied.is_set() or not RESET_ON_ACCESS_DENIED:
            return False

        _LOGGER.warning(
            "%s: access denied (MST+ANE) -- resetting the password and claiming "
            "the bus for Home Assistant (the Dometic app loses access)",
            self._base_name,
        )
        self._access_denied.clear()
        self._access_granted.clear()
        await self._async_send(client, CMD_RESET_PASSWORD)
        await asyncio.sleep(1.0)
        if self._dropped.is_set():
            _LOGGER.debug("%s: link dropped after reset; re-claim next poll", self._base_name)
            self.request_poll()
            return False
        if await self._async_request_access(client):
            _LOGGER.info("%s: password reset and re-claimed successfully", self._base_name)
            return True
        _LOGGER.warning("%s: still denied after reset attempt", self._base_name)
        return False

    async def _async_request_access(self, client: BleakClientWithServiceCache) -> bool:
        """Send APP+AEN and wait for grant/denial. True if granted."""
        self._access_granted.clear()
        self._access_denied.clear()
        await self._async_send(client, CMD_ACCESS.format(token=self._auth_token))
        try:
            await asyncio.wait_for(
                self._wait_either(self._access_granted, self._access_denied),
                timeout=HANDSHAKE_REPLY_TIMEOUT,
            )
        except TimeoutError:
            _LOGGER.debug("%s: no reply to access request", self._base_name)
            return False
        return self._access_granted.is_set()

    async def _wait_either(self, a: asyncio.Event, b: asyncio.Event) -> None:
        tasks = [asyncio.ensure_future(a.wait()), asyncio.ensure_future(b.wait())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()

    async def _async_send(
        self, client: BleakClientWithServiceCache, command: str
    ) -> None:
        """Write one ASCII command, waiting for an outbound credit first."""
        if self._dropped.is_set():
            raise BleakError("master closed the connection mid-handshake")
        try:
            await asyncio.wait_for(
                self._credit_event.wait(), timeout=CREDIT_WAIT_TIMEOUT
            )
        except TimeoutError as err:
            raise BleakError("no outbound credit to send command") from err

        _LOGGER.debug("%s: -> %r (credits=%d)", self._base_name, command, self._remote_credits)
        await client.write_gatt_char(
            WRITE_CHAR_UUID, command.encode("ascii"), response=False
        )
        self._remote_credits -= 1
        if self._remote_credits <= 0:
            self._credit_event.clear()

    async def _async_grant_local_credits(
        self, client: BleakClientWithServiceCache
    ) -> None:
        """Grant the master a fresh budget of inbound packets."""
        grant = INITIAL_LOCAL_CREDITS - self._local_credits
        if grant <= 0:
            return
        self._local_credits += grant
        _LOGGER.debug("%s: granting %d inbound credits", self._base_name, grant)
        with contextlib.suppress(BleakError):
            await client.write_gatt_char(
                RX_CREDITS_CHAR_UUID, bytes([grant & 0xFF]), response=True
            )

    async def _async_disconnect(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        with contextlib.suppress(BleakError, TimeoutError, OSError):
            await client.disconnect()

    # -- callbacks ----------------------------------------------------------

    def _on_disconnected(self, _client: BleakClientWithServiceCache) -> None:
        _LOGGER.debug("%s: disconnected", self._base_name)
        self._dropped.set()

    def _on_notification(
        self, sender: BleakGATTCharacteristic, data: bytearray
    ) -> None:
        raw = bytes(data)
        uuid = sender.uuid.lower()

        if uuid == TX_CREDITS_CHAR_UUID:
            if raw:
                self._remote_credits = min(self._remote_credits + raw[0], 0xFF)
                if self._remote_credits > 0:
                    self._credit_event.set()
            return

        if uuid == TX_MTU_CHAR_UUID:
            return

        # Data channel (0x0002): every packet here spends one inbound credit.
        self._spend_local_credit()
        _LOGGER.debug("%s: <- %s %r", self._base_name, raw.hex(" "), raw)

        if raw[:4] == b"MST+":
            self._handle_ascii_reply(raw)

        changed_nodes: set[int] = set()
        for frame in self._stream.feed(raw):
            if not frame.is_battery:
                # Second stream (type 0x4D): not battery registers. Keep the
                # latest of each for diagnostics, but never decode it as a
                # battery -- doing so invented phantom nodes before.
                self._aux_frames[f"{frame.type_:02X}:{frame.node:02X}:{frame.cmd:02X}"] = (
                    frame.payload.hex()
                )
                continue
            values = decode_frame(frame)
            node = frame.node
            if values is not None:
                self._apply(node, values)
                changed_nodes.add(node)
            elif frame.cmd in UNDECODED_COMMANDS:
                state = self._states.get(node)
                if state is not None:
                    undecoded = dict(state.undecoded)
                    undecoded[frame.cmd] = frame.payload.hex()
                    self._states[node] = state.with_values({"undecoded": undecoded})
            # Any frame for an already-identified battery proves it is still on
            # the bus. A relayed battery streams detail frames constantly but
            # its headline registers only occasionally, so keep it "available"
            # on any of its frames instead of dropping it between full updates.
            self._touch(node)

        if changed_nodes:
            self._notify()

    def _apply(self, node: int, values: dict[str, object]) -> None:
        """Merge decoded values into one node's state, tracking availability."""
        state = self._states.get(node)
        if state is None:
            state = TempraState(address=self.address, name=self._node_name(node))
        state = state.with_values(values)
        self._states[node] = state

        if state.voltage is not None and state.soc is not None:
            self._last_success[node] = time.monotonic()

        if node not in self._battery_nodes and all(
            getattr(state, field) is not None for field in _BATTERY_FIELDS
        ):
            self._battery_nodes.add(node)
            # Re-name now that we know the battery's index among its siblings.
            self._states[node] = state.with_values({})
            _LOGGER.info(
                "%s: discovered battery on bus node 0x%02X", self._base_name, node
            )
            if self._new_node_cb is not None:
                self._new_node_cb(node)

    def _touch(self, node: int) -> None:
        """Refresh a known battery's liveness from any of its frames.

        Only once the node has a full-enough reading (voltage and SoC) does a
        bare frame count as "still alive"; before that it must earn
        availability through decoded measurements like any other node. This is
        what keeps a slow relayed battery from dropping out between the rare
        polls that carry its headline registers.
        """
        state = self._states.get(node)
        if state is not None and state.voltage is not None and state.soc is not None:
            self._last_success[node] = time.monotonic()

    def _node_name(self, node: int) -> str:
        """A stable display name for one battery node."""
        return f"{self._base_name} · Node {node:02X}"

    def _handle_ascii_reply(self, raw: bytes) -> None:
        reply = raw[:7].decode("ascii", "replace")
        if reply == REPLY_ACCESS_GRANTED:
            self._access_granted.set()
        elif reply == REPLY_ACCESS_DENIED:
            self._access_denied.set()
        elif reply == REPLY_NETWORK:
            self._network_seen.set()
        elif reply == REPLY_WAIT:
            _LOGGER.debug("%s: bus asked to wait (MST+WAI)", self._base_name)

    def _spend_local_credit(self) -> None:
        """Account for one inbound packet, refilling the grant when it runs low."""
        self._local_credits -= 1
        if self._local_credits <= LOCAL_CREDIT_REFILL_THRESHOLD:
            client = self._client
            if client is not None:
                asyncio.create_task(self._async_grant_local_credits(client))

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()
