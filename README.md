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
Die Laufzeit ist noch nicht in einen startbaren Systemdienst eingebunden und wurde
nicht mit echten Würfeln oder einem Raspberry Pi geprüft.

| Teil | Status |
|---|---|
| Projektgerüst / Qualitätswerkzeuge | vorhanden |
| Würfel-Protokolldecoder | erster Stand, nur Unit-getestet |
| Entprellung / Reconnect-Backoff | erster Stand, nur Unit-getestet |
| Versionierte TOML-Konfiguration | Laden, Validieren und atomisches Speichern implementiert |
| Plugin-Vertrag und Entry-Point-Registry | erster Stand, nur Unit-getestet |
| BLE-Transportvertrag und Fake | implementiert, hardwarefrei getestet |
| BLE-Laufzeit und Bleak/BlueZ-Adapter | implementiert, mit Fakes getestet |
| BLE-Verbindung zu echten Würfeln / Raspberry Pi | nicht hardwareverifiziert |
| Hue, Sense HAT, Musik und Weboberfläche | nicht implementiert |

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
