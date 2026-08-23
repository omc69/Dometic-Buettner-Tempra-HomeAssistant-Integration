# Handover: Dometic Büttner Tempra TLB150 BLE → Home Assistant Integration

**An:** HA-Entwickler-Agent
**Von:** Christian
**Bezug:** `dometic_tempra_ble_protocol.md` (liegt im selben Paket, vollständige Protokoll-Reverse-Engineering-Doku — bitte zuerst lesen)
**Ziel:** Neues HA-Add-on `tempra_batteries`, analog zum bestehenden `caratec_batteries`-Add-on (Barrot BR2262e / PACE-Protokoll), das die Dometic Büttner Tempra TLB150 Batterien per BLE ausliest und die Werte per MQTT mit HA-Auto-Discovery bereitstellt.

---

## 1. Kontext / Referenzarchitektur

Es existiert bereits ein produktives HA-Add-on für ein anderes BMS (`caratec_batteries`, `bms.py` v2.2.0) mit folgender bewährter Struktur — bitte als Vorlage für Code-Stil, Config-Schema und MQTT-Topic-Konvention nutzen:

- Custom HA Add-on (Supervisor-basiert), Python, `bleak` für BLE.
- MQTT-Publish mit HA-Auto-Discovery (`homeassistant/sensor/<device_id>/<field>/config`).
- Bekannte Add-on-Config-Fallstricke (aus Erfahrung mit `caratec_batteries`, gelten vermutlich identisch hier):
  - `privileged` **muss eine Liste sein**, nicht ein einzelner String.
  - `host_network: true` erforderlich für BLE-Zugriff.
  - `apparmor: false` erforderlich, sonst blockiert das BLE-Subsystem.

**BLE-Limit:** Die Batterie erlaubt nur **eine gleichzeitige BLE-Verbindung**. Das Add-on darf also nicht gleichzeitig mit der Dometic-App auf dem Handy laufen — für den produktiven Betrieb unkritisch (App wird nur gelegentlich zur Diagnose genutzt), aber wichtig für Testing.

## 2. Zielgeräte

| Feld | Wert |
|---|---|
| Advertising-Name-Pattern | `KAA_<Seriennummer>_TLB150` |
| Bekannte Geräte im Feld | `KAA_502048_TLB150` (produktiv), `KAA_502269_TLB150` (zweite Batterie) |
| Anzahl Batterien im System | 3× TLB150 laut Setup (Victron/HA-Doku), aktuell 2 im Test |
| Verbindungslimit | 1 gleichzeitige BLE-Connection pro Batterie |

## 3. Implementierungsschritte

### 3.1 Verbindungsaufbau + Handshake (Pflicht)

```python
# Pseudocode — Details siehe dometic_tempra_ble_protocol.md Abschnitt 3
async def connect_and_handshake(client: BleakClient):
    WRITE_CHAR = "00000001-0000-1000-8000-008025000000"
    NOTIFY_CHAR = "00000002-0000-1000-8000-008025000000"

    await client.start_notify(NOTIFY_CHAR, notification_handler)

    await client.write_gatt_char(WRITE_CHAR, b"APP+AEN=f560f1deba")  # Auth-Token, siehe Doku Abschnitt 3
    await asyncio.sleep(0.3)
    await client.write_gatt_char(WRITE_CHAR, b"APP+NET")
    await asyncio.sleep(0.3)
    await client.write_gatt_char(WRITE_CHAR, b"APP+DAT")   # startet den Live-Telemetriestrom
    await asyncio.sleep(0.3)
    await client.write_gatt_char(WRITE_CHAR, b"APP+RDN=1")
```

**Wichtig:** Ohne `APP+DAT` bleibt der Notify-Kanal stumm. Reihenfolge einhalten, kurze Delays zwischen den Writes (App macht das auch, vermutlich zur Vermeidung von BLE-Write-Queue-Überlauf).

### 3.2 Notification-Parser

Alle Notify-Payloads auf `00000002` filtern nach Sync-Header `23 85 CF`. ASCII-Antworten (`MST+...`) können für Debug-Logging mitgeschrieben, aber für den Sensor-Betrieb ignoriert werden.

