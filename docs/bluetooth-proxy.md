# Bluetooth proxy setup

## Why this is needed

The integration works over a Raspberry Pi's onboard Bluetooth radio, but only
just. Measured on a three-battery bank in a motorhome, August 2026:

| Battery | Address | RSSI seen |
|---|---|---|
| `KAA_502048_TLB150` | `00:21:7E:72:EE:12` | −72 to −82 dBm |
| `KAA_502269_TLB150` | `48:02:AF:99:A4:93` | −74 to −88 dBm |
| `KAA_502039_TLB150` | `48:02:AF:99:A4:9B` | −74 to −76 dBm |

At those levels roughly half of all connection attempts fail, in ways that are
unmistakably link quality rather than protocol:

- the battery drops the link under a second after connecting, before the
  session write lands;
- the session write to `0x0003` returns GATT error 0x0E (Unlikely Error);
- `Service Discovery has not been performed yet`, i.e. the link went away
  mid-sequence.

Which battery loses a round rotates between runs, and Home Assistant reports
plenty of free connection slots throughout — so this is not contention for
slots and not one faulty battery. It is path loss.

Polling the batteries in turn (see the README) removes the contention between
them and keeps every battery updating within a minute, but it cannot recover
30 dB of path loss. A proxy near the batteries can: a typical placement inside
or next to the battery compartment reads −40 to −50 dBm, which is a different
regime entirely.

## What does not work

**A Shelly BLU Gateway.** It forwards advertisements to Home Assistant, which
is enough for sensors that broadcast their readings, but it cannot open a GATT
connection. The Tempra only streams telemetry over a connection, so a Shelly
is no help here. Home Assistant says so directly in its log — with a Shelly
gateway present and a battery in range it still reports:

```
Found 1 connection path(s), preferred order: hci0 (...)
```

One path, the host adapter. Only an ESPHome proxy adds a second.

**A different USB dongle** changes the receiver, not the distance or whatever
sits between it and the batteries. An external antenna may buy 5–10 dB, which
may or may not be enough. A proxy at the batteries is the reliable fix.

## Hardware

Any **ESP32**, **ESP32-C3** or **ESP32-S3** works. Two that do not, and are
easy to buy by mistake:

- **ESP32-S2** has no Bluetooth at all.
- **ESP8266** likewise.

Which board to pick depends on where it can sit:

- **Outside the battery compartment, in sight of the batteries** — any cheap
  board is fine (M5Stack Atom Lite, ESP32-DevKitC, NodeMCU-32S).
- **Inside a metal compartment** — take a board with an external antenna
  connector (an ESP32-WROOM-32**U** with a U.FL/IPEX pigtail) and route the
  antenna out, otherwise the proxy's own Wi-Fi is the next thing to fail.
- **Wired networking available** — an Olimex ESP32-POE-ISO gives power and
  network over one cable and takes Wi-Fi out of the equation.

One proxy is enough. Because the integration polls the batteries in turn it
only ever needs a single connection, and an ESP32 proxy supports three.

## ESPHome configuration

The setting that matters is `active: true`. Without it the proxy only relays
advertisements — exactly as useless here as the Shelly.

```yaml
esphome:
  name: tempra-proxy
  friendly_name: Tempra Bluetooth Proxy

esp32:
  board: esp32dev          # m5stack-atom for an Atom Lite, esp32-c3-devkitm-1 for a C3
  framework:
    type: esp-idf          # esp-idf handles BLE connections better than Arduino here

logger:
api:
  encryption:
    key: !secret api_encryption_key
ota:
  - platform: esphome
    password: !secret ota_password

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password
  # A proxy that loses Wi-Fi should come back on its own rather than sit in a
  # captive portal nobody will ever open.
  ap:
    ssid: "Tempra Proxy Fallback"

esp32_ble_tracker:
  scan_parameters:
    # Keep the radio listening almost continuously: the batteries advertise
    # steadily and Home Assistant uses those advertisements to decide when a
    # poll can succeed.
    interval: 1100ms
    window: 1100ms
    active: true

bluetooth_proxy:
  active: true             # <- without this, connections do not work
```

For a board with an external antenna, add:

```yaml
esp32:
  board: esp32dev
  framework:
    type: esp-idf

# Only for modules that actually have both antennas wired (e.g. -32U variants
# where the manufacturer exposes the switch); most boards need nothing here.
```

Flash it, and Home Assistant discovers the proxy through the ESPHome
integration on its own.

## Verifying it took over

Turn on debug logging for the integration:

```yaml
logger:
  logs:
    custom_components.dometic_tempra: debug
    habluetooth.wrappers: debug
```

Then look for the connection-path line. Before the proxy:

```
Found 1 connection path(s), preferred order: hci0 (2C:CF:67:8A:8C:C7) (RSSI=-88)
```

After, it should list two paths with the proxy scoring better, and connections
should go through it:

```
Found 2 connection path(s), preferred order: tempra-proxy (RSSI=-46) ..., hci0 (RSSI=-88) ...
```

Home Assistant picks the path with the better score by itself; nothing in the
integration needs changing.

What should improve:

- `poll failed` lines become rare instead of roughly every other attempt;
- all batteries report continuously rather than one going stale at a time;
- rarely-sent fields — state of health, rated capacity, cell voltages — fill
  in promptly, because the connection survives long enough to receive them.

## If it still misbehaves

Check the proxy's own RSSI to the batteries first. If it reads worse than
−70 dBm the proxy itself is too far away or shadowed, and moving it is worth
more than any further tuning. `POLL_INTERVAL` in
`custom_components/dometic_tempra/tempra_ble/const.py` can be lowered once the
link is good, since a healthy connection completes a snapshot in a couple of
seconds.
