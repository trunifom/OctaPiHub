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
| F-R3 | S | Mehrere Schritte je Aktion (z. B. Szene setzen, Lautstärke, 2 s warten, Musik starten) und einfache Bedingungen (Uhrzeit, Wochentag). Umgesetzt: Verzögerung 0–30 s je Aktion und Zeitfenster/Wochentage je Seite (mit Fakes getestet, nicht an Hardware). Ein „Nachtmodus“ als Bedingung gibt es nicht. |
| F-R4 | S | Entprellung: Seitenwechsel innerhalb < 1 s (einstellbar) führt nur die letzte Aktion aus. |
| F-R5 | M | Fehlgeschlagene Aktion beeinflusst andere Aktionen/Adapter nicht (Isolation). |
| F-R6 | S | Zustand beim Start: „letzte Szene wiederherstellen“ oder „nichts tun“ (einstellbar). |
| F-R7 | K | Joystick des Sense HAT und Weboberfläche können dieselben Aktionen auslösen. |
| F-R8 | M | Lichtsteuerung über Hue-Gruppen: Ein/Aus/Umschalten, Heller/Dunkler, Farbe setzen, nächste Farbe, nächste/vorherige Szene eines Raumes; Raum je Seite wählbar. Implementiert, nur gegen Fakes getestet. |
| F-R9 | M | Jede Würfelseite, die Halterung und jede Joystick-Richtung hat einen frei wählbaren Namen und eine Farbe; die Oberfläche zeigt je Seite im Klartext, was genau ausgelöst wird, und markiert die gerade aktive Seite. |
| F-R11 | M | Smart-Home-Aktionen für andere Systeme: Webhook, MQTT-Nachricht und Home-Assistant-Dienst (damit Matter, Zigbee, Google Home); Passwörter und Token nur in `secrets.env`; nur sicher nicht Zugestelltes wird wiederholt. Mit Fakes getestet, nicht mit echten Geräten. |
| F-R10 | M | Würfel lassen sich in der Weboberfläche per Bluetooth-Suche finden, hinzufügen und entfernen (1 bis 3); die Übersicht zeigt, wie viele verbunden sind. Die Belegung eines Würfels ist exportier- und auf einen anderen Würfel importierbar. Suche nur mit Fake geprüft. |

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
| F-M10 | M | Wiedergabeziele frei wählbar und je Playlist/Aktion einstellbar: Google Nest/Home (Cast), Nest-Hub-Displays, Audioausgang des Raspberry Pi, Bluetooth-Box. Es spielt immer nur ein Ziel. |
| F-M11 | M | Würfelseiten, Halterung (Seite 0) und Joystick-Richtungen können Playlists starten/pausieren und Wiedergabe steuern (Pause, Weiter, Stopp, Alles stoppen, Nächster/Vorheriger Titel, Lauter, Leiser); in der Weboberfläche als Aktionskatalog wählbar. |
| F-M12 | M | Playlists mit Quelle (Ordner, Datei, m3u/pls, Radio-Adresse, YouTube), Zufall, Wiederholen, Lautstärke und Ziel; Dateien nur aus dem Musikordner; Playlists exportier- und importierbar. |
| F-M13 | S | Ansicht „Musik“ auf der LED-Matrix (Zustand, Lautstärke, Titel per Laufschrift). |
| F-M14 | M | Je Playlist **und je Würfelseite** einstellbar: Zufall (an/aus), Wiederholen (aus / ganze Playlist / einzelner Titel) und Wiedergabegerät; zusätzlich ein Standardgerät je Würfel. Gilt für lokale Quellen und YouTube/YouTube Music über mpv; auf Cast-Geräten nicht für YouTube-Playlists (Gerät spielt sie selbst). Spotify nicht möglich. |
| F-M15 | S | Halterung (Würfel senkrecht, Seite 0): empfohlene Belegung „Wiedergabe stoppen“ für Musik, keine Aktion für Licht. |

**Geplant (nicht implementiert):** Bilder oder Dashboards auf Nest-Hub-Displays, Gerätegruppen, direkte Matter- und CoAP-Anbindung (heute über Home Assistant, Webhook oder MQTT).

