# PFLICHTENHEFT – OctaPiHub (Raspberry Pi 3)

> ## ⚠️ EIGENES PROJEKT – NICHT TEIL VON OCTALEDGER
> Projektname **„OctaPiHub“**: ein Smart-Home- und Musik-Hub auf dem Raspberry Pi, gesteuert durch zwei Timeular/ZEI-Würfel.
> Dieses Projekt ist **unabhängig** von OctaLedger, darf aber Teile davon übernehmen (siehe `RECHERCHE_…md` §18.7).
> Dieses Projekt liegt bewusst **außerhalb** des Git-Repositorys `OctaLedger` und erhält ein eigenes lokales Repository. Keine Geheimnisse committen; keine Veröffentlichung oder Push ohne ausdrückliche Freigabe.
> Stand: 24.11.2026 · Version 0.1 (Entwurf) · Begleitdokumente: `RECHERCHE_OctaPiHub-Raspberry-Pi.md`, `HANDBUCH_OctaPiHub-Raspberry-Pi.md`
> **Ehrlichkeitsvermerk:** Würfelprotokoll, Sense-HAT-Verhalten auf Trixie, Hue-Zertifikatsverhalten u. a. sind **nicht hardwareverifiziert** (siehe Recherche §18.11). Anforderungen, die davon abhängen, sind als „Prüfen“ markiert.

Priorität: **M** = Muss, **S** = Soll, **K** = Kann. Aufwand-/Termin-Angaben gibt es bewusst nicht.

---

## 1. Zweck und Ziele
1. Zwei Würfel steuern ein Smart Home **ohne PC**: Würfel 1 → Licht (Philips Hue), Würfel 2 → Musik.
2. Läuft **dauerhaft, still, headless** auf einem Raspberry Pi 3 im Wohnzimmer, startet nach Stromausfall von selbst.
3. Bedienung/Konfiguration per **Weboberfläche** und Kommandozeile; lokale Statusanzeige auf dem **Sense HAT**.
4. Zeigt Sensorwerte des Sense HAT an und stellt sie (optional) per MQTT bereit.
5. **Robust:** sauberes Errorhandling, Selbstheilung, nachvollziehbares Logging.

**Nicht-Ziele:** Zeiterfassung, Berichte, Cloud-Dienst, Sprachassistent, Ersatz für Home Assistant, Betrieb im Internet ohne VPN.

## 2. Systemkontext
```
 Würfel 1 ─BLE─┐                       ┌─ lokal HTTPS ─► Hue Bridge v2 (BSB002) ─► Lampen
 Würfel 2 ─BLE─┤                       ├─ Cast (lokal) ─► Nest Hub / Nest Speaker
               ├─► Raspberry Pi 3 ─────┼─ MQTT ────────► (optional) Home Assistant / Node-RED
 Sense HAT ────┘   (OctaPiHub-Dienst)    ├─ HTTP :8080 ──► Browser (Handy/PC) im LAN
 (LED, Sensoren,                       └─ SSH ─────────► Wartung
  Joystick)
```
Annahmen: WLAN-Heimnetz, Hue Bridge per Kabel, Internet vorhanden (Streaming), Nest-Geräte im selben Netz.

## 3. Randbedingungen
| ID | Randbedingung |
|---|---|
| RB-1 | Hardware: Raspberry Pi 3 (1 GB RAM), Sense HAT, USB-BLE-Dongle (empfohlen), gutes 5-V/2,5-A-Netzteil, SD-Karte ≥ 16 GB. |
| RB-2 | Betriebssystem: Raspberry Pi OS Lite 64-bit (Debian-13-Basis). |
| RB-3 | Sprache: Python ≥ 3.12 (Trixie liefert 3.13, nicht verifiziert), Code/Kommentare/Logs Englisch, Benutzertexte Deutsch. |
| RB-4 | Hue Bridge v2 (BSB002, Firmware 6.5.1978688000): nur lokale CLIP-v2-API (HTTPS). |
| RB-5 | Würfel: BLE nur Lesen/Abonnieren, keine Schreibzugriffe auf unbekannte Characteristics. |
| RB-6 | Keine Abhängigkeit von Timeular-Cloud/-App. |
| RB-7 | Alle Geheimnisse außerhalb des Repositorys. Lizenz vor Veröffentlichung klären (offen). |

