# Fehlersuche und Protokolle

Wenn etwas nicht klappt, gehen Sie in dieser Reihenfolge vor: **Meldung lesen → Fehlercode nachschlagen → Ursache prüfen → Protokoll ansehen → Diagnose-Paket speichern.**

## 1. Schnelle Diagnose
1. **Seite System:** Sind Temperatur, Speicher und Stromversorgung in Ordnung? Welche Meldungen stehen unten?
2. **Übersicht:** Welche Karte ist gelb oder rot?
3. **Fehlercode** der Meldung unter **Hilfe → Fehlercodes** nachschlagen. Dort steht, was er bedeutet und was zu tun ist.
4. Am Raspberry Pi: `octapihub --check` prüft die Einstellungen und Zugangsdaten (siehe unten).

## 2. Wie OctaPiHub mit Fehlern umgeht
| Mechanismus | Was er bewirkt |
|---|---|
| **Fehlerisolierung** | Fällt ein Teil aus (Sense HAT, Lautsprecher, Bridge, Würfel), laufen alle anderen weiter. Beim Start wird jeder Dienst einzeln gestartet; scheitert einer, erscheint ein gelbes Banner „… konnte nicht gestartet werden“. |
| **Warteschlange je Würfel** | Aktionen laufen der Reihe nach. Vorübergehende Fehler werden mit wachsenden Pausen wiederholt, abgelaufene verworfen. |
| **Kurze Gültigkeit** | Musik 60 s, Licht und Smart Home 120 s: eine Aktion, die nicht mehr rechtzeitig klappt, wird nicht Minuten später nachgeholt. |
| **Keine doppelte Wirkung** | Nur Fehler, bei denen **sicher nichts gesendet** wurde (Verbindung nicht möglich), werden wiederholt. Bei „abgelehnt“ oder „keine Antwort“ nicht, damit „Umschalten“ nicht zweimal wirkt. |
| **Atomares Speichern** | Einstellungen werden ganz oder gar nicht gespeichert. Ungültige Werte werden vorher abgelehnt, mit Erklärung am Feld. |
| **Sicherungskopien** | Vor Import und Änderung der Würfelliste wird die Konfiguration als `octapihub.toml.bak-<Zeit>` gesichert (die neuesten fünf bleiben). |
| **Fallback beim Start** | Ist die Konfigurationsdatei unbrauchbar, nutzt der Hub die neueste gültige Sicherung und zeigt einen Hinweis. Die kaputte Datei bleibt zum Reparieren liegen. |
| **Fehlerseite** | Ein unerwarteter Fehler zeigt eine ruhige Seite mit Code `CH-WEB-500` und einer **Kennung**, die im Protokoll steht; das Programm läuft weiter. |
| **Watchdog** | Hängt das Programm (blockierte Hauptschleife), meldet es sich nicht mehr bei systemd und wird nach höchstens 60 s neu gestartet (`journalctl` zeigt „Watchdog timeout“). Teilprobleme lösen keinen Neustart aus. |
| **Protokollierung** | Jeder Fehler wird mit Code ins Protokoll geschrieben, Zugangsdaten sind unkenntlich. Nicht abgefangene Ausnahmen in Threads und Hintergrundaufgaben werden ebenfalls protokolliert. |

## 3. Protokolle (Logs)
### Wo?
- **Oberfläche:** Seite *System* (letzte Meldungen, filterbar) und *Sichern & Wiederherstellen* (Download).
- **Systemdienst:** `journalctl -u octapihub -f` (live) oder `journalctl -u octapihub -n 100`.
- **Logdatei:** `/var/lib/octapihub/logs/octapihub.log` (rotierend: 1 MB je Datei, 5 alte Dateien, insgesamt ca. 6 MB).

### Ausführlichkeit
In `/etc/octapihub/octapihub.env` setzen: `OCTAPIHUB_LOG_LEVEL=DEBUG` (oder `INFO`, `WARNING`, `ERROR`), danach `sudo systemctl restart octapihub`. Auf der Befehlszeile: `--log-level`, Logordner mit `--log-dir` (`-` schaltet die Datei aus).

### Was steht drin, was nicht?
- Zugangsdaten, Token und Bearer-Werte sind **unkenntlich** (`***`); Bluetooth-Adressen sind gekürzt (`AA:**:**:**:**:FF`); Ausnahmetexte (die Geheimnisse enthalten könnten) werden nicht protokolliert, nur der Typ.
- Fehlercodes stehen in jeder Fehlerzeile. Suchen Sie danach mit `grep CH-MUS /var/lib/octapihub/logs/octapihub.log`.

### Stufen
| Stufe | Bedeutung |
|---|---|
| ERROR / CRITICAL | Etwas ist gescheitert, das Aufmerksamkeit braucht |
| WARNING | Etwas war nicht ideal, ist aber aufgefangen (z. B. Sicherung verwendet, Aktion abgelehnt) |
| INFO | Normale Ereignisse (gespeichert, gestartet, verbunden) |
| DEBUG | Technische Einzelheiten für die Fehlersuche |

## 4. Gesundheitsstatus für Überwachung
`http://<Adresse>:8080/healthz` liefert JSON für Überwachungsprogramme (z. B. Uptime Kuma): `status` ist `ok` oder `degraded`; `parts` zeigt je Teil (Sense HAT, Würfel, Hue, Musik, Smart Home), ob er in Ordnung ist, mit Fehlercodes. Die Adresse braucht kein Token und enthält keine Geheimnisse.