```python
def parse_frame(data: bytes) -> dict | None:
    if len(data) < 8 or data[0:3] != bytes.fromhex("2385CF"):
        return None
    cmd = data[3]
    payload = data[4:8]

    if cmd == 0x02:
        voltage = int.from_bytes(payload[0:2], "big") / 100.0
        raw_current = int.from_bytes(payload[2:4], "big")
        sign = -1 if (raw_current & 0x8000) else 1
        magnitude = (raw_current & 0x7FFF) / 100.0
        current = sign * magnitude
        return {"voltage": voltage, "current": current, "power": round(voltage * current, 1)}

    if cmd == 0x0B:
        return {"soc": payload[0]}

    if cmd == 0x0E:
        return {"soh": payload[0]}

    if cmd == 0x07:
        return {"capacity_ah": payload[3]}

    if cmd == 0x56:
        return {"cell_1_mv": int.from_bytes(payload[0:2], "big"),
                "cell_2_mv": int.from_bytes(payload[2:4], "big")}

    if cmd == 0x57:
        return {"cell_3_mv": int.from_bytes(payload[0:2], "big"),
                "cell_4_mv": int.from_bytes(payload[2:4], "big")}

    return None  # unbekanntes/nicht-implementiertes Cmd, siehe Doku Abschnitt 4.2
```

**Hinweis Vorzeichen-Bit:** In der Doku ist das Sign-Bit als `byte3 & 0x80` beschrieben (Byte-Ebene). Im Pseudocode oben ist das äquivalent über die 16-Bit-Maske `0x8000` auf das big-endian-Wort ausgedrückt — beide Schreibweisen sind identisch, im Zweifel gegen echte Captures testen.

### 3.3 MQTT-Sensoren (Auto-Discovery)

Analog zum `caratec_batteries`-Schema, Topic-Präfix z.B. `tempra_batteries/<seriennummer>/`:

| Sensor | Unit | HA device_class |
|---|---|---|
| `voltage` | V | voltage |
| `current` | A | current |
| `power` | W | power |
| `soc` | % | battery |
| `soh` | % | — |
| `capacity_ah` | Ah | — |
| `cell_1_mv` … `cell_4_mv` | mV | voltage |

### 3.4 Config-Schema (Add-on)

```yaml
options:
  devices:
    - name: "KAA_502048_TLB150"
      mac: "10:23:81:8B:13:AD"   # Beispiel — echte MACs im Deployment eintragen
    - name: "KAA_502269_TLB150"
      mac: "TBD"
  mqtt_broker: "core-mosquitto"
  poll_interval_hint: 5  # Sekunden, da Stream kontinuierlich läuft nach APP+DAT
privileged:
  - SYS_ADMIN   # Liste, nicht String! (bekannter Fallstrick)
host_network: true
apparmor: false
```

## 4. Bekannte Lücken — nicht blockierend für v1, aber vormerken

Siehe `dometic_tempra_ble_protocol.md` Abschnitt 4.2 für Details. Für v1 der Integration können diese Felder weggelassen werden:

- `0x0C`, `0x34`, `0x35`, `0x36` — vermutlich Temperatur/Innenwiderstand, noch nicht kalibriert.
- `0x60` — vermutlich Status-Flags (Poles Status, Internal Regulator), Bit-Mapping noch offen.
- `0x90`, `0xA0`, `0xA1`, `0xC0`, `0xF1`, `0xF2` — vermutlich Alarm-Register, bisher nur im "kein Alarm"-Zustand beobachtet.

**Für später:** Falls Dometic/Büttner (Support-Kontakt Herr Kortmann, `service@buettner-elektronik.de`) offizielle Protokoll-Docs liefert, Mapping-Tabelle in Abschnitt 4 der Protokoll-Doku entsprechend nachziehen.

## 5. Test-/Abnahmekriterien für v1

- [ ] Verbindung zu beiden bekannten Geräten (`502048`, `502269`) gleichzeitig möglich (sequenziell, da 1-Connection-Limit pro Gerät, aber parallel über zwei BLE-Sessions zu unterschiedlichen Geräten sollte gehen).
- [ ] Spannung, Strom, SOC in HA sichtbar und plausibel (Vergleich mit Dometic-App bei mind. 2 Lastzuständen: Idle und unter Last).
- [ ] Reconnect-Handling: Verbindungsabbruch (z.B. wenn App parallel verbindet) wird erkannt und Add-on versucht automatisch Reconnect + Handshake neu.
- [ ] HA-Auto-Discovery erzeugt alle Sensoren korrekt beim ersten Start, ohne manuelle `configuration.yaml`-Einträge.
