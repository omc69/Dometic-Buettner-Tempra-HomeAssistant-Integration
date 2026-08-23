#!/usr/bin/env python3
"""Connect to a Tempra TLB150 and dump its telemetry stream.

Standalone companion to the integration: it reuses the exact same protocol
layer but runs outside Home Assistant, which is what you want when working on
the open items in ``docs/dometic_tempra_ble_protocol.md`` -- e.g. toggling
shore power while watching 0x60, or sweeping load steps to pin down 0x36.

    pip install bleak
    python tools/tempra_dump.py --list
    python tools/tempra_dump.py --address AA:BB:CC:DD:EE:FF --raw

Remember the battery accepts a single BLE connection: quit the Dometic app
first, and stop the Home Assistant integration before running this.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from fnmatch import fnmatch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "dometic_tempra"))

from bleak import BleakClient, BleakScanner  # noqa: E402

from tempra_ble.const import (  # noqa: E402
    DEFAULT_AUTH_TOKEN,
    HANDSHAKE_DELAY,
    LOCAL_NAME_PATTERN,
    NOTIFY_CHAR_UUID,
    UNDECODED_COMMANDS,
    WRITE_CHAR_UUID,
)
from tempra_ble.parser import FrameStream, decode_frame  # noqa: E402


async def scan(timeout: float) -> None:
    """Print every Tempra battery in range."""
    print(f"Scanning {timeout:.0f}s for {LOCAL_NAME_PATTERN} ...")
    devices = await BleakScanner.discover(timeout=timeout)
    found = [d for d in devices if d.name and fnmatch(d.name, LOCAL_NAME_PATTERN)]
    if not found:
        print("No Tempra battery found.")
        return
    for device in found:
        print(f"  {device.address}  {device.name}")


async def dump(address: str, token: str, show_raw: bool, duration: float) -> None:
    """Connect, handshake, and print frames until the duration elapses."""
    stream = FrameStream()
    seen_undecoded: dict[int, str] = {}

    def on_notify(_sender: object, data: bytearray) -> None:
        for frame in stream.feed(bytes(data)):
            values = decode_frame(frame)
            if values is not None:
                rendered = "  ".join(f"{k}={v}" for k, v in values.items())
                print(f"0x{frame.cmd:02X}  {frame.payload.hex(' ')}  ->  {rendered}")
                continue
            payload = frame.payload.hex(" ")
            if not show_raw:
                # Only announce an undecoded command when its payload changes,
                # so a constant register does not drown out the interesting ones.
                if seen_undecoded.get(frame.cmd) == payload:
                    continue
                seen_undecoded[frame.cmd] = payload
            note = UNDECODED_COMMANDS.get(frame.cmd, "unknown")
            print(f"0x{frame.cmd:02X}  {payload}      ({note})")

    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        await client.start_notify(NOTIFY_CHAR_UUID, on_notify)
        for command in (f"APP+AEN={token}", "APP+NET", "APP+DAT", "APP+RDN=1"):
            print(f"  -> {command}")
            await client.write_gatt_char(WRITE_CHAR_UUID, command.encode("ascii"))
            await asyncio.sleep(HANDSHAKE_DELAY)
        print(f"Streaming for {duration:.0f}s (Ctrl-C to stop)\n")
        await asyncio.sleep(duration)


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="scan and exit")
    parser.add_argument("--address", help="battery MAC / UUID to connect to")
    parser.add_argument("--token", default=DEFAULT_AUTH_TOKEN, help="handshake token")
    parser.add_argument(
        "--raw", action="store_true", help="print undecoded frames on every repeat"
    )
    parser.add_argument(
        "--duration", type=float, default=60.0, help="seconds to stream (default 60)"
    )
    parser.add_argument(
        "--scan-timeout", type=float, default=10.0, help="scan seconds (default 10)"
    )
    args = parser.parse_args()

    try:
        if args.list or not args.address:
            asyncio.run(scan(args.scan_timeout))
            if not args.address:
                return
        asyncio.run(dump(args.address, args.token, args.raw, args.duration))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