## 4. Funktionale Anforderungen

### 4.1 Würfel (F-W)
| ID | Prio | Anforderung | Abnahme |
|---|---|---|---|
| F-W1 | M | Verbindung zu **zwei** Würfeln (frei benennbar, Adresse konfigurierbar). | Beide zeigen „verbunden“ im Dashboard. |
| F-W2 | M | Orientierungsereignisse (Seite 1–8, neutral) abonnieren und als Ereignis mit Zeitstempel (UTC) bereitstellen. | Seitenwechsel erscheint ≤ 1 s im Log. |
| F-W3 | M | Automatische Wiederverbindung mit Backoff (1 s → 60 s, Jitter), Neu-Abonnieren nach Reconnect. | Würfel entfernen/zurückbringen → Hub verbindet ohne Eingriff. |
| F-W4 | M | Stummschaltungs-Wachhund: „verbunden, aber keine Daten“ wird erkannt und führt zu Neuverbindung. | Simulierter Hänger → Reconnect ≤ konfigurierte Zeit. |
| F-W5 | S | Batteriestand je Würfel anzeigen (Prüfen: lesbar?). | Wert im Dashboard oder Hinweis „nicht verfügbar“. |
| F-W6 | M | Verbindungsaufbau nacheinander, nie parallel; ein Besitzer-Prozess für Bluetooth. | Kein „Operation already in progress“ im Dauerlauf. |
| F-W7 | S | Wahl des Bluetooth-Adapters (`hci0`/`hci1`) per Konfiguration. | Dongle wird genutzt. |
| F-W8 | S | Neutral-Seite (0/10) konfigurierbar behandeln (ignorieren, Pause, Aus). | Test je Einstellung. |

### 4.2 Regeln und Aktionen (F-R)
| ID | Prio | Anforderung |
|---|---|---|
| F-R1 | M | Zuordnung „Würfel × Seite → Aktion(en)“ in Konfigurationsdatei, in Weboberfläche bearbeitbar, vor Anwenden validiert. |
| F-R2 | M | Aktionen laufen über eine **Warteschlange (Outbox)** mit Wiederholung, Backoff und Ablaufzeit; Reihenfolge je Würfel gewahrt. |
| F-R3 | S | Mehrere Schritte je Aktion (z. B. Szene setzen, Lautstärke, 2 s warten, Musik starten) und einfache Bedingungen (Uhrzeit, Nachtmodus). |
| F-R4 | S | Entprellung: Seitenwechsel innerhalb < 1 s (einstellbar) führt nur die letzte Aktion aus. |
| F-R5 | M | Fehlgeschlagene Aktion beeinflusst andere Aktionen/Adapter nicht (Isolation). |
| F-R6 | S | Zustand beim Start: „letzte Szene wiederherstellen“ oder „nichts tun“ (einstellbar). |
| F-R7 | K | Joystick des Sense HAT und Weboberfläche können dieselben Aktionen auslösen. |

### 4.3 Licht – Philips Hue (F-H)
| ID | Prio | Anforderung |
|---|---|---|
| F-H1 | M | Kopplung per Knopfdruck auf der Bridge (Anleitung in der Oberfläche), App-Key sicher gespeichert. |
| F-H2 | M | Szenen/Räume/Zonen lesen und auswählen; Szene aktivieren (Prüfen: genauer Request). |
| F-H3 | S | Helligkeit/An-Aus je Raum. |
| F-H4 | M | Zertifikatsprüfung: Signify-signiertes Zertifikat gegen CA bzw. gespeicherten Fingerabdruck; bei Fehler klare Meldung, **kein** stilles Abschalten der Prüfung. |
| F-H5 | M | Ratenbegrenzung der Anfragen (Voreinstellung konservativ, z. B. höchstens wenige pro Sekunde), Umgang mit HTTP 429. |
| F-H6 | S | Szenenfarbe/Helligkeit für Sense-HAT-Anzeige bereitstellen. |
| F-H7 | K | Ereignisstrom (SSE) der Bridge nutzen, um Änderungen außerhalb des Hubs zu erkennen. |

