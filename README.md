# OctaPiHub

Eigenständiger, geplanter Smart-Home- und Musik-Hub für Raspberry Pi und andere
Linux-Systeme, gesteuert mit Timeular-/ZEI-Würfeln. Das Projekt ist unabhängig
von OctaLedger und Timeular.

## Stand

Die Umsetzung hat begonnen. Aktuell gibt es ein schlankes Python-Projektgerüst
sowie hardwareunabhängige Tests für die Würfelprotokoll- und Entprellungslogik.
Der Dienst selbst und echte Hardwareadapter sind noch nicht implementiert.

| Teil | Status |
|---|---|
| Projektgerüst / Qualitätswerkzeuge | vorhanden |
| Würfel-Protokolldecoder | erster Stand, nur Unit-getestet |
| Entprellung / Reconnect-Backoff | erster Stand, nur Unit-getestet |
| BLE-Verbindung zu echten Würfeln | nicht hardwareverifiziert |
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
- [Handbuch und Hardwaretests](HANDBUCH_OctaPiHub-Raspberry-Pi.md)
- [Recherche und offene Fragen](RECHERCHE_OctaPiHub-Raspberry-Pi.md)

Dieses Repository ist lokal. Geheimnisse und persönliche Betriebsdaten gehören
nicht hinein. Eine Lizenz ist noch nicht gewählt; vor einer Veröffentlichung
müssen Lizenz und Rechte an übernommenem Code geklärt werden.