**Stand F-M:** F-M1, F-M2, F-M3 (inkl. Bluetooth-Box), F-M4, F-M5, F-M7, F-M8 (Fehlerisolation, klare Meldung, kein Absturz; kein „Wecken“ eines schlafenden Geräts), F-M10 bis F-M13 sind **implementiert und mit Fakes getestet**; **keine** Prüfung mit echtem mpv, Google-Lautsprecher, Nest Hub oder Bluetooth-Box. F-M6 (Spotify) ist **nicht** umgesetzt: Spotify-Links werden erkannt und mit `CH-MUS-003` abgelehnt. F-M9 ist durch mehrere wählbare Ziele teilweise erfüllt (keine Gruppen, nicht mehrere gleichzeitig).

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
| F-S9 | M | Jede Sensorgruppe einzeln aktivierbar; Intervall, Verlauf, Anzeige (Wert/Graph) und Anzeigepräzision einstellbar. Rundung verändert keine gespeicherten Rohwerte. |
| F-S10 | S | Sense-HAT-Version und verfügbare Fähigkeiten erkennen; wenn automatische Erkennung nicht zuverlässig ist, Version manuell in der Konfiguration setzen. |

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
| F-K11 | M | Zeit: Die Uhr stellt das Betriebssystem über NTP; OctaPiHub zeigt Zeiten standardmäßig in der Zeitzone des Geräts, einstellbar und ohne Neustart wirksam (Oberfläche, LED-Uhr, Nachtmodus); die Seite System zeigt den NTP-Abgleich. Zeitstempel intern UTC. Mit Fakes getestet. |
| F-K12 | M | Selbsttest `--selftest`: prüft Voraussetzungen des Geräts (Python, Gruppen, Einstellungen, Token, Datenordner, Port, Bluetooth, I2C, Sense HAT, Joystick, mpv, yt-dlp, Tonkarte, pychromecast, Musikordner, Hue/MQTT/Home Assistant erreichbar, Zeitabgleich) und nennt zu jedem Problem den nächsten Schritt; Rückgabewert 0/1/2. Mit Fake-Prüfquelle getestet, nicht auf einem Raspberry Pi. |
| F-U9 | M | Es gibt **keinen Desktop**-Zwang; vollständige Bedienbarkeit ohne Monitor. |

### 4.8 Konfiguration, Daten, Sicherung (F-K)
| ID | Prio | Anforderung |
|---|---|---|
| F-K1 | M | Eine versionierte Konfigurationsdatei (`schema_version`), validiert beim Laden, atomar gespeichert. |
| F-K2 | M | Bei ungültiger Konfiguration: letzte gültige laden, Fehler melden, nicht abstürzen. |
| F-K3 | S | Export/Import der Konfiguration (ohne Geheimnisse), Import erst validieren, dann einmal anwenden. |
| F-K4 | M | Geheimnisse getrennt (`secrets.env`, 0600). |
| F-K5 | S | Backup-Skript für Konfiguration + Zustandsdatenbank. |
| F-K6 | M | Sense-HAT-Einstellungen liegen in derselben versionierten, validierten TOML-Konfiguration und werden später auch über die Weboberfläche bearbeitet. |
| F-K7 | M | Weboberfläche: Export und Import von Einstellungen (Musik, Playlists, Zuordnung, Sense HAT, Hue-Einstellungen ohne Schlüssel) als JSON; Import zuerst prüfen, dann alles oder nichts übernehmen, mit Sicherungskopie der bisherigen Datei. |
| F-K8 | S | Sensordaten (Verlauf) als CSV herunterladen (Messgröße, Zeitraum, Mittelwerte oder Rohwerte). |
| F-K9 | M | Protokollierung: Konsole/journald, letzte Meldungen in der Oberfläche, rotierende Logdatei mit Größenbegrenzung, einstellbare Ausführlichkeit, Zugangsdaten unkenntlich, Download ohne Geheimnisse. |
| F-K10 | M | Fehlerbehandlung: stabile Fehlercodes mit Katalog (Bedeutung, Abhilfe), globale Fehlerseite mit Kennung, abgesicherter Start je Dienst, Fallback auf die neueste gültige Sicherung der Konfiguration, `--check`, Gesundheitsstatus `/healthz`, Protokollierung unbehandelter Ausnahmen. |
| F-U9 | M | Eingebaute Hilfe in der Oberfläche (Schnellstart, Handbuch, Installation, Smart Home, Fehlersuche, Sicherheit, Glossar, FAQ, Fehlercodes) mit Suche und **Hilfe-Button an jedem Eingabefeld**. |