### 4.4 Musik und Audio (F-M)
| ID | Prio | Anforderung |
|---|---|---|
| F-M1 | M | Aktion „Wiedergabe starten/pausieren/weiter/zurück/Lautstärke ±“ über Würfel 2. |
| F-M2 | M | Ausgabe auf **Google-Cast-Gerät** (Nest Hub/Speaker; Ziel wählbar, feste IP möglich). |
| F-M3 | S | Lokale Ausgabe über Pi (3,5 mm/USB-DAC/HDMI) als Alternative/Fallback. |
| F-M4 | M | Quellen: lokale Musikdateien/Playlists (Phase 2). |
| F-M5 | S | Quelle: Webradio (Phase 2). |
| F-M6 | K | Streaming (z. B. Spotify Connect, Premium erforderlich) – Phase 4, rechtlich/technisch prüfen. |
| F-M7 | S | Titel/Zustand/Lautstärke als Ereignisse für die Anzeige. |
| F-M8 | M | Fehlerfall (Gerät schläft, nicht gefunden): Wecken/Wiederholen, dann klare Meldung; kein Absturz. |
| F-M9 | K | Mehrere Zielgeräte/Gruppen. |

### 4.5 Sense HAT – Anzeige (F-D)
| ID | Prio | Anforderung |
|---|---|---|
| F-D1 | M | LED-Matrix zeigt Systemstatus: Fehler (rot), Verbindungsstatus je Komponente (6 Pixel). |
| F-D2 | M | Ansichten: Lichtszene (Farbe + Helligkeitsbalken), Würfelseite, Würfelbatterie, Musik (Play/Pause, Lautstärke), Standby/aus. |
| F-D3 | S | Titel als Lauftext auf Anforderung (nicht blockierend). |
| F-D4 | M | Prioritätenregel: Fehler > Aktion (kurz) > Dauerstatus; Wechsel entprellt. |
| F-D5 | M | Nachtmodus per Zeitplan (Helligkeit minimal/aus) und manuell; `low_light`-Voreinstellung. |
| F-D6 | S | Automatische Helligkeit nach Umgebungslicht (nur Sense HAT v2, Prüfen). |
| F-D7 | M | Ausfall der Anzeige (I2C-Fehler) stoppt den Hub nicht; Warnung wird geloggt. |
| F-D8 | S | Anzeige wird nur aus *einem* Task geschrieben; keine Aufrufe aus dem BLE-Callback. |

### 4.6 Sense HAT – Sensoren und Joystick (F-S)
| ID | Prio | Anforderung |
|---|---|---|
| F-S1 | M | Temperatur, Luftfeuchtigkeit, Luftdruck periodisch lesen (Intervall einstellbar, Standard 10–60 s). |
| F-S2 | M | **Temperatur-Korrektur** (CPU-Wärme) mit einstellbarem Faktor/Offset; Rohwert und korrigierter Wert getrennt. |
| F-S3 | S | IMU (Lage, Beschleunigung, Kompass) optional lesbar und anzeigbar. |
| F-S4 | S | Messwert-Verlauf (SQLite, gedrosselt, Aufbewahrung konfigurierbar, z. B. 30 Tage, Aggregation) und Diagramm im Dashboard. |
| F-S5 | S | Veröffentlichung per MQTT (Themen, Einheiten, Discovery optional). |
| F-S6 | S | Joystick-Ereignisse als Quelle für Aktionen (z. B. Lautstärke, nächste Szene, Ansicht wechseln). |
| F-S7 | S | Farb-/Helligkeitssensor (nur v2) als Messwert. |
| F-S8 | M | Sensorfehler führen zu „Wert nicht verfügbar“, nicht zu Absturz. |

