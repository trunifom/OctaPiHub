# OctaPiHub

Eigenständiger, geplanter Smart-Home- und Musik-Hub für Raspberry Pi und andere
Linux-Systeme, gesteuert mit Timeular-/ZEI-Würfeln. Das Projekt ist unabhängig
von OctaLedger und Timeular.

## Stand

OctaPiHub ist ein lauffähiger Hub **für Simulation und Entwicklung**: Würfel (bis zu drei),
Hue-Licht, Musik, Sense-HAT-Sensoren, LED-Matrix und Joystick sowie eine Weboberfläche sind
implementiert und mit Fakes getestet. **Nichts davon wurde bisher mit echter Hardware geprüft**
(weder Würfel, Raspberry Pi, Sense HAT, Hue Bridge, Google-Lautsprecher, mpv noch Bluetooth-Box).
Nach Nutzerangabe wurde im separaten OctaLedger-Projekt ein echter Würfel mit dem Musikplayer
erprobt; das belegt weder OctaPiHub-BLE noch die Hue-Lichtsteuerung noch die neue Musikanbindung.

| Teil | Status |
|---|---|
| Würfel (BLE, Seiten 0–8 inkl. Halterung, Entprellung, Akkustand, bis zu 3 Würfel) | implementiert, mit Fakes getestet; nicht hardwareverifiziert |
| Regeln und SQLite-Outbox (Warteschlange je Würfel, Wiederholung, Ablauf, kurze Gültigkeit für Musik) | implementiert, lokal getestet |
| Hue (Kopplung, Szenen, Plugin `hue`) | implementiert, gegen lokale TLS-Fakes getestet; nicht mit echter Bridge |
| Musik: Quellen (Ordner, Datei, m3u/pls, Radio, YouTube), Warteschlange, Aktionen | implementiert, mit Fakes getestet |
| Musik: Google Nest / Home / Nest Hub (pychromecast), Audioausgang des Raspberry Pi und Bluetooth-Box (mpv) | implementiert, mit Fakes getestet; **kein echtes Gerät, kein echtes mpv, keine echte Box**; Spotify nicht abspielbar |
| Sense HAT (Sensoren, Verlauf, Temperaturkorrektur, LED-Matrix mit 21 Ansichten, Joystick als Steuerung und Aktionsquelle) | implementiert, mit Fakes getestet |
| Weboberfläche (Übersicht, Würfel/Zuordnung, Licht, Musik, Sense HAT, Anzeige, System, Einstellungen, Sichern) | implementiert, per Test und Browser-Screenshots am PC geprüft; nicht auf Raspberry Pi/Handy |
| Export/Import (Einstellungen, Playlists), CSV-Export der Sensordaten, Log-Download, rotierende Logdatei | implementiert, getestet |
| systemd-Dienst, Installationsanleitung | vorhanden; **nicht auf einem echten Raspberry Pi ausprobiert** |

Lokale Tests:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Der Befehl setzt Python >= 3.12 voraus. Auf einer Entwicklungsmaschine mit älterer
Python-Version kann pytest derzeit mit `PYTHONPATH=src` ausgeführt werden, sofern
die verwendete Version die Quellsyntax unterstützt.

- [Installation auf dem Raspberry Pi](deploy/INSTALL.md)
- [Benutzerhandbuch](docs/BENUTZERHANDBUCH.md)
- [Pflichtenheft](PFLICHTENHEFT_OctaPiHub-Raspberry-Pi.md)
- [Architektur](docs/ARCHITEKTUR.md)
- [Hardware-Einrichtung (Hintergrund)](HANDBUCH_OctaPiHub-Raspberry-Pi.md)
- [Recherche und offene Fragen](RECHERCHE_OctaPiHub-Raspberry-Pi.md)

Dieses Repository ist lokal. Geheimnisse und persönliche Betriebsdaten gehören
nicht hinein. Eine Lizenz ist noch nicht gewählt; vor einer Veröffentlichung
müssen Lizenz und Rechte an übernommenem Code geklärt werden.

