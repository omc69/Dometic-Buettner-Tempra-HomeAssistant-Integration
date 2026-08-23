# Dometic Büttner Tempra TLB150 — BLE-Protokoll (Reverse Engineering)

**Status:** Kernwerte (Spannung, Strom, SOC, SOH, Kapazität, Zellspannungen) vollständig entschlüsselt und gegen die Dometic-App verifiziert.
**Methode:** Apple PacketLogger (iOS-BLE-HCI-Trace) parallel zur Dometic-App, Korrelation von App-Anzeige ↔ Rohbytes über mehrere Lastzustände (Idle, Entladung, Solar-Ladung).
**Geräte:** `KAA_502048_TLB150`, `KAA_502269_TLB150` (identisches GATT-Schema, firmwarefest).
**Datum:** August 2026.

---

## 1. Geräte-Identifikation

| Feld | Wert |
|---|---|
| Advertising-Name | `KAA_<Seriennummer>_TLB150` |
| Manufacturer Data | `45081485` (4 Byte, statisch, produktweit identisch — keine Geräte-ID) |
| BLE-Verbindungslimit | **1 gleichzeitige Verbindung** — Dometic-App und externe Tools können nicht parallel verbunden sein |

## 2. GATT-Struktur

| Service | UUID | Zweck |
|---|---|---|
| Device Information | `180A` (Standard) | nur PnP ID (`2A50`), keine Nutzdaten |
| Proprietär | `FEFB` | alle Batteriedaten |

### Characteristics unter Service `FEFB` (Basis-UUID `0000000X-0000-1000-8000-008025000000`)

| Char-ID | Handle | Notify/Indicate | Rolle |
|---|---|---|---|
| `00000001` | 0x0018 | Nein | **Write** — ASCII-Kommandos an die Batterie |
| `00000002` | 0x001A | **Ja** | **Notify** — ASCII-Antworten + Binär-Telemetrie |
| `00000003` | 0x001D | Nein | Write, Zweck ungeklärt (gesehene Value: `C8`) |
| `00000004` | 0x001F | **Ja** (Indication) | Indication, gesehene Value: `8E`, Zweck ungeklärt |
| `00000009` | — | Nein | Zweck ungeklärt |
| `0000000A` | 0x0016 | **Ja** | Notify, in Captures nicht mit Nutzdaten gesehen |

---

## 3. Verbindungs-Handshake (Pflichtsequenz)

Ohne diese Sequenz auf `00000001` (Write Command) liefert die Batterie **keine** Telemetrie über `00000002`.

| Schritt | Write auf `00000001` | Antwort auf `00000002` | Zweck |
|---|---|---|---|
| 1 | `APP+AEN=<10-stelliger Hex-Token>` z.B. `f560f1deba` | `MST+AEN` | Auth/Pairing-Enable |
| 2 | `APP+NET` | `MST+NET=85CF0105000805010205 01` | Config-Query (liefert Sync-Marker `85CF`) |
| 3 | `APP+DAT` | — | **Startet den Live-Binärdatenstrom** auf `00000002` |
| 4 | `APP+IMP` bzw. `APP+IMP=<Hex>` | `MST+IMP=B0..BF...` (16 sequenzielle Antworten) | Chunk-/Index-Transfer, Zweck im Detail ungeklärt (evtl. Konfig-Tabelle) |
| 5 | `APP+RDN=1` | — | Vermutlich Streaming-Freigabe/"Ready" |

**Wichtig:** Der `AEN`-Token in Schritt 1 scheint pro Verbindung/Session generiert zu werden (nicht statisch geprüft) — in allen Captures wurde derselbe Wert `f560f1deba` von der App gesendet, das deutet auf einen aus der MAC-Adresse oder einem Geräte-Fingerprint abgeleiteten, aber App-seitig konstanten Wert hin. Für eine eigene HA-Integration einfach denselben Wert senden.

---

## 4. Binärprotokoll (Telemetrie-Frames auf `00000002`)

**Frame-Format:**
```
23 85 CF <cmd:1 Byte> <payload:4 Byte>
```
`23 85 CF` ist der feste Sync-Header. Danach folgt eine 1-Byte-Kommando-ID und ein 4-Byte-Payload.

### 4.1 Vollständig entschlüsselte Felder