**Stand F-K:** F-K1 bis F-K4, F-K6 bis F-K10 sind implementiert und getestet (Fakes/Temp-Dateien). F-K5 (Backup-Skript) ist nur als Anleitung (`tar`, `deploy/INSTALL.md` Abschnitt 10) und als Import-Sicherungskopie vorhanden, nicht als Skript. Importiert wird nur, was zu dieser Installation passt; die Würfelliste und Schlüssel werden bewusst nie übertragen.

Schema v3 bereitet die Sense-HAT-Einstellungen vor: Temperatur, Feuchte und Druck
sind standardmäßig aktiviert; IMU- und Farbsensorgruppen bleiben deaktiviert, sind
aber individuell konfigurierbar. Als Kompatibilitäts-Override sind `auto`, `v1` und
`v2` vorgesehen. Der typisierte Sensor-/Matrix-/Joystick-Port, der lazy geladene
Hardwareadapter und ein Fake für hardwarefreie Tests sind implementiert. Die automatische
Erkennung leitet v1 nicht allein aus dem Fehlen eines Farbsensors ab. Messworker,
Verlaufsspeicherung und GUI werden schrittweise ergänzt; der Adapter wurde nicht auf
echter Hardware geprüft.

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
| 2026-11-25 14:47:03 +01:00 | `ab28a39ae6de23a42e7bc13446b85c1c152b98b8` | Bleak/BlueZ-Adapter und BLE-Laufzeit mit serialisiertem Verbindungsaufbau, zwei Würfel-Workern, Entprellung, Status und begrenztem Reconnect; 56 lokale Unit-Tests, Ruff, Python-Formatprüfung und mypy strict bestanden. Keine Raspberry-Pi- oder Hardwareprüfung. |
| 2026-11-25 14:56:11 +01:00 | `6bd4101cde91a27bc70215d4204f1a787555cf60` | Validierte, deterministische TOML-Speicherung via temporärer Datei, fsync und atomarem Ersetzen; Fehler lassen die bisherige Datei intakt; 59 lokale Unit-Tests, Ruff, Python-Formatprüfung und mypy strict bestanden. |
| 2026-11-25 15:31:49 +01:00 | `69f7382384dbd723e2e5286ca125336adf32a1b2` | Schritt 4: Schema-v2-Regelzuordnungen mit v1-Kompatibilität, Rule Engine und SQLite-WAL-Outbox mit FIFO je Würfel, Retry/Backoff, Ablauf, Neustart-Recovery und Fehlerisolation; 79 lokale Tests, Ruff check, Python-Formatprüfung für `src`/`tests` und mypy strict bestanden. Keine Hardwareprüfung. Der repo-weite Formatcheck meldet eine bereits vorhandene Abweichung in `HANDBUCH_OctaPiHub-Raspberry-Pi.md`, die unverändert blieb. |
| 2026-11-25 15:37:24 +01:00 | `33f40e6676edb96e011eaff7db4bca3877af5741` | Schritt 5: automatisierter End-to-End-Pfad vom Fake-BLE-Würfel über Runtime, Regeln und SQLite-Outbox bis zum Fake-Hue-Plugin; Baseline wird ignoriert, zwei Aktionen werden der Reihe nach erfolgreich ausgeführt; 80 lokale Tests, Ruff check, Python-Formatprüfung und mypy strict bestanden. Keine OctaPiHub-Hardwareprüfung. Der Nutzer berichtete separat von einem Realtest mit Würfel/Musikplayer in OctaLedger; Hue-Licht wurde nicht real getestet. |
| 2026-11-25 16:03:48 +01:00 | `3a9b34460c0ada5741f676bee0e89338c923ad99` | Hue-CLIP-v2-Adapter für Szenenliste und Szenen-Recall mit TLS-CA-/Hostnameprüfung, optionaler CA-Datei, Drosselung und HTTP-429-`Retry-After`-Unterstützung in der Outbox; lokaler HTTPS-Fake-Server; 88 lokale Tests, Ruff, Python-Formatprüfung und mypy strict bestanden. Keine Prüfung mit realer Bridge; Signify-Zertifikat und Szenen-Recall müssen an der Bridge bestätigt werden. |
| 2026-11-25 16:27:11 +01:00 | `e965401c79efafbdb799ab26ed91e338de6b002a` | Sense-HAT-Konfiguration in Schema v3: v1/v2-Dateien bleiben lesbar und werden auf v3 migriert; Sensorguppen mit Intervall, Verlaufs- und Anzeigeeinstellungen, Temperaturkorrektur, Historienaufbewahrung, Matrix, Joystick sowie v1/v2-Override und v2-Farb-Sensorparameter; nur Temperatur/Feuchte/Druck standardmäßig aktiv. 95 Tests, Ruff, Formatprüfung, mypy strict und `git diff --check` bestanden. Keine Hardwareprüfung; Adapter, automatische Fähigkeitserkennung, Datenhaltung und GUI folgen separat. |
| 2026-11-25 16:41:29 +01:00 | `4d92751d39f219e5a5b4781e9e50f77a57398717` | Typisierter Sense-HAT-Anwendungs-Port und lazy Hardwareadapter für Umwelt-/IMU-Sensoren, v2-Farbsensor, Joystick und LED-Matrix; sichere automatische Fähigkeitserkennung, stabile Fehlercodes und hardwarefreier Fake. 119 Tests, Ruff check, Formatprüfung, mypy und `git diff --check` bestanden. Nicht auf echter Sense-HAT-Hardware verifiziert; Messworker, Verlaufsspeicherung und GUI folgen separat. |
| 2026-11-25 16:52:43 +01:00 | `effcd5426b5aea515cfaf2d33562a6fb04930fad` | Sense-HAT-Messworker: je aktivierter Sensorgruppe ein Task mit eigenem Intervall, Fehlerisolation pro Gruppe und Sink, Status `unavailable` für fehlende Hardware, Rohwerte unverändert mit separater Temperaturkorrektur. 127 Tests, Ruff, Formatprüfung, mypy und `git diff --check` bestanden. Nur mit Fake getestet, nicht auf echter Hardware verifiziert; Verlauf, GUI und Composition Root folgen separat. |
| 2026-11-25 16:56:38 +01:00 | `f4aa1ca6df91f0bc90c1548ef95e83a816069352` | Persistente Sense-HAT-Historie: gebündelt schreibender Recorder (nur Gruppen mit aktivem Verlauf, begrenzter Puffer, Fehlertoleranz), SQLite-WAL-Speicher mit Rohwerten, Aufbewahrungs-Löschung und Zeitreihen-/Bucket-Abfrage, Fehlercode CH-DB-002. 134 Tests, Ruff, Formatprüfung, mypy bestanden. Nur mit Temp-DB/Fakes getestet, nicht auf echter Hardware/SD-Karte verifiziert; GUI und Composition Root (inkl. flush beim Beenden) folgen. |
| 2026-11-25 17:33:36 +01:00 | `3b8562271c9c66d5a5218d1ca38f0315214f6e19` | Sense-HAT-Dienst und Composition Root: Lebenszyklus von Port, Messworker, Recorder und Verlaufsspeicher; Start/Stopp mit Flush, degradierter Betrieb bei fehlender Hardware oder defekter Historie, Farbsensor-Parameter nur bei aktivierter Farbgruppe. 141 Tests, Ruff, Formatprüfung, mypy bestanden. Nur mit Fakes getestet, nicht auf echter Hardware verifiziert; kein startbarer Hub-Prozess, IMU-Konfiguration und GUI fehlen noch. |
| 2026-11-25 18:12:17 +01:00 | `0612948effb92b22cc1ab239c51563dd3e0cb0ba` | Weboberfläche (FastAPI/Jinja2/SSE): Übersicht, Sense-HAT-Seite mit Live-Karten und Verlaufsdiagramm, Einstellungsformular mit deutschen Feldfehlern, Token-geschütztes atomares Speichern mit Neustart des Sense-HAT-Dienstes, Hell/Dunkel und zwei Schriftgrößen, Handy-Tab-Leiste, Demo-Modus. Temperatur/Feuchte/Druck zeigen standardmäßig Wert und Diagramm. 160 Tests, Ruff, Formatprüfung, mypy bestanden; Layout per Edge-Headless-Screenshots am PC geprüft (Desktop, schmale Breite). Nicht auf Raspberry Pi, echtem Handy oder mit Hilfsmitteln zur Barrierefreiheit geprüft; Seiten Würfel, Licht, Musik, System, Login und LAN-Erzwingung fehlen. |
| 2026-11-25 19:38:37 +01:00 | `d00ed74fa1cd3598466b8ec00c1b6d8231e7e9dc` | Würfel-Dienst (`CubeService`) und Composition für echte Adapter: BLE-Laufzeit, Regeln, Outbox und Plugins mit Router- und Dispatcher-Task je Würfel, Neustart ausgefallener Dispatcher, gemeldeter statt abstürzender Pipeline-Fehler (fehlendes Plugin, Datenordner). 165 Tests, Ruff, Formatprüfung, mypy bestanden. Nur mit Fakes getestet; weder echtes Bluetooth, echte Würfel noch Hue verifiziert; kein Hue-Plugin installierbar (keine Hue-Konfiguration). |
| 2026-11-25 19:41:57 +01:00 | `cc0d460fa0a318d7495cf815a872944e3caf6a56` | Würfel-Seite der Weboberfläche (Verbindungsstatus, zuletzt erkannte Seite, Zuordnung nur lesend, Fehlercodes, Übersichtskarte live) und gemeinsamer Start von Sense-HAT- und Würfel-Dienst durch `python -m octapihub.web`; Demo mit simulierten Würfeln. 168 Tests, Ruff, Formatprüfung, mypy bestanden; Seite per Edge-Headless-Screenshot geprüft. Keine Hardwareprüfung; Zuordnung nicht in der Oberfläche bearbeitbar, kein Systemdienst-/systemd-Paket. |
| 2026-11-25 19:47:43 +01:00 | `39ed01ad5b7476091e26644ac8b8ec7cbf48686e` | Konfigurationsschema v4 mit validiertem Abschnitt `[hue]` (aktiv, Host, Port, CA-Datei, TLS-Servername); v1–v3 werden migriert, ein Application Key in der TOML-Datei wird abgelehnt. Tests inkl. Grenzfälle je Feld; keine Hardware. |
| 2026-11-25 19:48:52 +01:00 | `6f2375eb6829e0d1e684883fc8f263881436df46` | Speicher für `secrets.env`: atomar, Anlage mit 0600, Ablehnung von für andere lesbaren Dateien, strenges Parsen ohne Wertausgabe, `CH-SEC-001`. Die zwei POSIX-Rechtetests wurden unter Windows übersprungen, also nicht ausgeführt. |
| 2026-11-25 19:54:04 +01:00 | `78589b51f52b39b0b1fc04f08712e488b9fb1b52` | Hue-Client: Kopplung per Knopfdruck (`CH-HUE-004` bei nicht gedrückter Taste), gemeinsame Fehlerübersetzung, optionaler TLS-Servername bei vollständiger Zertifikatsprüfung, `CH-HUE-005`. Gegen lokalen TLS-Fake getestet, nicht gegen eine echte Bridge; ob echte Bridges den Servernamen wie angenommen benötigen, ist ungeprüft. |
| 2026-11-25 19:55:56 +01:00 | `9edf66f24abc3666899d6504dd305a94e74eceb8` | `HueService`: Zustände, serialisiertes Koppeln mit typisierten Fehlern, Schlüsselspeicherung, Szenen-Cache (veraltet bei Fehlern), degradierter Start bei defekten Secrets oder CA-Datei, Plugin `hue` mit wiederholbarem Fehler vor dem Koppeln. 22 Tests; nur Fakes. |
| 2026-11-25 20:03:19 +01:00 | `25ed741341b48acc3a28f1baeef6adee07f7d9e3` | Seite Licht (Bridge eintragen, koppeln, Szenen laden/testen, trennen), Token-Schutz, Composition des Plugins `hue`, `SettingsManager.apply_hue`, Demo mit simulierter Bridge; Kettentest Würfelgeste → Outbox → Hue inklusive Wiederholung bis zum Koppeln. 283 Tests (2 unter Windows übersprungen), Ruff, Formatprüfung, mypy bestanden; Seite per Edge-Headless-Screenshot geprüft. Keine echte Hue Bridge, kein echter Würfel, kein Raspberry Pi. |
| 2026-11-25 20:24:33 +01:00 | `c4c10361f345e7e549dcc61cf3abd11381dd643a` | Bearbeitbare Würfelzuordnung (F-R1) auf der Seite Würfel: Auswahl einer Hue-Szene je Seite, Validierung, atomares Speichern, sofortiges Umschalten der Regeln ohne Neuverbindung, Rücksetzen der laufenden Regeln bei Schreibfehler, gesperrte Seiten für nicht darstellbare Aktionen. 314 Tests (2 unter Windows übersprungen), Ruff, Formatprüfung, mypy bestanden; Seite per Edge-Headless-Screenshot geprüft. Nur Fakes. |
| 2026-11-25 20:30:34 +01:00 | `e8b1c5733e8501355702b4d3bb931a1207f2fe5a` | Seite System (F-U1): Laufzeit, Auslastung, Speicher, CPU-Temperatur, Unterspannung/Drosselung, Speicherplatz mit je Quelle einzeln ausfallsicherem Lesen; Log-Redaktion (NF-7) und Protokollanzeige. Nur gegen Testdateien/Fakes, nicht auf einem Raspberry Pi; `vcgencmd`-Ausgabe nicht an echter Hardware geprüft. |
| 2026-11-25 20:33:02 +01:00 | `4b87d4282cc7ef415e57b7f93b0949288367fb4c` | Diagnose-Export (F-U8) als ZIP ohne Geheimnisse, volle Adressen und Bridge-Adresse (auch innerhalb von Logzeilen maskiert), nur mit Token. Der Test deckte beim Schreiben eine Lücke auf (Bridge-Adresse in Logzeilen), die vor dem Commit geschlossen wurde. |
| 2026-11-25 20:36:19 +01:00 | `6d03f542d236b57b0d11d5f5b363c0d17a9e96ce` | systemd-Unit mit Härtung, Beispielkonfiguration, Umgebungsdatei, Installationsanleitung, Befehl `octapihub`, `OCTAPIHUB_HOST`/`_PORT` und Startabbruch bei schwachem oder Platzhalter-Token (`CH-WEB-008`). 413 Tests (2 unter Windows übersprungen), Ruff, Formatprüfung, mypy bestanden. **Nicht unter systemd auf einem Raspberry Pi ausgeführt**; die Tests prüfen nur die Übereinstimmung der Dateien mit der Software. |
| 2026-11-25 21:10:39 +01:00 | `446f04bfe6d06a0714923610ca6f2d1917e669f4` | Konfigurationsschema v5: 1 bis 3 Würfel mit beliebiger Rolle (`light`, `music`, `general`) und LED-Matrix-Einstellungen (Helligkeit, Ansichten, Startansicht, Auto-Weiterschalten, Nachtmodus); v1–v4 werden migriert. Tests je ungültigem Feld und für 3-Würfel-Zuordnung. Keine Hardware. |
| 2026-11-25 21:18:14 +01:00 | `dd2215fcb55e37a3eb9e6b06e653f191b3b242ba` | Akkustand der Würfel (F-W5): lesender Zugriff auf das Standard-Merkmal *Battery Level* im Transportvertrag (Bleak, Fake), periodisches, nie verbindungsbeendendes Lesen mit letztem bekanntem Wert, Ladehinweise (≤20 % / ≤10 %) auf Würfel-Seite und Übersicht, Werte im Diagnose-Paket. **Ob echte ZEI-Würfel dieses Merkmal anbieten, ist ungeprüft** (nur Fakes). |
| 2026-11-25 21:26:49 +01:00 | `01948a2a2aa046e43aee185f66b7092e6b92062c` | LED-Matrix-Kern: Zeichenfläche, 3×5-Schrift, Icons, 19 Ansichten als reine Funktionen (u. a. Übersicht, Würfel, Akku, Klima, Verlauf, Uhren, Laufschrift, Kompass, Wasserwaage, Lampe, Regenbogen, Atmen, Palette) und Joystick-Zustandsmaschine (Ansicht, Auswahl, Helligkeit, Info, langer Druck, Drehung, Auto-Weiterschalten). Im Test fand die Robustheitsprüfung (NaN/Inf) echte Fehler, die behoben wurden. Keine Hardware. |
| 2026-11-25 21:36:00 +01:00 | `ca7efcad445164901da6c3c6ad3cb1886a06d2d1` | Anzeige-Dienst (F-D): ein Task besitzt Matrix und Joystick, Nachtfenster, Umgebungshelligkeit, Rückmeldungen zu Würfelaktionen, Hinweise bei Verbindungsverlust/Akku/Fehlern, Fehlerisolation mit Backoff und Erholung (F-D7), Neustart bei geänderten Einstellungen. 8 Wiederholungsläufe der Dienst-Tests stabil. Nur an Fake-Board geprüft. |
| 2026-11-25 21:44:47 +01:00 | `a48d8be51f7911df65b0c1a63bc001e8217c4047` | Seite Anzeige: Live-Vorschau (SSE), Galerie aller Ansichten, virtueller Joystick, Helligkeit, Einstellungen für Ansichten/Nachtmodus u. a.; Demo mit drei Würfeln und simulierten Akkus. 1057 Tests (2 unter Windows übersprungen), Ruff, Formatprüfung, mypy bestanden; Seiten per Edge-Headless-Screenshot geprüft. **Nicht an echter Sense-HAT-/Würfel-Hardware geprüft; die Joystick-Richtungen bei gedrehter Anzeige sind eine Annahme.** |
| 2026-11-25 22:01:53 +01:00 | `acb0c6d1d89ea3bcfa0ab0e896505a4e47f197fb` | Sense-HAT-Joystick als Aktionsquelle (F-R7): reservierter Quellenname `Joystick` mit fünf Eingängen, eigene Warteschlange, LED-Ansicht „Steuerung“ (kurzer Druck führt die Aktion aus, langes ◀ ▶ wechselt die Ansicht), Zuordnungsblock und „Jetzt auslösen“ in der Weboberfläche. Mit Fakes getestet; die Joystick-Richtungen bei gedrehter Anzeige sind eine Annahme, nicht an echter Hardware geprüft. |
| 2026-11-25 22:08:14 +01:00 | `daab29042125ff03d5ed739acffe4a1c6f16fa13` | Konfigurationsschema v6: Abschnitt `[music]` (Ziele, Playlists, Musikordner, Adresse für Wiedergabegeräte) mit strenger Validierung; Würfelseite 0 = Halterung ist zuordenbar (die Baseline löst nie aus). v1–v5 werden migriert. Tests je ungültigem Feld. Keine Hardware. |
| 2026-11-25 22:14:06 +01:00 | `bb4a70a28372a349bb817ca788589b615dce4e25` | Musikdienst: Quellenerkennung (Ordner, Datei, URL, YouTube, Spotify) mit Abwehr unsicherer Pfade und Links, Warteschlangenregeln, Aktionsplugin `music` (play, pause, resume, toggle, stop, stop.all, next, previous, volume.*), exklusive Wiedergabe, Zielauswahl, Zeitlimits, typisierte Fehler CH-MUS-001 bis 004 und kurze Gültigkeit von Musikaktionen in der Outbox; nicht behebbare Fehler werden nicht wiederholt. Nur mit Fake-Playern getestet. |
| 2026-11-25 22:21:00 +01:00 | `bac4210d0d0e2b26b908088e55cec252a5c9cb4c` | Bibliotheksauflösung (Ordner, m3u/pls, nur innerhalb des Musikordners), Token-Registry für Dateien, mpv-Player (JSON-IPC, geprüfter Gerätename, Bluetooth-Verbindung per `bluetoothctl`). 31 Tests mit Fakes; **kein echtes mpv, keine echte Box**. |
| 2026-11-25 22:28:22 +01:00 | `b608f9b97f03baef03be41ff6bbe1eff39c38fe7` | Google-Cast-Player (pychromecast, optionale Abhängigkeit `music`) mit eigener Warteschlange, Playerfabrik, Route `GET /media/{token}` für Dateien, Verdrahtung im Composition Root, `CH-MUS-005`. 1318 Tests, Ruff, Formatprüfung, mypy bestanden; **kein echtes Cast-Gerät**. |
| 2026-11-25 22:32:58 +01:00 | `2262c311f10005f92f992ccef93019d466e94076` | Seite Musik: Geräte und Playlists bearbeiten (alle Feldfehler gleichzeitig, deutsche Meldungen), atomar speichern und live anwenden, Geräte- und Audiosuche, Status, Steuerungstasten und Testwiedergabe. Mit Fake-Playern getestet. |
| 2026-11-25 22:35:15 +01:00 | `847d98a73dab5aa484fd718db93d8c7ee0d38d18` | Aktionskatalog im Zuordnungseditor: pro Würfelseite, Halterung und Joystick-Richtung Hue-Szene, Playlist abspielen/starten-pausieren oder Wiedergabe steuern; ungültige Auswahl wird je Feld abgelehnt. Getestet. |
| 2026-11-25 22:38:25 +01:00 | `12bfe8ff4acbdd8568db987c7962161afbffe6e8` | LED-Ansicht „Musik“ (Zustandssymbol, Lautstärkebalken, Titel per Laufschrift, Ticker-Hinweis); die Anzeige fällt nie wegen Musik aus. 1383 Tests, Ruff, Formatprüfung, mypy bestanden. Nur virtuelle Matrix, nicht auf dem echten Sense HAT. |
| 2026-11-25 22:45:27 +01:00 | `2d0b7431256acc3f7711af7317a14e89a79651cb` | Seite Sichern & Wiederherstellen: Export/Import von Einstellungen und Playlists (zweistufig, alles oder nichts, Sicherungskopie), CSV-Export der Sensordaten, Log-Download ohne Geheimnisse, rotierende Logdatei mit `--log-level`/`--log-dir`. 1426 Tests bestanden; nur mit Fakes und temporären Dateien. |
| 2026-11-25 22:50:03 +01:00 | `c2db872951c222eb8084003ec748fa8206b0febf` | Demo mit Fake-Musik, systemd-Unit mit Audio-Gruppe und mehr Speicherreserve, Beispielkonfiguration mit `[music]`, neu geschriebene Installationsanleitung `deploy/INSTALL.md` (inkl. Hardware-Prüfliste und Fehlertabelle). 1430 Tests bestanden. **Nicht auf einem Raspberry Pi ausgeführt.** |
| 2026-11-25 22:52:59 +01:00 | `936b46cd34f387974262f5b4ce4ffe663002843b` | Dokumentation: Benutzerhandbuch, Architektur, Pflichtenheft und README zu Musik, Export/Import und Logging; Nachweis der Funktions-Commits. Keine Hardwareprüfung. |
| 2026-11-25 23:02:24 +01:00 | `bc6200749dc1a6bd0e4c0eedd91842f7c216b347` | Live-Musikkarte auf der Übersicht (statt Platzhalter), Statusabfrage der Player alle 5 s, Cast-Streamtyp (Dateien gepuffert, Radio live). 1432 Tests bestanden. Nur Fakes. |
| 2026-11-25 23:15:01 +01:00 | `5617832b121cb1e3130d3ca1e26ad5ff179719cd` | Musik-Optionen je Würfelseite (Gerät, Zufall, Wiederholen), Wiedergabegerät je Würfel, Zufall für YouTube-Playlists über mpv/yt-dlp (Option ungeprüft). 1439 Tests bestanden. |
| 2026-11-25 23:29:09 +01:00 | `e43017e29ad6c042e17fbf567ebec5d227a7ce42` | Hue-Lichtsteuerung über Gruppen: Ein/Aus/Umschalten, Heller/Dunkler, Farben, nächste Farbe, nächste/vorherige Szene; Räume je Seite wählbar; Testtasten auf der Seite Licht. 1467 Tests bestanden; gegen lokalen TLS-Fake, nicht gegen eine echte Bridge. |
| 2026-11-25 23:44:03 +01:00 | `05826cd2d5fcdde11008c350c4fa5c4d0ff660ab` | Würfel koppeln per Bluetooth-Suche, hinzufügen und entfernen; Namen und Farben je Seite (Schema v7); Klartext-Details je Seite; aktive Seite live markiert; Export/Import eines einzelnen Würfels. 1512 Tests bestanden; nur Fake-Scanner und Fake-Würfel. |
| 2026-11-25 23:59:52 +01:00 | `8c33ce0dbdc4d3b51db020c8a769b4d20bc80b0d` | Smart-Home-Aktionen: Webhook, MQTT (eigener 3.1.1-Publisher) und Home Assistant (Weg zu Matter); Passwörter/Token nur in secrets.env; Wiederholung nur bei sicher nicht Zugestelltem. 1578 Tests bestanden; lokale Test-Server und Test-Broker, **kein echtes Gerät**. CoAP und direktes Matter nicht umgesetzt. |
| 2026-11-26 00:18:43 +01:00 | `a3ed0dff9a76a73bd4dbde9d058a92c74bff3609` | Fehlerbehandlung überarbeitet (Fehlerkatalog, globale Handler mit Kennung, abgesicherter Start, Konfigurations-Fallback, `--check`, `/healthz`, Protokollierung unbehandelter Ausnahmen); eingebaute Hilfe mit Suche und ⓘ-Hilfe an jedem Eingabefeld; behebt einen Absturz des Log-Filters. 1609 Tests bestanden. |
| 2026-11-26 00:18:45 +01:00 | `9fa391e150f9acbee19e112158a1fe03242a53f9` | README neu mit sieben Screenshots des Demo-Modus, Architektur- und Pflichtenheft-Ergänzungen; Benutzerhandbuch in die Software verlegt. Keine Hardwareprüfung. |

Die Einträge verweisen jeweils auf den Commit mit der Funktion. Änderungen an diesem
Nachweis erfolgen in einem nachfolgenden Dokumentations-Commit, damit der tatsächliche
Funktions-Commit-Hash festgehalten werden kann.