### 4.7 Weboberfläche und CLI (F-U)
| ID | Prio | Anforderung |
|---|---|---|
| F-U1 | M | Seiten (Deutsch): **Übersicht** (Status aller Komponenten), **Würfel** (Zustand, Zuordnung), **Licht** (Hue-Kopplung, Szenen), **Musik** (Ziel, Steuerung), **Sense HAT** (Live-Werte, Anzeige-Einstellungen), **System** (Uhrzeit, CPU-Temp, RAM, Spannung, Uptime, Logs), **Einstellungen**. |
| F-U2 | M | Jedes Feld mit Hilfetext; leere Zustände erklären, was zu tun ist; Fehler mit Code, nie Stacktraces. |
| F-U3 | M | Live-Aktualisierung (SSE), ohne Endlosschleife im Browserskript. |
| F-U4 | M | Funktioniert auf Handy; Hell/Dunkel-Umschalter und zwei Schriftgrößen. |
| F-U5 | M | Zugriff nur im LAN, Token/Login für schreibende Aktionen. |
| F-U6 | S | CLI `octapihubctl`: `status`, `logs`, `scenes`, `pair-hue`, `test-cube`, `restart`, `export-config`. |
| F-U7 | S | Knöpfe „Dienst neu starten“, „Bluetooth zurücksetzen“, „Pi neu starten“ (mit Bestätigung). |
| F-U8 | S | Diagnose-Export (ZIP, ohne Geheimnisse und volle Adressen). |
| F-U9 | M | Es gibt **keinen Desktop**-Zwang; vollständige Bedienbarkeit ohne Monitor. |

### 4.8 Konfiguration, Daten, Sicherung (F-K)
| ID | Prio | Anforderung |
|---|---|---|
| F-K1 | M | Eine versionierte Konfigurationsdatei (`schema_version`), validiert beim Laden, atomar gespeichert. |
| F-K2 | M | Bei ungültiger Konfiguration: letzte gültige laden, Fehler melden, nicht abstürzen. |
| F-K3 | S | Export/Import der Konfiguration (ohne Geheimnisse), Import erst validieren, dann einmal anwenden. |
| F-K4 | M | Geheimnisse getrennt (`secrets.env`, 0600). |
| F-K5 | S | Backup-Skript für Konfiguration + Zustandsdatenbank. |

## 5. Nichtfunktionale Anforderungen
| ID | Prio | Kategorie | Anforderung | Messung |
|---|---|---|---|---|
| NF-1 | M | Verfügbarkeit | Dienst startet automatisch beim Boot (systemd), auch nach Stromausfall; `Restart=always`. | Stecker ziehen → Hub funktioniert ohne Eingriff. |
| NF-2 | M | Selbstheilung | Hänger des Dienstes (WatchdogSec) und des Systems (Hardware-Watchdog) führen zu Neustart. | Kill/Stop-Test, simulierter Hänger. |
| NF-3 | M | Reaktion | Seitenwechsel → Hue-Szene **typisch ≤ 1 s** (Zielwert, nicht verifiziert), Musikaktion ≤ 2 s. | Zeitstempel im Log. |
| NF-4 | M | Ressourcen | Gesamtverbrauch RAM im Dauerbetrieb **< 400 MB** (Zielwert; Schätzung), CPU-Last im Leerlauf **< 15 %**; kein Speicherwachstum über 7 Tage. | `free`, `ps`, Verlaufsmessung. |
| NF-5 | M | Dauerlauf | 7 Tage ohne manuellen Eingriff, ≥ 1000 Seitenwechsel ohne Fehlverhalten. | Testprotokoll. |
| NF-6 | M | Robustheit | Ausfall einer Komponente (Hue, Cast, MQTT, Sense HAT, WLAN, einzelner Würfel) lässt die übrigen weiterlaufen. | Fehlerinjektion je Komponente. |
| NF-7 | M | Logging | Python-`logging` → journald; Stufen DEBUG–CRITICAL; Redaktion von Tokens/Schlüsseln/Passwörtern und gekürzten Geräteadressen; Größenlimit (z. B. 100 MB). | Stichprobe Logs. |
| NF-8 | M | Fehlercodes | Eigene Ausnahmehierarchie mit stabilen Codes (`CH-<BEREICH>-NNN`) und deutscher Benutzermeldung; Fremdfehler an der Grenze übersetzt (`raise … from`). | Code-Review, Tests. |
| NF-9 | M | Sicherheit | Dienst ohne Root, SSH nur mit Schlüssel, Weboberfläche nur LAN, keine offenen Router-Ports, Geheimnisse 0600. | Checkliste Handbuch Kap. 14. |
| NF-10 | M | Zeit | Intern UTC (timezone-aware), Anzeige in `Europe/Zurich`; NTP aktiv. | Test Sommer-/Winterzeit. |
| NF-11 | M | Datenschutz | Keine Daten verlassen das Heimnetz außer Streaming-/Hue-Cloud-Funktionen, die ausdrücklich aktiviert wurden. | Netzwerkprüfung. |
| NF-12 | S | SD-Karten-Schonung | Schreibrate niedrig (Verlaufs-Aggregation, WAL, Log-Limit). | Schreibmenge/Tag messen. |
| NF-13 | M | Wartbarkeit | Schichten: Domäne ↔ Adapter (BLE, Hue, Cast, MQTT, Sense HAT, Audio) ↔ Oberfläche; Typannotationen, `mypy --strict`, `ruff`, Unit-Tests, englische Docstrings. | CI-/Skriptlauf grün. |
| NF-14 | S | Testbarkeit | Alle Adapter hinter Schnittstellen, Simulation ohne Hardware (Fake-Würfel, Fake-Hue, Fake-Sense-HAT). | Tests laufen auf dem PC. |
| NF-15 | S | Updatefähigkeit | Update per `git pull` + `pip install -e .` + Dienst neu starten; Rollback möglich. | Handbuch Kap. 12. |
| NF-16 | M | Ehrlichkeit | Dokumentation kennzeichnet nicht hardwareverifizierte Funktionen. | Review. |

