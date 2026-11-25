# OctaPiHub – P0-Testplan

Stand: 2026-11-25.

## Aktuell automatisiert

Auf dem Entwicklungsrechner laufen reine Domänen-/Protokolltests:

- Ein-Byte-Orientierungsprotokoll
- bekannte und unbekannte Neutralwerte
- Baseline, Entprellung und Neutralstellung
- begrenztes exponentielles Reconnect-Backoff

Diese Tests beweisen keine BLE-, Hardware- oder Live-Netzwerkfunktion.

## P0-Hardwarematrix

| Probe | Ergebnis | Status |
|---|---|---|
| Raspberry Pi OS Lite 64-Bit und Python 3.12/3.13 | nicht geprüft | offen |
| BlueZ-Version und USB-Dongle/Adapterwahl | nicht geprüft | offen |
| Würfel 1: Dienste, Notify, Werte 0/10/1–8, Firmware | nicht geprüft | offen |
| Würfel 2: Dienste, Notify, Werte 0/10/1–8, Firmware | nicht geprüft | offen |
| Zwei Würfel serialisieren Verbindung und Wiederabonnement | nicht geprüft | offen |
| Batteriedienst und weitere lesbare Charakteristiken | nicht geprüft | offen |
| Sense HAT v1/v2, I²C, LED, Sensoren, Joystick | nicht geprüft | offen |
| Hue-Bridge-Kopplung, CLIP-v2-Szene, Zertifikatprüfung | nicht geprüft | offen |
| RAM/CPU im Leerlauf und Dauerlauf | nicht geprüft | offen |

Jede Hardwareprobe wird in späteren Sitzungen mit Datum, Gerätenummer statt voller
Bluetooth-Adresse, Firmware, OS-/Kernelversion, Ergebnis und Auswertung dokumentiert.