## 5. Einstellungen prüfen
`octapihub --check --config /etc/octapihub/octapihub.toml --secrets /etc/octapihub/secrets.env`
Rückgabewert **0** = in Ordnung, **1** = Warnungen (z. B. Sicherung verwendet, Zugangsdatei unsicher, Token fehlt), **2** = unbrauchbar. Die Ausgabe nennt den Grund.

## 6. Häufige Probleme
### Dienst und Oberfläche
| Beobachtung | Ursache und Lösung |
|---|---|
| Dienst startet nicht, `CH-WEB-008` | Token fehlt, ist zu kurz (mindestens 12 Zeichen) oder noch der Platzhalter. In `octapihub.env` ein langes, zufälliges Token setzen. |
| Dienst startet nicht, `CH-CFG-…` | Konfiguration ungültig: `octapihub --check` zeigt das Feld. Gibt es eine Sicherung, wird sie automatisch genutzt. |
| Oberfläche nicht erreichbar | Der Dienst lauscht standardmäßig nur auf `127.0.0.1`. Für das Heimnetz `OCTAPIHUB_HOST` auf die LAN-Adresse setzen (Installation, Abschnitt 8). |
| Speichern geht nicht (`CH-WEB-003/004`) | Token fehlt oder ist falsch. |
| Gelbes Banner „… konnte nicht gestartet werden“ | Dieser Dienst ist ausgefallen, der Rest läuft. Ursache im Protokoll (Code), dann Dienst neu starten. |

### Würfel
| Beobachtung | Ursache und Lösung |
|---|---|
| Bleibt „Verbindet …“ | Würfel gleichzeitig mit dem Smartphone gekoppelt (trennen), außer Reichweite, Akku leer, falsche Adresse. Kippen, damit er aufwacht. |
| Löst nichts aus | Ist er „Verbunden“? Ändert sich „zuletzt erkannte Seite“? Ist für die Seite eine Aktion gespeichert? Probieren Sie *Jetzt auslösen*. |
| Neuer Würfel wird nicht gefunden | Würfel kippen und die Suche wiederholen; Bluetooth am Pi eingeschaltet (`bluetoothctl show`)? Sonst die Adresse von Hand eintragen. |

### Licht
| Beobachtung | Ursache und Lösung |
|---|---|
| „Taste nicht gedrückt“ (`CH-HUE-004`) | Runde Taste an der Bridge drücken, dann innerhalb von 30 s koppeln. |
| Zertifikatsfehler (`CH-HUE-002`) | Unter Licht → Erweiterte Einstellungen die Zertifikatsdatei (CA) oder den Namen im Zertifikat eintragen. |
| „Nächste Szene“ tut nichts | Für den Raum gibt es keine Szenen: Szenen in der Hue-App anlegen, dann *Szenen laden*. |

### Musik
| Beobachtung | Ursache und Lösung |
|---|---|
| `CH-MUS-004` | Musik ist aus, kein Gerät gewählt, Playlist/Gerät gelöscht. |
| `CH-MUS-003` | Quelle nicht gefunden, außerhalb des Musikordners oder Spotify. |
| `CH-MUS-002` | Gerät nicht erreichbar: WLAN, Lautsprecher an, mpv installiert, Box in Reichweite. |
| `CH-MUS-005` | Es fehlt `pychromecast` oder `mpv`. |
| Datei auf Google-Gerät spielt nicht | Der Dienst lauscht nur auf `127.0.0.1`: `OCTAPIHUB_HOST` auf die LAN-Adresse setzen. |
| Datei „nicht gefunden“ | Musikordner liegt unter `/home`? Nach `/srv/music` verlegen. |

### Smart Home
Siehe [Smart Home](smarthome.md), Abschnitt „Testen und Fehler“.

### Sense HAT
| Beobachtung | Ursache und Lösung |
|---|---|
| Keine Messwerte, `CH-SENSE-001` | I2C einschalten (`sudo raspi-config nonint do_i2c 0`), `ls /dev/i2c-1`, Aufsatz fest aufgesteckt? Gruppe `i2c`. |
| Joystick tut nichts | Ansicht „Steuerung“ aktiv? Joystick unter Einstellungen eingeschaltet? Gruppe `input`? |
| Diagramm leer | Verlauf ausgeschaltet oder noch keine Werte (gebündeltes Speichern, einige Minuten). |

## 7. Diagnose-Paket
Seite **System → Diagnose-Paket**: ein ZIP mit Zustand, Konfiguration ohne Geheimnisse, gekürzten Adressen und den letzten Meldungen. Es enthält **keine** Zugangsdaten und lässt sich gefahrlos weitergeben, wenn Sie Hilfe suchen.

## 8. Wenn gar nichts hilft
1. `sudo systemctl status octapihub` und `journalctl -u octapihub -n 100`.
2. Dienst neu starten: `sudo systemctl restart octapihub`.
3. Gesicherte Konfiguration prüfen: `ls /etc/octapihub/` zeigt `octapihub.toml.bak-…`.
4. Notfalls mit der Beispielkonfiguration `deploy/octapihub.toml.example` neu beginnen und die Export-Datei importieren (*Sichern & Wiederherstellen*).