## 6. Schnittstellen (Überblick)
| Schnittstelle | Richtung | Details |
|---|---|---|
| BLE (BlueZ/D-Bus, Bleak) | Würfel → Hub | Notify auf Orientierungs-Characteristic; ggf. Batteriedienst |
| Hue CLIP v2 | Hub → Bridge | HTTPS, Header `hue-application-key`, Szenen/Räume, optional SSE |
| Google Cast | Hub → Nest | `pychromecast`, HTTP-Medien-URL im LAN |
| MQTT | Hub ↔ Broker | Themen `octapihub/<gerät>/<wert>`; Broker lokal (Mosquitto) |
| I2C/Framebuffer/evdev | Hub ↔ Sense HAT | `sense-hat`-Bibliothek |
| HTTP/SSE | Browser ↔ Hub | Port 8080, Token |
| systemd/journald | Hub ↔ OS | `sd_notify`, Logs |

## 7. Datenmodell (Kurzfassung)
- **Konfiguration** (TOML/YAML): `schema_version`, `cubes[]` (Name, Adresse, Rolle), `mappings[]` (Würfel, Seite → Aktionen), `hue` (Bridge-IP, Zertifikats-Modus), `audio` (Ziel, Quelle), `display` (Ansichten, Nachtmodus, Helligkeit), `sensors` (Intervall, Korrektur), `mqtt`, `web` (Bind, Port).
- **Zustands-DB (SQLite, WAL):** `events` (Würfelereignisse, begrenzt), `outbox` (offene Aktionen), `sensor_samples`/`sensor_agg`, `state` (letzte Szene/Lautstärke, Geräteinfos).
- **Geheimnisse:** `secrets.env` (Hue-Key, MQTT-Passwort, Web-Token, optional Spotify).

## 8. Fehlercode-Schema (Vorschlag)
`CH-CFG-0xx` Konfiguration · `CH-BLE-0xx` Würfel/Bluetooth · `CH-PLUG-0xx` Erweiterungsmodule · `CH-HUE-0xx` Hue · `CH-CAST-0xx` Cast/Audio · `CH-MQTT-0xx` MQTT · `CH-SENSE-0xx` Sense HAT · `CH-WEB-0xx` Oberfläche/API · `CH-SYS-0xx` System (RAM, Spannung, Speicher) · `CH-DB-0xx` Datenbank.
Jeder Code: stabil, dokumentiert (Ursache, Benutzermeldung, Abhilfe), in der Weboberfläche und im Log.

