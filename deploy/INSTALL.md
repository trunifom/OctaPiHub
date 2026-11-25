# OctaPiHub als Dienst auf dem Raspberry Pi installieren

> **Stand:** Diese Anleitung und die Dateien in `deploy/` wurden noch **nicht auf einem echten
> Raspberry Pi ausprobiert**. Sie sind aus der Systemd-Dokumentation abgeleitet und durch
> Tests auf Konsistenz mit der Software geprüft (Befehlszeile, Beispielkonfiguration).
> Melden Sie Abweichungen, sobald Sie sie auf dem Gerät finden.

## 1. Voraussetzungen
- Raspberry Pi OS Lite (64 Bit empfohlen) mit Python 3.12 oder neuer, Bluetooth (BlueZ) und,
  falls verwendet, aktiviertem I2C für das Sense HAT (`sudo raspi-config` → Interfaces → I2C).
- Für das Sense HAT zusätzlich: `sudo apt install sense-hat` bzw. das Python-Paket `sense-hat`
  im selben Python-Umfeld wie OctaPiHub.

## 2. Benutzer und Ordner
```bash
sudo useradd --system --create-home --home-dir /var/lib/octapihub --shell /usr/sbin/nologin octapihub
sudo usermod -aG bluetooth,i2c,input octapihub
sudo install -d -o octapihub -g octapihub -m 0750 /etc/octapihub /var/lib/octapihub
sudo install -d -o root -g root -m 0755 /opt/octapihub
```

## 3. Programm installieren
```bash
sudo git clone <Ihr-Repository> /opt/octapihub
sudo python3 -m venv /opt/octapihub/venv
sudo /opt/octapihub/venv/bin/pip install /opt/octapihub
```
Update später: `sudo git -C /opt/octapihub pull && sudo /opt/octapihub/venv/bin/pip install /opt/octapihub`
und danach `sudo systemctl restart octapihub`.

## 4. Konfiguration und Token
```bash
sudo install -o octapihub -g octapihub -m 0600 /opt/octapihub/deploy/octapihub.toml.example /etc/octapihub/octapihub.toml
sudo install -o octapihub -g octapihub -m 0600 /opt/octapihub/deploy/octapihub.env.example /etc/octapihub/octapihub.env
sudo nano /etc/octapihub/octapihub.toml      # Bluetooth-Adressen der Würfel eintragen
sudo nano /etc/octapihub/octapihub.env       # langes, zufälliges Token eintragen
```
Die Datei `secrets.env` (Hue-Schlüssel) legt OctaPiHub beim Koppeln selbst mit Rechten 0600 an.
Dateien mit offenen Rechten werden aus Sicherheitsgründen abgelehnt.

## 5. Dienst einrichten und starten
```bash
sudo cp /opt/octapihub/deploy/octapihub.service /etc/systemd/system/octapihub.service
sudo systemctl daemon-reload
sudo systemctl enable --now octapihub
sudo systemctl status octapihub
```

## 6. Weboberfläche öffnen
Standardmäßig lauscht der Dienst nur auf dem Gerät selbst (`127.0.0.1`). Für den Zugriff im
Heimnetz tragen Sie in `/etc/octapihub/octapihub.env` die LAN-Adresse des Geräts ein
(`OCTAPIHUB_HOST=192.168.1.50`), starten den Dienst neu und öffnen `http://192.168.1.50:8080`.
**Geben Sie keine Ports im Router frei.** Änderungen in der Oberfläche brauchen das Token.

## 7. Fehler suchen
- Meldungen ansehen: `journalctl -u octapihub -f` (Zugangsdaten sind unkenntlich gemacht).
- In der Oberfläche: Seite **System** (Zustand, letzte Meldungen, Diagnose-Paket).
- Dienst startet nicht: `systemd-analyze verify /etc/systemd/system/octapihub.service`.
- Bluetooth/I2C ohne Zugriff: Gruppenmitgliedschaft des Benutzers `octapihub` prüfen (`id octapihub`).