| Cmd | Feld | Formel | Beispiel-Rohdaten | Ergebnis | Verifiziert gegen App |
|---|---|---|---|---|---|
| `0x02` | **Spannung** | Byte 1-2 als uint16 `/ 100` → Volt | `05 3B ...` | 0x053B=1339 → 13,39V | ✓ 13,4V |
| `0x02` | **Strom** | Byte 3-4: Bit `0x80` in Byte 3 = Vorzeichen (1=Entladung/negativ, 0=Ladung/positiv); Betrag = `((Byte3 & 0x7F) << 8) \| Byte4) / 100` → Ampere | `... 85 48` | (0x05<<8\|0x48)/100 = 13,52A, negativ | ✓ -13,9A (Entladung, Klima an) |
| `0x02` | Strom (Ladefall) | s.o. | `... 00 98` | (0x00<<8\|0x98)/100 = 1,52A, positiv | ✓ Plausibel (Solar-Ladung, 98% SOC) |
| `0x0B` | **State of Charge (SOC)** | Byte 1 → % | `64 FF FF FF` | 100% | ✓ 100% |
| `0x0B` | SOC | s.o. | `63 FF FF FF` | 99% | ✓ 99% |
| `0x0B` | SOC | s.o. | `62 FF FF FF` | 98% | ✓ (Solar-Ladephase) |
| `0x0E` | **State of Health (SOH)** | Byte 1 → % | `64 00 00 00` | 100% | ✓ 100% |
| `0x07` | **Kapazität** | Byte 4 → Ah | `00 00 00 96` | 0x96=150 → 150Ah | ✓ 150Ah (TLB150 Nennkapazität) |
| `0x56` | Zellspannung 1+2 | Byte 1-2 `/1000`, Byte 3-4 `/1000` → Volt | `0D66 0D93` | 3430mV, 3475mV | ✓ Plausibel (4S LiFePO4, voll) |
| `0x57` | Zellspannung 3+4 | s.o. | `0D94 0D75` | 3476mV, 3445mV | ✓ Plausibel |
| `0x56`/`0x57` | Zellspannungen unter Last | s.o. | `0D02 0D0C` / `0D0D 0D04` | 3330-3341mV | ✓ Erwarteter Spannungsabfall unter -13,9A Last |

**Leistung (Watt)** wird **nicht** separat übertragen — die App berechnet sie clientseitig aus `Spannung × Strom` (verifiziert: 13,4V × -13,9A = -186,3W ≈ angezeigte -186W).

### 4.2 Noch nicht zugeordnete Felder

| Cmd | Beobachtete Rohdaten | Verhalten | Vermutung |
|---|---|---|---|
| `0x0C` | `02 EE FF FF` (konstant über alle Captures) | unverändert | Unklar — evtl. Kalibrierungs-/Referenzwert |
| `0x34` | `FF FF 02 4E` / `FF FF 02 52` / `FF FF 02 4B` (leicht variierend) | schwankt gering | evtl. Temperatur (roh, unkalibriert) |
| `0x35` | `08 19 04 DB` → `08 1A 04 E1` → `08 1A 04 E3` (leicht steigend) | steigt langsam | evtl. Temperatur oder BMS-interner Zähler |
| `0x36` | `08 1A FF FF` (idle) → `07 B7 FF FF` (Entladung) | ändert sich mit Laststatus | Kandidat für Innenwiderstand oder Peak-Strom-Marker |
| `0x54` | `00 4B 41 41` = ASCII `KAA` | konstant | Modell-/Hersteller-Präfix-String |
| `0x55` | `00 26 2D 97` | konstant | evtl. Seriennummer-Fragment oder Firmware-Build-ID |
| `0x60` | `60 00 01 00` | konstant in allen Captures (Poles Status=ON, Internal Regulator=OFF unverändert) | **Kandidat für Status-Flags-Bitfeld** — braucht Test mit Zustandswechsel (Landstrom kurz trennen/verbinden) zur Bit-Zuordnung |
| `0x90`, `0xA0`, `0xA1`, `0xC0`, `0xF1`, `0xF2` | überwiegend `00 00 00 00`, `0xA0`=`00 05 00 08`, `0xA1`=`05 01 02 08` | konstant, keine Alarme aktiv | vermutlich Alarm-/Status-Flag-Register (aktuell inaktiv, da keine Fehlerzustände im Test) |
| `0x14` | ASCII `NNN\n` (`4E 4E 4E 0A`) | konstant | Status-Kürzel oder Platzhalter, Bedeutung unklar |
| `0x00` | `00 00 00 00` (viele Wiederholungen am Frame-Ende) | Padding | vermutlich Stream-Terminator/Keepalive |

---

## 5. Offene Punkte für weitere Tests

1. **`0x60` Status-Flags:** Landstrom kurz trennen/wieder verbinden, dabei mitschneiden → sollte `Internal Regulator` oder `Poles Status` kippen lassen und das verantwortliche Bit sichtbar machen.
2. **`0x34`/`0x35`:** Vermutlich Temperatur — Test mit App-Blick gezielt auf Temperaturanzeige (falls die App eine numerische Temperatur zeigt, nicht nur "OK") und Korrelation der leicht schwankenden Rohwerte.
3. **`0x36`:** Verhalten unter verschiedenen Laststufen (z.B. 5A, 15A, 25A Entladung) sammeln, um zu prüfen, ob es linear mit Strom oder mit einem berechneten Innenwiderstand korreliert.
4. **`APP+IMP`-Sequenz (B0-BF):** Zweck unklar — evtl. Zellkonfigurationstabelle oder Balancing-Status. Niedrige Priorität für den produktiven HA-Sensor-Betrieb.