## 9. Abnahmekriterien (Gesamt)
1. Frische SD-Karte + Handbuch → lauffähiger Hub, ohne Monitor, in angemessener Zeit.
2. Stromausfall-Test: nach Wiederkehr alle Komponenten wieder grün ohne Eingriff.
3. Fehlerinjektionen NF-6 bestanden; keine unbehandelten Ausnahmen im Journal.
4. 7-Tage-Dauerlauf NF-5 ohne Speicherwachstum (Verlauf dokumentiert).
5. Beide Würfel steuern wie konfiguriert; Sense HAT zeigt Status/Szene/Musik/Batterie.
6. Dokumentation vollständig und ehrlich (NF-16).

## 10. Phasenplan (Reihenfolge, ohne Zeitangaben)
| Phase | Inhalt | Ergebnis / Ausstiegskriterium |
|---|---|---|
| **P0 Spike** | OS aufsetzen; BlueZ-Version, USB-Dongle, Würfel lesen (Skript); Sense HAT testen (LED, Sensoren, Joystick); Hue-Kopplung + Szene per Skript; Messung RAM; Entscheidung Streamlit vs. HTMX. | Alle „Prüfen“-Punkte beantwortet; Entscheidungsprotokoll |
| **P1 Kern** | Dienstgerüst, Config, Fehler/Logging, Würfel-Worker (2×), Regelwerk/Outbox, Hue-Adapter, systemd-Autostart + Watchdog. | Würfel 1 schaltet Szenen stabil |
| **P2 Anzeige & Sensoren** | Sense-HAT-Display (Ansichten, Nachtmodus), Sensoren + Korrektur, Joystick. | Anzeige/Sensoren laufen, Fehlertoleranz geprüft |
| **P3 Web & CLI** | Weboberfläche (FastAPI/HTMX/SSE), `octapihubctl`, Systemseite, Diagnose-Export. | Vollständige Bedienung ohne Monitor |
| **P4 Musik** | Cast-Ausgabe, lokale Dateien/Webradio, Musikanzeige. | Würfel 2 steuert Musik stabil |
| **P5 Härtung** | Hardware-Watchdog, Fehlerinjektion, Dauerlauf, Sicherheits-Checkliste, Backup, Doku. | Abnahme §9 |
| **P6 Optional** | MQTT/Home Assistant, Streaming (Spotify Connect), Tailscale, USB-Boot, PULSE-Test. | nach Bedarf |

## 11. Teststrategie
- **Unit-Tests** (PC, ohne Hardware): Protokollparser, Regelwerk, Outbox, Konfigurationsvalidierung, Fehlercodes, Redaktion.
- **Adapter-Tests mit Fakes:** Hue (Mockserver inkl. 429/Zertifikatsfehler), Cast (Fake), MQTT (lokaler Broker), Sense HAT (Emulator-Fake).
- **Hardwaretests auf dem Pi** (Matrix): Würfel trennen/wiederverbinden, Bluetooth-Dienst neu starten, WLAN trennen, Hue-Bridge vom Strom, Cast-Gerät aus, Stromausfall, Unterspannung, volle SD-Karte, 2 Würfel gleichzeitig.
- **Langzeit:** 7 Tage, Speicher-/CPU-Verlauf, Log-Auswertung.
- **Qualitäts-Gate vor jedem Commit:** `pytest`, `ruff check`, `ruff format --check`, `mypy --strict` (analog OctaLedger).

## 12. Risiken
| Risiko | Wirkung | Gegenmaßnahme |
|---|---|---|
| WLAN/BLE-Koexistenz Pi 3 | Würfel-Aussetzer | USB-BLE-Dongle, Onboard-BT aus |
| Würfel erlaubt nur einen Client | Konflikt mit PC/OctaLedger | Ein Besitzer, klare Trennung |
| 1 GB RAM | Absturz/Swap | Lite-OS, schlanke Oberfläche, `MemoryMax`, Messung |
| SD-Karten-Ausfall | Datenverlust | Qualitätskarte, wenig Schreiben, Backup, später USB-SSD |
| Hue-Zertifikat/Pinning | Verbindungsfehler | CA-Prüfung statt starrem Pinning, klare Meldung |
| Cast-Eigenheiten (Schlaf, mDNS) | Musik startet nicht | feste IP, Wecken, Wiederholung |
| Sense-HAT-Temperatur verfälscht | falsche Werte | Korrektur/Riser-Kabel |
| Sense HAT v1/v2 unbekannt | Funktionsumfang unklar | P0 prüfen |
| Musikquellen/Streaming rechtlich/technisch | Funktion fällt aus | Lokal/Webradio zuerst, Streaming optional |
| Lizenz unklar | Veröffentlichung blockiert | Vor Veröffentlichung entscheiden |

