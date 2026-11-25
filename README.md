# OctaPiHub

Eigenständiger, geplanter Smart-Home- und Musik-Hub für Raspberry Pi und andere
Linux-Systeme, gesteuert mit Timeular-/ZEI-Würfeln. Das Projekt ist unabhängig
von OctaLedger und Timeular.

## Stand

Die Umsetzung hat begonnen. Es gibt ein schlankes Python-Projektgerüst,
hardwareunabhängige Würfelprotokoll- und Entprellungslogik sowie Laden, Validierung
und atomare Speicherung der ersten versionierten TOML-Konfiguration. Es gibt außerdem
einen typisierten Plugin-Vertrag und Discovery für vertrauenswürdige Python-Plugins.
Der BLE-Transportvertrag und ein hardwarefreier Fake sind vorhanden; der produktive
Bleak-Adapter und eine hardwarefrei getestete Laufzeit für beide Würfel sind ergänzt.
Schema-v2-Regelzuordnungen und eine generische SQLite-WAL-Outbox sind implementiert.
Schema v3 ergänzt eine validierte Sense-HAT-Konfiguration mit pro Sensorgruppe
einstellbaren Messintervallen, Verlauf, Anzeigeart und Präzision. Ein typisierter
Anwendungs-Port, ein lazy geladener Hardwareadapter für Umweltwerte, IMU, v2-Farbsensor,
Joystick und LED-Matrix sowie ein konfigurierbarer Fake sind hardwarefrei getestet.
Automatische Fähigkeitserkennung stuft ein Board ohne erkannten Farbsensor bewusst
nicht automatisch als v1 ein.
Ein automatisierter End-to-End-Test führt ein simuliertes Würfelereignis durch Regeln
und Outbox bis zu einem Fake-Hue-Plugin. Ein echter Hue-CLIP-v2-Adapter kann Szenen
auflisten und aktivieren und wird gegen einen lokalen HTTPS-Fake-Server getestet.
Kopplung/Key-Verwaltung, ein startbarer Systemdienst und ein Musik-Aktionsadapter fehlen.
OctaPiHub wurde nicht mit echter Hardware oder einer Hue Bridge geprüft.
Nach Nutzerangabe wurde im separaten OctaLedger-Projekt ein echter Würfel mit dem
Musikplayer erprobt; das belegt weder OctaPiHub-BLE noch die Hue-Lichtsteuerung.

| Teil | Status |
|---|---|
| Projektgerüst / Qualitätswerkzeuge | vorhanden |
| Würfel-Protokolldecoder | erster Stand, nur Unit-getestet |
| Entprellung / Reconnect-Backoff | erster Stand, nur Unit-getestet |
| Versionierte TOML-Konfiguration | v1/v2-kompatibel; Schema v3 mit Sense-HAT-Einstellungen |
| Plugin-Vertrag und Entry-Point-Registry | erster Stand, nur Unit-getestet |
| Regelzuordnungen und SQLite-WAL-Outbox | generisch implementiert, lokal getestet |
| Fake-Würfel → Regeln → Outbox → Fake Hue | als lokaler End-to-End-Test abgedeckt |
| Hue CLIP v2 (Szenenliste/-Recall) | implementiert, TLS und API lokal gefaket; nicht hardwareverifiziert |
| BLE-Transportvertrag und Fake | implementiert, hardwarefrei getestet |
| BLE-Laufzeit und Bleak/BlueZ-Adapter | implementiert, mit Fakes getestet |
| BLE-Verbindung zu echten Würfeln / Raspberry Pi | nicht hardwareverifiziert |
| Sense-HAT-Port, Hardwareadapter und Fake | implementiert, hardwarefrei getestet; nicht auf echter Hardware verifiziert |
| Sense-HAT-Messworker (Intervalle je Sensorgruppe, Fehlerisolation, Temperaturkorrektur neben Rohwert) | implementiert, mit Fake getestet; nicht auf echter Hardware verifiziert |
| Sense-HAT-Sensorverlauf (SQLite/WAL, gebündeltes Schreiben, Aufbewahrung, Zeitreihen-Abfrage) | implementiert, mit Fake/Temp-DB getestet; noch nicht im Dienst verdrahtet |
| Sense-HAT-Weboberfläche | noch nicht implementiert |
| Hue-/Musikadapter | Hue CLIP v2 vorhanden; Pairing/Key-Verwaltung und Musik fehlen |

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

- [Pflichtenheft](PFLICHTENHEFT_OctaPiHub-Raspberry-Pi.md)
- [Architektur](docs/ARCHITEKTUR.md)
- [Handbuch und Hardwaretests](HANDBUCH_OctaPiHub-Raspberry-Pi.md)
- [Recherche und offene Fragen](RECHERCHE_OctaPiHub-Raspberry-Pi.md)

Dieses Repository ist lokal. Geheimnisse und persönliche Betriebsdaten gehören
nicht hinein. Eine Lizenz ist noch nicht gewählt; vor einer Veröffentlichung
müssen Lizenz und Rechte an übernommenem Code geklärt werden.