---

## 6. Nächster Schritt: HA-Integration

Mit den in Abschnitt 4.1 bestätigten Feldern lässt sich bereits ein vollständiger Sensor-Satz bauen:
- Spannung, Strom, Leistung (berechnet), SOC, SOH, Kapazität, 4× Zellspannung

Empfehlung: `bleak`-basiertes Python-Script analog zum bestehenden `caratec_batteries`-HA-Add-on (Barrot/PACE-Protokoll), das:
1. Verbindung aufbaut und den Handshake aus Abschnitt 3 automatisch durchführt,
2. Notify-Frames auf `00000002` parst und nach Cmd-ID dispatcht,
3. Werte per MQTT mit HA-Auto-Discovery published.

---

## 7. Corrections from live hardware (2026-08-23)

Observed by the Home Assistant integration against `KAA_502269_TLB150`
(`48:02:AF:99:A4:93`) on Home Assistant 2026.8.2 / BlueZ, Raspberry Pi 5
onboard adapter. These supersede the corresponding entries above.

### 7.1 Devices in the field

A third battery exists that is not listed in section 1:

| Name | Address |
|---|---|
| `KAA_502048_TLB150` | `00:21:7E:72:EE:12` |
| `KAA_502269_TLB150` | `48:02:AF:99:A4:93` |
| `KAA_502039_TLB150` | `48:02:AF:99:A4:9B` |

Note the two address ranges: `502269` and `502039` share an OUI, `502048`
does not — so the fleet spans at least two hardware or module revisions.

### 7.2 Actual GATT table

Handles differ from the iOS trace by one to two, and the properties are more
specific than section 2 records. Everything is addressed by UUID, so the
handle drift is harmless, but the **properties** matter.

| Char | Handle (actual) | Handle (doc) | Properties |
|---|---|---|---|
| `00000009` | 0x0012 | — | write |
| `0000000A` | 0x0014 | 0x0016 | **indicate** |
| `00000001` | 0x0017 | 0x0018 | **write-without-response** |
| `00000002` | 0x0019 | 0x001A | notify |
| `00000003` | 0x001C | 0x001D | write |
| `00000004` | 0x001E | 0x001F | indicate |

Services present: `1800`, `1801`, `180A` (only PnP ID `2A50`), and `FEFB`.

### 7.3 The C8 write to 0x0003 is mandatory

Section 2 records that the app writes `C8` to `00000003` but calls the purpose
unclear. It is what keeps the session alive.

Without it the battery **drops the connection about 1.2 seconds after
connecting**, reproducibly, on all three batteries, regardless of what is
written to the ASCII command channel. With it the connection stays up for at
least 6.5 seconds and the write is acknowledged (`00000003` is
write-with-response, so this is a real acknowledgement, not an assumption).

Send it right after subscribing to `00000002`, before the `APP+` sequence.

> An earlier revision of this section claimed that subscribing to `0000000A`
> caused the 1.2 s disconnect. That was wrong: the same drop occurs with no
> subscription at all. The cause was the missing `C8` write.

### 7.4 Ruled out

- **Pairing / an encrypted link.** The battery refuses it outright —
  `org.bluez.Error.AuthenticationFailed` — and drops the connection in the
  process. The link is meant to be unencrypted.
- **A command terminator.** Each of `""`, `\r\n`, `\n` and `\r` was tried
  against all three batteries. Every variant was met with silence.
- **Handshake timing.** The five writes now complete in about 0.5 s, well
  inside the window the battery holds a connection open.
- **Wrong UUIDs.** The discovered GATT table matches section 2 (see 7.2).

### 7.5 Open: the battery answers nothing

State of play: the connection is stable, the `C8` session write is
acknowledged, and `APP+AEN` / `NET` / `DAT` / `IMP` / `RDN=1` all go out — and
the battery sends **nothing at all**, neither binary telemetry nor an `MST+`
ASCII reply.

Note that `00000001` is *write-without-response*, so a command the battery
rejects is discarded silently. There is no evidence any `APP+` command has
ever been understood.

Remaining candidates:

1. **The `APP+AEN` token is per-device.** Section 3 assumes it is app-constant
   because every capture showed `f560f1deba` — but those captures appear to
   come from a single battery, and a token derived from the MAC or serial
   would look constant in that sample. The decisive test is a fresh capture
   against a *different* battery, ideally `KAA_502048_TLB150`, which is on the
   other hardware generation (see 7.1).
2. **The ASCII commands go somewhere else.** `00000009` (handle 0x0012,
   write-with-response) pairs naturally with `0000000A` (0x0014, indicate),
   and `0000000A` is the only channel that has ever produced data — a `9b 00`
   indication. The section 2 handle-to-UUID mapping came from an iOS trace
   whose handles are off by one or two from what the device actually reports.
3. **Something in the sequence is still missing**, in the same way the `C8`
   write was missing.
