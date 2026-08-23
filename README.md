<img src="icon.png" width="96" align="right" alt="Dometic">

# Dometic Büttner Tempra — Home Assistant Integration

Reads **Dometic Büttner Tempra TLB150** LiFePO₄ batteries over Bluetooth LE and
exposes them as native Home Assistant sensors. No MQTT broker, no add-on, no
cloud — the integration talks to the battery directly through Home Assistant's
own Bluetooth stack.

The BLE protocol was reverse engineered from HCI traces; the full write-up is in
[`docs/dometic_tempra_ble_protocol.md`](docs/dometic_tempra_ble_protocol.md).

## Sensors

| Sensor | Unit | Notes |
|---|---|---|
| Voltage | V | pack voltage |
| Current | A | negative = discharge, positive = charge |
| Power | W | derived as `voltage × current` — the battery does not transmit it, and the Dometic app computes it the same way |
| Battery | % | state of charge |
| State of health | % | diagnostic |
| Rated capacity | Ah | diagnostic, 150 Ah on a TLB150 |
| Cell 1–4 voltage | mV | diagnostic, useful for spotting drift in the 4S pack |

### Battery bank

With more than one battery configured, a **Tempra battery bank** device
aggregates them:

| Sensor | Unit | Notes |
|---|---|---|
| Bank voltage | V | mean — batteries in parallel share one voltage |
| Bank current | A | sum |
| Bank power | W | sum |
| Battery | % | state of charge, **weighted by capacity** — an even mean misreports a bank of mixed sizes |
| Remaining capacity | Ah | sum of each battery's share |
| Bank capacity | Ah | diagnostic, sum of the rated capacities |
| Cell spread | mV | diagnostic, widest gap between any two cells in the bank — the number worth watching on LiFePO₄ |
| Batteries reporting | | diagnostic, how many of the configured batteries answered |

Aggregates cover the batteries currently reporting, so one battery missing a
turn skews nothing silently — "Batteries reporting" says how many went in.

### Polling

Batteries take turns on the Bluetooth adapter: one connects, runs the
handshake, collects a reading, disconnects, and hands the radio on. A reading
per battery per minute, which is ample for a battery bank.

That is deliberate. Holding a connection open to every battery at once is
unreliable on a Raspberry Pi's onboard radio at the signal levels a bank in a
vehicle actually produces — whichever battery came third would fail, and which
one rotated between runs. Taking turns removes the contention.

## Requirements

- Home Assistant 2025.2 or newer with the `bluetooth` integration set up
- A **connectable** Bluetooth adapter in range of the battery — the host's own
  adapter or an ESPHome Bluetooth proxy. Passive-only proxies (e.g. a Shelly BLU
  Gateway) can see the battery advertise but cannot connect to it.

## Installation

**HACS** → ⋮ → *Custom repositories* → add
`https://github.com/omc69/Dometic-Buettner-Tempra-HomeAssistant-Integration`
as category *Integration* → install → restart Home Assistant.

Or copy `custom_components/dometic_tempra/` into your `config/custom_components/`
and restart.

## Adding batteries

Each battery is its own config entry, so a bank of two, three, or more is just
that many entries — nothing in the integration assumes a count.

Batteries advertise as `KAA_<serial>_TLB150` and are discovered automatically;
**Settings → Devices & Services** offers them as they appear. **Add integration
→ Dometic Büttner Tempra** then gives you two ways in:

- **Choose a battery that is in range** — pick from everything advertising right
  now, shown as `KAA_502048_TLB150 (10:23:81:8B:13:AD)`.
- **Enter a Bluetooth address** — type `AA:BB:CC:DD:EE:FF` directly, with an
  optional name. Use this for a battery that is not advertising at that moment,
  which happens whenever the Dometic app is connected to it: the app occupies
  the battery's only connection slot, so it disappears from discovery until you
  close the app. Colons, dashes, spaces, or no separator at all are all
  accepted.

Swapped a battery, or typed the address wrong? **Device → ⋮ → Reconfigure**
re-points an existing entry at a different address and keeps its entities,
history, and every dashboard reference.

## The one-connection rule

A TLB150 accepts **exactly one BLE connection at a time**. That has practical
consequences:

- While Home Assistant is connected, the **Dometic phone app cannot connect**,
  and vice versa. Closing the app frees the slot; the integration notices the
  next advertisement and reconnects without waiting out its backoff.
- Anything else on the same host that scans for and connects to BMS devices
  will fight for the same slot. If you run the **Batmon** add-on or a similar
  BLE battery monitor, exclude the Tempra batteries there.

Losing the connection is expected and handled: the integration reconnects with
backoff, re-runs the handshake, and marks entities unavailable while the stream
is down or has stalled for more than 90 seconds.

Note that the limit is one connection *per battery*, not one overall — a bank of
three batteries means three simultaneous connections held open on the same
adapter. A Raspberry Pi's onboard radio manages that, but it is close to what it
comfortably does alongside other BLE devices. If connections start flapping with
a larger bank, an ESPHome Bluetooth proxy near the batteries takes the load off
the host adapter and usually settles it.

## Troubleshooting

Turn on debug logging:

```yaml
logger:
  default: warning
  logs:
    custom_components.dometic_tempra: debug
```

**Settings → Devices & Services → Dometic Büttner Tempra → ⋮ → Download
diagnostics** dumps the current measurements *plus the raw payload of every
command that is not decoded yet* — see [open items](#open-protocol-items).

## Development

The protocol layer under `custom_components/dometic_tempra/tempra_ble/` has no
Home Assistant imports, and everything except `device.py` has no third-party
imports at all, so decoding is testable on its own:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest tests/ -q
```

Every byte sequence in `tests/test_parser.py` comes from a real capture,
together with the value the Dometic app displayed at that moment.

### Live capture tool

`tools/tempra_dump.py` runs the same handshake and parser outside Home
Assistant, which is the fastest way to work on the undecoded registers:

```bash
pip install bleak
python tools/tempra_dump.py --list
python tools/tempra_dump.py --address AA:BB:CC:DD:EE:FF --raw
```

Stop the integration first — one connection, remember.

## Open protocol items

Decoded and verified: voltage, current, SOC, SOH, capacity, cell voltages.
Still open (section 4.2 of the protocol document):

| Command | Hypothesis | How to pin it down |
|---|---|---|
| `0x34`, `0x35` | temperature, uncalibrated | correlate against the app's temperature reading |
| `0x36` | internal resistance or peak current | sweep discharge load in steps and log |
| `0x60` | status bitfield (poles status, internal regulator) | toggle shore power while capturing |
| `0x90`, `0xA0`, `0xA1`, `0xC0`, `0xF1`, `0xF2` | alarm registers | only ever seen in the no-alarm state |

The diagnostics dump and `tools/tempra_dump.py --raw` both surface these raw
payloads. PRs with captures welcome.

## Disclaimer

Not affiliated with, endorsed by, or supported by Dometic or Büttner
Elektronik. "Dometic" and the Dometic logo are trademarks of their respective
owners and are used here only to identify the compatible hardware. Use at your
own risk.

## License

MIT — see [LICENSE](LICENSE).
