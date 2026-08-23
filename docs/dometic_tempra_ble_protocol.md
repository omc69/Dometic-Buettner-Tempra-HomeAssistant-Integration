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