## Weboberfläche ausprobieren

```
pip install -e ".[dev]"
python -m octapihub.web --demo --port 8080      # simuliertes Sense HAT, Token: demo
python -m octapihub.web --config octapihub.toml --secrets secrets.env # echter Betrieb, Token aus OCTAPIHUB_WEB_TOKEN
```

Der Demo-Modus zeigt simulierte Werte und Beispieldaten und ist keine Messung. Ohne
`OCTAPIHUB_WEB_TOKEN` ist die Oberfläche nur lesend. Standardmäßig lauscht sie nur auf
`127.0.0.1`; für den Zugriff im LAN `--host` bewusst setzen und keine Router-Ports öffnen.

## Als Dienst auf dem Raspberry Pi

Dateien und Anleitung liegen in `deploy/` (systemd-Unit mit Härtung, Beispielkonfiguration, Umgebungsdatei, `INSTALL.md`); der Befehl `octapihub` startet die Oberfläche. Ein zu kurzes Token oder das Platzhalter-Token aus der Beispieldatei verhindert den Start (`CH-WEB-008`). **Noch nicht auf einem echten Raspberry Pi ausprobiert**; die Tests prüfen nur die Übereinstimmung der Dateien mit der Software.

## LED-Matrix und Joystick

Die 8×8-LEDs des Sense HAT zeigen **21 Ansichten**: Übersicht (Ampel je Komponente), Würfel, Steuerung, Musik, Akku, Temperatur, Luftfeuchtigkeit, Luftdruck, Verlauf, Uhr, Binäruhr, Laufschrift, Kompass, Wasserwaage, Umgebungsfarbe, Lampe, Regenbogen, Atmen, Farbpalette, Helligkeit und Aus. Joystick: links/rechts wechselt die Ansicht, hoch/runter wählt innerhalb der Ansicht (oder ändert die Helligkeit), Drücken erklärt per Laufschrift, langes Halten schaltet die Anzeige aus/ein. Würfelaktionen erscheinen kurz (Seite, dann Häkchen oder Kreuz); Verbindungsverlust, schwacher Akku und Fehler blinken als Symbol. Alles ist auf der Seite *Anzeige* live ansehbar und über einen virtuellen Joystick bedienbar. **Bis zu drei Würfel** mit frei wählbarer Rolle werden unterstützt; der Akkustand jedes Würfels wird gelesen und mit Ladehinweis angezeigt. Alles nur mit Fakes getestet, **nicht an echter Sense-HAT- oder Würfel-Hardware**.

## Musik

Würfelseiten, die Halterung und die Joystick-Richtungen können Musik abspielen und steuern
(Playlist starten oder pausieren, Pause, Weiter, Stopp, Nächster/Vorheriger Titel, Lauter, Leiser).
Wiedergabeziele sind Google Nest / Home / Nest Hub, der Audioausgang des Raspberry Pi oder eine
Bluetooth-Box; es spielt immer nur ein Ziel. Quellen sind Ordner und Dateien im Musikordner,
m3u/pls-Playlists, Radio-Adressen und YouTube. Einrichtung auf der Seite *Musik*, Zuordnung auf der
Seite *Würfel*. Optionale Abhängigkeit: `pip install ".[music]"` (pychromecast); `mpv` und `yt-dlp`
sind Systempakete. Bedienung: [Benutzerhandbuch](docs/BENUTZERHANDBUCH.md).

## Sichern, Importieren, Protokolle

Die Seite *Einstellungen → Sichern & Wiederherstellen* exportiert und importiert Einstellungen
(zweistufig prüfen, dann alles oder nichts übernehmen, mit Sicherungskopie), lädt Sensordaten als CSV
und Logdateien ohne Zugangsdaten herunter. Logs gehen zusätzlich in eine rotierende Datei
(`<data-dir>/logs/octapihub.log`; `--log-level`, `--log-dir`, `OCTAPIHUB_LOG_LEVEL`).