## 13. Offene Fragen
1. Sense HAT v1 oder v2? 2. USB-BLE-Dongle-Modell? 3. Welche Musikquellen wirklich (lokal, Webradio, Spotify)? 4. Nest-Zielgerät(e) und IP? 5. Home Assistant gewünscht (MQTT)? 6. Zugriff von unterwegs (Tailscale)? 7. Welche Hue-Szenen je Würfelseite? 8. Soll der Hub auch ohne WLAN lokal (z. B. Sense-HAT-Licht) reagieren? 9. Eigenes Git-Repository und Lizenz? 10. Zwei Würfel gleichzeitig – Anzeige links/rechts?

## 14. Glossar
**BLE** Bluetooth Low Energy · **CLIP v2** lokale Hue-API · **Outbox** Warteschlange offener Aktionen · **SSE** Server-Sent Events (Live-Daten zum Browser) · **HTMX** kleine JS-Bibliothek für serverseitig gerenderte Seiten · **Watchdog** Überwachung, die bei Hängern neu startet · **Sense HAT** Aufsteckplatine mit LED-Matrix/Sensoren/Joystick · **Cast** Google-Protokoll für Medien auf Nest-Geräten.

## 15. Entwicklungs- und Commitnachweis

Die folgenden Zeitangaben stammen aus dem lokalen Git-Commit (Zeitzone Europe/Zurich).
Ein Commit dokumentiert implementierte und lokal geprüfte Software; er ist kein
Nachweis für einen Hardwaretest. Commits werden nach erfolgreicher Prüfung erstellt.

| Datum und Uhrzeit | Commit | Fertiggestellte Funktion und Nachweis |
|---|---|---|
| 2026-11-25 13:49:07 +01:00 | `9888f4abde23d20dc7d72787e551b8f0585461a0` | Python-Projektbasis, BLE-Orientierungsdecoder, Baseline/Entprellung und begrenztes Reconnect-Backoff; 8 lokale Unit-Tests, Ruff und mypy strict bestanden. Keine Hardwareprüfung. |
| 2026-11-25 13:57:50 +01:00 | `580a40f7bfb6a1ab449d2642456fb10e4019eb4f` | Versioniertes TOML-Konfigurationsladen und Validierung für zwei Würfelrollen, BLE-Adapter, Adressen und Entprellzeit; insgesamt 22 lokale Unit-Tests, Ruff, mypy strict und Pylance-Diagnose bestanden. Keine Hardwareprüfung. |
| 2026-11-25 14:20:19 +01:00 | `5c363a32ebf53b071aa373d85d270401d6e06c97` | Typisierter Plugin-Vertrag, Python-Entry-Point-Discovery und atomare Registrierung; 34 lokale Unit-Tests, Ruff und mypy strict bestanden. Keine Hardwareprüfung. |
| 2026-11-25 14:27:05 +01:00 | `09cd3ca762ad8e33f75f1f1922bc8e01492ac5fb` | Read-only BLE-Transportvertrag, Prüfung der Orientation-Notify-/Indicate-Fähigkeit und Fake für Ereignisse, Verbindungsabbruch und Fehler; 42 lokale Unit-Tests, Ruff und mypy strict bestanden. Keine Hardwareprüfung. |

Die Einträge verweisen jeweils auf den Commit mit der Funktion. Änderungen an diesem
Nachweis erfolgen in einem nachfolgenden Dokumentations-Commit, damit der tatsächliche
Funktions-Commit-Hash festgehalten werden kann.
